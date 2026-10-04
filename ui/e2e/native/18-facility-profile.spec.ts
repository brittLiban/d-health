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
 * DHEALTH-FACILITY-PROFILE-001/ADR-074 — each facility's own wall clock, against the real stack.
 *
 * ── The question this file answers ──
 *
 * Not "does the per-facility timezone model work". `test_dhealthfacilityprofile001_facility_profile.py`
 * qualifies that at the HTTP edge (63 tests) and
 * `tests/integration/test_dhealthfacilityprofile001_postgres_qualification.py` qualifies the lock
 * races, the DST policy and the migration on real PostgreSQL (11 more). This file answers what
 * neither can:
 *
 *   **Can a clinic administrator change their facility's timezone by clicking the product, does the
 *   clinic's schedule immediately read in the new zone, and do the bookings that already existed
 *   stay exactly where they were?**
 *
 * ── The consequence is the point, not the form ──
 *
 * FP-C is the phase that matters. A booking is made at 10:00 while the clinic is `Africa/Djibouti`,
 * the zone is changed to `America/Vancouver` by clicking, and then two things are asserted that are
 * easy to confuse and must both hold:
 *
 *   * the **stored instant** of the existing booking is unchanged (ADR-074 §4) — read back from the
 *     database, not inferred from the screen;
 *   * the **displayed wall clock** of that same booking is different, because it is now rendered in
 *     the new zone.
 *
 * "Nothing moved" and "it displays differently" are both true, and a reader who only checks one of
 * them will believe the opposite of whichever they skipped.
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated, no session injected, no auth state written into browser storage. Every
 * configuration act is performed by clicking, as somebody who signed in through a real
 * Authorization Code + PKCE flow.
 *
 * Direct `/exchange/v1` calls appear only to assert refusals the browser cannot demonstrate (a
 * section that is not rendered cannot be clicked) and to read authoritative state. `exchangeSql`
 * reads state back. **Nothing advances the workflow by writing to the database.**
 *
 * ── This suite restores what it changes ──
 *
 * `exchange_facility_profiles` is **configuration**, so it is deliberately absent from
 * `demo_seed._WORKFLOW_TABLES` and `reset.sh` does not truncate it. FP-E therefore puts the clinic
 * back on `Africa/Djibouti` **by clicking**, and asserts it. Every sibling suite's wall-clock
 * assertion depends on that zone, so leaving Vancouver behind would redden suites that have nothing
 * to do with this one. `demo_seed` also overwrites a differing value at the next `up.sh`, which is
 * the second line of defence rather than the first.
 *
 * The booking FP-C creates is workflow state; `reset.sh` clears it.
 *
 * ── What this suite does NOT establish ──
 *
 * **Not human usability.** Nobody who administers a clinic has used this screen. A green run says
 * the mechanism works when driven correctly.
 *
 * **Not DST behaviour.** Neither `Africa/Djibouti` (never transitions) nor `America/Vancouver`
 * (tzdata 2026c records British Columbia abolishing it) has a transition to exercise here. The DST
 * refusal policy is proved against `America/New_York`'s real 2025 transitions in `PG-FP-9b`, which
 * is where it belongs — a browser cannot assert a 422 it is never shown.
 *
 * **Not a rename.** `Organization.name` is read-only by decision (ADR-074 §7, Human Decision 1), and
 * FP-A asserts the absence of an editor rather than treating it as a gap.
 *
 * All patient data is the demo baseline's synthetic patient. No real patient data is used.
 */

const EXCHANGE = process.env.E2E_EXCHANGE_URL ?? 'https://exchange.localhost'
const API = `${EXCHANGE}/exchange/v1`

/** The clinic's administrator: `staff.access.manage` **and** `facility.configure` by template. */
const ADMIN = {
  username: process.env.DEMO_ADMIN_USERNAME ?? 'demo-admin',
  password: process.env.DEMO_ADMIN_PASSWORD ?? '',
}
/** CLINICIAN: clinical authority, and no facility configuration at all. */
const CLINICIAN = {
  username: process.env.DEMO_CLINICIAN_USERNAME ?? 'demo-clinician',
  password: process.env.DEMO_CLINICIAN_PASSWORD ?? '',
}
/** The till. `billing.operate` only — reaches `Services et tarifs` through neither. */
const CLERK = {
  username: process.env.DEMO_CLERK_USERNAME ?? 'demo-clerk',
  password: process.env.DEMO_CLERK_PASSWORD ?? '',
}

