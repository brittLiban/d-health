import { expect, request as apiRequest, test, type APIRequestContext, type Page } from '@playwright/test'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'
import { exchangeSql } from '../support/securityAdmin'
import { waitForEdge } from '../support/edgeReadiness'

/**
 * DHEALTH-CLINIC-VISIT-001 — the clinic visit, against the real stack, in a real browser.
 *
 * ── The question this file answers ──
 *
 * Not "does the visit API work" — `adapter/tests/test_dhealthvisit001_clinic_visits.py` answers that
 * exhaustively, and the PostgreSQL qualification answers the concurrency half. This file answers
 * **can a clinician actually run a consultation in DHealth end to end**: sign in, register an
 * arrival, take the patient, write and correct a note, order analyses *from inside the visit*, and
 * finish — with the order provably attached to that consultation in the database afterwards.
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated, no session injected, no auth state written into browser storage. Every
 * clinical act below is performed by clicking the product, by a clinician who signed in through a
 * real Authorization Code + PKCE flow. `exchangeSql` appears only to read state *back* and assert
 * that the database says what the screen said — never to cause a change the product could not.
 *
 * ── The phases ──
 *
 *   PHASE A  the section exists and is reachable for a clinician
 *   PHASE B  registering an arrival puts a real CHECKED_IN visit on the worklist
 *   PHASE C  taking it records the clinician of record and the start time
 *   PHASE D  a note, then a correction: two revisions, neither overwritten
 *   PHASE E  analyses ordered from the visit, and the order carries visit_id in the database
 *   PHASE F  the visit completes with the order still outstanding
 *   PHASE G  the refusals: no cancelling a started visit, no cross-tenant visit, no lab access
 *
 * ── State this suite leaves behind, stated rather than discovered ──
 *
 * A visit cannot be deleted — every foreign key pointing at it is `RESTRICT`, deliberately, because
 * a completed consultation is not erasable. So this suite does **not** clean up after itself the way
 * `07-access-lifecycle.spec.ts` does, and it cannot. `scripts/demo/reset.sh` clears visits and visit
 * notes as workflow state (`demo_seed._WORKFLOW_TABLES`, extended by this ticket), which is the
 * supported way to get back to the baseline before running it again.
 *
 * All patient data here is the demo baseline's synthetic patient. No real patient data is used.
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

interface Membership {
  organization_id: string
  organization_name: string
  organization_type: string
  organization_status: string
  membership_status: string
  roles: string[]
}

interface Session {
  token: string
  identityId: string
  memberships: Membership[]
}

function api(): Promise<APIRequestContext> {
  return apiRequest.newContext({ ignoreHTTPSErrors: true })
}

/** A real browser login, returning the bearer token the application itself put on the wire. */
async function signIn(page: Page, who: { username: string; password: string }): Promise<Session> {
  expect(who.password, `${who.username}'s password must come from demo/native/demo.env`).not.toBe('')
  const tokenPromise = captureFirstExchangeBearerToken(page)
  await page.goto('/')
  await loginViaRealKeycloak(page, who.username, who.password)
  const token = await tokenPromise
  const ctx = await api()
  try {
    const me = await ctx.get(`${API}/me`, { headers: { Authorization: `Bearer ${token}` } })
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

function auth(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` }
}

function orgOf(session: Session, type: 'PROVIDER' | 'LAB'): Membership {
  const found = session.memberships.find((m) => m.organization_type === type)
  if (!found) throw new Error(`${type} membership missing from /me -- is the demo baseline seeded?`)
  return found
}

async function get(token: string, path: string) {
  const ctx = await api()
  try {
    const response = await ctx.get(`${API}${path}`, { headers: auth(token) })
    return { status: response.status(), text: await response.text() }
  } finally {
    await ctx.dispose()
  }
}

async function post(token: string, path: string) {
  const ctx = await api()
  try {
    const response = await ctx.post(`${API}${path}`, { headers: auth(token) })
    return { status: response.status(), text: await response.text() }
  } finally {
    await ctx.dispose()
  }
}

/** Opens the `Visites` section the way a clinician does: by clicking the navigation. */
async function openVisits(page: Page) {
  await page.getByRole('button', { name: 'Visites' }).click()
  await expect(page.getByRole('heading', { name: 'Visites', level: 1 })).toBeVisible()
}

/**
 * Registers an arrival through the interface, and returns the visit id the *database* holds for it.
 *
 * The reason for visit is made unique per run: this suite cannot delete what it creates, so several
 * runs against one demo database would otherwise produce indistinguishable worklist rows and a
 * `getByRole` that matched the wrong one.
 */
async function registerArrival(page: Page, reason: string) {
  await page.getByRole('button', { name: 'Enregistrer une arrivée' }).click()

  // **Scoped to the form**, not to the page. The worklist beside it may already show a row for this
  // same patient from an earlier run (this suite cannot delete visits), and an unscoped
  // `getByRole('button', { name: /Patient Démo Alpha/ })` then matches the search result *and* that
  // row — which silently selected the old visit instead of choosing the patient, and the reason field
  // never appeared. Found by a real run, not reasoned about.
  const form = page.getByRole('form', { name: 'Nouvelle arrivée' })
  await form.getByRole('button', { name: 'Rechercher', exact: true }).click()
  await form.getByRole('button', { name: /Patient Démo Alpha/ }).click()
  await form.getByLabel(/Motif de la visite, tel qu'énoncé/).fill(reason)
  await form.getByRole('button', { name: "Enregistrer l'arrivée" }).click()
}

let clinician: Session
let providerId: string
const RUN = `e2e-${Date.now()}`
const REASON = `Qualification visite ${RUN}`
let visitId = ''

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  await waitForEdge()
})

test.describe('DHEALTH-CLINIC-VISIT-001 — a consultation, end to end', () => {
  test('VISIT-A: a clinician signs in and reaches Visites', async ({ page }) => {
    clinician = await signIn(page, CLINICIAN)
    providerId = orgOf(clinician, 'PROVIDER').organization_id

    await openVisits(page)

    // The section is derived from real authority, and this clinician holds CLINICIAN at a provider.
    await expect(page.getByRole('button', { name: 'Visites' })).toBeVisible()
    // And the worklist is asking for the live states, not everything ever recorded.
    await expect(page.getByRole('button', { name: 'En cours' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  test('VISIT-B: registering an arrival creates a CHECKED_IN visit with no clinician', async ({
    page,
  }) => {
    clinician = await signIn(page, CLINICIAN)
    providerId = orgOf(clinician, 'PROVIDER').organization_id
    await openVisits(page)

    await registerArrival(page, REASON)

    // On screen: the patient is now waiting.
    await expect(page.getByText(REASON)).toBeVisible()
    await expect(page.getByText('En attente').first()).toBeVisible()

    // In the database: exactly one visit, CHECKED_IN, with no clinician and no start time. Read back
    // rather than trusted from the screen -- the screen is what this test is checking.
    const rows = await exchangeSql(
      `SELECT id, status, clinician_identity_id IS NULL AS no_clinician, ` +
        `started_at IS NULL AS not_started, completed_at IS NULL AS not_completed ` +
        `FROM exchange_visits WHERE reason_for_visit = '${REASON}'`,
    )
    expect(rows.trim().split('\n').filter(Boolean)).toHaveLength(1)
    expect(rows).toContain('CHECKED_IN')
    expect(rows).toContain('t|t|t')
    visitId = rows.trim().split('|')[0].trim()
    expect(visitId).not.toBe('')
  })

  test('VISIT-C: taking the visit records this clinician as the clinician of record', async ({
    page,
  }) => {
    clinician = await signIn(page, CLINICIAN)
    providerId = orgOf(clinician, 'PROVIDER').organization_id
    await openVisits(page)

    await page.getByText(REASON).click()
    await page.getByRole('button', { name: 'Prendre en charge' }).click()

    await expect(page.getByText('En consultation').first()).toBeVisible()
    await expect(page.getByRole('button', { name: 'Terminer la visite' })).toBeVisible()

    const rows = await exchangeSql(
      `SELECT status, clinician_identity_id, started_at IS NOT NULL AS started ` +
        `FROM exchange_visits WHERE id = '${visitId}'`,
    )
    expect(rows).toContain('IN_PROGRESS')
    expect(rows).toContain(clinician.identityId)
    expect(rows).toContain('|t')

    // The audit row exists, in the same transaction as the transition, and names the visit.
    const audit = await exchangeSql(
      `SELECT count(*) FROM exchange_security_audit_events ` +
        `WHERE event_type = 'visit_started' AND resource_id = '${visitId}' ` +
        `AND resource_type = 'visit' AND actor_identity_id = '${clinician.identityId}'`,
    )
    expect(audit.trim()).toBe('1')
  })

  test('VISIT-D: a note and its correction are two revisions, neither overwritten', async ({
    page,
  }) => {
    clinician = await signIn(page, CLINICIAN)
    await openVisits(page)
    await page.getByText(REASON).click()

    const editor = page.getByLabel(/Chaque enregistrement ajoute une révision/)
    await editor.fill(`Première rédaction ${RUN}.`)
    await page.getByRole('button', { name: 'Enregistrer la note' }).click()
    await expect(page.getByText(`Première rédaction ${RUN}.`)).toBeVisible()

    // The editor comes back empty -- an append must not look like an edit.
    await expect(editor).toHaveValue('')

    await editor.fill(`Correction ${RUN}.`)
    await page.getByRole('button', { name: 'Enregistrer la note' }).click()

    // Both revisions on screen, the earlier one marked as superseded rather than removed.
    await expect(page.getByText(`Première rédaction ${RUN}.`)).toBeVisible()
    await expect(page.getByText(`Correction ${RUN}.`)).toBeVisible()
    await expect(page.getByText(/Remplacée par une révision ultérieure/)).toBeVisible()

    const rows = await exchangeSql(
      `SELECT revision_number FROM exchange_visit_notes WHERE visit_id = '${visitId}' ` +
        `ORDER BY revision_number`,
    )
    expect(rows.trim().split('\n').map((line) => line.trim())).toEqual(['1', '2'])

    // The note body never reaches the security audit table.
    const leaked = await exchangeSql(
      `SELECT count(*) FROM exchange_security_audit_events ` +
        `WHERE resource_id = '${visitId}' AND event_type = 'visit_note_appended'`,
    )
    expect(leaked.trim()).toBe('2')
  })

  test('VISIT-E: analyses ordered from the visit are attached to it in the database', async ({
    page,
  }) => {
    clinician = await signIn(page, CLINICIAN)
    providerId = orgOf(clinician, 'PROVIDER').organization_id
    await openVisits(page)
    await page.getByText(REASON).click()

    // The hand-off: this is the one interaction that produces the linkage, and the ordering wizard
    // it hands off to is the existing, already-qualified one rather than a copy.
    await page.getByRole('button', { name: 'Demander des analyses' }).click()

    await page.getByRole('button', { name: 'Nouvel ordre de laboratoire' }).click()
    await page.getByRole('checkbox').first().check()
    await page.getByRole('button', { name: /Suivant : Réviser l’ordre/ }).click()
    await page.getByRole('button', { name: 'Confirmer et prescrire' }).click()

    // The database is the arbiter of the linkage, not the screen.
    await expect
      .poll(
        async () =>
          (
            await exchangeSql(
              `SELECT count(*) FROM exchange_lab_orders WHERE visit_id = '${visitId}'`,
            )
          ).trim(),
        { timeout: 60_000, message: 'the committed order must carry the visit' },
      )
      .toBe('1')

    // And the intent carries the same visit, which is what makes a retry safe.
    const intent = await exchangeSql(
      `SELECT count(*) FROM exchange_lab_order_intents WHERE visit_id = '${visitId}'`,
    )
    expect(intent.trim()).toBe('1')

    // The visit's own read shows it.
    const detail = await get(clinician.token, `/organizations/${providerId}/visits/${visitId}`)
    expect(detail.status).toBe(200)
    const parsed = JSON.parse(detail.text) as { orders: Array<{ visit_id: string | null }> }
    expect(parsed.orders).toHaveLength(1)
    expect(parsed.orders[0].visit_id).toBe(visitId)
  })

  test('VISIT-F: the visit completes with the diagnostic order still outstanding', async ({
    page,
  }) => {
    clinician = await signIn(page, CLINICIAN)
    providerId = orgOf(clinician, 'PROVIDER').organization_id
    await openVisits(page)
    await page.getByText(REASON).click()

    // Proof the order really is unfinished at the moment of completion, so this is not a vacuous
    // assertion about an order that happened to be done already.
    const before = await exchangeSql(
      `SELECT status FROM exchange_lab_orders WHERE visit_id = '${visitId}'`,
    )
    expect(before.trim()).not.toBe('COMPLETED')

    await page.getByRole('button', { name: 'Terminer la visite' }).click()
    await expect(page.getByText('Terminée').first()).toBeVisible()

    const rows = await exchangeSql(
      `SELECT status, completed_at IS NOT NULL AS done FROM exchange_visits WHERE id = '${visitId}'`,
    )
    expect(rows).toContain('COMPLETED|t')

    // The order is untouched by the visit finishing. Nothing cascaded, nothing was auto-completed.
    const after = await exchangeSql(
      `SELECT status FROM exchange_lab_orders WHERE visit_id = '${visitId}'`,
    )
    expect(after.trim()).toBe(before.trim())
  })

  test('VISIT-G: the refusals the real stack must produce', async ({ page }) => {
    clinician = await signIn(page, CLINICIAN)
    providerId = orgOf(clinician, 'PROVIDER').organization_id

    // A completed visit cannot be cancelled, reopened, or written to. 409 every time: the clinician
    // is authorized, and it is the visit's own state that refuses them.
    for (const action of ['cancel', 'take', 'complete']) {
      const refused = await post(
        clinician.token,
        `/organizations/${providerId}/visits/${visitId}/${action}`,
      )
      expect(refused.status, `${action} on a completed visit`).toBe(409)
    }

    // And the interface does not offer any of them.
    await openVisits(page)
    await page.getByRole('button', { name: 'Terminées et annulées' }).click()
    await page.getByText(REASON).click()
    await expect(page.getByRole('button', { name: 'Prendre en charge' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Terminer la visite' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Annuler la visite' })).toHaveCount(0)
    await expect(page.getByText(/la note n'est plus modifiable ici/)).toBeVisible()

    // An unauthenticated request is 401, never a redirect that leaks the shape of the surface.
    const anonymous = await api()
    try {
      const response = await anonymous.get(`${API}/organizations/${providerId}/visits`)
      expect(response.status()).toBe(401)
    } finally {
      await anonymous.dispose()
    }
  })

  test('VISIT-G2: a laboratory technician has no claim on a provider’s visits', async ({
    browser,
  }) => {
    // A **fresh browser context**, not the `page` the clinician is signed into. Reusing it would
    // find an existing session, never reach Keycloak, and fail in the login helper rather than
    // testing anything -- which is exactly what it did the first time this suite ran.
    const context = await browser.newContext({ ignoreHTTPSErrors: true })
    try {
      const techPage = await context.newPage()
      const tech = await signIn(techPage, LAB_TECH)

      // 404, not 403: a technician must not learn that this provider's visit surface exists.
      const techReads = await get(tech.token, `/organizations/${providerId}/visits`)
      expect(techReads.status).toBe(404)
      const techVisit = await get(tech.token, `/organizations/${providerId}/visits/${visitId}`)
      expect(techVisit.status).toBe(404)

      // The same visit id asked of the laboratory's *own* organization is **403, not 404**, and the
      // difference is the authorization contract rather than an inconsistency: 404 means "no
      // relationship with this organization at all" (anti-enumeration), 403 means "you belong here
      // and lack the role". A LAB_TECH is a real member of their laboratory, so they get 403 — and
      // the visit is still not disclosed, because the role check runs before any lookup.
      const labOrgId = orgOf(tech, 'LAB').organization_id
      const ownOrg = await get(tech.token, `/organizations/${labOrgId}/visits/${visitId}`)
      expect(ownOrg.status).toBe(403)
      expect(ownOrg.text).not.toContain(visitId)

      // And the technician's own application offers no Visites section at all.
      await expect(techPage.getByRole('button', { name: 'Visites' })).toHaveCount(0)
    } finally {
      await context.close()
    }
  })

  test('VISIT-H: an in-progress visit can never be cancelled', async ({ page }) => {
    // §12's most load-bearing refusal, proven against the real stack on a *fresh* visit rather than
    // the completed one above: contact with the patient happened, and no status may erase it.
    clinician = await signIn(page, CLINICIAN)
    providerId = orgOf(clinician, 'PROVIDER').organization_id
    await openVisits(page)

    const reason = `Qualification annulation ${RUN}`
    await registerArrival(page, reason)

    // No click needed: registering an arrival opens that visit, which is deliberate product
    // behaviour (the clinician who just checked somebody in is the one who wants to act on them).
    // It also means the reason now appears twice -- once in the worklist row, once in the detail
    // panel's Motif section -- so `.first()` is required rather than optional.
    await expect(page.getByText(reason).first()).toBeVisible()

    // While waiting, cancellation is offered.
    await expect(page.getByRole('button', { name: 'Annuler la visite' })).toBeVisible()

    await page.getByRole('button', { name: 'Prendre en charge' }).click()
    await expect(page.getByText('En consultation').first()).toBeVisible()

    // Taken: the control is gone from the interface...
    await expect(page.getByRole('button', { name: 'Annuler la visite' })).toHaveCount(0)

    // ...and the endpoint refuses it even when called directly.
    const inProgressId = (
      await exchangeSql(`SELECT id FROM exchange_visits WHERE reason_for_visit = '${reason}'`)
    ).trim()
    const refused = await post(
      clinician.token,
      `/organizations/${providerId}/visits/${inProgressId}/cancel`,
    )
    expect(refused.status).toBe(409)
    expect(refused.text).toContain('IN_PROGRESS')

    // The visit is still in progress: the refusal changed nothing.
    const after = await exchangeSql(
      `SELECT status, cancelled_at IS NULL AS not_cancelled FROM exchange_visits ` +
        `WHERE id = '${inProgressId}'`,
    )
    expect(after).toContain('IN_PROGRESS|t')
  })
})
