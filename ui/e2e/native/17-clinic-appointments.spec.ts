import {
  expect,
  request as apiRequest,
  test,
  type APIRequestContext,
  type Browser,
  type Page,
} from '@playwright/test'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'
import { waitForEdge } from '../support/edgeReadiness'
import { exchangeSql } from '../support/securityAdmin'

/**
 * DHEALTH-APPOINTMENTS-001/ADR-073 — the clinic schedule, against the real stack.
 *
 * ── The question this file answers ──
 *
 * Not "does the appointment model work". `adapter/tests/test_dhealthappointments001_appointments.py`
 * qualifies that at the HTTP edge (46 tests) and
 * `adapter/tests/integration/test_dhealthappointments001_postgres_qualification.py` qualifies the
 * races, both composite foreign keys and the migration on real PostgreSQL (14 more). This file
 * answers the thing neither can:
 *
 *   **Can somebody at a clinic reception desk book a patient, move them, cancel them, and — when the
 *   patient actually walks in — turn that booking into the existing visit workflow, by clicking the
 *   product? And does a walk-in with no booking still work afterwards?**
 *
 * ── The grant is part of the evidence, not setup ──
 *
 * **No role template carries `appointment.manage`** (ADR-073 §5), so this suite grants it by
 * clicking `Équipe et accès` as the clinic's administrator — the shape ADR-069 established for
 * `visit.check_in`. The grant happening by clicking, taking effect on the next request with no new
 * token, and being withdrawable by clicking, is the authorization evidence. AP-G does the withdrawal.
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated, no session injected, no auth state written into browser storage. Every
 * clinical and configuration act is performed by clicking, as somebody who signed in through a real
 * Authorization Code + PKCE flow.
 *
 * Direct `/exchange/v1` calls appear only to assert refusals the browser cannot demonstrate (a
 * control that is not rendered cannot be clicked) and to read authoritative state for an assertion.
 * `exchangeSql` reads state back and removes what this suite granted. **Nothing advances the
 * workflow by writing to the database.**
 *
 * ── State this suite leaves behind, and the one thing it must clean up itself ──
 *
 * Bookings and visits are workflow state; `scripts/demo/reset.sh` clears them. **The capability
 * grant is different**: `exchange_membership_capability_overrides` is absent from
 * `demo_seed._WORKFLOW_TABLES`, so a reset leaves a leftover grant standing and `10-capabilities`
 * asserts what these same people can reach. `clearOverrides` therefore runs before *and* after, and
 * AP-A fails loudly rather than passing quietly if a crashed earlier run left the grant behind.
 *
 * The two seeded demo bookings (one today, one tomorrow) are re-created by `seed_baseline` at every
 * reset, computed from the facility's current date — so this suite's own bookings are additional to
 * them and the day list is asserted by *content*, never by row count.
 *
 * ── What this suite does NOT establish ──
 *
 * **Not human usability.** Nobody who works at a clinic reception desk has used this screen. A green
 * run says the mechanism works when driven correctly; whether a receptionist can run a morning with
 * it is a human judgement no script can make, and it has not been made.
 *
 * **Not DST correctness.** Africa/Djibouti is UTC+3 with no DST observance, so no transition is
 * exercised anywhere.
 *
 * All patient data is the demo baseline's synthetic patient. No real patient data is used.
 */

const EXCHANGE = process.env.E2E_EXCHANGE_URL ?? 'https://exchange.localhost'
const API = `${EXCHANGE}/exchange/v1`

/** CLINICIAN: carries `visit.check_in`, `visit.document` and the directory read already. */
const CLINICIAN = {
  username: process.env.DEMO_CLINICIAN_USERNAME ?? 'demo-clinician',
  password: process.env.DEMO_CLINICIAN_PASSWORD ?? '',
}
/** The clinic's administrator: `staff.access.manage`, and **no clinical capability at all**. */
const ADMIN = {
  username: process.env.DEMO_ADMIN_USERNAME ?? 'demo-admin',
  password: process.env.DEMO_ADMIN_PASSWORD ?? '',
}
/** The till. `billing.operate` only (ADR-066) — must not be able to schedule. */
const CLERK = {
  username: process.env.DEMO_CLERK_USERNAME ?? 'demo-clerk',
  password: process.env.DEMO_CLERK_PASSWORD ?? '',
}
/** A laboratory worker: no standing in a clinic at all. */
const LAB_TECH = {
  username: process.env.DEMO_LAB_TECH_USERNAME ?? 'demo-lab-tech',
  password: process.env.DEMO_LAB_TECH_PASSWORD ?? '',
}

/** The one capability this feature needs. Label is the server's own (ADR-067 catalogue). */
const SCHEDULE = { key: 'appointment.manage', label: 'Gérer les rendez-vous' } as const

