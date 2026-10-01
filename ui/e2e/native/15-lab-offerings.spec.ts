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
 * DHEALTH-LAB-OFFERINGS-001 — which analyses a laboratory accepts, against the real stack.
 *
 * ── The question this file answers ──
 *
 * Not "does the offering model work". DH-EX-003 built it and
 * `adapter/tests/test_dhealthlabofferings001_offerings.py` qualifies it at the HTTP edge (27 tests),
 * with the concurrency and constraint half on real PostgreSQL (11 more). This file answers the thing
 * neither can: **can a laboratory administrator, clicking the product, turn an analysis off -- and
 * does a clinic then stop being able to order it, while the order it already placed is untouched.**
 *
 * That last clause is the one that matters. "Stops new orders" and "changes nothing historical" are
 * the two halves of the deactivation rule, and a product that got the first right and the second
 * wrong would silently cancel a patient's test.
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated, no session injected, no auth state written into browser storage. Every
 * configuration act below is performed by clicking the product, by somebody who signed in through a
 * real Authorization Code + PKCE flow.
 *
 * Direct `/exchange/v1` calls appear in exactly two places and neither advances the workflow:
 *   - PHASE E asserts the **backend** refuses a configuration attempt the UI never offers. A hidden
 *     control is usability; the server refusing is the boundary, and only a direct call shows which
 *     one is stopping it.
 *   - PHASE D re-reads the order to confirm the browser is showing the database's state rather than
 *     its own optimism.
 * Both use bearer tokens the application itself put on the wire.
 *
 * ── What this suite does NOT establish ──
 *
 * **Not human usability.** No laboratory administrator has used this screen. A green run says the
 * mechanism works when driven correctly, which is a different claim from "an administrator can
 * manage their catalogue this way", and no script can make the second.
 *
 * It establishes nothing about real-world service availability or accreditation. The only analysis
 * involved is the demo baseline's synthetic `Analyse de démonstration A`, and the product makes no
 * claim that the laboratory can or cannot perform anything in real life -- this is Hawva
 * configuration state, and PHASE A asserts the screen's own wording says so.
 *
 * ── State this suite leaves behind ──
 *
 * The offering is left **active**, because the golden journey and every sibling lab suite need it.
 * PHASE F restores it explicitly rather than relying on `reset.sh`, so a crashed run is the only way
 * to leave it off -- and PHASE A fails loudly on the next run if that happened.
 *
 * It also leaves one routed, accepted lab order behind. Nothing deletes a lab order by design;
 * `scripts/demo/reset.sh` clears it as workflow state.
 *
 * All patient data is the demo baseline's synthetic patient. No real patient data is used.
 */

const EXCHANGE = process.env.E2E_EXCHANGE_URL ?? 'https://exchange.localhost'
const API = `${EXCHANGE}/exchange/v1`

const CLINICIAN = {
  username: process.env.DEMO_CLINICIAN_USERNAME ?? 'demo-clinician',
  password: process.env.DEMO_CLINICIAN_PASSWORD ?? '',
}
const LAB_ADMIN = {
  username: process.env.DEMO_LAB_ADMIN_USERNAME ?? 'demo-lab-admin',
  password: process.env.DEMO_LAB_ADMIN_PASSWORD ?? '',
}
const LAB_TECH = {
  username: process.env.DEMO_LAB_TECH_USERNAME ?? 'demo-lab-tech',
  password: process.env.DEMO_LAB_TECH_PASSWORD ?? '',
}

const PATIENT = 'Patient Démo Alpha'
const ANALYSIS = 'Analyse de démonstration A'
const LAB_ORG = 'Laboratoire Démo DHealth'

/** The order this run routes while the analysis is still accepted. Must survive the disable. */
let historicalOrderReference = ''
let historicalOrderId = ''
let labId = ''
let labToken = ''
let offeringId = ''

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

/** The laboratory's offerings, read with the token the browser really obtained. */
async function readOfferings(
  token: string,
  asLab: string = labId,
): Promise<{ status: number; rows: Record<string, unknown>[] }> {
  const ctx = await api()
  try {
    const response = await ctx.get(`${API}/organizations/${asLab}/offerings`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    return { status: response.status(), rows: response.status() === 200 ? await response.json() : [] }
  } finally {
    await ctx.dispose()
  }
}

/** The analysis row on `Services et tarifs`, by the catalogue's own display name. */
function offeringRow(page: Page) {
  return page.locator('li').filter({ hasText: ANALYSIS }).first()
}

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  await waitForEdge()
})

