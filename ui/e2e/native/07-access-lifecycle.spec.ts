import { expect, request as apiRequest, test, type APIRequestContext, type Page } from '@playwright/test'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'
import { auditLineCount, exchangeSql } from '../support/securityAdmin'
import { waitForEdge } from '../support/edgeReadiness'

/**
 * DHEALTH-NATIVE-ACCESS-001 — the staff access lifecycle, against the real stack.
 *
 * ── The one question this file exists to answer ──
 *
 * DHEALTH-NATIVE-SEC-001 proved that DHealth refuses a withdrawn membership and a removed role on
 * the very next request, with the same still-valid token. It could not prove that anybody could
 * *cause* that state: `set_inactive` and `remove` had no caller, so SEC-6 and SEC-7 produced the
 * state with `psql`, and the finding was recorded as a P1 (F1).
 *
 * Every revocation below goes through the product instead. A real facility administrator signs in
 * through the real Keycloak with a real Authorization Code + PKCE flow, and calls the real
 * endpoints with the real token their own browser sent. Nothing here writes SQL to *cause* a
 * change; `exchangeSql` appears only to read state back and to assert that the database says what
 * the API said, which is a different use from SEC-6's.
 *
 * ── What is real ──
 *
 * Real Chromium, real Caddy edge, real Keycloak 26.7.1, real Exchange adapter, real PostgreSQL 16.
 * No token is fabricated, no session injected, no auth state written into browser storage. Every
 * "loses authority immediately" assertion re-uses the **same captured token** across the
 * revocation: no logout, no refresh, no second login. That is the property under test.
 *
 * ── Two organizations, two administrators ──
 *
 * `demo-admin` administers Clinique Démo DHealth; `demo-lab-admin` administers Laboratoire Démo
 * DHealth. Two rather than one, deliberately: an identity holding ORG_ADMIN in both would
 * legitimately succeed everywhere, and ACCESS-5 would prove nothing. Both hold ORG_ADMIN and no
 * clinical role, and neither holds any Keycloak realm role — a facility administrator is not a
 * platform administrator.
 *
 * ── State, and how it is restored ──
 *
 * Each test restores what it changed, in a `finally`, through the product's own inverse operation
 * wherever one exists. A run killed part way through can still leave a membership inactive or a
 * role missing; `scripts/demo/reset.sh` now genuinely recovers from that, which it could not before
 * this ticket (see `adapter/tests/test_demo_seed.py`'s `test_bs5_...`).
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
const ADMIN = {
  username: process.env.DEMO_ADMIN_USERNAME ?? 'demo-admin',
  password: process.env.DEMO_ADMIN_PASSWORD ?? '',
}
const LAB_ADMIN = {
  username: process.env.DEMO_LAB_ADMIN_USERNAME ?? 'demo-lab-admin',
  password: process.env.DEMO_LAB_ADMIN_PASSWORD ?? '',
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

interface StaffRow {
  membership_id: string
  identity_id: string
  display_name: string | null
  email: string | null
  status: string
  roles: string[]
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

// -- the four supported operations, as an administrator's browser calls them ----------------------

async function listStaff(token: string, organizationId: string) {
  const ctx = await api()
  try {
    const response = await ctx.get(`${API}/organizations/${organizationId}/memberships`, {
      headers: auth(token),
    })
    return { status: response.status(), body: response.ok() ? ((await response.json()) as StaffRow[]) : [] }
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

async function put(token: string, path: string) {
  const ctx = await api()
  try {
    const response = await ctx.put(`${API}${path}`, { headers: auth(token) })
    return { status: response.status(), text: await response.text() }
  } finally {
    await ctx.dispose()
  }
}

async function del(token: string, path: string) {
  const ctx = await api()
  try {
    const response = await ctx.delete(`${API}${path}`, { headers: auth(token) })
    return { status: response.status(), text: await response.text() }
  } finally {
    await ctx.dispose()
  }
}

const deactivate = (token: string, org: string, membership: string) =>
  post(token, `/organizations/${org}/memberships/${membership}/deactivate`)
const reactivate = (token: string, org: string, membership: string) =>
  post(token, `/organizations/${org}/memberships/${membership}/reactivate`)
const addRole = (token: string, org: string, membership: string, role: string) =>
  put(token, `/organizations/${org}/memberships/${membership}/roles/${role}`)
const removeRole = (token: string, org: string, membership: string, role: string) =>
  del(token, `/organizations/${org}/memberships/${membership}/roles/${role}`)

/** A protected read the persona under test is authorized for before its authority is withdrawn. */
async function get(token: string, path: string): Promise<number> {
  const ctx = await api()
  try {
    return (await ctx.get(`${API}${path}`, { headers: auth(token) })).status()
  } finally {
    await ctx.dispose()
  }
}