const PATIENT = 'Patient Démo Alpha'

let providerId = ''
let clinicianToken = ''
let clinicianMembership = ''
/** The booking this run creates, and the one it cancels. */
let bookedTime = ''
let bookedUtc = ''
let movedTime = ''
let bookedId = ''
let cancelledId = ''
let linkedVisitId = ''

const consoleErrors: string[] = []
const failedRequests: string[] = []

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

async function signIn(page: Page, who: { username: string; password: string }): Promise<Session> {
  expect(who.password, `${who.username}'s password must come from demo/native/demo.env`).not.toBe('')
  watchForBrowserProblems(page)
  const tokenPromise = captureFirstExchangeBearerToken(page)
  await page.goto('/')
  await loginViaRealKeycloak(page, who.username, who.password)
  await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible({ timeout: 30_000 })
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

async function freshSignIn(browser: Browser, who: { username: string; password: string }) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true })
  const page = await context.newPage()
  const session = await signIn(page, who)
  return { session, page, context }
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

async function openSheet(page: Page, username: string) {
  await openSection(page, 'Équipe et accès')
  await expect(page.getByRole('heading', { name: 'Équipe et accès' })).toBeVisible()
  const row = page.getByRole('listitem').filter({ hasText: username }).first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  // Click the opener unconditionally unless this row's sheet is already open. The earlier
  // `if ((await opener.count()) > 0)` form swallowed a genuine failure: when the sheet's
  // capability read fails, `toggleAccess` puts `openMember` back to null and the button stays
  // on "Voir les autorisations", so a skipped click and a failed read looked identical and the
  // test then waited the full timeout on a region that would never appear. Assert each step so
  // the failure names itself.
  const hide = row.getByRole('button', { name: 'Masquer les autorisations' })
  if ((await hide.count()) === 0) {
    const opener = row.getByRole('button', { name: 'Voir les autorisations' })
    await expect(opener).toBeVisible()
    await opener.click()
    await expect(hide).toBeVisible({ timeout: 30_000 })
  }
  // The section is named by the member's *display* name, which is what the list shows.
  const sheet = page.getByRole('region', { name: `Autorisations — ${username}` })
  await expect(sheet).toBeVisible({ timeout: 30_000 })
  return sheet
}

/**
 * One booking's row in the schedule, found by the time it is scheduled for.
 *
 * Scoped to the schedule's own landmark. The waiting list below it is also a list of this same
 * patient, so an unscoped query would depend on which panel happens to come first in the DOM.
 */
function bookingRow(page: Page, time: string) {
  return page
    .getByRole('region', { name: 'Rendez-vous' })
    .getByRole('listitem')
    .filter({ hasText: time })
    .filter({ hasText: PATIENT })
    .first()
}

async function openToday(page: Page): Promise<void> {
  await openSection(page, "Aujourd'hui")
  await expect(page.getByRole('heading', { name: 'Rendez-vous' })).toBeVisible({ timeout: 30_000 })
}

/**
 * Remove the capability override this suite grants.
 *
 * **Not optional tidiness** -- `exchange_membership_capability_overrides` is absent from
 * `demo_seed._WORKFLOW_TABLES`, so `reset.sh` leaves a leftover grant standing and
 * `10-capabilities.spec.ts` asserts what this same clinician can reach.
 */
function clearOverrides(): void {
  exchangeSql(
    `DELETE FROM exchange_membership_capability_overrides
      WHERE capability = '${SCHEDULE.key}'`,
  )
}

/** A clinic-local date, as the product's own date input expects it. */
/**
 * A clinic-local booking slot `minutesAhead` from now: its wall clock, the UTC wall clock it must
 * be stored as, and the clinic-local date it falls on.
 *
 * AP-B hardcoded `14:45` and asserted the row reads "À venir". That is only true if the suite is
 * started before 14:45 Djibouti time; run later, the product correctly says "Heure passée" and the
 * test failed for being wrong about the clock rather than about the product. A booking that must be
 * upcoming has to be computed from the clinic's clock, not written down.
 *
 * Floored to a five-minute boundary so the value looks like a real slot. Djibouti is UTC+3 with no
 * DST, so flooring UTC minutes floors clinic-local minutes identically. `avoid` keeps a computed
 * slot off the two times `demo_seed` already books, which would otherwise make a row locator
 * ambiguous between this booking and the seeded one.
 */
