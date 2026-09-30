import { expect, request as apiRequest, test, type APIRequestContext, type Browser, type Page } from '@playwright/test'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'
import { exchangeSql } from '../support/securityAdmin'
import { waitForEdge } from '../support/edgeReadiness'

/**
 * DHEALTH-LAB-RECEPTION-001 — the beginning of a laboratory's working day, against the real stack.
 *
 * ── The question this file answers ──
 *
 * Not "does `lab.queue.work` gate the intake endpoints" — `test_dhealthlabreception001_intake.py`
 * answers that in 23 tests through the HTTP edge, and the PostgreSQL qualification answers the
 * concurrency half in 8 more. This file answers the thing only a browser can: **can a laboratory
 * worker who holds no laboratory role see the diagnostic work actually routed to their laboratory,
 * identify whose it is, answer it, and hand the accepted work to a colleague — while remaining
 * unable to release a result?**
 *
 * ── The persona, and why it is this one ──
 *
 * `demo-lab-clerk` holds **`BILLING_CLERK` and nothing else**, at the laboratory. The ticket forbids
 * inventing a `LAB_RECEPTIONIST` role, and a laboratory *cashier* handed exactly one capability is a
 * stronger answer than a purpose-built persona: if any control or any gate keyed off a job title,
 * this suite could not get past LR-B3. It is also the composition the product actually expects in a
 * small laboratory — one person at the counter doing reception and the till.
 *
 * The grant is made by clicking `Équipe et accès` as the laboratory's administrator, and its effect
 * is observed in the cashier's own session. `demo_seed.py` is deliberately **not** changed to
 * pre-grant it: the grant happening by clicking, and being withdrawable by clicking, is the evidence.
 *
 * ── One capability, and the two it must not drag with it ──
 *
 * `lab.queue.work` is the whole grant. ADR-044 keeps result release a separate authority and ADR-066
 * keeps the till separate, so LR-B3 asserts all four facts at once — the role is untouched,
 * `lab.queue.work` is now held, `lab.result.release` is **not**, and `billing.operate` still is —
 * and LR-B7 shows the server refusing a release to the same person who just accepted the work.
 * No new capability was added for this ticket and no new order state was invented: `SELECTED` already
 * means "routed here, not yet answered", which is exactly what an incoming queue is.
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated and no session injected; every act below is performed by clicking the
 * product after a real Authorization Code + PKCE sign-in. Direct `/exchange/v1` calls appear only to
 * read `/me`, and to assert two **refusals** a browser cannot demonstrate on its own (the queue read
 * before the grant, and the release after it) — both with the bearer token the application itself put
 * on the wire. `exchangeSql` reads state back and removes what this suite granted.
 *
 * ── The phases ──
 *
 *   LR-B1   a laboratory cashier holds no intake authority: no work sections, and the queue API refuses
 *   LR-B2   a clinician prescribes and routes two orders to this laboratory, in a known order
 *   LR-B3   the laboratory administrator grants `Traiter les analyses` by clicking, changing no role
 *   LR-B4   the cashier opens `Ordres`, lands on the incoming queue, and both routed orders are there
 *   LR-B5   the incoming queue is oldest-routed-first, cross-checked against `selected_at`
 *   LR-B6   the cashier accepts the older order; it leaves the incoming queue and survives a reload
 *   LR-B7   intake authority is not release authority: no `Résultats` section, and the server refuses
 *   LR-B8   the cashier declines the other order; nothing asks for a reason; the refusal is terminal
 *   LR-B9   a technician continues the accepted work -- intake and processing are different people
 *   LR-B10  the grant is withdrawn: the queue API refuses again, `Ordres` disappears, billing intact
 *
 * **Specimens are deliberately absent.** D-3 is open and no specimen entity exists; LR-B9 records the
 * collection milestone the workflow already has and stops there. No label, barcode, appointment,
 * triage or priority appears anywhere below, and no waiting *duration* is read or asserted — this
 * ticket refused derived waiting time on the laboratory side exactly as DHEALTH-FRONT-DESK-001 did on
 * the clinic side. Ordering by a recorded routing instant is not displaying a number derived from one.
 *
 * ── State this suite leaves behind, stated rather than discovered ──
 *
 * Two real lab orders, one accepted and collected, one refused. Nothing deletes a lab order and this
 * suite does not try: `scripts/demo/reset.sh` clears them as workflow state, which is the supported
 * way back to the baseline. **The capability grant is different and is cleaned up here**, because
 * `exchange_membership_capability_overrides` is *not* in `demo_seed._WORKFLOW_TABLES` — a reset would
 * leave it standing, and `09-billing.spec.ts` asserts what this same cashier can reach. So
 * `clearOverrides` runs before *and* after, and LR-B1 fails loudly rather than passing quietly if a
 * crashed earlier run left the grant behind.
 *
 * All patient data here is the demo baseline's synthetic patient. No real patient data is used.
 *
 * Passing does not make laboratory reception usable: nobody who works at a laboratory counter has
 * tried it. That judgement is a human one and this file cannot make it.
 */