function rowFor(staff: StaffRow[], identityId: string): StaffRow {
  const found = staff.find((row) => row.identity_id === identityId)
  if (!found) throw new Error(`identity ${identityId} is not in this organization's staff list`)
  return found
}

/** Reads the authoritative transactional audit table. A *read*, never a cause. */
function auditRows(eventType: string, membershipId: string): number {
  const out = exchangeSql(
    `SELECT count(*) FROM exchange_security_audit_events
      WHERE event_type = '${eventType}' AND membership_id = '${membershipId}'`,
  )
  return Number.parseInt(out, 10)
}

function membershipStatusInDb(membershipId: string): string {
  return exchangeSql(
    `SELECT status FROM exchange_organization_memberships WHERE id = '${membershipId}'`,
  )
}

test.describe.configure({ mode: 'serial' })

// ================================================================================================
// The administrator can reach the surface at all
// ================================================================================================

test.describe('ACCESS-0: the administration surface is reachable', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('ACCESS-0a: an ORG_ADMIN-only account lands on Équipe et accès in a real browser', async ({
    page,
  }) => {
    // This is the P1 in its user-facing form. Before this ticket, `isUsableMembership` excluded an
    // ORG_ADMIN-only membership, so this exact account reached "Aucun établissement actif n'est
    // rattaché à votre compte" -- a facility's administrator holding real, server-honoured
    // authority and unable to open one screen with it.
    await signIn(page, ADMIN)
    const nav = page.getByRole('navigation', { name: 'Navigation principale' })
    await expect(nav.getByRole('button', { name: 'Équipe et accès' })).toBeVisible()
    await expect(
      page.getByText("Aucun établissement actif n'est rattaché à votre compte"),
    ).toHaveCount(0)

    // No clinical section, because ORG_ADMIN is not clinical authority.
    for (const absent of ["Aujourd'hui", 'Patients', 'Ordres', 'Résultats']) {
      await expect(nav.getByRole('button', { name: absent, exact: true })).toHaveCount(0)
    }

    // The screen itself, rendered: the staff of this establishment and what each may do.
    await expect(page.getByRole('heading', { name: 'Équipe et accès' })).toBeVisible()
    await expect(page.getByText(/Accès actif/).first()).toBeVisible()
  })

  test('ACCESS-0b: the surface shows this organization only, and nothing clinical', async ({
    page,
  }) => {
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')
    const { status, body } = await listStaff(admin.token, clinic.organization_id)
    expect(status).toBe(200)
    expect(body.length).toBeGreaterThan(0)

    // Every row is a membership of this organization, proved against the database rather than
    // trusted from the response shape.
    for (const row of body) {
      const owner = exchangeSql(
        `SELECT organization_id FROM exchange_organization_memberships WHERE id = '${row.membership_id}'`,
      )
      expect(owner).toBe(clinic.organization_id)
    }
    // Nothing credential-shaped and nothing clinical in the payload.
    const text = JSON.stringify(body).toLowerCase()
    for (const forbidden of ['password', 'secret', 'token', 'issuer', 'subject', 'patient']) {
      expect(text).not.toContain(forbidden)
    }
  })
})

// ================================================================================================
// ACCESS-1 / ACCESS-2: role revocation, same still-valid token
// ================================================================================================

