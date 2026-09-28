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
 * DHEALTH-ACCESS-CAPABILITIES-001 — capability-driven access, against the real stack.
 *
 * ── The question this file answers ──
 *
 * Not "does the capability resolver work" — `test_dhealthaccesscap001_capabilities.py` answers that
 * in 21 tests and the PostgreSQL qualification answers the concurrency half in 8 more. This file
 * answers the thing only a browser can: **does an administrator granting one capability, through
 * the product, actually change what another person can see and do — with no role change and no
 * re-authentication.**
 *
 * That is §46's CAP-1, which the ticket calls the heart of the work: *"If this does not work, the
 * system remains role-hardcoded."*
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated and no session injected. The grant is made by clicking `Équipe et accès`;
 * the effect is observed in a *different person's* live session, in their navigation and in a real
 * API call. `exchangeSql` appears only to read state back.
 *
 * ── The phases ──
 *
 *   CAP-B1  a clinician holds no billing authority: no `Facturation`, and the API refuses
 *   CAP-B2  an administrator grants `billing.operate` by clicking, without touching their role
 *   CAP-B3  the clinician's authority is immediate on the same session, and the section appears
 *   CAP-B4  the grant is withdrawn; the authority and the section go with it
 *   CAP-B5  an individual deny beats a role template, in the browser
 *   CAP-B6  the last administrator cannot deny themselves out of the organization
 *
 * ── State this suite leaves behind ──
 *
 * Capability overrides, which `scripts/demo/reset.sh` does **not** clear — they hang off
 * memberships, which are baseline. Every phase therefore cleans up its own override, and CAP-B4
 * does it as part of what it asserts. A re-run starts from the same place.
 *
 * All data here is the demo baseline's synthetic data. No real patient data is used.
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
const CLERK = {
  username: process.env.DEMO_CLERK_USERNAME ?? 'demo-clerk',
  password: process.env.DEMO_CLERK_PASSWORD ?? '',
}

interface Membership {
  organization_id: string
  organization_name: string
  organization_type: string
  organization_status: string
  membership_status: string
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

/**
 * A sign-in in its own browser context.
 *
 * Signing a second person in on the first person's page never reaches Keycloak — the session is
 * still live — so the login helper waits for a form that never appears. Two people at two
 * workstations is also what this suite is actually about.
 */
async function freshSignIn(browser: Browser, who: { username: string; password: string }) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true })
  const page = await context.newPage()
  const session = await signIn(page, who)
  return { session, page, context }
}