const EXCHANGE = process.env.E2E_EXCHANGE_URL ?? 'https://exchange.localhost'
const API = `${EXCHANGE}/exchange/v1`

const CLINICIAN = {
  username: process.env.DEMO_CLINICIAN_USERNAME ?? 'demo-clinician',
  password: process.env.DEMO_CLINICIAN_PASSWORD ?? '',
}
/** The intake persona for this suite: a laboratory cashier, composed into reception by one grant. */
const LAB_CLERK = {
  username: process.env.DEMO_LAB_CLERK_USERNAME ?? 'demo-lab-clerk',
  password: process.env.DEMO_LAB_CLERK_PASSWORD ?? '',
}
const LAB_ADMIN = {
  username: process.env.DEMO_LAB_ADMIN_USERNAME ?? 'demo-lab-admin',
  password: process.env.DEMO_LAB_ADMIN_PASSWORD ?? '',
}
const LAB_TECH = {
  username: process.env.DEMO_LAB_TECH_USERNAME ?? 'demo-lab-tech',
  password: process.env.DEMO_LAB_TECH_PASSWORD ?? '',
}

/** The one capability this ticket's feature needs. Its label is the server's own (ADR-067 catalog). */
const INTAKE = { key: 'lab.queue.work', label: 'Traiter les analyses' } as const
/** Must never come with it (ADR-044), and must never be displaced by it (ADR-066). */
const RELEASE = { key: 'lab.result.release', label: 'Libérer un résultat' } as const
const TILL = { key: 'billing.operate', label: 'Tenir la caisse' } as const

const PATIENT = 'Patient Démo Alpha'
const CATALOG_ENTRY = 'Analyse de démonstration A'
const LAB_ORG = 'Laboratoire Démo DHealth'

/** The two orders this run routes, in the order the clinician raised them. */
let firstReference = ''
let secondReference = ''
let firstOrderId = ''
let secondOrderId = ''
let labId = ''
let clerkMembership = ''

interface Membership {
  organization_id: string
  organization_type: string
  roles: string[]
  capabilities: string[]
}

interface Session {
  token: string
  identityId: string
  memberships: Membership[]
}

function api(): Promise<APIRequestContext> {
  return apiRequest.newContext({ ignoreHTTPSErrors: true })
}

function auth(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` }
}

async function signIn(page: Page, who: { username: string; password: string }): Promise<Session> {
  expect(who.password, `${who.username}'s password must come from demo/native/demo.env`).not.toBe('')
  const tokenPromise = captureFirstExchangeBearerToken(page)
  await page.goto('/')
  await loginViaRealKeycloak(page, who.username, who.password)
  const token = await tokenPromise
  const ctx = await api()
  try {
    const me = await ctx.get(`${API}/me`, { headers: auth(token) })
    expect(me.status(), 'a real login must authorize /me').toBe(200)
    const body = await me.json()
    return {
      token,
      identityId: body.identity_id as string,
      memberships: body.memberships as Membership[],
    }
  } finally {
    await ctx.dispose()
  }
}

/**
 * A sign-in in its own browser context.
 *
 * Signing a second person in on the first person's page never reaches Keycloak -- the session is
 * still live -- so the login helper would wait for a form that never appears. Four people at four
 * workstations is also what this suite is about.
 */
async function freshSignIn(browser: Browser, who: { username: string; password: string }) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true })
  const page = await context.newPage()
  const session = await signIn(page, who)
  return { session, page, context }
}

function labOf(session: Session): Membership {
  const found = session.memberships.find((m) => m.organization_type === 'LAB')
  if (!found) throw new Error('LAB membership missing from /me -- is the demo baseline seeded?')
  return found
}

function providerOf(session: Session): Membership {
  const found = session.memberships.find((m) => m.organization_type === 'PROVIDER')
  if (!found) throw new Error('PROVIDER membership missing from /me -- is the demo baseline seeded?')
  return found
}

function nav(page: Page) {
  return page.getByRole('navigation', { name: 'Navigation principale' })
}