test.describe('ACCESS-1/2: role revocation takes effect on the next request', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('ACCESS-1: removing CLINICIAN refuses the provider action with the same token', async ({
    page,
    browser,
  }) => {
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')

    // The clinician signs in for real, in their own browser context, and keeps their token.
    const clinicianContext = await browser.newContext({ ignoreHTTPSErrors: true })
    const clinicianPage = await clinicianContext.newPage()
    const clinician = await signIn(clinicianPage, CLINICIAN)

    const staff = await listStaff(admin.token, clinic.organization_id)
    const row = rowFor(staff.body, clinician.identityId)
    expect(row.roles).toContain('CLINICIAN')

    const auditBefore = auditRows('role_removed', row.membership_id)

    try {
      // Authority confirmed FIRST, so a later refusal cannot be a route that never worked.
      expect(await get(clinician.token, `/organizations/${clinic.organization_id}/orders`)).toBe(200)

      // Withdrawn through the product, by a real administrator.
      const removed = await removeRole(
        admin.token,
        clinic.organization_id,
        row.membership_id,
        'CLINICIAN',
      )
      expect(removed.status, removed.text).toBe(200)

      // The IDENTICAL token. No refresh, no re-login, no page reload.
      const after = await get(clinician.token, `/organizations/${clinic.organization_id}/orders`)
      // 403, not 404: the membership is still ACTIVE, so the caller genuinely belongs to the
      // organization and is refused the specific authority.
      expect(after).toBe(403)

      // And the browser that was already signed in cannot reach the workflow either. Reloading
      // does not re-authenticate -- the session is still valid; the authority is not.
      await clinicianPage.reload()
      const clinicianNav = clinicianPage.getByRole('navigation', { name: 'Navigation principale' })
      await expect(clinicianNav.getByRole('button', { name: 'Patients' })).toHaveCount(0)

      expect(auditRows('role_removed', row.membership_id)).toBe(auditBefore + 1)
    } finally {
      await addRole(admin.token, clinic.organization_id, row.membership_id, 'CLINICIAN')
      await clinicianContext.close()
    }
  })

  test('ACCESS-2: removing LAB_RESULT_RELEASER refuses release with the same token', async ({
    page,
    browser,
  }) => {
    const labAdmin = await signIn(page, LAB_ADMIN)
    const lab = orgOf(labAdmin, 'LAB')

    const releaserContext = await browser.newContext({ ignoreHTTPSErrors: true })
    const releaserPage = await releaserContext.newPage()
    const releaser = await signIn(releaserPage, RELEASER)

    const staff = await listStaff(labAdmin.token, lab.organization_id)
    const row = rowFor(staff.body, releaser.identityId)
    expect(row.roles).toContain('LAB_RESULT_RELEASER')

    try {
      expect(await get(releaser.token, `/organizations/${lab.organization_id}/release-queue`)).toBe(
        200,
      )

      const removed = await removeRole(
        labAdmin.token,
        lab.organization_id,
        row.membership_id,
        'LAB_RESULT_RELEASER',
      )
      expect(removed.status, removed.text).toBe(200)

      expect(await get(releaser.token, `/organizations/${lab.organization_id}/release-queue`)).toBe(
        403,
      )
    } finally {
      await addRole(labAdmin.token, lab.organization_id, row.membership_id, 'LAB_RESULT_RELEASER')
      await releaserContext.close()
    }
  })

  test('ACCESS-8: a role ADDED mid-session is honoured on the next request too', async ({
    page,
    browser,
  }) => {
    // The model is symmetric, which is what makes it DB-derived rather than token-derived. SEC-9
    // observed this with direct SQL; here the grant goes through the product.
    const labAdmin = await signIn(page, LAB_ADMIN)
    const lab = orgOf(labAdmin, 'LAB')

    const techContext = await browser.newContext({ ignoreHTTPSErrors: true })
    const techPage = await techContext.newPage()
    const tech = await signIn(techPage, LAB_TECH)

    const staff = await listStaff(labAdmin.token, lab.organization_id)
    const row = rowFor(staff.body, tech.identityId)
    expect(row.roles).not.toContain('LAB_RESULT_RELEASER')

    try {
      expect(await get(tech.token, `/organizations/${lab.organization_id}/release-queue`)).toBe(403)

      const added = await addRole(
        labAdmin.token,
        lab.organization_id,
        row.membership_id,
        'LAB_RESULT_RELEASER',
      )
      expect(added.status, added.text).toBe(200)

      expect(await get(tech.token, `/organizations/${lab.organization_id}/release-queue`)).toBe(200)
    } finally {
      await removeRole(labAdmin.token, lab.organization_id, row.membership_id, 'LAB_RESULT_RELEASER')
      await techContext.close()
    }
  })
})

// ================================================================================================
// ACCESS-3 / ACCESS-4: membership deactivation and reactivation
// ================================================================================================

