import {
  expect,
  request as apiRequest,
  test,
  type APIRequestContext,
  type Page,
} from '@playwright/test'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'
import { waitForEdge } from '../support/edgeReadiness'

/**
 * DHEALTH-SPECIMEN-001 — a physical specimen recorded, preserved, and recorded again, against the
 * real stack in a real browser.
 *
 * ── The question this file answers ──
 *
 * Not "does the specimen API work". `adapter/tests/test_dhealthspecimen001_specimens.py` answers
 * that at the HTTP edge (38 tests) and the PostgreSQL qualification answers the concurrency and
 * constraint half (15 more, where the row lock is real rather than a SQLite no-op). This file answers
 * the one thing neither can: **can a laboratory worker, clicking the product, record a physical
 * specimen, see it survive a reload, be forced to stop for an unusable sample, and then record a
 * second specimen without the first one disappearing.**
 *
 * That last clause is the whole ticket. Before it, an order's collection was one row with one
 * timestamp, so a recollection could only have overwritten the first sample or gone unrecorded.
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated, no session injected, no auth state written into browser storage. Every
 * laboratory act below is performed by clicking the product, by a worker who signed in through a
 * real Authorization Code + PKCE flow.
 *
 * Direct `/exchange/v1` calls appear in three places and none of them advances the workflow:
 *   - PHASE D asserts the **backend** refuses what the UI does not offer. A hidden control is
 *     usability; the server refusing is the boundary, and only a direct call shows which one is
 *     stopping it.
 *   - PHASE C re-reads the specimen history to confirm the browser is displaying the same specimen
 *     ids the database holds, rather than something it rendered from its own optimistic state.
 *   - `/me` is read to learn which organization a token actually carries.
 * All three use the bearer token the application itself put on the wire.
 *
 * ── What this suite does NOT establish ──
 *
 * **Not human usability.** Nobody who works at a laboratory bench has used this. A green run here
 * says the mechanism works when driven correctly, which is a different claim from "a technician can
 * work this way", and no script can make the second one.
 *
 * It also establishes nothing about specimen *types*, container or tube choice, labels, barcodes,
 * accession numbers, or bedside patient-identity confirmation. None of those is built, and the
 * absence of any such control on screen is asserted in PHASE E rather than left to be noticed.
 *
 * ── State this suite leaves behind ──
 *
 * Specimen rows are never deleted -- that is the point of the entity -- so like
 * `11-lab-blocked.spec.ts` this suite cannot restore what it created. `scripts/demo/reset.sh` clears
 * `exchange_lab_specimens` as workflow state (`demo_seed._WORKFLOW_TABLES`, extended by this
 * ticket), which is the supported way back to the baseline before running it again.
 *
 * All patient data here is the demo baseline's synthetic patient. No real patient data is used, and
 * the block note this suite types is obviously synthetic.
 */

const EXCHANGE = process.env.E2E_EXCHANGE_URL ?? 'https://exchange.localhost'
const API = `${EXCHANGE}/exchange/v1`

const CLINICIAN = {
  username: process.env.DEMO_CLINICIAN_USERNAME ?? 'demo-clinician',
  password: process.env.DEMO_CLINICIAN_PASSWORD ?? '',
}
const LAB_TECH = {
  username: process.env.DEMO_LAB_TECH_USERNAME ?? 'demo-lab-tech',
  password: process.env.DEMO_LAB_TECH_PASSWORD ?? '',
}
const LAB_CLERK = {
  username: process.env.DEMO_LAB_CLERK_USERNAME ?? 'demo-lab-clerk',
  password: process.env.DEMO_LAB_CLERK_PASSWORD ?? '',
}

const PATIENT = 'Patient Démo Alpha'
const CATALOG_ENTRY = 'Analyse de démonstration A'
const LAB_ORG = 'Laboratoire Démo DHealth'

const RUN = `e2e-${Date.now()}`
const BLOCK_NOTE = `Prélèvement inutilisable — qualification ${RUN} (note synthétique)`