async function openSection(page: Page, section: string): Promise<void> {
  await nav(page).getByRole('button', { name: section, exact: true }).click()
}

/** This identity's capabilities at this organization, straight from `/me`. */
async function capabilities(token: string, organizationId: string): Promise<string[]> {
  const ctx = await api()
  try {
    const body = await (await ctx.get(`${API}/me`, { headers: auth(token) })).json()
    return (
      (body.memberships as Membership[]).find((m) => m.organization_id === organizationId)
        ?.capabilities ?? []
    )
  } finally {
    await ctx.dispose()
  }
}

async function queueStatus(token: string, organizationId: string): Promise<number> {
  const ctx = await api()
  try {
    const response = await ctx.get(`${API}/organizations/${organizationId}/lab-orders?limit=100`, {
      headers: auth(token),
    })
    return response.status()
  } finally {
    await ctx.dispose()
  }
}

/** The laboratory's own queue, as the API returns it -- used to resolve full identifiers. */
async function queueRows(
  token: string,
  organizationId: string,
): Promise<Array<{ order_id: string; status: string }>> {
  const ctx = await api()
  try {
    const response = await ctx.get(`${API}/organizations/${organizationId}/lab-orders?limit=100`, {
      headers: auth(token),
    })
    expect(response.status(), 'the laboratory queue must be readable').toBe(200)
    return (await response.json()) as Array<{ order_id: string; status: string }>
  } finally {
    await ctx.dispose()
  }
}

/** Open a member's capability sheet in `Équipe et accès`, addressed by **username**. */
async function openSheet(page: Page, username: string) {
  await openSection(page, 'Équipe et accès')
  await expect(page.getByRole('heading', { name: 'Équipe et accès' })).toBeVisible()
  const row = page.getByRole('listitem').filter({ hasText: username }).first()
  await expect(row).toBeVisible()
  const opener = row.getByRole('button', { name: 'Voir les autorisations' })
  if ((await opener.count()) > 0) {
    await opener.click()
  }
  // The region names the person, so operating on the wrong sheet is not expressible.
  return page.getByRole('region', { name: `Autorisations — ${username}` })
}

/**
 * The incoming-queue cards, in the order the page renders them.
 *
 * Matched by the text every queue card carries and the detail panel's own reference line cannot be
 * confused with, because the panel is not a `listitem`. Scoping this way rather than to "the list"
 * is deliberate: the navigation is also a list of listitems, and a page-wide `listitem` locator
 * finds it first -- the exact mistake `12-front-desk.spec.ts` records making.
 */
function queueCards(page: Page) {
  return page.getByRole('listitem').filter({ hasText: "Référence de l'ordre" })
}

/** Every order identifier on the current queue tab, in rendered order. */
async function renderedOrderIds(page: Page): Promise<string[]> {
  await expect(queueCards(page).first()).toBeVisible({ timeout: 30_000 })
  const texts = await queueCards(page).locator('code').allInnerTexts()
  return texts.map((text) => text.trim())
}

/** Open this run's order from whichever tab is showing, by its full identifier. */
async function openCard(page: Page, orderId: string): Promise<void> {
  const card = queueCards(page).filter({ hasText: orderId }).first()
  await expect(card).toBeVisible({ timeout: 30_000 })
  await card.getByRole('button', { name: `Examiner l'ordre de ${PATIENT}` }).click()
  await expect(page.getByText(orderId).first()).toBeVisible({ timeout: 30_000 })
}

/**
 * Switch queue tabs and wait for the switch to have actually happened.
 *
 * The wait is not politeness. Every tab renders the same two orders in this run, so the cards from
 * the tab being left are indistinguishable by content from the ones arriving -- a single read taken
 * between the click and the refetch returns the *previous* tab's list and silently answers the wrong
 * question. Found exactly that way: LR-B5's review-view assertion failed against the incoming list.
 *
 * Waiting for the queue request to come back is necessary and not sufficient -- React still has a
 * render to do -- so the assertions that depend on rendered order poll as well.
 */
async function openTab(page: Page, label: string): Promise<void> {
  const settled = page.waitForResponse(
    (response) => response.url().includes('/lab-orders') && response.status() === 200,
    { timeout: 30_000 },
  )
  await page.getByRole('tab', { name: label }).click()
  await settled
  await expect(page.getByRole('tab', { name: label, selected: true })).toBeVisible()
}