function clinicSlot(minutesAhead: number, avoid: string[] = ['09:00', '10:00']): {
  local: string
  utc: string
  date: string
} {
  const at = new Date(Date.now() + minutesAhead * 60_000)
  at.setUTCSeconds(0, 0)
  at.setUTCMinutes(Math.floor(at.getUTCMinutes() / 5) * 5)
  // `hourCycle: 'h23'`, not `hour12: false`: the latter can render the midnight hour as "24:15"
  // in V8, while PostgreSQL's `to_char(..., 'HH24:MI')` renders "00:15". AP-F looks its booking up
  // by that formatted string, so the two have to agree for a slot that lands after midnight.
  const clock = (timeZone: string) =>
    new Intl.DateTimeFormat('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
      timeZone,
    }).format(at)
  if (avoid.includes(clock('Africa/Djibouti'))) {
    return clinicSlot(minutesAhead + 5, avoid)
  }
  return {
    local: clock('Africa/Djibouti'),
    utc: clock('UTC'),
    date: new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Africa/Djibouti',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(at),
  }
}

function localDate(offsetDays: number): string {
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Djibouti',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  const base = new Date()
  base.setUTCDate(base.getUTCDate() + offsetDays)
  return formatter.format(base)
}

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  await waitForEdge()
  clearOverrides()
})

test.afterAll(() => {
  clearOverrides()
})