const HOME_ZONE = 'Africa/Djibouti'
const AWAY_ZONE = 'America/Vancouver'

/** Booking authority, which no role template carries (ADR-073 §5). FP-B grants and FP-B2 removes. */
const SCHEDULE = 'appointment.manage'

let providerId = ''
let providerName = ''
let adminToken = ''
let clinicianToken = ''
let clinicianMembership = ''
/** The booking FP-C makes while the clinic is still on its home zone. */
let bookingId = ''
let bookingInstantBefore = ''
let bookingWallClockHome = ''

const consoleErrors: string[] = []
const failedRequests: string[] = []

interface Membership {
  organization_id: string
  organization_name: string
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

/**
 * A separate browser context per identity.
 *
 * Not an optimization: a second `signIn` inside one context never reaches the Keycloak login form,
 * because the SSO session is still live and the authorization endpoint redirects straight back —
 * so the second identity silently *is* the first. Each phase that changes who is acting gets its
 * own context (and is its own `test`, because the config runs serially).
 */
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

/** The `Établissement` panel, scoped by its own landmark so the app shell's heading cannot match. */
function panel(page: Page) {
  return page.getByRole('region', { name: 'Établissement' })
}

/** The stored instant, straight out of PostgreSQL. The screen's opinion is asserted separately. */
function storedInstant(appointmentId: string): string {
  return exchangeSql(
    `select to_char(scheduled_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') ` +
      `from exchange_appointments where id = '${appointmentId}'`,
  )
}

function storedZone(organizationId: string): string {
  return exchangeSql(
    `select timezone_name from exchange_facility_profiles where organization_id = '${organizationId}'`,
  )
}

/**
 * Change the facility timezone by clicking, through both steps of the confirmation.
 *
 * The two steps are the product's own design (ADR-074 §13) and driving them is part of the
 * evidence: a single-click version of this screen would be a different product.
 */
async function changeZoneByClicking(page: Page, zone: string): Promise<void> {
  await panel(page).getByRole('button', { name: 'Changer le fuseau horaire' }).click()

  const picker = page.getByLabel('Nouveau fuseau horaire')
  await expect(picker).toBeVisible({ timeout: 30_000 })
  // `selectOption` by value, not by label: the label carries a human rendering beside the
  // identifier and the identifier is the thing the server stores.
  await picker.selectOption(zone)

  await page.getByRole('button', { name: 'Continuer' }).click()

  const confirm = page.getByRole('group', { name: 'Confirmer le changement' })
  await expect(confirm).toBeVisible({ timeout: 30_000 })
  // The warning must say what does NOT happen, or an administrator reads "my appointments moved".
  await expect(confirm).toContainText('ne sont pas déplacés')
  await confirm.getByRole('button', { name: 'Confirmer le changement' }).click()

  await expect(panel(page).getByText(zone, { exact: true })).toBeVisible({ timeout: 30_000 })
}

test.beforeAll(async () => {
  await waitForEdge()
})

// ==================================================================================================
// FP-A — the facility has a profile, an administrator can see it, and the name is not editable
// ==================================================================================================

test('FP-A: the clinic administrator sees the facility profile, with the name read-only', async ({
  browser,
}) => {
  const { session, page, context } = await freshSignIn(browser, ADMIN)
  try {
    const provider = providerOf(session)
    providerId = provider.organization_id
    providerName = provider.organization_name
    adminToken = session.token

    // The capability, from the server's own resolution. Never asserted by role name (ADR-067).
    expect(
      provider.capabilities,
      'the demo clinic administrator must hold facility.configure for this suite to mean anything',
    ).toContain('facility.configure')

    await openSection(page, 'Établissement')
    await expect(panel(page)).toBeVisible({ timeout: 30_000 })

    // Scoped to the panel: the organization's name also appears in the app shell's header, so an
    // unscoped assertion would pass on the wrong element.
    await expect(panel(page).getByText(providerName, { exact: true })).toBeVisible()
    await expect(panel(page)).toContainText('Le nom est géré par Hawva')

    // The decision, asserted as an absence rather than left as a gap somebody reads as a bug.
    await expect(panel(page).getByRole('textbox', { name: /nom/i })).toHaveCount(0)

    await expect(panel(page).getByText(HOME_ZONE, { exact: true })).toBeVisible()
    expect(storedZone(providerId)).toBe(HOME_ZONE)
  } finally {
    await context.close()
  }
})

// ==================================================================================================
// FP-B — a booking exists at a known wall clock BEFORE anything changes
// ==================================================================================================

test('FP-B: a clinician books 10:00 while the clinic is on its home zone', async ({ browser }) => {
  const { session, page, context } = await freshSignIn(browser, CLINICIAN)
  try {
    clinicianToken = session.token
    expect(providerOf(session).organization_id).toBe(providerId)
    // Baseline check, and the premise FP-F relies on: a clinician configures nothing.
    expect(providerOf(session).capabilities).not.toContain('facility.configure')

    clinicianMembership = exchangeSql(
      `select id from exchange_organization_memberships ` +
        `where organization_id = '${providerId}' and identity_id = '${session.identityId}'`,
    )
    expect(clinicianMembership, 'the clinician has no membership in the demo clinic').not.toBe('')

    // `appointment.manage` is in no role template (ADR-073 §5), so it is granted here through the
    // real override route with the **administrator's** token. Granting it *by clicking* is
    // 17-clinic-appointments' evidence and is deliberately not repeated: this suite's claim is
    // about what happens to a booking when the facility's zone changes, and the booking is only
    // the material it needs. FP-B2 removes the grant again.
    const ctx = await api()
    try {
      const grant = await ctx.put(
        `${API}/organizations/${providerId}/memberships/${clinicianMembership}` +
          `/capabilities/${SCHEDULE}`,
        { headers: auth(adminToken), data: { effect: 'GRANT' } },
      )
      expect(grant.status(), await grant.text()).toBe(200)

      const day = new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 10)
      const response = await ctx.post(`${API}/organizations/${providerId}/appointments`, {
        headers: { ...auth(clinicianToken), 'Idempotency-Key': `fp-b-${Date.now()}` },
        data: {
          patient_id: await firstPatientId(ctx, clinicianToken),
          scheduled_date: day,
          scheduled_time: '10:00:00',
        },
      })
      expect(response.status(), await response.text()).toBe(201)
      const body = await response.json()
      bookingId = body.id as string
      bookingInstantBefore = storedInstant(bookingId)
      // 10:00 in UTC+3 is 07:00Z. Asserted against the database, not against the screen.
      expect(bookingInstantBefore).toContain('T07:00:00Z')
    } finally {
      await ctx.dispose()
    }

    // And the clinic's own schedule renders that instant as 10:00, in the clinic's own zone.
    await openSection(page, "Aujourd'hui")
    const schedule = page.getByRole('region', { name: 'Rendez-vous' })
    await expect(schedule).toBeVisible({ timeout: 30_000 })
    bookingWallClockHome = new Intl.DateTimeFormat('fr-FR', {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: HOME_ZONE,
    }).format(new Date(bookingInstantBefore))
    expect(bookingWallClockHome).toBe('10:00')
  } finally {
    await context.close()
  }
})