test.describe('DHEALTH-LAB-OFFERINGS-001 — what this laboratory accepts', () => {
  // ── PHASE A — the configuration surface exists and is reachable ──────────────────────────────

  test('OF-A: a laboratory administrator finds the analyses this laboratory accepts', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    labToken = await signIn(page, LAB_ADMIN)
    labId = await labOrganizationId(labToken)

    // Not a new top-level section: the existing configuration surface.
    await openSection(page, 'Services et tarifs')
    await expect(page.getByRole('heading', { name: 'Analyses réalisées' })).toBeVisible({
      timeout: 30_000,
    })

    // The baseline starts accepting. If a crashed earlier run left it off, this fails loudly here
    // rather than making a later phase confusing.
    const row = offeringRow(page)
    await expect(row).toBeVisible()
    await expect(row).toContainText('accepte les nouveaux ordres')
    await expect(row.getByRole('button', { name: 'Ne plus accepter' })).toBeVisible()

    // The wording is Hawva configuration state and does not claim anything about real capability.
    await expect(page.getByText(/accepte de recevoir via Hawva/)).toBeVisible()

    const offerings = await readOfferings(labToken)
    expect(offerings.status).toBe(200)
    expect(offerings.rows).toHaveLength(1)
    expect(offerings.rows[0].active).toBe(true)
    offeringId = offerings.rows[0].id as string

    // The offering carries no copy of the catalogue's name: the name on screen came from the
    // catalogue read, joined on `catalog_entry_id`.
    expect(Object.keys(offerings.rows[0]).sort()).toEqual(
      ['active', 'catalog_entry_id', 'id', 'lab_organization_id', 'local_test_code'].sort(),
    )
  })

  test('OF-A2: the panel shows no price, no specimen field and no ranking', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_ADMIN)
    await openSection(page, 'Services et tarifs')
    await expect(page.getByRole('heading', { name: 'Analyses réalisées' })).toBeVisible({
      timeout: 30_000,
    })

    const panel = page.locator('section').filter({ hasText: 'Analyses réalisées' }).first()
    const text = ((await panel.textContent()) ?? '').toLowerCase()
    for (const absent of [
      'djf',
      'tarif',
      'tube',
      'contenant',
      'volume',
      'anticoagulant',
      'code-barres',
      'meilleur',
      'classement',
      'étoile',
    ]) {
      expect(text, `"${absent}" must not appear: nothing in this panel models it`).not.toContain(
        absent,
      )
    }
  })

  // ── PHASE B — a clinic orders it while it is accepted ────────────────────────────────────────

  test('OF-B: a clinic routes an order for the accepted analysis', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, CLINICIAN)

    await openSection(page, 'Patients')
    await expect(page.getByText(PATIENT).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: /^Ouvrir/ }).first().click()

    await page.getByRole('button', { name: 'Nouvel ordre de laboratoire' }).click()
    await page.getByRole('checkbox', { name: new RegExp(ANALYSIS) }).check()
    await page.getByRole('button', { name: /^Suivant/ }).click()
    await page.getByRole('button', { name: 'Confirmer et prescrire' }).click()

    // The laboratory is offered because it accepts this analysis. The radio being present IS the
    // eligibility assertion -- a laboratory that did not offer it would not be listed.
    await expect(page.getByText(/Choisir un laboratoire destinataire/)).toBeVisible({
      timeout: 30_000,
    })
    await page.getByRole('radio', { name: new RegExp(LAB_ORG) }).check()
    await page.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }).click()

    await openSection(page, 'Ordres')
    const row = page.getByRole('row').filter({ hasText: PATIENT }).first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    // Column order: Patient, Analyses, Statut, Laboratoire destinataire, Date, Référence, Parcours.
    historicalOrderReference = (await row.locator('td').nth(5).innerText())
      .replace('…', '')
      .trim()
    expect(historicalOrderReference.length).toBeGreaterThan(4)
  })

  test('OF-B2: the laboratory receives it and accepts it', async ({ page }) => {
    watchForBrowserProblems(page)
    const techToken = await signIn(page, LAB_TECH)

    await openSection(page, 'Ordres')
    const card = page
      .locator('li,div')
      .filter({ hasText: historicalOrderReference })
      .filter({ hasText: PATIENT })
      .first()
    await expect(card).toBeVisible({ timeout: 30_000 })
    await card.getByRole('button', { name: new RegExp(`Examiner l.ordre de ${PATIENT}`) }).click()
    await page.getByRole('button', { name: "Accepter l'ordre" }).click()
    await expect(page.getByText('Accepté').first()).toBeVisible({ timeout: 30_000 })

    const ctx = await api()
    try {
      const queue = await ctx.get(`${API}/organizations/${labId}/lab-orders?limit=100`, {
        headers: { Authorization: `Bearer ${techToken}` },
      })
      expect(queue.status()).toBe(200)
      const orders = (await queue.json()) as { order_id: string }[]
      const match = orders.find((o) =>
        o.order_id.startsWith(historicalOrderReference.toLowerCase()),
      )
      expect(match, 'the routed order must be in this laboratory queue').toBeTruthy()
      historicalOrderId = match!.order_id
    } finally {
      await ctx.dispose()
    }
  })

  // ── PHASE C — the administrator turns it off ─────────────────────────────────────────────────

  test('OF-C: disabling says plainly that existing orders do not change, and persists', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    labToken = await signIn(page, LAB_ADMIN)
    await openSection(page, 'Services et tarifs')
    await expect(page.getByRole('heading', { name: 'Analyses réalisées' })).toBeVisible({
      timeout: 30_000,
    })

    await offeringRow(page).getByRole('button', { name: 'Ne plus accepter' }).click()

    // The confirmation is the feature: an administrator who fears this cancels a patient's test
    // will not use it.
    await expect(page.getByText(/Les ordres déjà reçus ne changent pas/)).toBeVisible({
      timeout: 30_000,
    })
    await expect(offeringRow(page)).toContainText("n'accepte plus de nouveaux ordres")

    // A genuine reload, not a re-render: this must be a fact, not a screen state.
    await page.reload()
    await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({
      timeout: 30_000,
    })
    await openSection(page, 'Services et tarifs')
    await expect(offeringRow(page)).toContainText("n'accepte plus de nouveaux ordres")

    const offerings = await readOfferings(labToken)
    expect(offerings.rows[0].active).toBe(false)
  })

  // ── PHASE D — the two halves of the deactivation rule ───────────────────────────────────────

  test('OF-D: a NEW order can no longer be routed to this laboratory', async ({ page }) => {
    watchForBrowserProblems(page)
    const clinicianToken = await signIn(page, CLINICIAN)

    await openSection(page, 'Patients')
    await page.getByRole('button', { name: /^Ouvrir/ }).first().click()
    await page.getByRole('button', { name: 'Nouvel ordre de laboratoire' }).click()
    await page.getByRole('checkbox', { name: new RegExp(ANALYSIS) }).check()
    await page.getByRole('button', { name: /^Suivant/ }).click()
    await page.getByRole('button', { name: 'Confirmer et prescrire' }).click()

    // The truthful empty state: no eligible laboratory, said plainly, with no fabricated fallback.
    await expect(
      page.getByText(
        /Aucun laboratoire configuré ne peut actuellement réaliser toutes les analyses de cet ordre/,
      ),
    ).toBeVisible({ timeout: 30_000 })
    await expect(page.getByRole('radio', { name: new RegExp(LAB_ORG) })).toHaveCount(0)

    // And the server refuses a hand-typed assignment for the same order -- hiding the radio is
    // usability, the refusal is the boundary. The order id is read from the clinic's own list.
    const ctx = await api()
    try {
      const me = await ctx.get(`${API}/me`, {
        headers: { Authorization: `Bearer ${clinicianToken}` },
      })
      const providerId = (
        (await me.json()).memberships as { organization_id: string; organization_type: string }[]
      ).find((m) => m.organization_type === 'PROVIDER')!.organization_id

      const orders = await ctx.get(`${API}/organizations/${providerId}/orders?limit=100`, {
        headers: { Authorization: `Bearer ${clinicianToken}` },
      })
      expect(orders.status()).toBe(200)
      const unassigned = ((await orders.json()) as { id: string; status: string }[]).find(
        (o) => o.status === 'AWAITING_LAB_SELECTION',
      )
      expect(unassigned, 'the new order should be awaiting a laboratory').toBeTruthy()

      const refused = await ctx.post(
        `${API}/organizations/${providerId}/orders/${unassigned!.id}/assign-lab`,
        {
          headers: {
            Authorization: `Bearer ${clinicianToken}`,
            'Content-Type': 'application/json',
          },
          data: { lab_organization_id: labId },
        },
      )
      expect(refused.status(), 'a stale or hand-typed assignment must be refused').toBe(409)
      // Refused for the right reason, named -- not the generic readiness message.
      expect(JSON.stringify(await refused.json())).toContain('offering')
    } finally {
      await ctx.dispose()
    }
  })

  test('OF-D2: the order placed earlier is untouched and still workable', async ({ page }) => {
    watchForBrowserProblems(page)
    const techToken = await signIn(page, LAB_TECH)

    await openSection(page, 'Ordres')
    await page.getByRole('tab', { name: 'Tous' }).click()
    const card = page
      .locator('li,div')
      .filter({ hasText: historicalOrderReference })
      .filter({ hasText: PATIENT })
      .first()
    await expect(card, 'a disabled analysis must not hide work already held').toBeVisible({
      timeout: 30_000,
    })
    await card.getByRole('button', { name: new RegExp(`Examiner l.ordre de ${PATIENT}`) }).click()

    // Still ACCEPTED, not cancelled, not rejected.
    await expect(page.getByText('Accepté').first()).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Annulé')).toHaveCount(0)

    // And the server agrees, including the item's frozen service identity.
    const ctx = await api()
    try {
      const order = await ctx.get(`${API}/organizations/${labId}/lab-orders/${historicalOrderId}`, {
        headers: { Authorization: `Bearer ${techToken}` },
      })
      expect(order.status()).toBe(200)
      const body = await order.json()
      expect(body.status).toBe('ACCEPTED')
      const names = (body.requested_tests as { catalog_display_name_snapshot: string }[]).map(
        (t) => t.catalog_display_name_snapshot,
      )
      expect(names, 'the frozen snapshot must still name the analysis').toContain(ANALYSIS)
    } finally {
      await ctx.dispose()
    }
  })

  // ── PHASE E — who may configure, and who may not ────────────────────────────────────────────

  test('OF-E: a laboratory technician is offered no configuration and refused by the server', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    // `demo-lab-tech` holds LAB_TECH only: `lab.queue.work`, no `facility.configure`.
    const techToken = await signIn(page, LAB_TECH)

    // The section is not offered at all, which is usability.
    await expect(
      page.getByRole('navigation').getByRole('button', { name: 'Services et tarifs', exact: true }),
    ).toHaveCount(0)

    // And the server refuses both the read and the mutation, which is the boundary.
    const read = await readOfferings(techToken)
    expect(read.status, 'queue work must not read facility configuration').toBe(403)

    const ctx = await api()
    try {
      const write = await ctx.patch(`${API}/organizations/${labId}/offerings/${offeringId}`, {
        headers: { Authorization: `Bearer ${techToken}`, 'Content-Type': 'application/json' },
        data: { active: true },
      })
      expect(write.status(), 'queue work must not change what the laboratory accepts').toBe(403)
    } finally {
      await ctx.dispose()
    }

    // Nothing changed.
    const after = await readOfferings(labToken)
    expect(after.rows[0].active).toBe(false)
  })

  test('OF-E2: a clinician cannot configure the destination laboratory', async ({ page }) => {
    watchForBrowserProblems(page)
    const clinicianToken = await signIn(page, CLINICIAN)

    // 404, not 403: a non-member must not learn that the organization id is real.
    const read = await readOfferings(clinicianToken)
    expect(read.status).toBe(404)

    const ctx = await api()
    try {
      const write = await ctx.patch(`${API}/organizations/${labId}/offerings/${offeringId}`, {
        headers: { Authorization: `Bearer ${clinicianToken}`, 'Content-Type': 'application/json' },
        data: { active: true },
      })
      expect(write.status()).toBe(404)

      // A guessed organization id is indistinguishable from the real one it is not a member of.
      const guessed = await ctx.get(
        `${API}/organizations/00000000-0000-4000-8000-000000000001/offerings`,
        { headers: { Authorization: `Bearer ${clinicianToken}` } },
      )
      expect([403, 404]).toContain(guessed.status())
    } finally {
      await ctx.dispose()
    }
  })

  // ── PHASE F — restore, and the clinic can order again ───────────────────────────────────────

  test('OF-F: re-enabling restores ordering, and the panel says so', async ({ page }) => {
    watchForBrowserProblems(page)
    labToken = await signIn(page, LAB_ADMIN)
    await openSection(page, 'Services et tarifs')
    await expect(page.getByRole('heading', { name: 'Analyses réalisées' })).toBeVisible({
      timeout: 30_000,
    })

    await offeringRow(page).getByRole('button', { name: 'Accepter à nouveau' }).click()
    await expect(page.getByText(/accepte désormais les nouveaux ordres/)).toBeVisible({
      timeout: 30_000,
    })
    await expect(offeringRow(page)).toContainText('accepte les nouveaux ordres')

    const offerings = await readOfferings(labToken)
    expect(offerings.rows[0].active, 'the baseline must be left accepting for sibling suites').toBe(
      true,
    )
  })

  test('OF-F2: the clinic can route a new order again', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, CLINICIAN)

    await openSection(page, 'Patients')
    await page.getByRole('button', { name: /^Ouvrir/ }).first().click()
    await page.getByRole('button', { name: 'Nouvel ordre de laboratoire' }).click()
    await page.getByRole('checkbox', { name: new RegExp(ANALYSIS) }).check()
    await page.getByRole('button', { name: /^Suivant/ }).click()
    await page.getByRole('button', { name: 'Confirmer et prescrire' }).click()

    await expect(page.getByRole('radio', { name: new RegExp(LAB_ORG) })).toBeVisible({
      timeout: 30_000,
    })
    await page.getByRole('radio', { name: new RegExp(LAB_ORG) }).check()
    await page.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }).click()
    await openSection(page, 'Ordres')
    await expect(page.getByRole('row').filter({ hasText: PATIENT }).first()).toBeVisible({
      timeout: 30_000,
    })
  })

  // ── PHASE G — layout and a clean console ────────────────────────────────────────────────────

  test('OF-G: the panel is usable at a narrow staff width', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_ADMIN)
    // 768px is a supported staff width -- DH-USABILITY-001's O-1b was raised at exactly this one.
    await page.setViewportSize({ width: 768, height: 900 })
    await openSection(page, 'Services et tarifs')
    await expect(page.getByRole('heading', { name: 'Analyses réalisées' })).toBeVisible({
      timeout: 30_000,
    })

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow, 'the page must not scroll horizontally at 768px').toBeLessThanOrEqual(1)

    // The control is reachable by keyboard and visibly focused once reached. Tab, not `.focus()`:
    // Chromium does not match `:focus-visible` for programmatic focus, which reports no focus ring
    // against a product that styles one correctly.
    const control = offeringRow(page).getByRole('button', { name: 'Ne plus accepter' })
    await expect(control).toBeVisible()
    await page.locator('body').click({ position: { x: 2, y: 2 } })
    let reached = false
    for (let presses = 0; presses < 80 && !reached; presses += 1) {
      await page.keyboard.press('Tab')
      reached = await control.evaluate((element) => element === document.activeElement)
    }
    expect(reached, 'the configuration control must be reachable by Tab alone').toBe(true)
    const outlined = await control.evaluate((element) => {
      const style = getComputedStyle(element)
      const width = Number.parseFloat(style.outlineWidth || '0')
      return (
        (style.outlineStyle !== 'none' && width > 0) ||
        (style.boxShadow !== 'none' && style.boxShadow !== '')
      )
    })
    expect(outlined, 'a keyboard-focused control must show a visible focus ring').toBe(true)

    await page.setViewportSize({ width: 1280, height: 900 })
  })

  test('OF-G2: no unexpected console error and no failed request across the journey', async () => {
    expect(
      consoleErrors,
      `console errors during the offerings journey:\n${consoleErrors.join('\n')}`,
    ).toEqual([])
    expect(
      failedRequests,
      `failed network requests during the offerings journey:\n${failedRequests.join('\n')}`,
    ).toEqual([])
  })
})
