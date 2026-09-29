import { expect, request as apiRequest, test, type APIRequestContext, type Page } from '@playwright/test'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'
import { waitForEdge } from '../support/edgeReadiness'

/**
 * DHEALTH-LAB-BLOCKED-001 — an order a laboratory cannot continue, against the real stack, in a
 * real browser.
 *
 * ── The question this file answers ──
 *
 * Not "does the block API work" — `adapter/tests/test_dhealthlabblocked001_blocked_orders.py`
 * answers that exhaustively, and the PostgreSQL qualification answers the concurrency half. This
 * file answers **can a laboratory worker actually say "we cannot continue this" and be believed**:
 * sign in, open a real assigned order, block it with a structured reason, watch the block survive a
 * full browser reload, find the work refused while it stands, resolve it, and carry the order
 * through to a released result that the ordering clinician then reviews.
 *
 * The safety invariant `docs/NATIVE-OPERATING-PRODUCT.md` §16 states for this feature is that a
 * blocked order must appear as **work owed to a human, never a silent state**. PHASE B and PHASE F
 * are where that is actually tested: the reason is legible on the laboratory's queue row, and the
 * ordering clinic is told its order stopped.
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated, no session injected, no auth state written into browser storage. Every
 * laboratory act below is performed by clicking the product, by a worker who signed in through a
 * real Authorization Code + PKCE flow.
 *
 * Direct `/exchange/v1` calls appear in exactly two places, and neither advances the workflow:
 *   - PHASE C asserts the **backend** refuses a prohibited step. The UI does not offer the control,
 *     which is usability; the server refusing it is the boundary, and only a direct call can show
 *     that the browser is not what is stopping it.
 *   - `/me` is read to learn which organization a token actually carries.
 * Both use the bearer token the application itself put on the wire, never a fabricated one.
 *
 * ── State this suite leaves behind, stated rather than discovered ──
 *
 * Block episodes are never deleted — `resolve` resolves them and there is no delete path, by
 * design. So like `08-clinic-visit.spec.ts` this suite cannot restore what it created.
 * `scripts/demo/reset.sh` clears `exchange_lab_order_blocks` as workflow state
 * (`demo_seed._WORKFLOW_TABLES`, extended by this ticket), which is the supported way back to the
 * baseline before running it again. The free-text note is made unique per run so that a database
 * carrying earlier runs still yields unambiguous selectors.
 *
 * All patient data here is the demo baseline's synthetic patient. No real patient data is used, and
 * the note this suite types is obviously synthetic.
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
const RELEASER = {
  username: process.env.DEMO_RELEASER_USERNAME ?? 'demo-releaser',
  password: process.env.DEMO_RELEASER_PASSWORD ?? '',
}

const PATIENT = 'Patient Démo Alpha'
const CATALOG_ENTRY = 'Analyse de démonstration A'
const LAB_ORG = 'Laboratoire Démo DHealth'

const RUN = `e2e-${Date.now()}`
/** Obviously synthetic, and unique per run so repeated runs stay distinguishable on screen. */
const BLOCK_NOTE = `Blocage de qualification ${RUN} — note interne synthétique`

/** The order this run creates, carried between phases. */
let orderReference = ''
/** The full identifier, resolved from the laboratory queue -- see `resolveOrderId`. */
let orderId = ''
let labId = ''
let labToken = ''

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

/** A real browser login, returning the bearer token the application itself put on the wire. */
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

/** The laboratory organization the signed-in token actually carries. */
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
 * Opens this run's own order in the laboratory queue, by the reference the clinic screen showed.
 *
 * Switches to `Tous` first, deliberately. The queue opens on `Nouveaux` (SELECTED), and this order
 * moves through ACCEPTED, BLOCKED and IN_PROGRESS across the phases below -- a helper pinned to any
 * one filter would work for one phase and silently fail to find the order in the next.
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

/**
 * The order's real identifier, resolved from the laboratory's own queue API.
 *
 * `orderReference` is what the clinic *screen* shows, which is deliberately truncated for reading.
 * The direct-call refusal assertions below need the full identifier, and inventing it by
 * un-truncating a display string would be guessing. This reads the authoritative value from the
 * same endpoint the queue itself uses, with the token the browser really obtained.
 */
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

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  await waitForEdge()
})