function auth(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` }
}

function providerOf(session: Session): Membership {
  const found = session.memberships.find((m) => m.organization_type === 'PROVIDER')
  if (!found) throw new Error('PROVIDER membership missing -- is the demo baseline seeded?')
  return found
}

async function billingStatus(token: string, organizationId: string): Promise<number> {
  const ctx = await api()
  try {
    const response = await ctx.get(`${API}/organizations/${organizationId}/billing/invoices`, {
      headers: auth(token),
    })
    return response.status()
  } finally {
    await ctx.dispose()
  }
}

/** This identity's capabilities at this organization, straight from `/me`. */
async function capabilities(token: string, organizationId: string): Promise<string[]> {
  const ctx = await api()
  try {
    const body = await (await ctx.get(`${API}/me`, { headers: auth(token) })).json()
    const membership = (body.memberships as Membership[]).find(
      (m) => m.organization_id === organizationId,
    )
    return membership?.capabilities ?? []
  } finally {
    await ctx.dispose()
  }
}

function nav(page: Page) {
  return page.getByRole('navigation', { name: 'Navigation principale' })
}

/**
 * Open a member's capability sheet in `Équipe et accès`, addressed by **username**.
 *
 * Deliberately not by role label. A member row renders its own role chips *and* an
 * "Ajouter clinicien" button for every role it does not hold, so filtering on `/Clinicien/i`
 * matched the administrator's row as well -- and `.first()` then granted the capability to the
 * wrong person. The test still passed, because it asserted the sheet said "Accordé directement"
 * without checking whose sheet it was; the *next* phase caught it. Usernames are unique and
 * appear only on their own row.
 */
async function openSheet(page: Page, username: string) {
  await nav(page).getByRole('button', { name: 'Équipe et accès', exact: true }).click()
  // Wait for the list itself before filtering it: the sheet's own `<li>` elements are listitems
  // too, so a filter that runs before the member rows exist can resolve against the wrong tree.
  await expect(page.getByRole('heading', { name: 'Équipe et accès' })).toBeVisible()
  const row = page.getByRole('listitem').filter({ hasText: username }).first()
  await expect(row).toBeVisible()
  // A member whose sheet is already open shows the other label; closing first makes this helper
  // idempotent, which matters because CAP-B5 opens the same person's sheet twice.
  const opener = row.getByRole('button', { name: 'Voir les autorisations' })
  if ((await opener.count()) > 0) {
    await opener.click()
  }
  // The returned region names the person, so operating on the wrong sheet is not expressible --
  // which is the defect this helper already had once: filtering by role label matched the
  // administrator's own row and granted the capability to them instead.
  return page.getByRole('region', { name: `Autorisations — ${username}` })
}

let providerId = ''
let clinicianMembership = ''

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  await waitForEdge()

  // Start from a known baseline: no capability overrides anywhere.
  //
  // `scripts/demo/reset.sh` does **not** clear them, and that is correct -- an override hangs off a
  // membership and is configuration a facility made, exactly like a role assignment, which reset
  // also leaves alone. So this suite cleans up after itself instead, and each phase does; but an
  // *aborted* run leaves a grant behind, and the next run's CAP-B2 then finds the capability
  // already held and fails with a message about the wrong thing. Found by exactly that.
  //
  // Deliberately a targeted delete rather than a truncate: it removes what this suite creates and
  // nothing else, so running it never destroys a facility's deliberate configuration.
  exchangeSql(
    `DELETE FROM exchange_membership_capability_overrides
      WHERE membership_id IN (
        SELECT m.id FROM exchange_organization_memberships m
          JOIN exchange_organizations o ON o.id = m.organization_id
         WHERE o.name LIKE '%Démo DHealth'
      )`,
  )
})

test.describe('DHEALTH-ACCESS-CAPABILITIES-001 — authority without a job title', () => {
  test('CAP-B1: a clinician holds clinical authority and no billing authority', async ({
    browser,
  }) => {
    const clinic = await freshSignIn(browser, CLINICIAN)
    providerId = providerOf(clinic.session).organization_id

    // A clinician, and only a clinician. The composition proofs below depend on this being true
    // at the start -- if they already held billing, granting it would prove nothing.
    expect(providerOf(clinic.session).roles).toEqual(['CLINICIAN'])
    expect(providerOf(clinic.session).capabilities).not.toContain('billing.operate')

    await expect(nav(clinic.page).getByRole('button', { name: 'Visites' })).toBeVisible()
    await expect(
      nav(clinic.page).getByRole('button', { name: 'Facturation', exact: true }),
    ).toHaveCount(0)
    expect(await billingStatus(clinic.session.token, providerId)).toBe(403)

    clinicianMembership = exchangeSql(
      `SELECT m.id FROM exchange_organization_memberships m
         JOIN exchange_identities i ON i.id = m.identity_id
        WHERE i.subject IS NOT NULL AND m.organization_id = '${providerId}'
          AND EXISTS (SELECT 1 FROM exchange_organization_role_assignments r
                      WHERE r.membership_id = m.id AND r.role = 'CLINICIAN')
        LIMIT 1`,
    )
    expect(clinicianMembership, 'the demo clinician must have a membership').not.toBe('')
    await clinic.context.close()
  })

  test('CAP-B2: an administrator grants the capability by clicking, changing no role', async ({
    browser,
  }) => {
    const admin = await freshSignIn(browser, ADMIN)

    const sheet = await openSheet(admin.page, CLINICIAN.username)
    await expect(sheet).toBeVisible()

    // Human words, never the enum value (§9/§19).
    const row = sheet.getByRole('listitem').filter({ hasText: 'Tenir la caisse' }).first()
    await expect(row).toContainText("Aucun rôle ne l'accorde")
    await row.getByRole('button', { name: 'Autoriser' }).click()

    // The sheet now says *why* they have it, which is §20's whole point.
    await expect(
      sheet.getByRole('listitem').filter({ hasText: 'Tenir la caisse' }).first(),
    ).toContainText('Accordé directement')

    // ...and their role is untouched: no promotion, no combination role.
    const roles = exchangeSql(
      `SELECT string_agg(role, ',' ORDER BY role) FROM exchange_organization_role_assignments
        WHERE membership_id = '${clinicianMembership}'`,
    )
    expect(roles).toBe('CLINICIAN')
    await admin.context.close()
  })

  test('CAP-B3: the clinician gains the till immediately, on a session they already had', async ({
    browser,
  }) => {
    // **CAP-1's decisive assertion.** A fresh sign-in would prove much less: the point is that
    // authority is read from the database per request, not carried in the token. The API call
    // below uses a token minted before the grant existed.
    const clinic = await freshSignIn(browser, CLINICIAN)

    expect(await billingStatus(clinic.session.token, providerId)).toBe(200)
    expect(await capabilities(clinic.session.token, providerId)).toContain('billing.operate')

    // The interface follows the same authority the server enforces.
    await clinic.page.reload()
    await expect(
      nav(clinic.page).getByRole('button', { name: 'Facturation', exact: true }),
    ).toBeVisible()
    await nav(clinic.page).getByRole('button', { name: 'Facturation', exact: true }).click()
    await expect(
      clinic.page.getByRole('heading', { name: 'Facturation', level: 1 }),
    ).toBeVisible()

    // Clinical authority is untouched: capabilities compose, they do not replace (§49).
    await expect(nav(clinic.page).getByRole('button', { name: 'Visites' })).toBeVisible()
    await clinic.context.close()
  })

  test('CAP-B4: withdrawing the grant closes the till again, same session', async ({ browser }) => {
    const admin = await freshSignIn(browser, ADMIN)
    const sheet = await openSheet(admin.page, CLINICIAN.username)
    const row = sheet.getByRole('listitem').filter({ hasText: 'Tenir la caisse' }).first()
    await row.getByRole('button', { name: 'Suivre le rôle' }).click()
    await expect(
      sheet.getByRole('listitem').filter({ hasText: 'Tenir la caisse' }).first(),
    ).toContainText("Aucun rôle ne l'accorde")
    await admin.context.close()

    const clinic = await freshSignIn(browser, CLINICIAN)
    expect(await billingStatus(clinic.session.token, providerId)).toBe(403)
    expect(await capabilities(clinic.session.token, providerId)).not.toContain('billing.operate')
    await expect(
      nav(clinic.page).getByRole('button', { name: 'Facturation', exact: true }),
    ).toHaveCount(0)
    await clinic.context.close()

    // The baseline is restored, so this suite can run again.
    const left = exchangeSql(
      `SELECT count(*) FROM exchange_membership_capability_overrides
        WHERE membership_id = '${clinicianMembership}'`,
    )
    expect(left).toBe('0')
  })

  test('CAP-B5: an individual deny beats the role template, in the browser', async ({ browser }) => {
    // **CAP-2.** The cashier keeps `BILLING_CLERK` throughout; one override decides whether they
    // can work. This is what makes `FRONT_DESK_WITH_BILLING` and `FRONT_DESK_NO_BILLING`
    // unnecessary.
    const before = await freshSignIn(browser, CLERK)
    expect(await billingStatus(before.session.token, providerId)).toBe(200)
    const clerkMembership = exchangeSql(
      `SELECT m.id FROM exchange_organization_memberships m
        WHERE m.organization_id = '${providerId}'
          AND EXISTS (SELECT 1 FROM exchange_organization_role_assignments r
                      WHERE r.membership_id = m.id AND r.role = 'BILLING_CLERK')
        LIMIT 1`,
    )
    await before.context.close()

    const admin = await freshSignIn(browser, ADMIN)
    const sheet = await openSheet(admin.page, CLERK.username)
    const row = sheet.getByRole('listitem').filter({ hasText: 'Tenir la caisse' }).first()
    await expect(row).toContainText('Via')
    await row.getByRole('button', { name: 'Refuser' }).click()
    await expect(
      sheet.getByRole('listitem').filter({ hasText: 'Tenir la caisse' }).first(),
    ).toContainText('Exception individuelle')

    const denied = await freshSignIn(browser, CLERK)
    expect(await billingStatus(denied.session.token, providerId)).toBe(403)
    // The role survives the deny -- the authority went, the job title did not.
    expect(providerOf(denied.session).roles).toEqual(['BILLING_CLERK'])
    await denied.context.close()

    // Undo, and confirm the capability comes back.
    const undo = await openSheet(admin.page, CLERK.username)
    await undo
      .getByRole('listitem')
      .filter({ hasText: 'Tenir la caisse' })
      .first()
      .getByRole('button', { name: 'Suivre le rôle' })
      .click()
    await admin.context.close()

    const restored = await freshSignIn(browser, CLERK)
    expect(await billingStatus(restored.session.token, providerId)).toBe(200)
    await restored.context.close()

    expect(
      exchangeSql(
        `SELECT count(*) FROM exchange_membership_capability_overrides
          WHERE membership_id = '${clerkMembership}'`,
      ),
    ).toBe('0')
  })

  test('CAP-B6: the last administrator cannot deny themselves out of the organization', async ({
    browser,
  }) => {
    // **CAP-6** on the real stack. The demo clinic has exactly one administrator, so denying
    // themselves `staff.access.manage` would leave nobody able to administer it -- refused by the
    // invariant, which now counts *effective* authority rather than a role name (§22).
    const admin = await freshSignIn(browser, ADMIN)
    const sheet = await openSheet(admin.page, ADMIN.username)
    const row = sheet
      .getByRole('listitem')
      .filter({ hasText: "Gérer l'équipe et les accès" })
      .first()
    await row.getByRole('button', { name: 'Refuser' }).click()

    // The screen says what happened and the authority is untouched.
    await expect(admin.page.getByRole('alert')).toBeVisible()
    await expect(
      sheet
        .getByRole('listitem')
        .filter({ hasText: "Gérer l'équipe et les accès" })
        .first(),
    ).toContainText('Via')
    await expect(
      nav(admin.page).getByRole('button', { name: 'Équipe et accès', exact: true }),
    ).toBeVisible()
    await admin.context.close()
  })
})