test.describe('ACCESS-3/4: membership deactivation and reactivation', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('ACCESS-3: deactivation refuses the organization with the same token', async ({
    page,
    browser,
  }) => {
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')

    const clinicianContext = await browser.newContext({ ignoreHTTPSErrors: true })
    const clinicianPage = await clinicianContext.newPage()
    const clinician = await signIn(clinicianPage, CLINICIAN)

    const staff = await listStaff(admin.token, clinic.organization_id)
    const row = rowFor(staff.body, clinician.identityId)
    const auditBefore = auditRows('membership_deactivated', row.membership_id)

    try {
      expect(await get(clinician.token, `/organizations/${clinic.organization_id}/orders`)).toBe(200)

      const withdrawn = await deactivate(admin.token, clinic.organization_id, row.membership_id)
      expect(withdrawn.status, withdrawn.text).toBe(200)
      expect(membershipStatusInDb(row.membership_id)).toBe('INACTIVE')

      // Same token. 404 now, not 403: the identity has no ACTIVE relationship to this
      // organization at all, which is indistinguishable from the organization not existing.
      expect(await get(clinician.token, `/organizations/${clinic.organization_id}/orders`)).toBe(404)

      // §24: the row survives and so does every clinical attribution hanging off it.
      expect(
        Number.parseInt(
          exchangeSql(
            `SELECT count(*) FROM exchange_organization_memberships WHERE id = '${row.membership_id}'`,
          ),
          10,
        ),
      ).toBe(1)

      // §3: the Keycloak identity is untouched. They can still sign in, and /me still answers.
      expect(await get(clinician.token, '/me')).toBe(200)
      const freshContext = await browser.newContext({ ignoreHTTPSErrors: true })
      const freshPage = await freshContext.newPage()
      try {
        // A *new* real login with the same Keycloak account still succeeds -- deactivating an
        // organization membership is emphatically not disabling a user.
        const again = await signIn(freshPage, CLINICIAN)
        const stillListed = again.memberships.find(
          (m) => m.organization_id === clinic.organization_id,
        )
        expect(stillListed?.membership_status).toBe('INACTIVE')
      } finally {
        await freshContext.close()
      }

      expect(auditRows('membership_deactivated', row.membership_id)).toBe(auditBefore + 1)
    } finally {
      await reactivate(admin.token, clinic.organization_id, row.membership_id)
      await clinicianContext.close()
    }
  })

  test('ACCESS-4: reactivation restores the same membership and its surviving roles', async ({
    page,
    browser,
  }) => {
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')

    const clinicianContext = await browser.newContext({ ignoreHTTPSErrors: true })
    const clinicianPage = await clinicianContext.newPage()
    const clinician = await signIn(clinicianPage, CLINICIAN)

    const staff = await listStaff(admin.token, clinic.organization_id)
    const row = rowFor(staff.body, clinician.identityId)
    const rolesBefore = [...row.roles].sort()

    try {
      expect((await deactivate(admin.token, clinic.organization_id, row.membership_id)).status).toBe(
        200,
      )

      const restored = await reactivate(admin.token, clinic.organization_id, row.membership_id)
      expect(restored.status, restored.text).toBe(200)
      const body = JSON.parse(restored.text) as StaffRow

      // The SAME row, never a duplicate: identity continuity and audit history survive.
      expect(body.membership_id).toBe(row.membership_id)
      expect(body.status).toBe('ACTIVE')
      // The documented (not chosen) semantic: roles survive deactivation, so reactivation restores
      // exactly what is still attached. The response states them for this reason.
      expect([...body.roles].sort()).toEqual(rolesBefore)
      expect(
        Number.parseInt(
          exchangeSql(
            `SELECT count(*) FROM exchange_organization_memberships
              WHERE organization_id = '${clinic.organization_id}'
                AND identity_id = '${clinician.identityId}'`,
          ),
          10,
        ),
      ).toBe(1)

      // And the restored authority is real, on the next request with the original token.
      expect(await get(clinician.token, `/organizations/${clinic.organization_id}/orders`)).toBe(200)
      expect(auditRows('membership_reactivated', row.membership_id)).toBeGreaterThan(0)
    } finally {
      await reactivate(admin.token, clinic.organization_id, row.membership_id)
      await clinicianContext.close()
    }
  })
})

// ================================================================================================
// ACCESS-5: the cross-organization boundary
// ================================================================================================