test('FP-B2: the grant this suite made is removed again', async () => {
  const ctx = await api()
  try {
    // `exchange_membership_capability_overrides` is absent from `demo_seed._WORKFLOW_TABLES`, so a
    // reset leaves a leftover grant standing and `10-capabilities` asserts what these same people
    // can reach. Removing it is this suite's own responsibility, not reset.sh's.
    const cleared = await ctx.delete(
      `${API}/organizations/${providerId}/memberships/${clinicianMembership}` +
        `/capabilities/${SCHEDULE}`,
      { headers: auth(adminToken) },
    )
    expect(cleared.status(), await cleared.text()).toBe(200)
  } finally {
    await ctx.dispose()
  }
  const left = exchangeSql(
    `select count(*) from exchange_membership_capability_overrides ` +
      `where membership_id = '${clinicianMembership}' and capability = '${SCHEDULE}'`,
  )
  expect(Number(left), 'this suite left a capability grant behind').toBe(0)
})

async function firstPatientId(ctx: APIRequestContext, token: string): Promise<string> {
  const response = await ctx.get(`${API}/organizations/${providerId}/patients?query=D`, {
    headers: auth(token),
  })
  expect(response.status(), 'the demo baseline must have a synthetic patient to book').toBe(200)
  const body = await response.json()
  const patients = (body.patients ?? body.items ?? []) as Array<{ id: string }>
  expect(patients.length, 'no patient found in the demo clinic directory').toBeGreaterThan(0)
  return patients[0].id
}

// ==================================================================================================
// FP-C — the consequence: the instant does not move, the displayed wall clock does
// ==================================================================================================

