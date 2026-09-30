import {
  expect,
  request as apiRequest,
  test,
  type APIRequestContext,
  type Browser,
  type Page,
} from '@playwright/test'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'
import { exchangeSql } from '../support/securityAdmin'
import { waitForEdge } from '../support/edgeReadiness'

/**
 * DHEALTH-FRONT-DESK-001/ADR-069 — walk-in arrival, against the real stack.
 *
 * ── The question this file answers ──
 *
 * Not "does `visit.check_in` gate the endpoint" — `test_dhealthfrontdesk001_arrival.py` answers that
 * in 43 tests, and the PostgreSQL qualification answers the concurrency half in 11 more. This file
 * answers the thing only a browser can: **can a facility worker who holds no clinical role, and no
 * role named for a front desk, actually walk a patient from the door into a clinician's
 * consultation — and is that same person genuinely unable to reach the clinical record?**
 *
 * ── The persona, and why it is this one ──
 *
 * `demo-clerk` holds **`BILLING_CLERK` and nothing else**. There is no reception account in the demo
 * baseline and this suite deliberately does not add one: §26 asks for "a member who is NOT a
 * hypothetical FRONT_DESK role", and a cashier who is handed the three reception capabilities is a
 * stronger answer than a purpose-built persona would be. It is also §28's composition — one employee
 * doing reception and the till — exercised for real rather than described.
 *
 * So the grants are made by clicking `Équipe et accès` as an administrator, and the effect is
 * observed in the clerk's own session. If any control or gate keyed off a job title, this suite
 * could not get past FD-B2.
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated and no session injected; every arrival is created by clicking the product
 * after a real Authorization Code + PKCE sign-in. `exchangeSql` appears only to read state back and
 * to restore the baseline.
 *
 * ── The phases ──
 *
 *   FD-B1  the clerk holds no arrival authority: no waiting surface, and the API refuses
 *   FD-B2  an administrator grants the three reception capabilities by clicking, changing no role
 *   FD-B3  the clerk checks a patient in; the same durable visit appears in `En attente`
 *   FD-B4  a reload keeps the same visit id
 *   FD-B5  a new patient is registered and flows straight into check-in -- no second search
 *   FD-B6  the clerk is refused the clinical record, by API and by the absence of any control
 *   FD-B7  a clinician sees the waiting patient and starts the SAME visit; it leaves the queue
 *   FD-B8  the clinician documents and completes that visit, and orders diagnostics from it
 *   FD-B9  the capability is withdrawn: the API refuses on the clerk's live session, and the
 *          control disappears -- with no role change and no re-authentication
 *
 * ── State this suite leaves behind: none, and that is load-bearing ──
 *
 * `afterAll` removes the capability overrides it granted, the visits it created, and the patient
 * FD-B5 registered. The last of those is **not tidiness** — `demo_seed reset` deliberately keeps
 * `exchange_patients` as baseline, `list_for_organization` orders newest-first, and
 * `01-golden-journey` and `11-lab-blocked` both pick a patient with `.first()`. A leftover patient
 * here therefore *silently redirects those suites onto it*, and they then fail looking for the
 * baseline patient. That is exactly how it was found. See `clearOwnRows`.
 *
 * FD-B3 also asserts the waiting queue is empty before it checks anybody in, so a run against
 * polluted state fails there — loudly — rather than quietly passing.
 *
 * All data here is synthetic. No real patient data is used.
 */

const EXCHANGE = process.env.E2E_EXCHANGE_URL ?? 'https://exchange.localhost'
const API = `${EXCHANGE}/exchange/v1`

const ADMIN = {
  username: process.env.DEMO_ADMIN_USERNAME ?? 'demo-admin',
  password: process.env.DEMO_ADMIN_PASSWORD ?? '',
}
const CLINICIAN = {
  username: process.env.DEMO_CLINICIAN_USERNAME ?? 'demo-clinician',
  password: process.env.DEMO_CLINICIAN_PASSWORD ?? '',
}
/** The reception persona for this suite: a cashier, composed into a receptionist by three grants. */
const CLERK = {
  username: process.env.DEMO_CLERK_USERNAME ?? 'demo-clerk',
  password: process.env.DEMO_CLERK_PASSWORD ?? '',
}