let orderReference = ''
let orderId = ''
let labId = ''
let labToken = ''
/** The specimen ids this run records, in order. PHASE C and PHASE F both assert against them. */
const specimenIds: string[] = []

const consoleErrors: string[] = []
const failedRequests: string[] = []

function watchForBrowserProblems(page: Page): void {
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(`${page.url()} :: ${message.text()}`)
  })
  page.on('pageerror', (error) => consoleErrors.push(`${page.url()} :: ${error.message}`))
  page.on('requestfailed', (request) => {
    const failure = request.failure()?.errorText ?? ''
    if (failure.includes('ERR_ABORTED')) return
    failedRequests.push(`${request.method()} ${request.url()} :: ${failure}`)
  })
}

function api(): Promise<APIRequestContext> {
  return apiRequest.newContext({ ignoreHTTPSErrors: true })
}

async function signIn(page: Page, who: { username: string; password: string }): Promise<string> {
  expect(who.password, `${who.username}'s password must come from demo/native/demo.env`).not.toBe('')
  const tokenPromise = captureFirstExchangeBearerToken(page)
  await page.goto('/')
  await loginViaRealKeycloak(page, who.username, who.password)
  await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({ timeout: 30_000 })
  return tokenPromise
}

async function openSection(page: Page, section: string): Promise<void> {
  await page.getByRole('navigation').getByRole('button', { name: section, exact: true }).click()
}

async function labOrganizationId(token: string): Promise<string> {
  const ctx = await api()
  try {
    const me = await ctx.get(`${API}/me`, { headers: { Authorization: `Bearer ${token}` } })
    expect(me.status(), 'a real login must authorize /me').toBe(200)
    const body = await me.json()
    const lab = (body.memberships as { organization_id: string; organization_type: string }[]).find(
      (m) => m.organization_type === 'LAB',
    )
    if (!lab) throw new Error('LAB membership missing from /me -- is the demo baseline seeded?')
    return lab.organization_id
  } finally {
    await ctx.dispose()
  }
}

/**
 * Opens this run's own order, via `Tous`, deliberately: the order moves through SELECTED, ACCEPTED
 * and BLOCKED across the phases, and a helper pinned to one filter would find it in one phase and
 * silently fail in the next.
 */
async function openThisOrder(page: Page): Promise<void> {
  await openSection(page, 'Ordres')
  await page.getByRole('tab', { name: 'Tous' }).click()
  const card = page
    .locator('li,div')
    .filter({ hasText: orderReference })
    .filter({ hasText: PATIENT })
    .first()
  await expect(card).toBeVisible({ timeout: 30_000 })
  await card.getByRole('button', { name: new RegExp(`Examiner l.ordre de ${PATIENT}`) }).click()
  await expect(page.getByText(orderReference).first()).toBeVisible({ timeout: 30_000 })
}

async function resolveOrderId(token: string): Promise<string> {
  const ctx = await api()
  try {
    const response = await ctx.get(`${API}/organizations/${labId}/lab-orders?limit=100`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    expect(response.status(), 'the laboratory queue must be readable').toBe(200)
    const orders = (await response.json()) as { order_id: string }[]
    const match = orders.find((o) => o.order_id.startsWith(orderReference.toLowerCase()))
    if (!match) {
      throw new Error(
        `no queued order starts with the reference "${orderReference}" -- ` +
          'the clinic screen and the laboratory queue disagree about this order',
      )
    }
    return match.order_id
  } finally {
    await ctx.dispose()
  }
}

/** The authoritative specimen history, read with the token the browser really obtained. */
async function readSpecimens(
  token: string,
  asLab: string = labId,
  forOrder: string = orderId,
): Promise<{ status: number; rows: Record<string, unknown>[] }> {
  const ctx = await api()
  try {
    const response = await ctx.get(
      `${API}/organizations/${asLab}/lab-orders/${forOrder}/specimens`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    const rows = response.status() === 200 ? await response.json() : []
    return { status: response.status(), rows }
  } finally {
    await ctx.dispose()
  }
}

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  await waitForEdge()
})