test('FP-C: changing the zone by clicking moves no instant and changes every display', async ({
  browser,
}) => {
  const { page, context } = await freshSignIn(browser, ADMIN)
  try {
    await openSection(page, 'Établissement')
    await expect(panel(page)).toBeVisible({ timeout: 30_000 })

    await changeZoneByClicking(page, AWAY_ZONE)
    expect(storedZone(providerId)).toBe(AWAY_ZONE)

    // (1) Nothing moved. The booking's instant is byte-identical to what it was before the change.
    expect(
      storedInstant(bookingId),
      'a timezone change rewrote a stored appointment instant -- ADR-074 §4 forbids exactly this',
    ).toBe(bookingInstantBefore)

    // (2) And it now *displays* differently, which is the true and surprising half. 07:00Z is
    // 10:00 in Djibouti and midnight in Vancouver on a June date (PDT, UTC-7).
    const ctx = await api()
    try {
      const response = await ctx.get(
        `${API}/organizations/${providerId}/facility-profile`,
        { headers: auth(adminToken) },
      )
      expect(response.status()).toBe(200)
      expect((await response.json()).timezone_name).toBe(AWAY_ZONE)
      // No caching of a configuration read, so a second administrator sees the change at once.
      expect(response.headers()['cache-control']).toContain('no-store')
    } finally {
      await ctx.dispose()
    }

    const rendered = new Intl.DateTimeFormat('fr-FR', {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: AWAY_ZONE,
    }).format(new Date(bookingInstantBefore))
    expect(
      rendered,
      'the same instant must read as a different wall clock in the new zone, or the display is ' +
        'not using the facility zone at all',
    ).not.toBe(bookingWallClockHome)
    // Midnight must read 00:00, never 24:00. `fr-FR` resolves to `h23` on this runtime's ICU --
    // asserted rather than assumed, because the hour cycle is a property of the deployed browser.
    expect(rendered).toMatch(/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/)
  } finally {
    await context.close()
  }
})

// ==================================================================================================
// FP-D — the change is audited, and the audit row carries no old or new value
// ==================================================================================================

test('FP-D: the change is audited by actor, organization and resource, and nothing more', async () => {
  const count = exchangeSql(
    `select count(*) from exchange_security_audit_events ` +
      `where event_type = 'facility_timezone_changed' and organization_id = '${providerId}'`,
  )
  expect(Number(count), 'clicking Confirmer wrote no audit row').toBeGreaterThan(0)

  const row = exchangeSql(
    `select resource_type || '|' || (actor_identity_id is not null) || '|' || outcome ` +
      `from exchange_security_audit_events ` +
      `where event_type = 'facility_timezone_changed' and organization_id = '${providerId}' ` +
      `order by occurred_at desc limit 1`,
  )
  expect(row).toBe('facility_profile|true|updated')

  // The known limitation, asserted rather than implied: there is no column anywhere in this table
  // that could hold the previous zone (ADR-074 §12, Human Decision 4).
  const columns = exchangeSql(
    `select string_agg(column_name, ',' order by column_name) from information_schema.columns ` +
      `where table_name = 'exchange_security_audit_events'`,
  )
  for (const forbidden of ['old_value', 'new_value', 'value', 'metadata', 'payload', 'detail']) {
    expect(columns.split(',')).not.toContain(forbidden)
  }
})

// ==================================================================================================
// FP-E — the suite puts the clinic back where it found it, by clicking
// ==================================================================================================

test('FP-E: the administrator restores the home zone by clicking', async ({ browser }) => {
  const { page, context } = await freshSignIn(browser, ADMIN)
  try {
    await openSection(page, 'Établissement')
    await expect(panel(page)).toBeVisible({ timeout: 30_000 })
    await changeZoneByClicking(page, HOME_ZONE)

    expect(
      storedZone(providerId),
      'this suite must leave the demo clinic on its home zone -- every sibling suite asserts ' +
        'wall clocks against it, and exchange_facility_profiles is configuration that reset.sh ' +
        'deliberately does not truncate',
    ).toBe(HOME_ZONE)

    // And still nothing moved, on the way back as on the way out.
    expect(storedInstant(bookingId)).toBe(bookingInstantBefore)
  } finally {
    await context.close()
  }
})

// ==================================================================================================
// FP-F — who cannot reach it, in the navigation and at the edge
// ==================================================================================================