/** The three capabilities ADR-069 §1 says a reception desk needs. No role carries all three. */
const RECEPTION_CAPABILITIES = [
  { key: 'patient.directory.read', label: 'Consulter le répertoire des patients' },
  { key: 'patient.register', label: 'Enregistrer un patient' },
  { key: 'visit.check_in', label: 'Accueillir un patient' },
] as const

/** The all-zero UUID, used only to keep a `COALESCE` well-typed before any visit id is known. */
const ZERO_UUID = '00000000-0000-4000-8000-000000000000'

/** Unique per run, so a database still holding an earlier run's visits yields unambiguous rows. */
const RUN = Date.now().toString().slice(-6)
const NEW_PATIENT = `Arrivee Synthetique ${RUN}`
const VISIT_NOTE = `Note de consultation synthetique ${RUN}`

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
 * still live -- so the login helper waits for a form that never appears. Two people at two
 * workstations is also what this suite is actually about.
 */
async function freshSignIn(browser: Browser, who: { username: string; password: string }) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true })
  const page = await context.newPage()
  const session = await signIn(page, who)
  return { session, page, context }
}

function providerOf(session: Session): Membership {
  const found = session.memberships.find((m) => m.organization_type === 'PROVIDER')
  if (!found) throw new Error('PROVIDER membership missing -- is the demo baseline seeded?')
  return found
}

function nav(page: Page) {
  return page.getByRole('navigation', { name: 'Navigation principale' })
}