/**
 * Prescribe one order for the baseline patient and route it to the demo laboratory, by clicking.
 *
 * Returns the reference the **clinic** screen shows, which is deliberately truncated for reading --
 * the full identifier is resolved from the laboratory's own queue later, never by un-truncating a
 * display string.
 */
async function prescribeAndRoute(page: Page, known: string[]): Promise<string> {
  await openSection(page, 'Patients')
  await expect(page.getByText(PATIENT).first()).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: /^Ouvrir/ }).first().click()

  await page.getByRole('button', { name: 'Nouvel ordre de laboratoire' }).click()
  await page.getByRole('checkbox', { name: new RegExp(CATALOG_ENTRY) }).check()
  await page.getByRole('button', { name: /^Suivant/ }).click()
  await page.getByRole('button', { name: 'Confirmer et prescrire' }).click()

  await expect(page.getByText(/Choisir un laboratoire destinataire/)).toBeVisible({ timeout: 30_000 })
  await page.getByRole('radio', { name: new RegExp(LAB_ORG) }).check()
  await page.getByRole('button', { name: "Confirmer l'envoi au laboratoire" }).click()

  await openSection(page, 'Ordres')
  const rows = page.getByRole('row').filter({ hasText: PATIENT })
  await expect(rows.first()).toBeVisible({ timeout: 30_000 })
  // Column order: Patient, Analyses, Statut, Laboratoire destinataire, Date, Référence, Parcours.
  const references: string[] = []
  for (const row of await rows.all()) {
    references.push((await row.locator('td').nth(5).innerText()).replace('…', '').trim())
  }
  const fresh = references.filter((reference) => !known.includes(reference))
  expect(fresh, 'prescribing must add exactly one order to the clinic table').toHaveLength(1)
  expect(fresh[0].length).toBeGreaterThan(4)
  return fresh[0]
}

/**
 * Remove the capability override this suite grants.
 *
 * **Not optional tidiness.** `exchange_membership_capability_overrides` is absent from
 * `demo_seed._WORKFLOW_TABLES`, so `scripts/demo/reset.sh` leaves a leftover grant standing, and
 * `09-billing.spec.ts` asserts what this same laboratory cashier can reach. Targeted rather than a
 * truncate, and scoped to the demo organizations, so it can never destroy a facility's deliberate
 * configuration. Same discipline as `10-capabilities.spec.ts` and `12-front-desk.spec.ts`, which
 * both found the need for it the hard way.
 */
function clearOverrides() {
  exchangeSql(
    `DELETE FROM exchange_membership_capability_overrides
      WHERE membership_id IN (
        SELECT m.id FROM exchange_organization_memberships m
          JOIN exchange_organizations o ON o.id = m.organization_id
         WHERE o.name LIKE '%Démo DHealth'
      )`,
  )
}

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  await waitForEdge()
  clearOverrides()
})

test.afterAll(() => {
  // The intake authority must not outlive the suite that granted it -- see `clearOverrides`.
  clearOverrides()
})