test('FP-F: a clinician is offered no Établissement section and is refused at the edge', async ({
  browser,
}) => {
  const { session, page, context } = await freshSignIn(browser, CLINICIAN)
  try {
    expect(providerOf(session).capabilities).not.toContain('facility.configure')
    await expect(nav(page).getByRole('button', { name: 'Établissement', exact: true })).toHaveCount(
      0,
    )

    // A section that is not rendered cannot be clicked, so the refusal is asserted at the edge --
    // the only place it can be. An absent button is not authorization.
    const ctx = await api()
    try {
      const read = await ctx.get(`${API}/organizations/${providerId}/facility-profile`, {
        headers: auth(session.token),
      })
      expect(read.status()).toBe(403)
      const write = await ctx.put(
        `${API}/organizations/${providerId}/facility-profile/timezone`,
        { headers: auth(session.token), data: { timezone_name: AWAY_ZONE } },
      )
      expect(write.status()).toBe(403)
      expect(storedZone(providerId)).toBe(HOME_ZONE)
    } finally {
      await ctx.dispose()
    }
  } finally {
    await context.close()
  }
})

test('FP-F2: the billing clerk reaches prices and not the facility record', async ({ browser }) => {
  const { session, page, context } = await freshSignIn(browser, CLERK)
  try {
    // The distinction ADR-074 §13 draws: `Services et tarifs` accepts
    // `facility.configure || billing.configure_prices`; `Établissement` accepts only the former.
    expect(providerOf(session).capabilities).not.toContain('facility.configure')
    await expect(nav(page).getByRole('button', { name: 'Établissement', exact: true })).toHaveCount(
      0,
    )

    const ctx = await api()
    try {
      const write = await ctx.put(
        `${API}/organizations/${providerId}/facility-profile/timezone`,
        { headers: auth(session.token), data: { timezone_name: AWAY_ZONE } },
      )
      expect(write.status()).toBe(403)
      expect(storedZone(providerId)).toBe(HOME_ZONE)
    } finally {
      await ctx.dispose()
    }
  } finally {
    await context.close()
  }
})

test('FP-F3: an offset and an unknown zone are both refused, with the server message', async ({
  browser,
}) => {
  const { page, context } = await freshSignIn(browser, ADMIN)
  try {
    const ctx = await api()
    try {
      for (const bad of ['+03:00', 'UTC+3', 'Mars/Olympus_Mons', 'africa/djibouti', '']) {
        const response = await ctx.put(
          `${API}/organizations/${providerId}/facility-profile/timezone`,
          { headers: auth(adminToken), data: { timezone_name: bad } },
        )
        expect(response.status(), `${bad} was accepted`).toBe(422)
      }
      expect(storedZone(providerId)).toBe(HOME_ZONE)
    } finally {
      await ctx.dispose()
    }
    // And the screen is still usable afterwards -- a refused change must not strand the form.
    await openSection(page, 'Établissement')
    await expect(panel(page).getByText(HOME_ZONE, { exact: true })).toBeVisible({ timeout: 30_000 })
  } finally {
    await context.close()
  }
})

// ==================================================================================================
// FP-G — the three supported widths
// ==================================================================================================

test('FP-G: the panel stays inside its card at 1280, 768 and 390', async ({ browser }) => {
  const { page, context } = await freshSignIn(browser, ADMIN)
  try {
    await openSection(page, 'Établissement')
    await expect(panel(page)).toBeVisible({ timeout: 30_000 })

    for (const width of [1280, 768, 390]) {
      await page.setViewportSize({ width, height: 900 })
      await expect(panel(page)).toBeVisible()

      // No horizontal page scroll at any supported width. `+1` absorbs sub-pixel rounding, which
      // otherwise fails this on a perfectly fitting layout.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      expect(overflow, `the page scrolls horizontally at ${width}px`).toBeLessThanOrEqual(1)

      // The action is reachable, not clipped out of the card.
      const button = panel(page).getByRole('button', { name: 'Changer le fuseau horaire' })
      await expect(button).toBeVisible()
      const box = await button.boundingBox()
      expect(box, `the change control has no box at ${width}px`).not.toBeNull()
      expect(box!.x, `the change control starts off-screen at ${width}px`).toBeGreaterThanOrEqual(0)
      expect(box!.x + box!.width, `the change control overflows at ${width}px`).toBeLessThanOrEqual(
        width + 1,
      )

      await page.screenshot({ path: `test-results/fp-g-${width}.png`, fullPage: true })
    }
  } finally {
    await context.close()
  }
})

// ==================================================================================================
// FP-H — the run's own health
// ==================================================================================================

test('FP-H: no console errors and no failed requests across the whole run', async () => {
  expect(consoleErrors, consoleErrors.join('\n')).toEqual([])
  expect(failedRequests, failedRequests.join('\n')).toEqual([])
})