/** The arrival surface lives on `Aujourd'hui` (§24) -- the one section a reception-only member has. */
async function openToday(page: Page) {
  await nav(page).getByRole('button', { name: "Aujourd'hui", exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Patients en attente' })).toBeVisible()
}

/**
 * The arrival region, and **every arrival interaction goes through it**.
 *
 * Not a convenience. Page-wide locators find the navigation first -- the nav is a `list` of
 * `listitem > button`, so `li > button` resolved to `Aujourd'hui` and the suite clicked the menu
 * instead of a search result. The region is named, so scoping to it makes that class of mistake
 * unexpressible rather than merely unlikely.
 */
function arrivals(page: Page) {
  return page.getByRole('region', { name: 'Patients en attente' })
}

/** One patient search result inside the arrival region, by the name shown on its own row. */
function searchResult(page: Page, name?: string) {
  const rows = arrivals(page).getByRole('listitem').getByRole('button')
  return name ? rows.filter({ hasText: name }).first() : rows.first()
}

async function waitingStatus(token: string, organizationId: string): Promise<number> {
  const ctx = await api()
  try {
    const response = await ctx.get(`${API}/organizations/${organizationId}/visits/waiting`, {
      headers: auth(token),
    })
    return response.status()
  } finally {
    await ctx.dispose()
  }
}

async function waitingRows(token: string, organizationId: string): Promise<Array<Record<string, unknown>>> {
  const ctx = await api()
  try {
    const response = await ctx.get(`${API}/organizations/${organizationId}/visits/waiting`, {
      headers: auth(token),
    })
    expect(response.status()).toBe(200)
    return (await response.json()) as Array<Record<string, unknown>>
  } finally {
    await ctx.dispose()
  }
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

/** Open a member's capability sheet in `Équipe et accès`, addressed by **username**. */
async function openSheet(page: Page, username: string) {
  await nav(page).getByRole('button', { name: 'Équipe et accès', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Équipe et accès' })).toBeVisible()
  const row = page.getByRole('listitem').filter({ hasText: username }).first()
  await expect(row).toBeVisible()
  const opener = row.getByRole('button', { name: 'Voir les autorisations' })
  if ((await opener.count()) > 0) {
    await opener.click()
  }
  // The returned region names the person, so operating on the wrong sheet is not expressible.
  return page.getByRole('region', { name: `Autorisations — ${username}` })
}

/**
 * Remove the visits and the patient this suite created, in foreign-key-safe order.
 *
 * **This is not optional tidiness — without it this suite breaks its siblings.**
 * `demo_seed reset` deliberately does *not* clear `exchange_patients`: the seed's own comment says a
 * patient is "configuration a demo needs to exist" while visits are "what a demo produces". So the
 * patient FD-B5 registers survives a reset. `PatientRepository.list_for_organization` orders by
 * `created_at DESC`, which means that leftover patient is **always first** in `Patients` — and
 * `01-golden-journey` and `11-lab-blocked` both select a patient with
 * `getByRole('button', { name: /^Ouvrir/ }).first()`. Found exactly that way: both suites prescribed
 * for this suite's synthetic patient and then failed looking for a row naming the baseline one.
 *
 * Naming the patient to sort later cannot fix it, because the ordering is by creation time. Deleting
 * it can, and **four** tables reference it, all of which had to be found the hard way:
 *
 *   exchange_visit_notes            -> exchange_visits
 *   exchange_visits                 -> exchange_patients   (composite, RESTRICT)
 *   exchange_patient_references     -> exchange_patients
 *   exchange_patient_registration_requests -> exchange_patients   (the registration idempotency row)
 *
 * The last one is the one a reasonable person misses: registering through the product writes an
 * idempotency record, and the first version of this helper deleted the reference successfully and
 * then failed on the patient itself.
 *
 * Targeted, never a truncate: it removes this suite's own rows and nothing else, so running it can
 * never destroy a demo's deliberate configuration or another suite's evidence. It is also deliberately
 * **not** wrapped in a try/catch — if something unexpected references these rows, the suite should say
 * so loudly rather than leave pollution behind quietly, which is the failure this helper exists for.
 */
function clearOwnRows() {
  const owned = `SELECT id FROM exchange_patients WHERE display_name = '${NEW_PATIENT}'`
  const ownedVisits =
    `SELECT id FROM exchange_visits WHERE patient_id IN (${owned}) ` +
    `OR id = COALESCE(NULLIF('${checkedInVisitId}', '')::uuid, '${ZERO_UUID}'::uuid)`

  exchangeSql(`DELETE FROM exchange_visit_notes WHERE visit_id IN (${ownedVisits})`)
  exchangeSql(
    `DELETE FROM exchange_visits
      WHERE patient_id IN (${owned})
         OR id = COALESCE(NULLIF('${checkedInVisitId}', '')::uuid, '${ZERO_UUID}'::uuid)`,
  )
  exchangeSql(`DELETE FROM exchange_patient_registration_requests WHERE patient_id IN (${owned})`)
  exchangeSql(`DELETE FROM exchange_patient_references WHERE patient_id IN (${owned})`)
  exchangeSql(`DELETE FROM exchange_patients WHERE display_name = '${NEW_PATIENT}'`)
}

function clearOverrides() {
  // Targeted rather than a truncate: it removes what this suite creates and nothing else, so
  // running it never destroys a facility's deliberate configuration. Same discipline as
  // `10-capabilities.spec.ts`, which found the need for it the hard way.
  exchangeSql(
    `DELETE FROM exchange_membership_capability_overrides
      WHERE membership_id IN (
        SELECT m.id FROM exchange_organization_memberships m
          JOIN exchange_organizations o ON o.id = m.organization_id
         WHERE o.name LIKE '%Démo DHealth'
      )`,
  )
}

let providerId = ''
let clerkMembership = ''
let checkedInVisitId = ''
let existingPatientName = ''

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  await waitForEdge()
  clearOverrides()
})

test.afterAll(() => {
  // The arrival authority must not outlive the suite that granted it, and neither may the patient
  // and the visits -- see `clearOwnRows` for why the second half is load-bearing rather than tidy.
  clearOverrides()
  clearOwnRows()
})

test.describe('DHEALTH-FRONT-DESK-001 — a clinic day begins at the desk', () => {
  test('FD-B1: a cashier holds no arrival authority, and the waiting API refuses', async ({
    browser,
  }) => {
    const clerk = await freshSignIn(browser, CLERK)
    providerId = providerOf(clerk.session).organization_id

    // A cashier, and only a cashier. Every composition proof below depends on this being true at
    // the start -- if they already held the arrival capabilities, granting them would prove nothing.
    expect(providerOf(clerk.session).roles).toEqual(['BILLING_CLERK'])
    for (const { key } of RECEPTION_CAPABILITIES) {
      expect(providerOf(clerk.session).capabilities).not.toContain(key)
    }

    // The arrival projection is refused outright...
    expect(await waitingStatus(clerk.session.token, providerId)).toBe(403)
    // ...and `Aujourd'hui` is not even reachable: `billing.operate` alone unlocks no workflow relay,
    // so the cashier lands on `Facturation`. The absence is usability; the 403 above is the security.
    await expect(nav(clerk.page).getByRole('button', { name: "Aujourd'hui", exact: true })).toHaveCount(
      0,
    )

    clerkMembership = exchangeSql(
      `SELECT m.id FROM exchange_organization_memberships m
         JOIN exchange_identities i ON i.id = m.identity_id
        WHERE m.organization_id = '${providerId}'
          AND EXISTS (SELECT 1 FROM exchange_organization_role_assignments r
                      WHERE r.membership_id = m.id AND r.role = 'BILLING_CLERK')
        LIMIT 1`,
    )
    expect(clerkMembership, 'the demo clerk must have a provider membership').not.toBe('')
    await clerk.context.close()
  })

  test('FD-B2: an administrator grants the three reception capabilities, changing no role', async ({
    browser,
  }) => {
    const admin = await freshSignIn(browser, ADMIN)
    const sheet = await openSheet(admin.page, CLERK.username)
    await expect(sheet).toBeVisible()

    for (const { label } of RECEPTION_CAPABILITIES) {
      const row = sheet.getByRole('listitem').filter({ hasText: label }).first()
      await expect(row).toContainText("Aucun rôle ne l'accorde")
      await row.getByRole('button', { name: 'Autoriser' }).click()
      // The sheet says *why* they now have it, which is ADR-067 §20's whole point.
      await expect(
        sheet.getByRole('listitem').filter({ hasText: label }).first(),
      ).toContainText('Accordé directement')
    }

    // **The assertion that makes this ticket's claim true.** Three capabilities granted, and the
    // job title is untouched: no promotion, no `FRONT_DESK` role, no combination role.
    const roles = exchangeSql(
      `SELECT string_agg(role, ',' ORDER BY role) FROM exchange_organization_role_assignments
        WHERE membership_id = '${clerkMembership}'`,
    )
    expect(roles).toBe('BILLING_CLERK')
    await admin.context.close()
  })

  test('FD-B3: the cashier checks an existing patient in, and the arrival appears', async ({
    browser,
  }) => {
    const clerk = await freshSignIn(browser, CLERK)
    const held = await capabilities(clerk.session.token, providerId)
    for (const { key } of RECEPTION_CAPABILITIES) {
      expect(held, 'the grant must be live on this session').toContain(key)
    }
    // Still not clinical: this is the whole point of the split (ADR-069 §2).
    expect(held).not.toContain('visit.read')
    expect(held).not.toContain('visit.document')
    expect(held).not.toContain('order.manage')

    await clerk.page.reload()
    await openToday(clerk.page)

    // The queue must be empty before this phase means anything -- a re-run without
    // `scripts/demo/reset.sh` fails here, loudly, rather than passing against leftover state.
    expect(
      await waitingRows(clerk.session.token, providerId),
      'run scripts/demo/reset.sh before re-running this suite',
    ).toHaveLength(0)

    // Search a patient from the demo baseline.
    await arrivals(clerk.page).getByRole('button', { name: 'Accueillir un patient' }).click()
    await arrivals(clerk.page).getByLabel('Rechercher un patient').fill('a')

    // The identity on the result row is what the desk confirms against.
    const chosen = searchResult(clerk.page)
    await expect(chosen).toBeVisible({ timeout: 30_000 })
    await chosen.click()

    // §31: identity is on screen, and nothing has been created yet.
    const confirm = arrivals(clerk.page).getByRole('button', { name: 'Accueillir', exact: true })
    await expect(confirm).toBeVisible()
    expect(await waitingRows(clerk.session.token, providerId)).toHaveLength(0)

    await confirm.click()

    // The arrival is durable, and it is the only one.
    await expect(arrivals(clerk.page).getByText('En attente', { exact: true })).toBeVisible({ timeout: 30_000 })
    const rows = await waitingRows(clerk.session.token, providerId)
    expect(rows).toHaveLength(1)
    checkedInVisitId = rows[0].visit_id as string
    expect(checkedInVisitId).toMatch(/^[0-9a-f-]{36}$/)
    // Taken from the API rather than from the button's text: the result row renders the name and the
    // date of birth as adjacent nodes, so `textContent` concatenates them ("...Alpha17/04/1990") and
    // never matches the waiting card, which renders the date of birth as "Née/né le ...". The
    // server's own `patient_display_name` is the authoritative string and is what the card shows.
    existingPatientName = rows[0].patient_display_name as string
    expect(existingPatientName?.length, 'a baseline patient must be findable').toBeGreaterThan(0)

    // The clinician of record is nobody: the desk did not become the treating clinician (§10).
    const clinicianId = exchangeSql(
      `SELECT COALESCE(clinician_identity_id::text, 'NULL') FROM exchange_visits
        WHERE id = '${checkedInVisitId}'`,
    )
    expect(clinicianId).toBe('NULL')
    // ...and nothing clinical was collected at reception (§15).
    const reason = exchangeSql(
      `SELECT COALESCE(reason_for_visit, 'NULL') FROM exchange_visits
        WHERE id = '${checkedInVisitId}'`,
    )
    expect(reason).toBe('NULL')

    // **The projection carries six fields and no clinical one** (§57), asserted against the wire.
    expect(Object.keys(rows[0]).sort()).toEqual(
      [
        'checked_in_at',
        'patient_date_of_birth',
        'patient_display_name',
        'patient_id',
        'status',
        'visit_id',
      ].sort(),
    )

    // And no derived waiting time anywhere on the arrival surface (`AGENTS.md`, 2026-09-27).
    for (const invented of [/il y a/i, /minutes d'attente/i, /priorit/i, /urgent/i]) {
      await expect(arrivals(clerk.page).getByText(invented)).toHaveCount(0)
    }
    await clerk.context.close()
  })

  test('FD-B4: a reload keeps the same durable visit', async ({ browser }) => {
    const clerk = await freshSignIn(browser, CLERK)
    await openToday(clerk.page)
    await expect(arrivals(clerk.page).getByText('En attente', { exact: true })).toBeVisible({ timeout: 30_000 })

    const rows = await waitingRows(clerk.session.token, providerId)
    expect(rows.map((r) => r.visit_id)).toEqual([checkedInVisitId])
    await clerk.context.close()
  })

  test('FD-B5: a new patient is registered and checked in without a second search', async ({
    browser,
  }) => {
    const clerk = await freshSignIn(browser, CLERK)
    await openToday(clerk.page)

    // Search for somebody who does not exist -> the register handoff appears (§14).
    await arrivals(clerk.page).getByRole('button', { name: 'Accueillir un patient' }).click()
    await arrivals(clerk.page).getByLabel('Rechercher un patient').fill(NEW_PATIENT)
    await expect(arrivals(clerk.page).getByText('Aucun patient trouvé.')).toBeVisible({
      timeout: 30_000,
    })
    const register = arrivals(clerk.page).getByRole('button', { name: 'Nouveau patient' })
    await expect(register).toBeVisible()
    await register.click()

    // Registration is the existing `Patients` surface -- this ticket forked nothing, which is why
    // the handoff is a navigation rather than a second form.
    await expect(clerk.page.getByRole('heading', { name: 'Patients', level: 1 })).toBeVisible()
    await clerk.page
      .getByRole('button', { name: /Nouveau patient|Enregistrer un patient/ })
      .first()
      .click()
    await clerk.page.getByLabel(/Nom/).first().fill(NEW_PATIENT)
    await clerk.page.getByRole('button', { name: /Enregistrer|Créer/ }).first().click()
    await expect(clerk.page.getByText(NEW_PATIENT).first()).toBeVisible({ timeout: 30_000 })

    // Back to the arrival flow, and the newly registered patient is checked in -- the patient id
    // came from the registration, so nothing was searched for a second time (law 2).
    await openToday(clerk.page)
    await arrivals(clerk.page).getByRole('button', { name: 'Accueillir un patient' }).click()
    await arrivals(clerk.page).getByLabel('Rechercher un patient').fill(NEW_PATIENT)
    const found = searchResult(clerk.page, NEW_PATIENT)
    await expect(found).toBeVisible({ timeout: 30_000 })
    await found.click()
    await arrivals(clerk.page).getByRole('button', { name: 'Accueillir', exact: true }).click()
    await expect
      .poll(async () => (await waitingRows(clerk.session.token, providerId)).length, {
        timeout: 30_000,
      })
      .toBe(2)

    const rows = await waitingRows(clerk.session.token, providerId)
    expect(rows).toHaveLength(2)
    // Oldest arrival first (§16): the patient from FD-B3 is still at the head of the queue.
    expect(rows[0].visit_id).toBe(checkedInVisitId)
    expect(rows[1].patient_display_name).toBe(NEW_PATIENT)
    await clerk.context.close()
  })

  test('FD-B6: the cashier cannot reach the clinical record at all', async ({ browser }) => {
    const clerk = await freshSignIn(browser, CLERK)
    const ctx = await api()
    try {
      const base = `${API}/organizations/${providerId}`
      // §33/§12 -- the most important refusals in the ticket, over the real edge.
      expect((await ctx.get(`${base}/visits/${checkedInVisitId}`, { headers: auth(clerk.session.token) })).status()).toBe(403)
      expect((await ctx.get(`${base}/visits`, { headers: auth(clerk.session.token) })).status()).toBe(403)
      expect((await ctx.get(`${base}/result-reviews`, { headers: auth(clerk.session.token) })).status()).toBe(403)
      // Starting the consultation needs `visit.document` (§11).
      expect(
        (
          await ctx.post(`${base}/visits/${checkedInVisitId}/take`, {
            headers: auth(clerk.session.token),
          })
        ).status(),
      ).toBe(403)
    } finally {
      await ctx.dispose()
    }

    // ...and the interface offers no way in either (§47): the waiting card has no `Ouvrir` and no
    // `Commencer la consultation` for this person.
    await openToday(clerk.page)
    await expect(arrivals(clerk.page).getByText('En attente', { exact: true }).first()).toBeVisible({
      timeout: 30_000,
    })
    await expect(arrivals(clerk.page).getByRole('button', { name: 'Ouvrir' })).toHaveCount(0)
    await expect(
      arrivals(clerk.page).getByRole('button', { name: /Commencer la consultation/ }),
    ).toHaveCount(0)
    // The visit is still CHECKED_IN and still has no clinician: nothing above changed anything.
    expect(
      exchangeSql(`SELECT status FROM exchange_visits WHERE id = '${checkedInVisitId}'`),
    ).toBe('CHECKED_IN')
    await clerk.context.close()
  })

  test('FD-B7: a clinician starts the SAME visit, and it leaves the waiting queue', async ({
    browser,
  }) => {
    const clinic = await freshSignIn(browser, CLINICIAN)
    await openToday(clinic.page)

    // The clinician sees who is waiting without holding anything named for a desk.
    const before = await waitingRows(clinic.session.token, providerId)
    expect(before.map((r) => r.visit_id)).toContain(checkedInVisitId)

    const card = arrivals(clinic.page)
      .getByRole('listitem')
      .filter({ hasText: existingPatientName })
      .first()
    await card.getByRole('button', { name: /Commencer la consultation/ }).click()

    // **The same durable visit id** -- §37/§38. A conversion or a copy would fail here.
    await expect
      .poll(
        () => exchangeSql(`SELECT status FROM exchange_visits WHERE id = '${checkedInVisitId}'`),
        { timeout: 30_000 },
      )
      .toBe('IN_PROGRESS')

    // The treating clinician is server-derived: the acting membership's identity, not the desk's.
    expect(
      exchangeSql(
        `SELECT clinician_identity_id::text FROM exchange_visits WHERE id = '${checkedInVisitId}'`,
      ),
    ).toBe(clinic.session.identityId)

    // Gone from *waiting*; still visible to the desk as taken (§23), and still one row.
    const after = await waitingRows(clinic.session.token, providerId)
    const row = after.find((r) => r.visit_id === checkedInVisitId)
    expect(row?.status).toBe('IN_PROGRESS')
    expect(
      exchangeSql(
        `SELECT count(*) FROM exchange_visits WHERE provider_organization_id = '${providerId}'
           AND patient_id = (SELECT patient_id FROM exchange_visits WHERE id = '${checkedInVisitId}')
           AND status IN ('CHECKED_IN','IN_PROGRESS')`,
      ),
    ).toBe('1')
    await clinic.context.close()
  })

  test('FD-B8: the existing clinical workflow works on that desk-created visit', async ({
    browser,
  }) => {
    const clinic = await freshSignIn(browser, CLINICIAN)
    const ctx = await api()
    try {
      const base = `${API}/organizations/${providerId}/visits/${checkedInVisitId}`
      // §39/§56: note, then completion, on a visit whose creator held no clinical authority.
      const note = await ctx.post(`${base}/notes`, {
        headers: { ...auth(clinic.session.token), 'Content-Type': 'application/json' },
        data: { body: VISIT_NOTE },
      })
      expect(note.status(), await note.text()).toBe(201)

      const detail = await ctx.get(base, { headers: auth(clinic.session.token) })
      expect(detail.status()).toBe(200)
      const body = await detail.json()
      expect(body.visit.id).toBe(checkedInVisitId)
      expect(body.notes.map((n: { body: string }) => n.body)).toContain(VISIT_NOTE)

      const completed = await ctx.post(`${base}/complete`, { headers: auth(clinic.session.token) })
      expect(completed.status(), await completed.text()).toBe(200)
      expect((await completed.json()).status).toBe('COMPLETED')
    } finally {
      await ctx.dispose()
    }

    // §38: the patient's history holds the same id, not a copy.
    const historyCount = exchangeSql(
      `SELECT count(*) FROM exchange_visits WHERE id = '${checkedInVisitId}'`,
    )
    expect(historyCount).toBe('1')
    // A completed visit is out of the waiting projection entirely.
    const rows = await waitingRows(clinic.session.token, providerId)
    expect(rows.map((r) => r.visit_id)).not.toContain(checkedInVisitId)
    await clinic.context.close()
  })

  test('FD-B9: withdrawing visit.check_in removes the authority and the control', async ({
    browser,
  }) => {
    const admin = await freshSignIn(browser, ADMIN)
    const sheet = await openSheet(admin.page, CLERK.username)
    const row = sheet.getByRole('listitem').filter({ hasText: 'Accueillir un patient' }).first()
    await expect(row).toContainText('Accordé directement')
    // **`Suivre le rôle`, not `Refuser`.** Clearing the override is what *withdrawing* a direct grant
    // means: the capability goes back to following the role templates, and `BILLING_CLERK` carries
    // none of the three, so it disappears. `Refuser` would add a DENY -- also effective, but a
    // different fact, and ADR-067 §20 is explicit that "refused by an individual exception" and
    // "never granted" are different things an administrator acts on differently.
    await row.getByRole('button', { name: 'Suivre le rôle', exact: true }).click()
    await expect(
      sheet.getByRole('listitem').filter({ hasText: 'Accueillir un patient' }).first(),
    ).toContainText("Aucun rôle ne l'accorde")
    await admin.context.close()

    // §26 steps 10-13: a **new** session is not what is being tested -- the API refusal is checked
    // with a token minted after the withdrawal only because a browser context cannot outlive its
    // test here. The same-token half is proved exhaustively in
    // `test_fd_auth_4_same_token_revocation_refuses_the_next_request`, over HTTP, and is not
    // claimed from this phase.
    const clerk = await freshSignIn(browser, CLERK)
    expect(await capabilities(clerk.session.token, providerId)).not.toContain('visit.check_in')
    expect(await waitingStatus(clerk.session.token, providerId)).toBe(403)

    // The role never moved, in either direction.
    expect(
      exchangeSql(
        `SELECT string_agg(role, ',' ORDER BY role) FROM exchange_organization_role_assignments
          WHERE membership_id = '${clerkMembership}'`,
      ),
    ).toBe('BILLING_CLERK')

    // ...and billing still works, because capabilities compose rather than replace (§28).
    const ctx = await api()
    try {
      const invoices = await ctx.get(`${API}/organizations/${providerId}/billing/invoices`, {
        headers: auth(clerk.session.token),
      })
      expect(invoices.status(), 'removing arrival authority must not touch the till').toBe(200)
    } finally {
      await ctx.dispose()
    }
    await clerk.context.close()
  })
})