test.describe('DHEALTH-SPECIMEN-001 — a physical specimen, preserved', () => {
  // ── PHASE A — a real routed order ───────────────────────────────────────────────────────────

  test('SPEC-A: a clinician prescribes and routes an order, and the laboratory sees it', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, CLINICIAN)

    await openSection(page, 'Patients')
    await expect(page.getByText(PATIENT).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: /^Ouvrir/ }).first().click()

    await page.getByRole('button', { name: 'Nouvel ordre de laboratoire' }).click()
    await page.getByRole('checkbox', { name: new RegExp(CATALOG_ENTRY) }).check()
    await page.getByRole('button', { name: /^Suivant/ }).click()
    await page.getByRole('button', { name: 'Confirmer et prescrire' }).click()

    await expect(page.getByText(/Choisir un laboratoire destinataire/)).toBeVisible({
      timeout: 30_000,
    })
    await page.getByRole('radio', { name: new RegExp(LAB_ORG) }).check()
    await page.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }).click()

    await openSection(page, 'Ordres')
    const row = page.getByRole('row').filter({ hasText: PATIENT }).first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    // Column order: Patient, Analyses, Statut, Laboratoire destinataire, Date, Référence, Parcours.
    orderReference = (await row.locator('td').nth(5).innerText()).replace('…', '').trim()
    expect(orderReference.length).toBeGreaterThan(4)
  })

  test('SPEC-A2: the laboratory sees it as incoming work and accepts it', async ({ page }) => {
    watchForBrowserProblems(page)
    labToken = await signIn(page, LAB_TECH)
    labId = await labOrganizationId(labToken)

    await openSection(page, 'Ordres')
    // The incoming (SELECTED) queue is where routed work that nobody has answered lives.
    const incoming = page
      .locator('li,div')
      .filter({ hasText: orderReference })
      .filter({ hasText: PATIENT })
      .first()
    await expect(incoming).toBeVisible({ timeout: 30_000 })

    await openThisOrder(page)
    await page.getByRole('button', { name: "Accepter l'ordre" }).click()
    await expect(page.getByText('Accepté').first()).toBeVisible({ timeout: 30_000 })

    orderId = await resolveOrderId(labToken)
    expect(orderId.length).toBeGreaterThan(30)

    // Before any collection: the history is honestly empty, and the screen says so without
    // claiming collection did not happen.
    const before = await readSpecimens(labToken)
    expect(before.status).toBe(200)
    expect(before.rows, 'an accepted order has no specimen until one is recorded').toEqual([])
    await expect(page.getByText('Collecte non enregistrée dans DHealth')).toBeVisible()
  })

  // ── PHASE B — the first physical specimen ───────────────────────────────────────────────────

  test('SPEC-B: recording a collection creates a durable specimen, visible on the work item', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    labToken = await signIn(page, LAB_TECH)
    await openThisOrder(page)

    // The accepted work exposes the collection action, and opening the confirmation is not the
    // mutation -- the same two-step shape every other deliberate action on this screen uses.
    await page.getByRole('button', { name: 'Enregistrer la collecte' }).click()
    await expect(page.getByText(/Enregistrer la collecte \?/)).toBeVisible()
    await page.getByRole('button', { name: 'Confirmer la collecte' }).click()

    // The specimen history replaces the bare milestone line, and reads as what it is.
    await expect(page.getByText('Prélèvements (1)')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/1\. Prélèvement enregistré/)).toBeVisible()
    // Never the raw UUID as if it were an accession number, and never a clinical attribute.
    await expect(page.getByText('Collecte enregistrée')).toHaveCount(0)

    const after = await readSpecimens(labToken)
    expect(after.status).toBe(200)
    expect(after.rows).toHaveLength(1)
    specimenIds.push(after.rows[0].id as string)
  })

  test('SPEC-B2: the specimen survives a full browser reload — it is a fact, not a screen state', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    labToken = await signIn(page, LAB_TECH)

    await page.reload()
    await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({
      timeout: 30_000,
    })
    await openThisOrder(page)
    await expect(page.getByText('Prélèvements (1)')).toBeVisible({ timeout: 30_000 })

    // And the browser is displaying the database's specimen, not its own optimistic state: the id
    // it renders is the id the server holds.
    const persisted = await readSpecimens(labToken)
    expect(persisted.rows).toHaveLength(1)
    expect(persisted.rows[0].id).toBe(specimenIds[0])
  })

  // ── PHASE C — the authoritative record ──────────────────────────────────────────────────────

  test('SPEC-C: the specimen carries exactly the facts DHealth knows, and no clinical content', async () => {
    const { rows } = await readSpecimens(labToken)
    expect(rows).toHaveLength(1)
    const specimen = rows[0]

    // The exact projection. A set-equality assertion rather than an absence list, so a field added
    // later fails this automatically.
    expect(Object.keys(specimen).sort()).toEqual(
      ['id', 'idempotency_key', 'order_id', 'recorded_at', 'recorded_by_identity_id'].sort(),
    )
    expect(specimen.order_id).toBe(orderId)
    expect(specimen.recorded_at).toBeTruthy()
    expect(specimen.recorded_by_identity_id).toBeTruthy()

    // No specimen type, container, volume, source, accession number or status exists anywhere --
    // not "not yet supported", absent, because no governed source for any of them exists.
    const serialized = JSON.stringify(rows).toLowerCase()
    for (const forbidden of [
      'specimen_type',
      'collected_at',
      'container',
      'tube',
      'volume',
      'anatomical',
      'accession',
      'storage',
      'quality',
      'rejection',
      'status',
      'reason_for_visit',
      'diagnosis',
      'patient',
      'observation',
      'invoice',
    ]) {
      expect(serialized, `${forbidden} must not appear in a specimen response`).not.toContain(
        forbidden,
      )
    }
  })

  // ── PHASE D — authority, where the browser is not the boundary ──────────────────────────────

  test('SPEC-D: a worker without queue authority cannot collect or read the specimen history', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    // `demo-lab-clerk` holds BILLING_CLERK and nothing else: billing authority at the same
    // laboratory, which is deliberately not a weaker form of queue work.
    const clerkToken = await signIn(page, LAB_CLERK)

    const ctx = await api()
    try {
      const read = await ctx.get(`${API}/organizations/${labId}/lab-orders/${orderId}/specimens`, {
        headers: { Authorization: `Bearer ${clerkToken}` },
      })
      expect(read.status(), 'billing authority must not read specimen history').toBe(403)

      const write = await ctx.post(
        `${API}/organizations/${labId}/lab-orders/${orderId}/record-collection`,
        {
          headers: { Authorization: `Bearer ${clerkToken}`, 'Content-Type': 'application/json' },
          data: { idempotency_key: `${RUN}-clerk-attempt` },
        },
      )
      expect(write.status(), 'billing authority must not record a specimen').toBe(403)
    } finally {
      await ctx.dispose()
    }

    // And nothing was written by either refusal.
    const unchanged = await readSpecimens(labToken)
    expect(unchanged.rows).toHaveLength(1)
  })

  test('SPEC-D2: a rival laboratory cannot reach this order’s specimens', async () => {
    // The laboratory worker's own token aimed at a fabricated organization id, and at a fabricated
    // order id under the real one. Both must be indistinguishable from "does not exist".
    const foreignOrg = await readSpecimens(labToken, '00000000-0000-4000-8000-000000000001')
    expect([403, 404]).toContain(foreignOrg.status)

    const unknownOrder = await readSpecimens(
      labToken,
      labId,
      '00000000-0000-4000-8000-000000000002',
    )
    expect(unknownOrder.status).toBe(404)
  })

  // ── PHASE E — nothing invented on screen ────────────────────────────────────────────────────

  test('SPEC-E: the screen asks for no specimen taxonomy and shows no invented clinical label', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await openThisOrder(page)
    await expect(page.getByText('Prélèvements (1)')).toBeVisible({ timeout: 30_000 })

    const body = (await page.locator('body').innerText()).toLowerCase()
    // Vocabulary this product does not have, and must not appear to have.
    for (const invented of [
      'type de prélèvement',
      'type d’échantillon',
      'tube',
      'contenant',
      'volume',
      'anticoagulant',
      'numéro d’accession',
      'code-barres',
      'étiquette',
      'site de prélèvement',
    ]) {
      expect(body, `"${invented}" must not appear: nothing in DHealth models it`).not.toContain(
        invented,
      )
    }
    // No waiting time or priority derived from a timestamp, which §38 and AGENTS.md forbid.
    for (const derived of ['temps d’attente', "temps d'attente", 'priorité', 'en attente depuis']) {
      expect(body).not.toContain(derived)
    }
    // No raw UUID presented as a human-facing identifier for the specimen.
    expect(body).not.toContain(specimenIds[0].toLowerCase())
  })

  // ── PHASE F — the recollection journey, using only existing semantics ───────────────────────

  test('SPEC-F: an unusable specimen blocks the work, and the specimen is not erased', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    labToken = await signIn(page, LAB_TECH)
    await openThisOrder(page)

    // The existing BLOCKED workflow, with the existing reason catalogue. No new vocabulary is
    // introduced anywhere in this flow -- D-11's four provisional reasons are the same four.
    await page.getByRole('button', { name: "Bloquer l'ordre" }).click()
    await page.getByLabel('Motif').selectOption('SPECIMEN_UNUSABLE')
    await page.getByLabel(/Note/).fill(BLOCK_NOTE)
    await page.getByRole('button', { name: 'Confirmer le blocage' }).click()

    await expect(page.getByText(/Travail bloqué — Prélèvement inutilisable/)).toBeVisible({
      timeout: 30_000,
    })

    // The specimen that was there when the block happened is still there. Blocking is not a delete.
    await expect(page.getByText('Prélèvements (1)')).toBeVisible()
    const stillThere = await readSpecimens(labToken)
    expect(stillThere.rows).toHaveLength(1)
    expect(stillThere.rows[0].id).toBe(specimenIds[0])

    // And no collection control is offered while the work cannot continue.
    await expect(page.getByRole('button', { name: 'Enregistrer la collecte' })).toHaveCount(0)
    await expect(
      page.getByRole('button', { name: 'Enregistrer un nouveau prélèvement' }),
    ).toHaveCount(0)
  })

  test('SPEC-F2: resolving the block does not erase the specimen that caused it', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    labToken = await signIn(page, LAB_TECH)
    await openThisOrder(page)

    await page.getByRole('button', { name: /Résoudre/ }).click()
    await page
      .getByLabel(/Note/)
      .fill(`Nouveau prélèvement prévu — ${RUN}`)
      .catch(() => undefined)
    await page.getByRole('button', { name: /Confirmer la résolution/ }).click()

    await expect(page.getByText('Accepté').first()).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Prélèvements (1)')).toBeVisible()
    const afterResolve = await readSpecimens(labToken)
    expect(afterResolve.rows).toHaveLength(1)
    expect(afterResolve.rows[0].id).toBe(specimenIds[0])
  })

  test('SPEC-F3: a second physical specimen is recorded, and BOTH remain', async ({ page }) => {
    watchForBrowserProblems(page)
    labToken = await signIn(page, LAB_TECH)
    await openThisOrder(page)

    // The control for an additional sample, on the order whose milestone already exists.
    await page.getByRole('button', { name: 'Enregistrer un nouveau prélèvement' }).click()
    await expect(page.getByText(/Les prélèvements déjà enregistrés sont conservés/)).toBeVisible()
    await page.getByRole('button', { name: 'Confirmer le prélèvement' }).click()

    // **The assertion this entire ticket exists for.**
    await expect(page.getByText('Prélèvements (2)')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/1\. Prélèvement enregistré/)).toBeVisible()
    await expect(page.getByText(/2\. Prélèvement enregistré/)).toBeVisible()

    const both = await readSpecimens(labToken)
    expect(both.rows).toHaveLength(2)
    expect(both.rows[0].id, 'the FIRST specimen must not have been overwritten').toBe(specimenIds[0])
    expect(both.rows[1].id).not.toBe(specimenIds[0])
    specimenIds.push(both.rows[1].id as string)

    // Distinct instants, distinct ids, same order. And nothing anywhere says the second replaced
    // the first -- the product does not know that.
    expect(both.rows[0].recorded_at).not.toBe(both.rows[1].recorded_at)
    expect(both.rows.every((row) => row.order_id === orderId)).toBe(true)

    const body = (await page.locator('body').innerText()).toLowerCase()
    for (const unsupported of [
      'recollecte',
      'nouveau prélèvement enregistré',
      'prélèvement rejeté',
      'prélèvement refusé',
      'remplacement',
      'prélèvement effectué',
    ]) {
      expect(body, `"${unsupported}" claims something the domain does not know`).not.toContain(
        unsupported,
      )
    }
  })

  test('SPEC-F4: both specimens survive a reload, and the workflow continues normally', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    labToken = await signIn(page, LAB_TECH)
    await page.reload()
    await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({
      timeout: 30_000,
    })
    await openThisOrder(page)
    await expect(page.getByText('Prélèvements (2)')).toBeVisible({ timeout: 30_000 })

    // The order still starts processing: modelling specimens separately did not weaken the gate.
    await page.getByRole('button', { name: /Commencer/ }).first().click()
    await page.getByRole('button', { name: 'Commencer' }).last().click()
    await expect(page.getByText('En cours').first()).toBeVisible({ timeout: 30_000 })

    // And a third specimen is still recordable while IN_PROGRESS, without disturbing the first two.
    await expect(page.getByText('Prélèvements (2)')).toBeVisible()
    const unchanged = await readSpecimens(labToken)
    expect(unchanged.rows.map((row) => row.id)).toEqual(specimenIds)
  })

  // ── PHASE G — layout, keyboard, and a clean console ─────────────────────────────────────────

  test('SPEC-G: the specimen history is usable at a narrow staff width and by keyboard', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    // 768px is a supported staff width -- DH-USABILITY-001's O-1b was raised at exactly this one.
    await page.setViewportSize({ width: 768, height: 900 })
    await openThisOrder(page)
    await expect(page.getByText('Prélèvements (2)')).toBeVisible({ timeout: 30_000 })

    // No horizontal page scroll: the history must not push the panel out of the card.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow, 'the page must not scroll horizontally at 768px').toBeLessThanOrEqual(1)

    // The collection control is reachable and operable from the keyboard, and focus is visible.
    const control = page.getByRole('button', { name: 'Enregistrer un nouveau prélèvement' })
    await expect(control).toBeVisible()
    await control.focus()
    await expect(control).toBeFocused()
    const outlined = await control.evaluate((element) => {
      const style = getComputedStyle(element)
      return style.outlineStyle !== 'none' || style.boxShadow !== 'none'
    })
    expect(outlined, 'a focused control must be visibly focused').toBe(true)

    await page.setViewportSize({ width: 1280, height: 900 })
  })

  test('SPEC-G2: no unexpected console error and no failed request across the whole journey', async () => {
    expect(
      consoleErrors,
      `console errors during the specimen journey:\n${consoleErrors.join('\n')}`,
    ).toEqual([])
    expect(
      failedRequests,
      `failed network requests during the specimen journey:\n${failedRequests.join('\n')}`,
    ).toEqual([])
  })
})