test.describe('ACCESS-5: cross-organization boundary', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('ACCESS-5: Clinique A’s administrator cannot touch Laboratoire B, knowing every UUID', async ({
    page,
    browser,
  }) => {
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')

    const labAdminContext = await browser.newContext({ ignoreHTTPSErrors: true })
    const labAdminPage = await labAdminContext.newPage()
    const labAdmin = await signIn(labAdminPage, LAB_ADMIN)
    const lab = orgOf(labAdmin, 'LAB')

    try {
      // Every id below is REAL and known to the caller: the laboratory's organization UUID, a real
      // membership UUID inside it, and the user's identity UUID.
      const labStaff = await listStaff(labAdmin.token, lab.organization_id)
      expect(labStaff.status).toBe(200)
      const target = labStaff.body.find((r) => r.roles.includes('LAB_TECH'))
      expect(target, 'the demo laboratory must have a LAB_TECH').toBeTruthy()
      const membershipId = target!.membership_id

      // Listing: refused, with the no-relationship shape.
      const listed = await listStaff(admin.token, lab.organization_id)
      expect(listed.status).toBe(404)

      // All four mutations: refused.
      const attempts = [
        await deactivate(admin.token, lab.organization_id, membershipId),
        await reactivate(admin.token, lab.organization_id, membershipId),
        await addRole(admin.token, lab.organization_id, membershipId, 'LAB_RESULT_RELEASER'),
        await removeRole(admin.token, lab.organization_id, membershipId, 'LAB_TECH'),
      ]
      for (const attempt of attempts) {
        expect(attempt.status, attempt.text).toBe(404)
        // No leakage: the body must not confirm the organization, the membership, or that the row
        // exists somewhere else.
        expect(attempt.text).not.toContain(lab.organization_id)
        expect(attempt.text).not.toContain(membershipId)
        expect(attempt.text.toLowerCase()).not.toContain('another organization')
      }

      // The narrower leak, and the one the service's own ownership check exists for: the caller
      // names an organization they legitimately administer, while pointing at the *other*
      // organization's membership id. The organization check passes; ownership must refuse.
      const crossed = await deactivate(admin.token, clinic.organization_id, membershipId)
      expect(crossed.status, crossed.text).toBe(404)
      expect(JSON.parse(crossed.text).detail).toBe('membership not found')

      // A fabricated membership id answers identically, so the response cannot be used as an
      // existence oracle.
      const fabricated = await deactivate(
        admin.token,
        clinic.organization_id,
        '00000000-0000-4000-8000-000000000000',
      )
      expect(fabricated.status).toBe(crossed.status)
      expect(fabricated.text).toBe(crossed.text)

      // Nothing happened to the laboratory's membership.
      expect(membershipStatusInDb(membershipId)).toBe('ACTIVE')
    } finally {
      await labAdminContext.close()
    }
  })

  test('ACCESS-5b: a clinician and a lab technician are refused the surface outright', async ({
    page,
    browser,
  }) => {
    // §17: hiding the navigation item is convenience. A hand-typed request must still be refused.
    const clinician = await signIn(page, CLINICIAN)
    const clinic = orgOf(clinician, 'PROVIDER')

    const techContext = await browser.newContext({ ignoreHTTPSErrors: true })
    const techPage = await techContext.newPage()
    const tech = await signIn(techPage, LAB_TECH)
    const lab = orgOf(tech, 'LAB')

    try {
      // Members of the organization who lack ORG_ADMIN: 403, because they legitimately belong.
      expect((await listStaff(clinician.token, clinic.organization_id)).status).toBe(403)
      expect((await listStaff(tech.token, lab.organization_id)).status).toBe(403)

      const adminContext = await browser.newContext({ ignoreHTTPSErrors: true })
      const adminPage = await adminContext.newPage()
      try {
        const admin = await signIn(adminPage, ADMIN)
        const staff = await listStaff(admin.token, clinic.organization_id)
        const self = rowFor(staff.body, admin.identityId)
        // A clinician calling the mutation endpoints directly: refused, and nothing changes.
        expect(
          (await removeRole(clinician.token, clinic.organization_id, self.membership_id, 'ORG_ADMIN'))
            .status,
        ).toBe(403)
        expect(
          (await deactivate(clinician.token, clinic.organization_id, self.membership_id)).status,
        ).toBe(403)
        expect(membershipStatusInDb(self.membership_id)).toBe('ACTIVE')
      } finally {
        await adminContext.close()
      }
    } finally {
      await techContext.close()
    }
  })
})

// ================================================================================================
// ACCESS-6 / ACCESS-7: administrative lockout, and safe handover
// ================================================================================================