test.describe('DHEALTH-LAB-RECEPTION-001 — a laboratory day begins at the counter', () => {
  // ── LR-B1 — the persona, before anything is granted ────────────────────────────────────────

  test('LR-B1: a laboratory cashier holds no intake authority, and the queue API refuses', async ({
    browser,
  }) => {
    const clerk = await freshSignIn(browser, LAB_CLERK)
    const membership = labOf(clerk.session)
    labId = membership.organization_id

    // A cashier, and only a cashier. Every composition proof below depends on this being true at the
    // start -- if they already held intake authority, granting it would prove nothing. A failure
    // here after a crashed run means a leftover row in `exchange_membership_capability_overrides`,
    // which `scripts/demo/reset.sh` does not clear; delete it rather than weakening this assertion.
    expect(membership.roles, 'demo-lab-clerk must be BILLING_CLERK and nothing else').toEqual([
      'BILLING_CLERK',
    ])
    expect(membership.capabilities).not.toContain(INTAKE.key)
    expect(membership.capabilities).not.toContain(RELEASE.key)
    expect(membership.capabilities).toContain(TILL.key)

    clerkMembership = exchangeSql(
      `SELECT m.id FROM exchange_organization_memberships m
         WHERE m.organization_id = '${labId}' AND m.identity_id = '${clerk.session.identityId}'`,
    )
    expect(clerkMembership, 'the cashier must have exactly one membership row here').toMatch(
      /^[0-9a-f-]{36}$/,
    )

    // The work queue is refused outright...
    expect(await queueStatus(clerk.session.token, labId)).toBe(403)

    // ...and neither work section is even reachable: `billing.operate` alone unlocks no laboratory
    // surface, so the cashier lands on `Facturation`. The absence is usability; the 403 is the
    // security, and only the direct call above can show which one is doing the work.
    await expect(nav(clerk.page).getByRole('button', { name: 'Ordres', exact: true })).toHaveCount(0)
    await expect(
      nav(clerk.page).getByRole('button', { name: "Aujourd'hui", exact: true }),
    ).toHaveCount(0)
    await expect(nav(clerk.page).getByRole('button', { name: 'Résultats', exact: true })).toHaveCount(
      0,
    )
    await expect(nav(clerk.page).getByRole('button', { name: 'Facturation', exact: true })).toBeVisible()

    await clerk.context.close()
  })

  // ── LR-B2 — real work, routed here ────────────────────────────────────────────────────────

  test('LR-B2: a clinician prescribes and routes two orders to this laboratory', async ({
    browser,
  }) => {
    const clinician = await freshSignIn(browser, CLINICIAN)
    const provider = providerOf(clinician.session)
    expect(provider.capabilities, 'the clinician must be able to raise an order').toContain(
      'order.manage',
    )

    // Two, and in a known order: LR-B5's claim is that the laboratory sees the one that has been
    // waiting longest first, which one order cannot demonstrate.
    firstReference = await prescribeAndRoute(clinician.page, [])
    secondReference = await prescribeAndRoute(clinician.page, [firstReference])
    expect(secondReference).not.toBe(firstReference)

    await clinician.context.close()
  })

  // ── LR-B3 — the grant, by clicking, changing no role ──────────────────────────────────────

  test('LR-B3: the laboratory administrator grants intake authority, changing no role', async ({
    browser,
  }) => {
    const admin = await freshSignIn(browser, LAB_ADMIN)
    const sheet = await openSheet(admin.page, LAB_CLERK.username)
    await expect(sheet).toBeVisible()

    const intakeRow = sheet.getByRole('listitem').filter({ hasText: INTAKE.label }).first()
    await expect(intakeRow).toContainText("Aucun rôle ne l'accorde")
    await intakeRow.getByRole('button', { name: 'Autoriser' }).click()
    // The sheet says *why* they now have it, which is ADR-067 §20's whole point.
    await expect(
      sheet.getByRole('listitem').filter({ hasText: INTAKE.label }).first(),
    ).toContainText('Accordé directement')

    // **The release authority is deliberately left alone**, and stays visibly ungranted. ADR-044
    // says entry and release are separate authorities; this is that sentence on screen.
    await expect(
      sheet.getByRole('listitem').filter({ hasText: RELEASE.label }).first(),
    ).toContainText("Aucun rôle ne l'accorde")

    // **The assertion that makes this ticket's claim true.** One capability granted, and the job
    // title is untouched: no promotion, no `LAB_RECEPTIONIST`, no combination role.
    const roles = exchangeSql(
      `SELECT string_agg(role, ',' ORDER BY role) FROM exchange_organization_role_assignments
        WHERE membership_id = '${clerkMembership}'`,
    )
    expect(roles).toBe('BILLING_CLERK')

    // Exactly one override row, and it is a GRANT of exactly the one capability.
    const overrides = exchangeSql(
      `SELECT string_agg(capability || ':' || effect, ',' ORDER BY capability)
         FROM exchange_membership_capability_overrides WHERE membership_id = '${clerkMembership}'`,
    )
    expect(overrides).toBe(`${INTAKE.key}:GRANT`)

    await admin.context.close()
  })

  // ── LR-B4 — the routed work is visible to the person who was granted the authority ────────

  test('LR-B4: the cashier opens the incoming queue and finds both routed orders', async ({
    browser,
  }) => {
    const clerk = await freshSignIn(browser, LAB_CLERK)
    const held = await capabilities(clerk.session.token, labId)
    expect(held, 'the grant must be live on this session').toContain(INTAKE.key)
    // Still not a releaser, and still the cashier. This is the whole point of one capability.
    expect(held).not.toContain(RELEASE.key)
    expect(held).toContain(TILL.key)

    // The laboratory's own "what needs me now" surface counts the routed work rather than leaving
    // it to be discovered by opening a tab.
    await openSection(clerk.page, "Aujourd'hui")
    await expect(clerk.page.getByRole('heading', { name: 'Accueil Laboratoire' })).toBeVisible()
    await expect(clerk.page.getByText('Nouveaux ordres')).toBeVisible()

    await openSection(clerk.page, 'Ordres')
    // The queue opens on the incoming tab by itself -- reception is the default question a
    // laboratory asks, so nothing here clicks a filter first.
    await expect(clerk.page.getByRole('tab', { name: 'Nouveaux', selected: true })).toBeVisible()

    const rows = await queueRows(clerk.session.token, labId)
    const selected = rows.filter((row) => row.status === 'SELECTED')
    // A re-run without `scripts/demo/reset.sh` fails here, loudly, rather than passing against
    // leftover state -- LR-B5's ordering claim is only meaningful over this run's own two orders.
    expect(
      selected.length,
      'exactly this run\'s two routed orders must be awaiting an answer -- run scripts/demo/reset.sh first',
    ).toBe(2)

    const matchFirst = selected.find((row) => row.order_id.startsWith(firstReference.toLowerCase()))
    const matchSecond = selected.find((row) => row.order_id.startsWith(secondReference.toLowerCase()))
    expect(matchFirst, 'the clinic screen and the laboratory queue must agree about the first order').toBeDefined()
    expect(matchSecond, 'the clinic screen and the laboratory queue must agree about the second order').toBeDefined()
    firstOrderId = matchFirst!.order_id
    secondOrderId = matchSecond!.order_id

    // Whose work it is, and what was asked for, on the queue row itself -- the two things reception
    // has to establish before answering. Nothing here is a diagnosis or an interpretation.
    const card = queueCards(clerk.page).filter({ hasText: firstOrderId }).first()
    await expect(card).toContainText(PATIENT)
    await expect(card).toContainText(CATALOG_ENTRY)
    await expect(card).toContainText('Nouvel ordre — à accepter')

    await clerk.context.close()
  })

  // ── LR-B5 — the ordering defect this ticket actually fixed ────────────────────────────────

  test('LR-B5: the incoming queue shows the order that has been waiting longest first', async ({
    browser,
  }) => {
    const clerk = await freshSignIn(browser, LAB_CLERK)
    await openSection(clerk.page, 'Ordres')

    const rendered = await renderedOrderIds(clerk.page)
    const firstAt = rendered.indexOf(firstOrderId)
    const secondAt = rendered.indexOf(secondOrderId)
    expect(firstAt, 'the first order must be on the incoming queue').toBeGreaterThanOrEqual(0)
    expect(secondAt, 'the second order must be on the incoming queue').toBeGreaterThanOrEqual(0)

    // Relative, never positional: a laboratory whose backlog carries earlier work legitimately has
    // older orders above these two, and asserting an index of 0 would fail for that reason rather
    // than for this one.
    expect(
      firstAt,
      'the order routed first must appear above the one routed after it',
    ).toBeLessThan(secondAt)

    // Cross-checked against the fact the sort is supposed to be reading, so a queue that happened
    // to be right for some other reason cannot satisfy this phase. `selected_at` is written once
    // per order and never updated, which is why it and not `updated_at` is the routing instant.
    const byRouting = exchangeSql(
      `SELECT string_agg(lab_order_id::text, ',' ORDER BY selected_at ASC, lab_order_id ASC)
         FROM exchange_lab_selections
        WHERE lab_order_id IN ('${firstOrderId}', '${secondOrderId}')`,
    )
    expect(byRouting).toBe(`${firstOrderId},${secondOrderId}`)

    // And the review views are unchanged: newest activity first is right for a question about what
    // changed, and the asymmetry is what makes the incoming sort deliberate rather than accidental.
    // Exact rather than relative here, and it can be: at this point in the run these are the only
    // two orders this laboratory has, so `Tous` must be precisely the reverse of the incoming queue.
    await openTab(clerk.page, 'Tous')
    await expect
      .poll(() => renderedOrderIds(clerk.page), {
        message: 'the review views must still sort newest activity first',
        timeout: 20_000,
      })
      .toEqual([secondOrderId, firstOrderId])

    // No elapsed time is displayed anywhere on the queue. Ordering by a recorded instant is not
    // deriving a duration from one, and the second needs the clinical lead's sign-off this ticket
    // does not have.
    await openTab(clerk.page, 'Nouveaux')
    await expect(queueCards(clerk.page).first()).toBeVisible()
    for (const card of await queueCards(clerk.page).all()) {
      await expect(card).not.toContainText(/depuis \d|il y a \d|\d+\s*(min|minutes|h|heures)\b/)
    }

    await clerk.context.close()
  })

  // ── LR-B6 — accepting, and the acceptance being a fact rather than a screen state ─────────

  test('LR-B6: the cashier accepts the older order, and it leaves the incoming queue', async ({
    browser,
  }) => {
    const clerk = await freshSignIn(browser, LAB_CLERK)
    await openSection(clerk.page, 'Ordres')
    await openCard(clerk.page, firstOrderId)

    await clerk.page.getByRole('button', { name: "Accepter l'ordre" }).click()
    await expect(clerk.page.getByText('Accepté').first()).toBeVisible({ timeout: 30_000 })

    // A genuine reload, not a re-render: nothing about this state may depend on what this tab
    // happens to remember.
    await clerk.page.reload()
    await expect(clerk.page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({
      timeout: 30_000,
    })
    await openSection(clerk.page, 'Ordres')

    // Gone from the incoming queue -- answered work is not incoming work, which is the projection's
    // whole meaning...
    const incoming = await renderedOrderIds(clerk.page)
    expect(incoming).not.toContain(firstOrderId)
    expect(incoming, 'the unanswered order must still be waiting').toContain(secondOrderId)

    // ...and present under `Acceptés`, which is where it belongs now.
    await openTab(clerk.page, 'Acceptés')
    await expect
      .poll(() => renderedOrderIds(clerk.page), { timeout: 20_000 })
      .toContain(firstOrderId)

    // The server agrees, which is the part that makes it durable rather than displayed.
    const rows = await queueRows(clerk.session.token, labId)
    expect(rows.find((row) => row.order_id === firstOrderId)?.status).toBe('ACCEPTED')

    await clerk.context.close()
  })

  // ── LR-B7 — intake authority is not release authority ────────────────────────────────────

  test('LR-B7: the same person who accepted the work cannot release its result', async ({
    browser,
  }) => {
    const clerk = await freshSignIn(browser, LAB_CLERK)

    // No `Résultats` section, which is usability: the cashier is never offered work their authority
    // cannot complete.
    await expect(nav(clerk.page).getByRole('button', { name: 'Résultats', exact: true })).toHaveCount(
      0,
    )

    // The refusal is the boundary, and only a direct call shows that the browser is not what is
    // stopping it. `_require_lab_result_releaser` runs before any order is looked up, so this is a
    // statement about authority and not about the order's state. The body is well-formed on purpose:
    // a malformed one would be rejected at validation and would prove nothing about authorization.
    const ctx = await api()
    try {
      const response = await ctx.post(
        `${API}/organizations/${labId}/lab-orders/${firstOrderId}/release-results`,
        {
          headers: auth(clerk.session.token),
          data: { expected_result_fingerprint: 'synthetique-jamais-valide' },
        },
      )
      expect(
        response.status(),
        'possessing intake authority must never imply result-release authority',
      ).toBe(403)
    } finally {
      await ctx.dispose()
    }

    // And the capability itself is still absent -- the accept did not quietly widen anything.
    expect(await capabilities(clerk.session.token, labId)).not.toContain(RELEASE.key)

    await clerk.context.close()
  })

  // ── LR-B8 — declining, and the reason that deliberately does not exist ───────────────────

  test('LR-B8: the cashier declines the other order, and nothing asks for a reason', async ({
    browser,
  }) => {
    const clerk = await freshSignIn(browser, LAB_CLERK)
    await openSection(clerk.page, 'Ordres')
    await openCard(clerk.page, secondOrderId)

    await clerk.page.getByRole('button', { name: "Refuser l'ordre" }).click()

    // **The finding this phase pins.** The existing decline is a pure state transition: there is no
    // reason column, no taxonomy and nothing reason-shaped reaching the clinic. The confirmation
    // step asks for confirmation and nothing else. Inventing a reason vocabulary here is exactly the
    // "obviously should" instinct `AGENTS.md` forbids, and D-11 is already open because the other
    // laboratory reason vocabulary has not been validated by anyone who works in a laboratory.
    const confirm = clerk.page.getByRole('button', { name: 'Confirmer le refus' })
    await expect(confirm).toBeVisible()
    await expect(clerk.page.getByRole('textbox')).toHaveCount(0)
    await expect(clerk.page.getByRole('combobox')).toHaveCount(0)
    await confirm.click()

    await expect(clerk.page.getByText('Refusé').first()).toBeVisible({ timeout: 30_000 })

    await clerk.page.reload()
    await expect(clerk.page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({
      timeout: 30_000,
    })
    await openSection(clerk.page, 'Ordres')

    // A refusal is terminal and does not come back as incoming work. `Aucun ordre` rather than a
    // card list is the honest rendering of an empty backlog.
    await expect(
      clerk.page.getByText('Aucun ordre ne correspond à ce filtre.'),
    ).toBeVisible({ timeout: 30_000 })
    await expect(queueCards(clerk.page)).toHaveCount(0)

    await openTab(clerk.page, 'Refusés')
    await expect
      .poll(() => renderedOrderIds(clerk.page), { timeout: 20_000 })
      .toContain(secondOrderId)

    const rows = await queueRows(clerk.session.token, labId)
    expect(rows.find((row) => row.order_id === secondOrderId)?.status).toBe('REJECTED')

    await clerk.context.close()
  })

  // ── LR-B9 — the handoff into the workflow that already existed ───────────────────────────

  test('LR-B9: a technician continues the work the cashier accepted', async ({ browser }) => {
    const tech = await freshSignIn(browser, LAB_TECH)
    expect(labOf(tech.session).roles, 'the technician is a technician, not a receptionist').toContain(
      'LAB_TECH',
    )

    await openSection(tech.page, 'Ordres')
    await openTab(tech.page, 'Acceptés')
    await openCard(tech.page, firstOrderId)

    // One transition, deliberately. Reception's job ends when the work is accepted; carrying this
    // order all the way to a released result is `01-golden-journey.spec.ts`'s claim, and re-walking
    // it here would change what that suite's number means.
    await tech.page.getByRole('button', { name: 'Enregistrer la collecte' }).click()
    await tech.page.getByRole('button', { name: 'Confirmer la collecte' }).click()
    await expect(tech.page.getByRole('button', { name: "Commencer l'analyse" })).toBeVisible({
      timeout: 30_000,
    })

    // Intake by one person, processing by another, on the same order -- which is what "hands
    // accepted work into the existing workflow" has to mean to be worth claiming.
    const collected = exchangeSql(
      `SELECT count(*) FROM exchange_lab_sample_collections WHERE lab_order_id = '${firstOrderId}'`,
    )
    expect(collected).toBe('1')

    await tech.context.close()
  })

  // ── LR-B10 — withdrawal, and what must survive it ───────────────────────────────────────

  test('LR-B10: withdrawing the grant removes intake, changes no role, and leaves the till alone', async ({
    browser,
  }) => {
    const admin = await freshSignIn(browser, LAB_ADMIN)
    const sheet = await openSheet(admin.page, LAB_CLERK.username)
    const intakeRow = sheet.getByRole('listitem').filter({ hasText: INTAKE.label }).first()

    // **`Suivre le rôle`, not `Refuser`.** Clearing the override is what *withdrawing* a direct
    // grant means; adding a DENY would record a different decision -- "explicitly forbidden" and
    // "never granted" are two things an administrator acts on differently.
    await intakeRow.getByRole('button', { name: 'Suivre le rôle' }).click()
    await expect(
      sheet.getByRole('listitem').filter({ hasText: INTAKE.label }).first(),
    ).toContainText("Aucun rôle ne l'accorde")
    await admin.context.close()

    const clerk = await freshSignIn(browser, LAB_CLERK)
    const membership = labOf(clerk.session)

    // The authority is gone from the server's answer and from the server's behaviour...
    expect(membership.capabilities).not.toContain(INTAKE.key)
    expect(await queueStatus(clerk.session.token, labId)).toBe(403)
    await expect(nav(clerk.page).getByRole('button', { name: 'Ordres', exact: true })).toHaveCount(0)

    // ...the job title never changed in either direction...
    expect(membership.roles).toEqual(['BILLING_CLERK'])
    const roles = exchangeSql(
      `SELECT string_agg(role, ',' ORDER BY role) FROM exchange_organization_role_assignments
        WHERE membership_id = '${clerkMembership}'`,
    )
    expect(roles).toBe('BILLING_CLERK')

    // ...and the till is untouched, which is ADR-066's independence in the other direction: taking
    // reception away from a cashier must not take the cash register with it.
    expect(membership.capabilities).toContain(TILL.key)
    await expect(
      nav(clerk.page).getByRole('button', { name: 'Facturation', exact: true }),
    ).toBeVisible()

    // The work this run created is still there and still answered -- withdrawing somebody's
    // authority is not a way to undo what they did.
    const remaining = exchangeSql(
      `SELECT string_agg(status, ',' ORDER BY status) FROM exchange_lab_orders
        WHERE id IN ('${firstOrderId}', '${secondOrderId}')`,
    )
    expect(remaining).toBe('ACCEPTED,REJECTED')

    await clerk.context.close()
  })
})