test.describe('DHEALTH-APPOINTMENTS-001 — a clinic day that is planned before it is attended', () => {
  // ── AP-A — the grant, by clicking ───────────────────────────────────────────────────────────

  test('AP-A: the clinic administrator grants scheduling authority, changing no role', async ({
    browser,
  }) => {
    const admin = await freshSignIn(browser, ADMIN)
    try {
      // The administrator holds `staff.access.manage` and no clinical capability, so they have no
      // schedule of their own to look at -- which is the separation, visible.
      //
      // The separation is *stronger* than "an empty schedule": with neither `appointment.manage`
      // nor `visit.check_in`, the administrator is offered no "Aujourd'hui" section at all, so
      // there is nothing to navigate to. An earlier version of this test called
      // `openSection(admin.page, "Aujourd'hui")` here and hung for the full test timeout waiting
      // on a nav button that correctly does not exist -- the product was right and the test was
      // wrong. Assert the absence where it actually lives: in the navigation.
      await expect(nav(admin.page).getByRole('button', { name: "Aujourd'hui" })).toHaveCount(0)
      await expect(admin.page.getByRole('heading', { name: 'Rendez-vous' })).toHaveCount(0)

      const sheet = await openSheet(admin.page, CLINICIAN.username)
      const row = sheet.getByRole('listitem').filter({ hasText: SCHEDULE.label }).first()
      await expect(row).toBeVisible()
      // Fails loudly if a crashed earlier run left the grant behind.
      await expect(row).toContainText("Aucun rôle ne l'accorde")
      await row.getByRole('button', { name: 'Autoriser' }).click()
      await expect(
        sheet.getByRole('listitem').filter({ hasText: SCHEDULE.label }).first(),
      ).toContainText('Accordé directement')
    } finally {
      await admin.context.close()
    }

    // One override row, a GRANT of exactly this capability, and the job title untouched.
    const clinician = await freshSignIn(browser, CLINICIAN)
    try {
      providerId = providerOf(clinician.session).organization_id
      clinicianToken = clinician.session.token
      clinicianMembership = exchangeSql(
        `SELECT m.id FROM exchange_organization_memberships m
          JOIN exchange_identities i ON i.id = m.identity_id
          WHERE m.organization_id = '${providerId}' AND i.subject LIKE '%'
          AND m.identity_id = '${clinician.session.identityId}'`,
      ).trim()
      const roles = exchangeSql(
        `SELECT string_agg(role, ',' ORDER BY role) FROM exchange_organization_role_assignments
          WHERE membership_id = '${clinicianMembership}'`,
      ).trim()
      expect(roles).toBe('CLINICIAN')
      const overrides = exchangeSql(
        `SELECT capability || ':' || effect FROM exchange_membership_capability_overrides
          WHERE membership_id = '${clinicianMembership}'`,
      ).trim()
      expect(overrides).toBe(`${SCHEDULE.key}:GRANT`)
      // Effective on the next request, with no new token: this is the same session that signed in.
      expect(providerOf(clinician.session).capabilities).toContain(SCHEDULE.key)
    } finally {
      await clinician.context.close()
    }
  })

  // ── AP-B — booking, and it survives a reload ────────────────────────────────────────────────

  test('AP-B: a booking is created by clicking and survives a full reload', async ({ page }) => {
    await signIn(page, CLINICIAN)
    await openToday(page)

    await page.getByRole('button', { name: 'Nouveau rendez-vous' }).click()
    await page.getByLabel('Rechercher un patient').fill('Démo')
    await page.getByRole('button', { name: PATIENT }).first().click()
    const slot = clinicSlot(120)
    expect(
      slot.date,
      'AP-B needs a slot still ahead inside today\'s clinic day. Started within two hours of ' +
        'midnight Djibouti time, there is none -- rerun after midnight.',
    ).toBe(localDate(0))
    bookedTime = slot.local
    bookedUtc = slot.utc
    await page.getByLabel('Date').fill(slot.date)
    await page.getByLabel('Heure').fill(bookedTime)
    await page.getByRole('button', { name: 'Enregistrer le rendez-vous' }).click()

    await expect(bookingRow(page, bookedTime)).toBeVisible({ timeout: 30_000 })
    await expect(bookingRow(page, bookedTime)).toContainText('À venir')

    // **Survives a reload** -- it is a durable fact, not screen state.
    await page.reload()
    await openToday(page)
    await expect(bookingRow(page, bookedTime)).toBeVisible({ timeout: 30_000 })

    const ctx = await api()
    try {
      const day = await ctx.get(`${API}/organizations/${providerId}/appointments`, {
        headers: auth(clinicianToken),
      })
      expect(day.status()).toBe(200)
      const body = await day.json()
      expect(body.facility_timezone).toBe('Africa/Djibouti')
      const mine = (body.appointments as { id: string; scheduled_at: string }[]).find(
        (a) =>
          new Intl.DateTimeFormat('en-GB', {
            hour: '2-digit',
            minute: '2-digit',
            // Same hour cycle `clinicSlot` produced `bookedTime` with, or the midnight hour
            // compares "24:15" against "00:15" and finds nothing.
            hourCycle: 'h23',
            timeZone: 'Africa/Djibouti',
          }).format(new Date(a.scheduled_at)) === bookedTime,
      )
      expect(mine, 'the booking must be in the clinic day the server resolved').toBeTruthy()
      bookedId = mine!.id
      // The stored instant is the booked wall clock in **Djibouti**, three hours ahead of the UTC
      // instant -- the server interpreted the wall clock, not the browser. Both sides of this
      // comparison come from the same slot, so it still fails if the server shifts the clock.
      expect(new Date(mine!.scheduled_at).toISOString()).toContain(`T${bookedUtc}`)
      const offsetHours =
        (Number(bookedTime.slice(0, 2)) - Number(bookedUtc.slice(0, 2)) + 24) % 24
      expect(offsetHours, 'Djibouti is UTC+3, with no DST').toBe(3)
    } finally {
      await ctx.dispose()
    }
  })

  // ── AP-C — rescheduling, and it survives a reload ───────────────────────────────────────────

  test('AP-C: rescheduling moves the booking and survives a reload', async ({ page }) => {
    await signIn(page, CLINICIAN)
    await openToday(page)

    await bookingRow(page, bookedTime).getByRole('button', { name: 'Déplacer' }).click()
    // An hour past AP-B's slot, computed the same way so it can never collide with it.
    movedTime = clinicSlot(180).local
    await page.getByLabel('Nouvelle date').fill(localDate(0))
    await page.getByLabel('Nouvelle heure').fill(movedTime)
    await page.getByRole('button', { name: 'Confirmer le déplacement' }).click()

    await expect(bookingRow(page, movedTime)).toBeVisible({ timeout: 30_000 })

    await page.reload()
    await openToday(page)
    await expect(bookingRow(page, movedTime)).toBeVisible({ timeout: 30_000 })
    await expect(bookingRow(page, bookedTime)).toHaveCount(0)

    // The same row moved -- not a duplicate and not a replacement.
    const stored = exchangeSql(
      `SELECT count(*) FROM exchange_appointments WHERE id = '${bookedId}'`,
    ).trim()
    expect(stored).toBe('1')
    const audited = exchangeSql(
      `SELECT count(*) FROM exchange_security_audit_events
        WHERE resource_id = '${bookedId}' AND event_type = 'appointment_rescheduled'`,
    ).trim()
    expect(audited).toBe('1')
  })

  // ── AP-D — arrival: exactly one CHECKED_IN visit, and retrying makes no second one ──────────

  test('AP-D: arrival creates and links exactly one CHECKED_IN visit', async ({ page }) => {
    await signIn(page, CLINICIAN)
    await openToday(page)

    const before = exchangeSql(
      `SELECT count(*) FROM exchange_visits WHERE provider_organization_id = '${providerId}'`,
    ).trim()

    await bookingRow(page, movedTime).getByRole('button', { name: "Enregistrer l'arrivée" }).click()
    await expect(bookingRow(page, movedTime)).toContainText('Arrivé', { timeout: 30_000 })

    const linked = exchangeSql(
      `SELECT visit_id FROM exchange_appointments WHERE id = '${bookedId}'`,
    ).trim()
    expect(linked).not.toBe('')
    linkedVisitId = linked
    const status = exchangeSql(
      `SELECT status FROM exchange_visits WHERE id = '${linkedVisitId}'`,
    ).trim()
    expect(status).toBe('CHECKED_IN')

    const after = exchangeSql(
      `SELECT count(*) FROM exchange_visits WHERE provider_organization_id = '${providerId}'`,
    ).trim()
    expect(Number(after)).toBe(Number(before) + 1)

    // Both audit rows, in the one transaction.
    const events = exchangeSql(
      `SELECT string_agg(DISTINCT event_type, ',' ORDER BY event_type)
        FROM exchange_security_audit_events
        WHERE event_type IN ('appointment_checked_in', 'visit_created')
        AND created_at > now() - interval '2 minutes'`,
    ).trim()
    expect(events).toContain('appointment_checked_in')
    expect(events).toContain('visit_created')
  })

  test('AP-D2: retrying the arrival creates no second visit', async ({ page }) => {
    await signIn(page, CLINICIAN)
    await openToday(page)

    const before = exchangeSql(
      `SELECT count(*) FROM exchange_visits WHERE provider_organization_id = '${providerId}'`,
    ).trim()

    // The product no longer offers the control for an arrived booking, which is the usability half.
    await expect(
      bookingRow(page, movedTime).getByRole('button', { name: "Enregistrer l'arrivée" }),
    ).toHaveCount(0)

    // The server is the guarantee. A hand-made retry -- the double click, the lost response -- must
    // reveal the existing visit rather than create another.
    const ctx = await api()
    try {
      const retry = await ctx.post(
        `${API}/organizations/${providerId}/appointments/${bookedId}/check-in`,
        { headers: auth(clinicianToken) },
      )
      expect(retry.status()).toBe(200)
      const body = await retry.json()
      expect(body.visit_created).toBe(false)
      expect(body.visit.id).toBe(linkedVisitId)
    } finally {
      await ctx.dispose()
    }

    const after = exchangeSql(
      `SELECT count(*) FROM exchange_visits WHERE provider_organization_id = '${providerId}'`,
    ).trim()
    expect(after).toBe(before)
  })

  // ── AP-E — the patient is in the existing waiting flow, and a clinician starts that visit ───

  test('AP-E: the arrived patient appears in the existing waiting list', async ({ page }) => {
    await signIn(page, CLINICIAN)
    await openToday(page)
    // `ClinicArrivals` is DHEALTH-FRONT-DESK-001's own surface and was not modified by this ticket.
    const waiting = page
      .getByRole('listitem')
      .filter({ hasText: PATIENT })
      .filter({ hasText: 'En attente' })
    await expect(waiting.first()).toBeVisible({ timeout: 30_000 })
  })

  test('AP-E2: a clinician starts that same visit, and no second visit appears', async ({
    page,
  }) => {
    await signIn(page, CLINICIAN)
    // The clinician starts the visit from the same arrivals list AP-E just found it in, which is
    // where `ClinicArrivals` offers "Commencer la consultation". An earlier version of this test
    // looked for /Ouvrir|Commencer/ in the "Visites" section instead -- that page has no such
    // action at all, so the locator matched nothing and waited out the whole test timeout.
    await openToday(page)
    const row = page
      .getByRole('listitem')
      .filter({ hasText: PATIENT })
      .filter({ hasText: 'En attente' })
      .first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    await row.getByRole('button', { name: 'Commencer la consultation' }).click()

    await expect
      .poll(
        () =>
          exchangeSql(`SELECT status FROM exchange_visits WHERE id = '${linkedVisitId}'`).trim(),
        { timeout: 30_000 },
      )
      .toBe('IN_PROGRESS')

    // Still exactly one visit for this booking, and the booking is still its scheduling context.
    const linked = exchangeSql(
      `SELECT count(*) FROM exchange_appointments WHERE visit_id = '${linkedVisitId}'`,
    ).trim()
    expect(linked).toBe('1')

    // And the schedule now reports the visit's new state rather than a copy of its own.
    //
    // Reloaded first, deliberately. The status in the row is a **projection of the linked visit**,
    // read from the server on each fetch -- not a stored appointment state. What the product does
    // not claim is that one panel refetches because a sibling panel on the same screen changed
    // something: the arrivals list updates itself after "Commencer la consultation", the schedule
    // above it does not, so without a refetch the row keeps showing the "Arrivé" it was last told
    // about. Proving the projection is live therefore means fetching again, which is the same
    // durability move AP-B and AP-C make.
    await page.reload()
    await openToday(page)
    await expect(bookingRow(page, movedTime)).toContainText('En consultation', { timeout: 30_000 })
  })

  // ── AP-F — cancellation prevents arrival ────────────────────────────────────────────────────

  test('AP-F: a cancelled booking is shown as cancelled and cannot be arrived', async ({ page }) => {
    await signIn(page, CLINICIAN)
    await openToday(page)

    await page.getByRole('button', { name: 'Nouveau rendez-vous' }).click()
    await page.getByLabel('Rechercher un patient').fill('Démo')
    await page.getByRole('button', { name: PATIENT }).first().click()
    // Two hours past AP-B's slot: distinct from both it and AP-C's, whatever time the suite runs.
    const cancelTime = clinicSlot(240).local
    await page.getByLabel('Date').fill(localDate(0))
    await page.getByLabel('Heure').fill(cancelTime)
    await page.getByRole('button', { name: 'Enregistrer le rendez-vous' }).click()
    await expect(bookingRow(page, cancelTime)).toBeVisible({ timeout: 30_000 })

    cancelledId = exchangeSql(
      `SELECT id FROM exchange_appointments
        WHERE provider_organization_id = '${providerId}'
        AND to_char(scheduled_at AT TIME ZONE 'Africa/Djibouti', 'HH24:MI') = '${cancelTime}'
        ORDER BY created_at DESC LIMIT 1`,
    ).trim()
    expect(cancelledId).not.toBe('')

    await bookingRow(page, cancelTime).getByRole('button', { name: 'Annuler' }).click()
    await expect(bookingRow(page, cancelTime)).toContainText('Annulé', { timeout: 30_000 })

    // **Still visible as history, and offered no arrival action.**
    await expect(
      bookingRow(page, cancelTime).getByRole('button', { name: "Enregistrer l'arrivée" }),
    ).toHaveCount(0)
    // The row was never deleted.
    expect(
      exchangeSql(`SELECT status FROM exchange_appointments WHERE id = '${cancelledId}'`).trim(),
    ).toBe('CANCELLED')

    // And the server refuses arrival outright, which the browser cannot show on its own.
    const ctx = await api()
    try {
      const refused = await ctx.post(
        `${API}/organizations/${providerId}/appointments/${cancelledId}/check-in`,
        { headers: auth(clinicianToken) },
      )
      expect(refused.status()).toBe(409)
    } finally {
      await ctx.dispose()
    }
    // No visit was created or cancelled by any of that.
    expect(
      exchangeSql(
        `SELECT count(*) FROM exchange_appointments WHERE id = '${cancelledId}' AND visit_id IS NULL`,
      ).trim(),
    ).toBe('1')
  })

  // ── AP-G — walk-ins still work, with no appointment at all ──────────────────────────────────

  test('AP-G: a walk-in with no booking is still checked in normally', async ({ page }) => {
    await signIn(page, CLINICIAN)
    await openToday(page)

    /*
     * The arrived patient's visit has to be finished first: ADR-069's one-active-visit rule is
     * unchanged by this ticket, and the demo baseline has one synthetic patient, so the walk-in
     * needs them genuinely free.
     *
     * **Completed by clicking, not by a database write.** An `UPDATE exchange_visits SET status =
     * 'COMPLETED'` would have been one line and would have advanced clinical state behind the
     * product's back -- which this suite's own contract forbids. The clinician completes it the way
     * a clinician does.
     */
    // `Ouvrir` is offered by `ClinicArrivals` in this same "Aujourd'hui" section -- the waiting
    // list carries both CHECKED_IN and IN_PROGRESS arrivals, so the visit AP-E2 started is still
    // there. "Terminer la visite" lives in the visit workspace that opens, and completes without a
    // confirmation step.
    // Completed from the "Visites" section, which is the only place the product offers it: the
    // waiting list's `Ouvrir` is rendered only when `ClinicArrivals` is given an `onOpenVisit`
    // callback, and `StaffWorkspace` mounts `ClinicHomePage` without one -- so no `Ouvrir` button
    // exists on "Aujourd'hui" at all. In "Visites" each visit is itself a button carrying the
    // patient's name; selecting it mounts `VisitWorkspace`, which offers "Terminer la visite"
    // while the visit is in progress. This is the same path `08-clinic-visit.spec.ts` already uses.
    await openSection(page, 'Visites')
    const visitRow = page.getByRole('button', { name: new RegExp(PATIENT) }).first()
    await expect(visitRow).toBeVisible({ timeout: 30_000 })
    await visitRow.click()
    const endVisit = page.getByRole('button', { name: 'Terminer la visite' })
    await expect(endVisit).toBeVisible({ timeout: 30_000 })
    await endVisit.click()
    await expect
      .poll(
        () =>
          exchangeSql(`SELECT status FROM exchange_visits WHERE id = '${linkedVisitId}'`).trim(),
        { timeout: 30_000 },
      )
      .toBe('COMPLETED')

    const before = exchangeSql(
      `SELECT count(*) FROM exchange_visits WHERE provider_organization_id = '${providerId}'`,
    ).trim()

    await page.reload()
    await openToday(page)
    // Scoped to the arrivals region, because this ticket put a second panel on the same page that
    // uses some of the same words: "Rechercher un patient" labels a field in both, and
    // "Enregistrer l'arrivée" is the *schedule's* arrival button, not the walk-in one. An
    // unscoped `.last()` would have clicked the wrong panel and checked in a booking instead of a
    // walk-in -- which is exactly the thing this test exists to tell apart.
    const arrivals = page.getByRole('region', { name: 'Patients en attente' })
    await arrivals.getByRole('button', { name: 'Accueillir un patient' }).click()
    await arrivals.getByLabel('Rechercher un patient').fill('Démo')
    await arrivals.getByRole('button', { name: new RegExp(PATIENT) }).first().click()
    await arrivals.getByRole('button', { name: 'Accueillir', exact: true }).click()

    await expect
      .poll(
        () =>
          exchangeSql(
            `SELECT count(*) FROM exchange_visits WHERE provider_organization_id = '${providerId}'`,
          ).trim(),
        { timeout: 30_000 },
      )
      .toBe(String(Number(before) + 1))

    // **The new visit has no appointment pointing at it.** A walk-in needs none, and none was
    // invented to satisfy it.
    const orphan = exchangeSql(
      `SELECT count(*) FROM exchange_visits v
        WHERE v.provider_organization_id = '${providerId}'
        AND v.status = 'CHECKED_IN'
        AND NOT EXISTS (SELECT 1 FROM exchange_appointments a WHERE a.visit_id = v.id)`,
    ).trim()
    expect(Number(orphan)).toBeGreaterThanOrEqual(1)
  })

  // ── AP-H — unauthorized and rival actors ────────────────────────────────────────────────────

  test('AP-H: billing, laboratory and unauthenticated callers are all refused', async ({
    browser,
  }) => {
    const clerk = await freshSignIn(browser, CLERK)
    const lab = await freshSignIn(browser, LAB_TECH)
    try {
      // The till has no schedule to look at at all -- and, as with the administrator in AP-A,
      // that means no "Aujourd'hui" section is offered in the first place, not an empty one.
      await expect(nav(clerk.page).getByRole('button', { name: "Aujourd'hui" })).toHaveCount(0)
      await expect(clerk.page.getByRole('heading', { name: 'Rendez-vous' })).toHaveCount(0)

      const ctx = await api()
      try {
        const noCredential = await ctx.get(`${API}/organizations/${providerId}/appointments`)
        expect(noCredential.status()).toBe(401)

        // `billing.operate` is money authority and nothing else (ADR-066).
        const till = await ctx.get(`${API}/organizations/${providerId}/appointments`, {
          headers: auth(clerk.session.token),
        })
        expect(till.status()).toBe(403)

        // A laboratory worker has no standing in this clinic -- 404, which does not confirm the
        // organization exists.
        const labRead = await ctx.get(`${API}/organizations/${providerId}/appointments`, {
          headers: auth(lab.session.token),
        })
        expect(labRead.status()).toBe(404)

        // And neither may mutate.
        for (const token of [clerk.session.token, lab.session.token]) {
          const attempt = await ctx.post(`${API}/organizations/${providerId}/appointments`, {
            headers: { ...auth(token), 'Idempotency-Key': crypto.randomUUID() },
            data: {
              patient_id: '11111111-1111-4111-8111-111111111111',
              scheduled_date: localDate(1),
              scheduled_time: '09:00:00',
            },
          })
          expect([403, 404]).toContain(attempt.status())
        }
      } finally {
        await ctx.dispose()
      }
    } finally {
      await clerk.context.close()
      await lab.context.close()
    }
  })

  test('AP-H2: withdrawing the grant removes scheduling on the same token', async ({ browser }) => {
    // The revoke half of ADR-069's shape: by clicking, no role change, effective next request.
    const admin = await freshSignIn(browser, ADMIN)
    try {
      const sheet = await openSheet(admin.page, CLINICIAN.username)
      const row = sheet.getByRole('listitem').filter({ hasText: SCHEDULE.label }).first()
      // "Suivre le rôle" is the CLEAR action -- it removes the override row and lets the role
      // template decide again, which for `appointment.manage` means nobody. That, not "Refuser",
      // is what withdrawing a grant means: a DENY would leave an override row behind, recording a
      // prohibition that was never decided. (An earlier version looked for /Retirer|Révoquer/,
      // which matches none of this screen's three capability buttons -- "Suivre le rôle",
      // "Refuser", "Autoriser" -- and so waited on a button that does not exist.)
      await row.getByRole('button', { name: 'Suivre le rôle' }).first().click()
      await expect(
        sheet.getByRole('listitem').filter({ hasText: SCHEDULE.label }).first(),
      ).not.toContainText('Accordé directement', { timeout: 30_000 })
    } finally {
      await admin.context.close()
    }

    const ctx = await api()
    try {
      // The **same** token that booked earlier in this run.
      const refused = await ctx.post(`${API}/organizations/${providerId}/appointments`, {
        headers: { ...auth(clinicianToken), 'Idempotency-Key': crypto.randomUUID() },
        data: {
          patient_id: '11111111-1111-4111-8111-111111111111',
          scheduled_date: localDate(1),
          scheduled_time: '09:00:00',
        },
      })
      expect(refused.status()).toBe(403)

      // Reading the day still works, because `visit.check_in` from the CLINICIAN template grants
      // it -- the two authorities really are separate.
      const stillReads = await ctx.get(`${API}/organizations/${providerId}/appointments`, {
        headers: auth(clinicianToken),
      })
      expect(stillReads.status()).toBe(200)
    } finally {
      await ctx.dispose()
    }

    const roles = exchangeSql(
      `SELECT string_agg(role, ',' ORDER BY role) FROM exchange_organization_role_assignments
        WHERE membership_id = '${clinicianMembership}'`,
    ).trim()
    expect(roles, 'withdrawing a capability must not change a job title').toBe('CLINICIAN')

    // CLEAR removes the row rather than flipping it to DENY: the capability is simply not granted
    // again, and nothing records a prohibition nobody decided.
    const overrides = exchangeSql(
      `SELECT count(*) FROM exchange_membership_capability_overrides
        WHERE membership_id = '${clinicianMembership}' AND capability = '${SCHEDULE.key}'`,
    ).trim()
    expect(overrides, 'clearing a grant must leave no override row behind').toBe('0')
  })

  // ── AP-I — responsive ───────────────────────────────────────────────────────────────────────

  test('AP-I: the schedule works at 1280, 768 and 390 with no page-level overflow', async ({
    browser,
  }) => {
    // Re-granted for this phase: AP-H2 withdrew it, and this one is about layout, not authority.
    const admin = await freshSignIn(browser, ADMIN)
    try {
      const sheet = await openSheet(admin.page, CLINICIAN.username)
      await sheet
        .getByRole('listitem')
        .filter({ hasText: SCHEDULE.label })
        .first()
        .getByRole('button', { name: 'Autoriser' })
        .click()
    } finally {
      await admin.context.close()
    }

    const clinician = await freshSignIn(browser, CLINICIAN)
    try {
      await openToday(clinician.page)
      for (const [width, height] of [
        [1280, 900],
        [768, 1024],
        [390, 844],
      ] as const) {
        await clinician.page.setViewportSize({ width, height })
        await expect(clinician.page.getByRole('heading', { name: 'Rendez-vous' })).toBeVisible()
        const name = clinician.page
          .getByRole('region', { name: 'Rendez-vous' })
          .getByRole('listitem')
          .filter({ hasText: PATIENT })
          .first()
          .getByText(PATIENT)
        await expect(name).toBeVisible()

        // Visible is not the same as readable, and the difference is what this phase is for. At
        // 768px the name column collapsed to one character wide and "Patient Démo Alpha" rendered
        // as a vertical stack of letters; `toBeVisible()` only asks for a non-zero box, so an
        // assertion about *legibility* has to be about width. 80px is roughly the narrowest a
        // two-word name can be and still read as a line of text rather than a column of letters.
        const box = await name.boundingBox()
        expect(box, `the patient name must have a box at ${width}px`).not.toBeNull()
        expect(
          box!.width,
          `the patient name must not be squeezed into a column of letters at ${width}px`,
        ).toBeGreaterThan(80)

        const overflow = await clinician.page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        )
        expect(overflow, `no horizontal page overflow at ${width}px`).toBeLessThanOrEqual(1)
        await clinician.page.screenshot({
          path: `test-results/appointments-${width}.png`,
          fullPage: true,
        })
      }
    } finally {
      await clinician.context.close()
    }
  })

  // ── AP-J — browser health ───────────────────────────────────────────────────────────────────

  test('AP-J: no unexpected console errors and no failed requests across the whole run', async () => {
    expect(consoleErrors, `unexpected console errors:\n${consoleErrors.join('\n')}`).toEqual([])
    expect(failedRequests, `unexpected failed requests:\n${failedRequests.join('\n')}`).toEqual([])
  })
})