test.describe('ACCESS-6/7: last-administrator protection and handover', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('ACCESS-6: the only administrator cannot remove their own ORG_ADMIN or deactivate themselves', async ({
    page,
  }) => {
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')
    const staff = await listStaff(admin.token, clinic.organization_id)
    const self = rowFor(staff.body, admin.identityId)

    // The premise, asserted rather than assumed: exactly one active administrator here.
    const activeAdmins = staff.body.filter(
      (r) => r.status === 'ACTIVE' && r.roles.includes('ORG_ADMIN'),
    )
    expect(activeAdmins.map((r) => r.membership_id)).toEqual([self.membership_id])

    // A DELTA, never an absolute count. `exchange_security_audit_events` is deliberately NOT in
    // `demo_seed`'s `_WORKFLOW_TABLES`: a security audit row records that authority changed, and a
    // demo reset is not a reason to erase it. So rows from an earlier run legitimately survive, and
    // the first draft of this test asserted `toBe(0)` -- which passed on a fresh database and went
    // red on the second run, for a reason that was the audit table behaving correctly.
    const deactivationAuditBefore = auditRows('membership_deactivated', self.membership_id)

    const roleRemoval = await removeRole(
      admin.token,
      clinic.organization_id,
      self.membership_id,
      'ORG_ADMIN',
    )
    expect(roleRemoval.status, roleRemoval.text).toBe(409)
    expect(JSON.parse(roleRemoval.text).detail).toContain('no active administrator')

    const selfWithdrawal = await deactivate(admin.token, clinic.organization_id, self.membership_id)
    expect(selfWithdrawal.status, selfWithdrawal.text).toBe(409)

    // A rolled-back transaction, not a half-applied one: the authority genuinely survives both
    // refusals, and the administrator is still an administrator on the next request.
    expect(membershipStatusInDb(self.membership_id)).toBe('ACTIVE')
    expect((await listStaff(admin.token, clinic.organization_id)).status).toBe(200)
    // And no NEW audit row claims a change that did not happen.
    expect(auditRows('membership_deactivated', self.membership_id)).toBe(deactivationAuditBefore)
  })

  test('ACCESS-7: a safe handover is possible, and the organization keeps an administrator throughout', async ({
    page,
    browser,
  }) => {
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')

    const clinicianContext = await browser.newContext({ ignoreHTTPSErrors: true })
    const clinicianPage = await clinicianContext.newPage()
    const clinician = await signIn(clinicianPage, CLINICIAN)

    const staff = await listStaff(admin.token, clinic.organization_id)
    const self = rowFor(staff.body, admin.identityId)
    const successor = rowFor(staff.body, clinician.identityId)

    try {
      // ORG_ADMIN is assignable by an organization administrator under the EXISTING policy
      // (`ALLOWED_ROLES_BY_ORG_TYPE` lists it for both organization types), so succession needs no
      // boundary to be weakened.
      const promoted = await addRole(
        admin.token,
        clinic.organization_id,
        successor.membership_id,
        'ORG_ADMIN',
      )
      expect(promoted.status, promoted.text).toBe(200)

      // Two active administrators now, so the first may step down.
      const steppedDown = await removeRole(
        admin.token,
        clinic.organization_id,
        self.membership_id,
        'ORG_ADMIN',
      )
      expect(steppedDown.status, steppedDown.text).toBe(200)

      // Same token, immediately: the outgoing administrator has genuinely lost the surface.
      expect((await listStaff(admin.token, clinic.organization_id)).status).toBe(403)

      // The successor holds it, and the organization never had zero administrators.
      const after = await listStaff(clinician.token, clinic.organization_id)
      expect(after.status).toBe(200)
      const admins = after.body.filter(
        (r) => r.status === 'ACTIVE' && r.roles.includes('ORG_ADMIN'),
      )
      expect(admins.map((r) => r.membership_id)).toEqual([successor.membership_id])

      // The successor is now the last administrator, and is protected in turn.
      expect(
        (
          await removeRole(
            clinician.token,
            clinic.organization_id,
            successor.membership_id,
            'ORG_ADMIN',
          )
        ).status,
      ).toBe(409)

      // Hand it back, through the product.
      expect(
        (await addRole(clinician.token, clinic.organization_id, self.membership_id, 'ORG_ADMIN'))
          .status,
      ).toBe(200)
      expect(
        (
          await removeRole(
            admin.token,
            clinic.organization_id,
            successor.membership_id,
            'ORG_ADMIN',
          )
        ).status,
      ).toBe(200)
    } finally {
      // Whatever happened above, restore the baseline: the demo administrator is an administrator
      // and the clinician is not.
      await addRole(admin.token, clinic.organization_id, self.membership_id, 'ORG_ADMIN').catch(
        () => undefined,
      )
      await removeRole(
        admin.token,
        clinic.organization_id,
        successor.membership_id,
        'ORG_ADMIN',
      ).catch(() => undefined)
      await clinicianContext.close()
    }
  })

  test('ACCESS-9: a deactivated administrator cannot keep administering with their stale token', async ({
    page,
    browser,
  }) => {
    // §14. Needs two administrators to set up at all, because deactivating the only one is refused
    // -- which is itself the point of ACCESS-6.
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')

    const clinicianContext = await browser.newContext({ ignoreHTTPSErrors: true })
    const clinicianPage = await clinicianContext.newPage()
    const clinician = await signIn(clinicianPage, CLINICIAN)

    const staff = await listStaff(admin.token, clinic.organization_id)
    const self = rowFor(staff.body, admin.identityId)
    const second = rowFor(staff.body, clinician.identityId)

    try {
      expect(
        (await addRole(admin.token, clinic.organization_id, second.membership_id, 'ORG_ADMIN'))
          .status,
      ).toBe(200)

      // The second administrator withdraws the first one's access.
      const withdrawn = await deactivate(
        clinician.token,
        clinic.organization_id,
        self.membership_id,
      )
      expect(withdrawn.status, withdrawn.text).toBe(200)

      // The first administrator's token is still cryptographically valid and still held by a
      // signed-in browser. It must administer nothing.
      expect((await listStaff(admin.token, clinic.organization_id)).status).toBe(404)
      expect(
        (await removeRole(admin.token, clinic.organization_id, second.membership_id, 'ORG_ADMIN'))
          .status,
      ).toBe(404)
      // Above all: they must not be able to let themselves back in.
      const selfReactivation = await reactivate(
        admin.token,
        clinic.organization_id,
        self.membership_id,
      )
      expect(selfReactivation.status, selfReactivation.text).toBe(404)
      expect(membershipStatusInDb(self.membership_id)).toBe('INACTIVE')
    } finally {
      // Restored by the second administrator, who legitimately still holds the authority.
      await reactivate(clinician.token, clinic.organization_id, self.membership_id).catch(
        () => undefined,
      )
      await removeRole(
        clinician.token,
        clinic.organization_id,
        second.membership_id,
        'ORG_ADMIN',
      ).catch(() => undefined)
      await clinicianContext.close()
    }
  })
})