test.describe('DHEALTH-LAB-BLOCKED-001 — a laboratory that cannot continue', () => {
  // ── PHASE A — a real order, and a laboratory worker who can see it ──────────────────────

  test('BLOCK-A: a clinician prescribes and routes an order, and the laboratory sees it', async ({
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

  // ── PHASE B — blocking ──────────────────────────────────────────────────────────────────

  test('BLOCK-B: the laboratory accepts the order, then blocks it with a structured reason', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    labToken = await signIn(page, LAB_TECH)
    labId = await labOrganizationId(labToken)

    await openThisOrder(page)
    await page.getByRole('button', { name: "Accepter l'ordre" }).click()
    await expect(page.getByText('Accepté').first()).toBeVisible({ timeout: 30_000 })

    // The action exists for an accepted order, and opening it is not the mutation.
    await page.getByRole('button', { name: "Bloquer l'ordre" }).click()

    // The form asks for exactly two things. It does not ask who is blocking, when, at which
    // laboratory, or what state the order should come back to -- all four are the server's.
    await page.getByLabel('Motif').selectOption('SPECIMEN_UNUSABLE')
    await page.getByLabel(/Note/).fill(BLOCK_NOTE)
    await page.getByRole('button', { name: 'Confirmer le blocage' }).click()

    // The order is visibly blocked, and says why -- in French, never as a raw enum.
    await expect(page.getByText(/Travail bloqué — Prélèvement inutilisable/)).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.getByText(BLOCK_NOTE).first()).toBeVisible()
    await expect(page.getByText('SPECIMEN_UNUSABLE')).toHaveCount(0)
  })

  test('BLOCK-B2: the block survives a full browser reload — it is a fact, not a screen state', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)

    // A genuine reload, not a re-render: nothing about this state may depend on what this tab
    // happens to remember.
    await page.reload()
    await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({
      timeout: 30_000,
    })

    await openSection(page, 'Ordres')
    await page.getByRole('tab', { name: 'Bloqués' }).click()

    // §16's safety invariant, tested: the stalled order appears as work owed, with the reason
    // legible on the queue row itself rather than only inside a detail panel.
    const card = page.locator('li,div').filter({ hasText: orderReference }).first()
    await expect(card).toBeVisible({ timeout: 30_000 })
    await expect(card).toContainText('Bloqué')
    await expect(card).toContainText('Motif : Prélèvement inutilisable')

    // And the history survived too.
    await openThisOrder(page)
    await expect(page.getByRole('heading', { name: 'Blocages' })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(BLOCK_NOTE).first()).toBeVisible()
  })

  test('BLOCK-B3: the count on Aujourd’hui tells the laboratory there is work owed', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await openSection(page, "Aujourd'hui")

    // A status nobody monitors is not an operational workflow (§27). This is where a laboratory
    // asks "what needs me now", so the blocked order has to be counted here.
    const tile = page.locator('div').filter({ hasText: /^Bloqués/ }).first()
    await expect(tile).toBeVisible({ timeout: 30_000 })
    await expect(tile).not.toContainText(/^Bloqués0$/)
  })

  // ── PHASE C — enforcement ───────────────────────────────────────────────────────────────

  test('BLOCK-C: the work is refused while the block stands — by the server, not the browser', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    const token = await signIn(page, LAB_TECH)
    orderId = await resolveOrderId(token)
    await openThisOrder(page)

    // The UI does not offer the prohibited controls...
    await expect(page.getByRole('button', { name: 'Enregistrer la collecte' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: "Commencer l'analyse" })).toHaveCount(0)

    // ...and, far more importantly, the backend refuses them when asked directly. Hiding a control
    // is usability; the server is the boundary. This is the only way to show the difference.
    const ctx = await api()
    try {
      const base = `${API}/organizations/${labId}/lab-orders/${orderId}`
      for (const path of ['/record-collection', '/start-processing', '/accept']) {
        const response = await ctx.post(`${base}${path}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        expect(
          response.status(),
          `${path} must be refused while the order is blocked`,
        ).toBe(409)
      }
    } finally {
      await ctx.dispose()
    }
  })

  test('BLOCK-C2: a blocked order is not releasable, and is not on the release queue', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    const releaserToken = await signIn(page, RELEASER)

    // The releaser's own release surface is `Résultats`, the same label the clinician's review
    // screen carries -- the section a person sees is derived from their authority, not from the
    // word.
    await openSection(page, 'Résultats')
    // Nothing to release: the order is blocked, so it is not a release candidate at all.
    await expect(page.getByText(orderReference)).toHaveCount(0)

    const ctx = await api()
    try {
      // A well-formed body on purpose. `null` was tried first and produced a 422 from the request
      // schema -- a refusal, but for the wrong reason, which would have let this assertion pass
      // even if blocking did nothing. The fingerprint value itself is irrelevant here because
      // `_release_in_session` checks the order's status *before* it ever compares a fingerprint,
      // so a 409 from this call is the block and nothing else.
      const response = await ctx.post(
        `${API}/organizations/${labId}/lab-orders/${orderId}/release-results`,
        {
          headers: { Authorization: `Bearer ${releaserToken}` },
          data: { expected_result_fingerprint: 'f'.repeat(64) },
        },
      )
      expect(
        response.status(),
        `a blocked order must not be releasable: ${await response.text()}`,
      ).toBe(409)
    } finally {
      await ctx.dispose()
    }
  })

  // ── PHASE D — the ordering clinic ───────────────────────────────────────────────────────

  test('BLOCK-D: the ordering clinician is told the order stopped, and why — and no more', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, CLINICIAN)
    await openSection(page, 'Ordres')

    const row = page.getByRole('row').filter({ hasText: orderReference }).first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    await expect(row).toContainText('Bloqué au laboratoire')
    await expect(row).toContainText('Motif : Prélèvement inutilisable')

    // §26's minimum-necessary rule, asserted against the whole rendered page: the laboratory's
    // internal note never crosses the organization boundary.
    await expect(page.getByText(BLOCK_NOTE)).toHaveCount(0)
    expect(await page.content()).not.toContain(BLOCK_NOTE)
  })

  // ── PHASE E — resolution ────────────────────────────────────────────────────────────────

  test('BLOCK-E: an authorized worker resolves the block and the server restores the state', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await openThisOrder(page)

    await page.getByRole('button', { name: 'Résoudre le blocage' }).click()
    // The confirmation states where the order will go. It does not offer a choice.
    await expect(page.getByRole('alert')).toContainText(/reprendra à l.état/)
    await page.getByRole('button', { name: 'Confirmer la reprise' }).click()

    // Back to exactly the state the block interrupted -- ACCEPTED, because that is where it was.
    await expect(page.getByText('Accepté').first()).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/Travail bloqué/)).toHaveCount(0)

    // And the episode is still there, as history, marked resolved.
    await expect(page.getByRole('heading', { name: 'Blocages' })).toBeVisible()
    await expect(page.getByText(/Prélèvement inutilisable — résolu/)).toBeVisible()
    await expect(page.getByText(BLOCK_NOTE).first()).toBeVisible()
  })

  test('BLOCK-E2: a second, independent block episode does not overwrite the first', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await openThisOrder(page)

    // Carry the work forward first, so the second block genuinely comes from a different state.
    await page.getByRole('button', { name: 'Enregistrer la collecte' }).click()
    await page.getByRole('button', { name: 'Confirmer la collecte' }).click()
    await expect(page.getByText(/Collecte enregistrée/)).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: "Commencer l'analyse" }).click()
    await page.getByRole('button', { name: 'Commencer', exact: true }).click()
    await expect(page.getByText('En cours').first()).toBeVisible({ timeout: 30_000 })

    await page.getByRole('button', { name: "Bloquer l'ordre" }).click()
    await page.getByLabel('Motif').selectOption('CLARIFICATION_REQUIRED')
    await page.getByRole('button', { name: 'Confirmer le blocage' }).click()
    await expect(page.getByText(/Travail bloqué — Précision nécessaire/)).toBeVisible({
      timeout: 30_000,
    })

    // §33: both episodes visible, neither overwritten. This is the assertion a single mutable
    // `block_reason` column could never satisfy.
    await expect(page.getByText(/Prélèvement inutilisable — résolu/)).toBeVisible()
    await expect(page.getByText(/Précision nécessaire — en cours/)).toBeVisible()

    // Resolve it, and this time the server restores IN_PROGRESS -- a different destination from
    // the first episode's, chosen by the server from what it recorded, not by this browser.
    await page.getByRole('button', { name: 'Résoudre le blocage' }).click()
    await page.getByRole('button', { name: 'Confirmer la reprise' }).click()
    await expect(page.getByText('En cours').first()).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/Précision nécessaire — résolu/)).toBeVisible()
  })

  // ── PHASE F — the workflow completes normally afterwards ────────────────────────────────

  test('BLOCK-F: the laboratory enters and completes the result once the block is gone', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, LAB_TECH)
    await openThisOrder(page)

    await expect(page.getByText(/Saisie des résultats/)).toBeVisible({ timeout: 30_000 })

    // The fields come from the pinned template, not from this file, and every definition the
    // template marks required has to be answered -- the demo template declares one numeric and one
    // free-text. Copied from `01-golden-journey.spec.ts`, which found the hard way that a
    // `spinbutton` selector silently misses the text field and leaves "Terminer la saisie"
    // correctly disabled.
    const required = page.getByLabel(/Requis/)
    await expect(required.first()).toBeVisible({ timeout: 30_000 })
    const count = await required.count()
    expect(count, 'the demo template declares required observations').toBeGreaterThan(0)
    for (let index = 0; index < count; index += 1) {
      const field = required.nth(index)
      const numeric = (await field.getAttribute('inputmode')) === 'decimal'
      await field.fill(numeric ? '5.4' : 'Observation de démonstration')
    }
    await page.getByRole('button', { name: 'Enregistrer les valeurs' }).click()
    await expect(page.getByText(/valeur saisie n.est pas encore enregistrée/)).toBeHidden({
      timeout: 30_000,
    })

    await page.getByRole('button', { name: 'Terminer la saisie' }).click()
    await page.getByRole('button', { name: 'Confirmer la fin de la saisie' }).click()
    await expect(page.getByText(/Saisie terminée/).first()).toBeVisible({ timeout: 30_000 })
  })

  test('BLOCK-G: a separate releaser releases the result, and the clinician reviews it', async ({
    page,
  }) => {
    watchForBrowserProblems(page)
    await signIn(page, RELEASER)
    await openSection(page, 'Résultats')

    await page.getByRole('button', { name: new RegExp(`Examiner les résultats de ${PATIENT}`) }).click()
    await expect(page.getByText(/5.4/).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: 'Libérer les résultats' }).click()
    await page.getByRole('button', { name: /Confirmer la libération|Libérer/ }).last().click()
    await expect(page.getByText(/Résultats libérés/).first()).toBeVisible({ timeout: 30_000 })
  })

  test('BLOCK-H: the clinic reads the released result and records a review', async ({ page }) => {
    watchForBrowserProblems(page)
    await signIn(page, CLINICIAN)
    await openSection(page, 'Résultats')

    await expect(page.getByText(PATIENT).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: /^Examiner/ }).first().click()
    await expect(page.getByRole('tab', { name: /Résultat actuel/ })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/5.4/).first()).toBeVisible()

    await page.getByLabel('Aucun suivi requis').check()
    await page.getByRole('button', { name: 'Confirmer la revue' }).click()
    await expect(page.getByText(/Revue enregistrée/)).toBeVisible({ timeout: 30_000 })
  })

  test('the journey produced no browser console errors and no failed requests', async () => {
    expect(consoleErrors, `console errors during the journey:\n${consoleErrors.join('\n')}`).toEqual(
      [],
    )
    expect(
      failedRequests,
      `failed network requests during the journey:\n${failedRequests.join('\n')}`,
    ).toEqual([])
  })
})