// ================================================================================================
// Platform authority, errors, and audit
// ================================================================================================

test.describe('ACCESS-10: platform authority cannot be manufactured', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('ACCESS-10: an ORG_ADMIN cannot grant PLATFORM_ADMIN or any invented role', async ({
    page,
  }) => {
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')
    const staff = await listStaff(admin.token, clinic.organization_id)
    const self = rowFor(staff.body, admin.identityId)
    const rolesBefore = [...self.roles].sort()

    for (const forbidden of [
      'PLATFORM_ADMIN',
      'dhealth-platform-admin',
      'SUPER_ADMIN',
      'OWNER',
      // A real organization role this organization's TYPE may not hold.
      'LAB_TECH',
    ]) {
      const attempt = await addRole(admin.token, clinic.organization_id, self.membership_id, forbidden)
      expect(attempt.status, `${forbidden}: ${attempt.text}`).toBe(422)
    }

    // Nothing was written, and the /me view of this identity still carries no platform authority.
    //
    // A DELTA against the roles held before the attempts, not `toEqual(['ORG_ADMIN'])`. The absolute
    // form couples this assertion to a pristine baseline, and `scripts/demo/reset.sh` cannot
    // guarantee one: it converges the declared roles and never removes an extra grant, so a stray
    // role survives a reset. NC-ACCESS-5 left exactly such a row behind and this test then failed
    // for a reason that had nothing to do with the code under test. Same fix, same reason, as
    // ACCESS-6's audit-row count.
    const rolesAfter = [
      ...(await listStaff(admin.token, clinic.organization_id)).body.find(
        (r) => r.membership_id === self.membership_id,
      )!.roles,
    ].sort()
    expect(rolesAfter).toEqual(rolesBefore)
    const me = await api()
    try {
      const body = await (
        await me.get(`${API}/me`, { headers: auth(admin.token) })
      ).json()
      expect(body.platform_admin).toBe(false)
    } finally {
      await me.dispose()
    }
  })

  test('ACCESS-11: state conflicts are reported truthfully and change nothing', async ({ page }) => {
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')
    const staff = await listStaff(admin.token, clinic.organization_id)
    const self = rowFor(staff.body, admin.identityId)
    const target = staff.body.find(
      (r) => r.membership_id !== self.membership_id && r.status === 'ACTIVE',
    )
    expect(target, 'this test needs a second active member').toBeTruthy()
    const membershipId = target!.membership_id

    try {
      // Already active.
      expect((await reactivate(admin.token, clinic.organization_id, membershipId)).status).toBe(409)
      // Role not held.
      expect(
        (await removeRole(admin.token, clinic.organization_id, membershipId, 'ORG_ADMIN')).status,
      ).toBe(409)
      // Already inactive.
      expect((await deactivate(admin.token, clinic.organization_id, membershipId)).status).toBe(200)
      expect((await deactivate(admin.token, clinic.organization_id, membershipId)).status).toBe(409)
      // A role cannot be granted to an inactive membership -- the existing model, unchanged.
      expect(
        (await addRole(admin.token, clinic.organization_id, membershipId, 'ORG_ADMIN')).status,
      ).toBe(409)
      // But a role can be REMOVED from one, which is how somebody is restored with less authority.
      const heldRoles = target!.roles
      if (heldRoles.length > 0) {
        expect(
          (await removeRole(admin.token, clinic.organization_id, membershipId, heldRoles[0])).status,
        ).toBe(200)
        expect(
          (await addRole(admin.token, clinic.organization_id, membershipId, heldRoles[0])).status,
          'a role added to an inactive membership must still be refused',
        ).toBe(409)
      }
    } finally {
      await reactivate(admin.token, clinic.organization_id, membershipId).catch(() => undefined)
      for (const role of target!.roles) {
        await addRole(admin.token, clinic.organization_id, membershipId, role).catch(() => undefined)
      }
    }
  })

  test('ACCESS-12: every successful mutation is audited, and no refusal is', async ({ page }) => {
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')
    const staff = await listStaff(admin.token, clinic.organization_id)
    const self = rowFor(staff.body, admin.identityId)
    const target = staff.body.find(
      (r) => r.membership_id !== self.membership_id && r.status === 'ACTIVE',
    )!
    const membershipId = target.membership_id

    const before = {
      assigned: auditRows('role_assigned', membershipId),
      removed: auditRows('role_removed', membershipId),
      deactivated: auditRows('membership_deactivated', membershipId),
      reactivated: auditRows('membership_reactivated', membershipId),
    }
    const refusalLogBefore = auditLineCount()

    try {
      expect((await addRole(admin.token, clinic.organization_id, membershipId, 'ORG_ADMIN')).status)
        .toBe(200)
      expect(
        (await removeRole(admin.token, clinic.organization_id, membershipId, 'ORG_ADMIN')).status,
      ).toBe(200)
      expect((await deactivate(admin.token, clinic.organization_id, membershipId)).status).toBe(200)
      expect((await reactivate(admin.token, clinic.organization_id, membershipId)).status).toBe(200)

      expect(auditRows('role_assigned', membershipId)).toBe(before.assigned + 1)
      expect(auditRows('role_removed', membershipId)).toBe(before.removed + 1)
      expect(auditRows('membership_deactivated', membershipId)).toBe(before.deactivated + 1)
      expect(auditRows('membership_reactivated', membershipId)).toBe(before.reactivated + 1)

      // Each row carries what §19 requires: actor, organization, subject, and a timestamp.
      // `true`, not `t`: a boolean concatenated with `||` renders as the full word, unlike the `t`
      // psql prints for a bare boolean column. The first draft asserted `t` and went red for that
      // reason alone, with the three identifiers already correct.
      const detail = exchangeSql(
        `SELECT actor_identity_id || '|' || organization_id || '|' || subject_identity_id
           || '|' || (created_at IS NOT NULL)
           FROM exchange_security_audit_events
          WHERE event_type = 'membership_deactivated' AND membership_id = '${membershipId}'
          ORDER BY created_at DESC LIMIT 1`,
      )
      expect(detail).toBe(
        `${admin.identityId}|${clinic.organization_id}|${target.identity_id}|true`,
      )

      // A REFUSAL writes no row in the authoritative table. The last-administrator refusal in
      // particular deliberately does not reach the file-backed refusal writer either: the actor is
      // authorized, and calling it `authorization_refused` would misreport that.
      const refusedBefore = auditRows('role_removed', self.membership_id)
      expect(
        (await removeRole(admin.token, clinic.organization_id, self.membership_id, 'ORG_ADMIN'))
          .status,
      ).toBe(409)
      expect(auditRows('role_removed', self.membership_id)).toBe(refusedBefore)
      expect(auditLineCount(), 'a 409 is not an authorization refusal').toBe(refusalLogBefore)
    } finally {
      await reactivate(admin.token, clinic.organization_id, membershipId).catch(() => undefined)
      await removeRole(admin.token, clinic.organization_id, membershipId, 'ORG_ADMIN').catch(
        () => undefined,
      )
    }
  })
})

// ================================================================================================
// The UI, driven for real
// ================================================================================================

test.describe('ACCESS-13: the administrator withdraws access through the screen', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('ACCESS-13: a real click withdraws a real colleague, with a confirmation that names them', async ({
    page,
    browser,
  }) => {
    // The completion standard is a *supported product path*, not another endpoint. This drives the
    // actual screen: find the colleague, click, read the restatement, confirm, and then observe the
    // consequence on a token that was issued before any of it happened.
    const admin = await signIn(page, ADMIN)
    const clinic = orgOf(admin, 'PROVIDER')

    const clinicianContext = await browser.newContext({ ignoreHTTPSErrors: true })
    const clinicianPage = await clinicianContext.newPage()
    const clinician = await signIn(clinicianPage, CLINICIAN)
    const staff = await listStaff(admin.token, clinic.organization_id)
    const row = rowFor(staff.body, clinician.identityId)

    try {
      expect(await get(clinician.token, `/organizations/${clinic.organization_id}/orders`)).toBe(200)

      await page.getByRole('navigation', { name: 'Navigation principale' })
        .getByRole('button', { name: 'Équipe et accès' })
        .click()
      await expect(page.getByRole('heading', { name: 'Équipe et accès' })).toBeVisible()

      const personRow = page.locator('li', {
        has: page.getByText(row.display_name ?? row.email ?? ''),
      }).first()
      await personRow.getByRole('button', { name: "Retirer l'accès" }).click()

      // The restatement names the person and the establishment, and never says "supprimé".
      const panel = page.getByRole('region', { name: /Retirer l'accès/ })
      await expect(panel).toContainText(row.display_name ?? '')
      await expect(panel).toContainText(clinic.organization_name)
      await expect(panel).not.toContainText(/supprim/i)
      await expect(panel).not.toContainText(/désactiv/i)

      await panel.getByRole('button', { name: "Retirer l'accès" }).click()

      await expect(page.getByRole('status')).toContainText(
        `n'a plus accès à ${clinic.organization_name}`,
      )
      expect(membershipStatusInDb(row.membership_id)).toBe('INACTIVE')

      // The colleague's already-signed-in browser, holding the token it was issued before the
      // click: refused on the next request.
      expect(await get(clinician.token, `/organizations/${clinic.organization_id}/orders`)).toBe(404)
    } finally {
      await reactivate(admin.token, clinic.organization_id, row.membership_id).catch(
        () => undefined,
      )
      await clinicianContext.close()
    }
  })
})
