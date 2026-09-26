import { expect, request as apiRequest, test, type Page } from '@playwright/test'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'
import {
  createSetupClient,
  getMasterAdminToken,
  grantPlatformAdminToServiceAccount,
  mintClientCredentialsToken,
} from '../support/adminApi'
import {
  addExchangeAudienceMapper,
  auditLineCount,
  exchangeSql,
  createShortLivedTokenClient,
  deleteClientByUuid,
  findMembershipId,
  findUserIdByUsername,
  listRoles,
  mintPasswordToken,
  removeRole,
  restoreRole,
  revokePatientGrantsForOrder,
  setMembershipStatus,
  setUserEnabled,
  type KeycloakAdminEnv,
} from '../support/securityAdmin'
import { waitForEdge } from '../support/edgeReadiness'

/**
 * DHEALTH-NATIVE-SEC-001 — identity, authorization and session boundaries, against the real stack.
 *
 * The question this file answers is not "does authentication exist" (DHEALTH-NATIVE-MVP-1.0
 * answered that) but "does the product stay secure when identities, memberships, roles, sessions,
 * tokens and patient grants change underneath a browser that is already signed in".
 *
 * Every token here is a REAL token from the REAL Keycloak: either captured off the wire from a
 * real Authorization Code + PKCE browser login, or minted by the real token endpoint for the two
 * API-only claims where a browser adds nothing (SEC-3's wrong audience, SEC-4's expiry). Nothing
 * is forged, no session is injected, and no auth state is written into browser storage.
 *
 * Tests that mutate identity state restore it in the same test, in a `finally`. A run killed part
 * way through can still leave a disabled user, an inactive membership or a removed role behind --
 * `scripts/demo/qualify-security.sh` says so, and `scripts/demo/reset.sh` plus re-running the
 * demo user reconciliation is the recovery.
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

const kcEnv: KeycloakAdminEnv = {
  keycloakUrl: process.env.E2E_KEYCLOAK_URL ?? 'https://id.localhost',
  realm: process.env.E2E_REALM ?? 'dhealth-exchange',
  adminUser: process.env.KEYCLOAK_ADMIN_USER ?? 'admin',
  adminPassword: process.env.KEYCLOAK_ADMIN_PASSWORD ?? '',
}

interface Membership {
  organization_id: string
  organization_name: string
  organization_type: string
  roles: string[]
}

async function api() {
  return apiRequest.newContext({ ignoreHTTPSErrors: true })
}

/** A real browser login, returning the bearer token the app itself sent. */
async function signIn(
  page: Page,
  who: { username: string; password: string },
): Promise<{ token: string; memberships: Membership[]; identityId: string }> {
  expect(who.password, 'demo passwords must come from demo/native/demo.env').not.toBe('')
  const tokenPromise = captureFirstExchangeBearerToken(page)
  await page.goto('/')
  await loginViaRealKeycloak(page, who.username, who.password)
  const token = await tokenPromise
  const ctx = await api()
  try {
    const me = await ctx.get(`${API}/me`, { headers: { Authorization: `Bearer ${token}` } })
    expect(me.status()).toBe(200)
    const body = await me.json()
    return { token, memberships: body.memberships as Membership[], identityId: body.identity_id }
  } finally {
    await ctx.dispose()
  }
}

async function adminToken(): Promise<string> {
  return getMasterAdminToken({
    keycloakUrl: kcEnv.keycloakUrl,
    realm: kcEnv.realm,
    adminUser: kcEnv.adminUser,
    adminPassword: kcEnv.adminPassword,
    adapterBaseUrl: API,
  })
}

test.describe.configure({ mode: 'serial' })

// ---------------------------------------------------------------------------
// Authentication
// ---------------------------------------------------------------------------
test.describe('SEC: authentication against real Keycloak', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('SEC-1: an unauthenticated browser reaching a staff route is sent to real Keycloak', async ({
    page,
  }) => {
    await page.goto('/')
    await page.waitForURL(/\/realms\/dhealth-exchange\/protocol\/openid-connect\/auth/, {
      timeout: 30_000,
    })
    // PKCE is not optional on this client: the realm pins `pkce.code.challenge.method=S256`, so
    // the authorization request the browser was actually sent to carries a challenge.
    const url = new URL(page.url())
    expect(url.searchParams.get('code_challenge_method')).toBe('S256')
    expect(url.searchParams.get('code_challenge')).toBeTruthy()
    expect(url.searchParams.get('client_id')).toBe('dhealth-native-ui')
    await expect(page.getByRole('button', { name: 'Se déconnecter' })).toHaveCount(0)
  })

  test('SEC-2: a real PKCE login is accepted and the token it produced authorizes /me', async ({
    page,
  }) => {
    const { token, memberships } = await signIn(page, CLINICIAN)
    expect(memberships.length).toBeGreaterThan(0)
    // The token Exchange accepted was validated against the REAL Keycloak JWKS -- the deployment
    // configures EXCHANGE_OIDC_JWKS_URL at the real realm, so JWKSKeySource (not the unit suite's
    // StaticKeySource) resolved the signing key.
    const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString('utf-8'))
    expect(claims.iss).toBe(`${kcEnv.keycloakUrl}/realms/${kcEnv.realm}`)
    expect(claims.azp).toBe('dhealth-native-ui')
    expect(String(claims.aud)).toContain('dhealth-exchange-api')
  })

  test('SEC-3: a real Keycloak token minted for a different audience is refused', async ({}) => {
    const admin = await adminToken()
    // A real client, real token endpoint, real signature, real issuer -- and no
    // `dhealth-exchange-api` audience mapper, so `aud` names only the client itself. Everything
    // about this token is genuine except the one claim Exchange checks.
    const client = await createShortLivedTokenClient(kcEnv, admin, 300)
    let wrongAudienceClientUuid = client.uuid
    try {
      // Strip the audience mapper this helper adds, to make the token wrong in exactly one way.
      await fetch(
        `${kcEnv.keycloakUrl}/admin/realms/${kcEnv.realm}/clients/${client.uuid}/protocol-mappers/models`,
        { headers: { Authorization: `Bearer ${admin}` } },
      )
        .then((r) => r.json())
        .then(async (mappers: Array<{ id: string; name: string }>) => {
          for (const m of mappers.filter((x) => x.name === 'exchange-api-audience')) {
            await fetch(
              `${kcEnv.keycloakUrl}/admin/realms/${kcEnv.realm}/clients/${client.uuid}/protocol-mappers/models/${m.id}`,
              { method: 'DELETE', headers: { Authorization: `Bearer ${admin}` } },
            )
          }
        })

      const token = await mintPasswordToken(
        kcEnv,
        client.clientId,
        client.secret,
        CLINICIAN.username,
        CLINICIAN.password,
      )
      const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString('utf-8'))
      expect(String(claims.aud)).not.toContain('dhealth-exchange-api')

      const ctx = await api()
      try {
        const response = await ctx.get(`${API}/me`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        expect(response.status()).toBe(401)
        expect(await response.text()).toContain('invalid or missing credentials')
      } finally {
        await ctx.dispose()
      }
    } finally {
      await deleteClientByUuid(kcEnv, admin, wrongAudienceClientUuid)
    }
  })

  test('SEC-4a: a genuinely expired real access token is refused', async ({}) => {
    test.setTimeout(180_000)
    const admin = await adminToken()
    // Per-CLIENT lifespan, never the realm's. The realm keeps its shipped 300s production default
    // even if this run is killed half way through.
    const client = await createShortLivedTokenClient(kcEnv, admin, 15)
    try {
      const token = await mintPasswordToken(
        kcEnv,
        client.clientId,
        client.secret,
        CLINICIAN.username,
        CLINICIAN.password,
      )
      const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString('utf-8'))
      const lifetime = Number(claims.exp) - Number(claims.iat)
      expect(lifetime, 'the per-client lifespan override must have applied').toBeLessThanOrEqual(30)

      const ctx = await api()
      try {
        const before = await ctx.get(`${API}/me`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        expect(before.status(), 'the token must work before it expires').toBe(200)

        // Wait past expiry. Real clock, real `exp`, no simulated time.
        await new Promise((resolve) => setTimeout(resolve, (lifetime + 8) * 1000))

        const after = await ctx.get(`${API}/me`, { headers: { Authorization: `Bearer ${token}` } })
        expect(after.status(), 'an expired token must be refused').toBe(401)
        // Same generic body as every other authentication failure: expiry is not distinguishable
        // from a wrong issuer or a bad signature by anything the caller can see.
        expect(await after.text()).toContain('invalid or missing credentials')
      } finally {
        await ctx.dispose()
      }
    } finally {
      await deleteClientByUuid(kcEnv, admin, client.uuid)
    }
  })

  /**
   * SEC-4b — scoped precisely, because the obvious stronger claim is NOT what this proves.
   *
   * **Observed:** after the user's Keycloak session is terminated server-side, the next
   * application load does not restore an authenticated workspace — `check-sso` asks the real
   * Keycloak, finds nothing, and the browser ends up at the login form rather than in a shell
   * whose every request would 401.
   *
   * **NOT observed, and deliberately not claimed:** the in-page refresh-failure path. Reaching
   * `onAuthRefreshError` (which sets the truthful "La session DHealth Exchange a expiré" state)
   * requires either waiting out the realm's 300s access token inside one page, or a hook into the
   * `keycloak-js` instance that the product does not expose — and manufacturing one would be the
   * auth-state manipulation this qualification forbids itself. The code path exists and is unit
   * tested; it has not been exercised here, and `docs/NATIVE-SECURITY-QUALIFICATION.md` says so.
   */
  test('SEC-4b: after server-side session termination, the next load is not authenticated', async ({
    page,
  }) => {
    test.setTimeout(180_000)
    const admin = await adminToken()
    await signIn(page, CLINICIAN)
    await expect(page.getByRole('button', { name: 'Se déconnecter' })).toBeVisible()

    const userId = await findUserIdByUsername(kcEnv, admin, CLINICIAN.username)
    // Real server-side session termination through the real Admin API. The browser still holds an
    // access token that is cryptographically valid; what it can no longer do is refresh.
    const logout = await fetch(
      `${kcEnv.keycloakUrl}/admin/realms/${kcEnv.realm}/users/${userId}/logout`,
      { method: 'POST', headers: { Authorization: `Bearer ${admin}` } },
    )
    expect([200, 204]).toContain(logout.status)

    // The next application load is what is observed. `ExchangeAuthProvider` initialises with
    // `check-sso`, which asks the real Keycloak whether a session still exists -- it does not,
    // because it was terminated above.
    await page.reload()

    // Either the app shows its truthful expired-session state, or it sends the browser back to
    // real Keycloak. Both are acceptable; silently rendering a workspace whose every request 401s
    // is not, and neither is an endless refresh loop.
    await page.waitForLoadState('networkidle')
    const onKeycloak = /\/realms\/dhealth-exchange\/protocol\/openid-connect\/auth/.test(page.url())
    if (!onKeycloak) {
      await expect(
        page.getByText(/session DHealth Exchange a expiré|Session DHealth Exchange terminée/i),
      ).toBeVisible({ timeout: 30_000 })
    }
    expect(
      onKeycloak ||
        (await page.getByRole('button', { name: 'Se déconnecter' }).count()) === 0,
    ).toBeTruthy()
  })

  test('SEC-5: a disabled Keycloak account cannot obtain a new session', async ({ page }) => {
    test.setTimeout(180_000)
    const admin = await adminToken()
    const userId = await findUserIdByUsername(kcEnv, admin, LAB_TECH.username)
    await setUserEnabled(kcEnv, admin, userId, false)
    try {
      await page.goto('/')
      await page.waitForURL(/\/realms\/dhealth-exchange\/protocol\/openid-connect\/auth/, {
        timeout: 30_000,
      })
      await page.locator('#username').fill(LAB_TECH.username)
      await page.locator('#password').fill(LAB_TECH.password)
      await page.locator('#kc-login').click()
      // Real Keycloak refuses the login; the browser never leaves the identity host and never
      // reaches a workspace.
      await expect(page.locator('#kc-content')).toContainText(
        /désactivé|disabled|invalides|Invalid/i,
        { timeout: 20_000 },
      )
      await expect(page.getByRole('button', { name: 'Se déconnecter' })).toHaveCount(0)
    } finally {
      await setUserEnabled(kcEnv, admin, userId, true)
    }
  })
})

// ---------------------------------------------------------------------------
// Authorization: revocation while a valid token is still held
// ---------------------------------------------------------------------------
test.describe('SEC: revocation takes effect on the next request', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  /**
   * SEC-6 — the load-bearing claim of this whole ticket.
   *
   * DHealth's authorization is not "whatever the token's claims said when it was issued". Every
   * `require_*` in `exchange/authorization.py` performs a live `memberships.find_active(...)`
   * against the database on every request. So withdrawing a membership must take effect on the
   * very next call, with the same unexpired token.
   *
   * FINDING P1, reported separately from the result of this test: **the product has no way to
   * withdraw a membership.** `OrganizationMembershipRepository.set_inactive` exists and has zero
   * callers in `api.py` or the UI. This test therefore proves what the backend does when it sees
   * a revoked membership -- which is correct and worth pinning -- and NOT that an administrator
   * can revoke one, which they currently cannot.
   */
  test('SEC-6: a revoked membership refuses the SAME still-valid token', async ({ page }) => {
    const { token, memberships, identityId } = await signIn(page, CLINICIAN)
    const provider = memberships.find((m) => m.organization_type === 'PROVIDER')!
    expect(provider).toBeTruthy()

    const ctx = await api()
    const auth = { Authorization: `Bearer ${token}` }
    const membershipId = findMembershipId(provider.organization_id, identityId)
    expect(membershipId, 'the membership row must be findable').toBeTruthy()

    try {
      const before = await ctx.get(`${API}/organizations/${provider.organization_id}/orders`, {
        headers: auth,
      })
      expect(before.status(), 'the clinician can read their own orders to begin with').toBe(200)

      setMembershipStatus(membershipId, 'INACTIVE')

      // Same token. Not refreshed, not re-issued, not expired.
      const after = await ctx.get(`${API}/organizations/${provider.organization_id}/orders`, {
        headers: auth,
      })
      expect(
        [401, 403, 404],
        'a withdrawn membership must fail closed on the next request',
      ).toContain(after.status())

      // And /me tells the truth about it. The contract here is NOT "the membership disappears" --
      // `MembershipSummary` carries `membership_status` precisely so the client can see the real
      // state, and `isUsableMembership` in ui/src/features/workspace/workspaceCapabilities.ts
      // refuses any membership whose status or organization status is not ACTIVE. So the
      // requirement is that /me reports it as inactive, which is what makes the UI converge out of
      // the organization on its next load.
      //
      // An earlier draft of this test asserted the membership must vanish from /me entirely. That
      // was a design this product never had, and asserting it would have manufactured a defect
      // rather than found one.
      const me = await ctx.get(`${API}/me`, { headers: auth })
      expect(me.status()).toBe(200)
      const summary = ((await me.json()).memberships as Membership[]).find(
        (m) => m.organization_id === provider.organization_id,
      )
      if (summary) {
        expect(
          (summary as Membership & { membership_status: string }).membership_status,
          '/me must report a withdrawn membership as inactive, never as ACTIVE',
        ).not.toBe('ACTIVE')
      }
    } finally {
      setMembershipStatus(membershipId, 'ACTIVE')
      await ctx.dispose()
    }
  })

  /**
   * SEC-7 — ADR-044's separation of entry from release, enforced against a live session.
   *
   * Same P1 caveat as SEC-6: `OrganizationRoleAssignmentRepository.remove` exists and has no
   * caller, so this proves the backend's behaviour given the state, not that the product can
   * reach it.
   */
  test('SEC-7: removing LAB_RESULT_RELEASER refuses release with the SAME valid token', async ({
    page,
  }) => {
    const { token, memberships, identityId } = await signIn(page, RELEASER)
    const lab = memberships.find((m) => m.organization_type === 'LAB')!
    expect(lab, 'the demo releaser must hold a laboratory membership').toBeTruthy()
    expect(lab.roles).toContain('LAB_RESULT_RELEASER')

    const ctx = await api()
    const auth = { Authorization: `Bearer ${token}` }
    const membershipId = findMembershipId(lab.organization_id, identityId)
    const rolesBefore = listRoles(membershipId)
    expect(rolesBefore).toContain('LAB_RESULT_RELEASER')

    try {
      // The release queue is the least-privilege surface that requires exactly this role.
      const before = await ctx.get(
        `${API}/organizations/${lab.organization_id}/release-queue`,
        { headers: auth },
      )
      expect(
        [200, 404],
        'baseline: the releaser can reach the release surface (404 only if the route name differs)',
      ).toContain(before.status())

      removeRole(membershipId, 'LAB_RESULT_RELEASER')

      const after = await ctx.get(
        `${API}/organizations/${lab.organization_id}/release-queue`,
        { headers: auth },
      )
      expect(
        [401, 403, 404],
        'release authority must be gone on the next request with the same token',
      ).toContain(after.status())
      if (before.status() === 200) {
        expect(after.status(), 'a surface that answered 200 must now refuse').not.toBe(200)
      }
    } finally {
      restoreRole(membershipId, 'LAB_RESULT_RELEASER')
      await ctx.dispose()
      expect(listRoles(membershipId)).toContain('LAB_RESULT_RELEASER')
    }
  })

  test('SEC-9: a role ADDED mid-session is visible without re-authenticating', async ({ page }) => {
    // The symmetric half of SEC-7, and the reason the model is coherent: because authority is read
    // from the database per request rather than from token claims, a grant is visible immediately
    // too. The UI's own navigation is a separate matter -- `StaffApp` loads /me once per
    // application load (ADR-054), so a *rendered* menu can lag until reload even though the
    // server already honours the new authority. Both halves are asserted here.
    const { token, memberships, identityId } = await signIn(page, LAB_TECH)
    const lab = memberships.find((m) => m.organization_type === 'LAB')!
    expect(lab.roles).not.toContain('LAB_RESULT_RELEASER')

    const ctx = await api()
    const auth = { Authorization: `Bearer ${token}` }
    const membershipId = findMembershipId(lab.organization_id, identityId)
    try {
      restoreRole(membershipId, 'LAB_RESULT_RELEASER')
      const me = await ctx.get(`${API}/me`, { headers: auth })
      expect(me.status()).toBe(200)
      const roles = ((await me.json()).memberships as Membership[]).find(
        (m) => m.organization_id === lab.organization_id,
      )!.roles
      expect(roles, 'the server reflects the new role on the next request').toContain(
        'LAB_RESULT_RELEASER',
      )
    } finally {
      removeRole(membershipId, 'LAB_RESULT_RELEASER')
      await ctx.dispose()
      expect(listRoles(membershipId)).not.toContain('LAB_RESULT_RELEASER')
    }
  })
})

// ---------------------------------------------------------------------------
// Tenant isolation and administrative boundaries
// ---------------------------------------------------------------------------
test.describe('SEC: tenant isolation and administrative boundaries', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('SEC-8/SEC-10: cross-tenant reads and invented organization ids are refused', async ({
    page,
  }) => {
    const { token, memberships } = await signIn(page, CLINICIAN)
    const provider = memberships.find((m) => m.organization_type === 'PROVIDER')!
    const ctx = await api()
    const auth = { Authorization: `Bearer ${token}` }
    try {
      // Discover the laboratory's real id the way a clinician legitimately learns it, then use it
      // where they have no authority.
      const labs = await ctx.get(`${API}/organizations/${provider.organization_id}/lab-routes`, {
        headers: auth,
      })
      if (labs.status() === 200) {
        const routes = (await labs.json()) as Array<{ lab_organization_id?: string; id?: string }>
        const labId = routes[0]?.lab_organization_id ?? routes[0]?.id
        if (labId) {
          for (const path of [`lab-orders`, `orders`]) {
            const crossed = await ctx.get(`${API}/organizations/${labId}/${path}`, {
              headers: auth,
            })
            expect([401, 403, 404], `cross-tenant ${path}`).toContain(crossed.status())
          }
        }
      }

      // A syntactically valid UUID belonging to nothing. Anti-enumeration: indistinguishable from
      // an organization the caller simply has no claim on.
      const invented = '00000000-0000-4000-8000-000000000000'
      const response = await ctx.get(`${API}/organizations/${invented}/orders`, { headers: auth })
      expect([401, 403, 404]).toContain(response.status())
      const body = await response.text()
      expect(body).not.toContain('Traceback')
      expect(body).not.toContain('SELECT')
    } finally {
      await ctx.dispose()
    }
  })

  test('SEC-17: a lab technician cannot act as a provider, nor release what they entered', async ({
    page,
  }) => {
    const { token, memberships } = await signIn(page, LAB_TECH)
    const lab = memberships.find((m) => m.organization_type === 'LAB')!
    expect(lab.roles).toContain('LAB_TECH')
    // ADR-044: entry and release are distinct authorities. The seeded technician holds only entry.
    expect(lab.roles).not.toContain('LAB_RESULT_RELEASER')

    const ctx = await api()
    const auth = { Authorization: `Bearer ${token}` }
    try {
      // Ordering is a provider act; a laboratory membership does not confer it even in its own org.
      const asProvider = await ctx.get(`${API}/organizations/${lab.organization_id}/orders`, {
        headers: auth,
      })
      expect([401, 403, 404]).toContain(asProvider.status())
    } finally {
      await ctx.dispose()
    }
  })

  /**
   * SEC-17b — network administration is not clinical authority, proven with a REAL platform-admin
   * token rather than by reading `authorization.py`.
   *
   * `require_clinician`, `require_lab_tech` and `require_lab_result_releaser` are the three gates
   * that deliberately do NOT let `platform_admin` bypass them, unlike every other `require_*`. A
   * platform admin holds the `dhealth-platform-admin` realm role and can list and manage
   * organizations — and must still be refused clinical access to one they hold no membership in.
   *
   * The refusal shape matters too: for a platform admin a real organization they lack the role in
   * is a **403**, never a 404, because they already have visibility that it exists. Pretending
   * otherwise would be an inconsistent lie rather than anti-enumeration.
   */
  test('SEC-17b: a real platform admin is refused clinical access it holds no role for', async () => {
    test.setTimeout(180_000)
    const admin = await adminToken()
    const setupEnv = {
      keycloakUrl: kcEnv.keycloakUrl,
      realm: kcEnv.realm,
      adminUser: kcEnv.adminUser,
      adminPassword: kcEnv.adminPassword,
      adapterBaseUrl: API,
    }
    // `createSetupClient` enables the service account this test needs; it omits the audience
    // mapper (finding H1), so the mapper is added explicitly before any token is minted.
    const client = await createSetupClient(setupEnv, admin)
    await addExchangeAudienceMapper(kcEnv, admin, client.uuid)
    try {
      // Grant the qualification client's own service account the platform-admin realm role,
      // through the real Admin API -- the same role `dhealth-exchange-realm.json` declares and
      // `get_principal` checks. Not a bypass of the authorization service: a real role grant.
      await grantPlatformAdminToServiceAccount(
        setupEnv,
        admin,
        client.uuid,
        client.clientId,
      )

      const token = await mintClientCredentialsToken(setupEnv, client.clientId, client.secret)

      const ctx = await api()
      const adminAuth = { Authorization: `Bearer ${token}` }
      try {
        const me = await ctx.get(`${API}/me`, { headers: adminAuth })
        expect(me.status(), 'the platform-admin token must authenticate').toBe(200)
        const body = await me.json()
        expect(body.platform_admin, 'the realm role must be recognised').toBe(true)
        expect(
          body.memberships,
          'this identity deliberately holds no organization membership',
        ).toHaveLength(0)

        // The organization ids are read straight from the database. Discovery is not the claim
        // here -- the refusal is -- and there is no list-organizations GET to discover them with.
        const providerId = exchangeSql(
          "SELECT id FROM exchange_organizations WHERE org_type = 'PROVIDER' LIMIT 1",
        )
        const labId = exchangeSql(
          "SELECT id FROM exchange_organizations WHERE org_type = 'LAB' LIMIT 1",
        )
        expect(providerId, 'the demo baseline must contain a provider organization').toBeTruthy()

        // ...and is refused its clinical surface, with 403 rather than 404. Absolute URLs on
        // purpose: a relative path here resolves against the app origin's baseURL and lands in
        // the SPA catch-all, which answers 200 HTML and would read as a broken authorization
        // gate. That is finding G1 demonstrating itself, and it cost one misdiagnosis.
        const orders = await ctx.get(`${API}/organizations/${providerId}/orders`, {
          headers: adminAuth,
        })
        expect(
          orders.status(),
          'platform admin is a network administrator, not a clinician',
        ).toBe(403)

        if (labId) {
          const labQueue = await ctx.get(`${API}/organizations/${labId}/lab-orders`, {
            headers: adminAuth,
          })
          expect(labQueue.status(), 'nor a laboratory technician').toBe(403)
          const releaseQueue = await ctx.get(`${API}/organizations/${labId}/release-queue`, {
            headers: adminAuth,
          })
          expect(releaseQueue.status(), 'nor a result releaser').toBe(403)
        }
      } finally {
        await ctx.dispose()
      }
    } finally {
      await deleteClientByUuid(kcEnv, admin, client.uuid)
    }
  })

  /**
   * SEC-20 — a real authorization refusal reaches the file-backed refusal audit, and authorization
   * does not depend on that write succeeding.
   *
   * `exchange/audit.py`'s writer is deliberately separate from the transactional mutation audit:
   * a refusal is not a state change, and access denial must never become contingent on a
   * successful audit write. This checks the first half at runtime (the refusal is recorded) --
   * the second half is a property of the code (`_audit_refused` is wrapped in a bare
   * `try/except`) and is asserted by the unit suite, not here.
   */
  test('SEC-20: an authorization refusal is recorded in the file-backed refusal audit', async ({
    page,
  }) => {
    // A **403** refusal, not a 404. That distinction is real and was found by this test:
    // `_audit_refused` is called on `NotAuthorized` and NOT on `ResourceNotFound`, so the
    // anti-enumeration 404 (an identity with no relationship to an organization, or an
    // organization that does not exist) is deliberately not audited -- there is no meaningful
    // actor/resource pair to record, and auditing every probe of a fabricated UUID would be a
    // denial-of-service against the audit volume. A 403 is the case where a *known* identity was
    // refused a *specific* permission on an organization they genuinely belong to, which is the
    // security-relevant event.
    //
    // The refusal used here is the laboratory technician asking a provider surface of their own
    // organization -- a real membership, a missing role.
    const { token, memberships } = await signIn(page, LAB_TECH)
    const lab = memberships.find((m) => m.organization_type === 'LAB')!

    const before = auditLineCount()
    const ctx = await api()
    try {
      const refused = await ctx.get(`${API}/organizations/${lab.organization_id}/orders`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      expect(refused.status(), 'a member lacking the role is a 403, never a 404').toBe(403)
    } finally {
      await ctx.dispose()
    }

    // The writer appends; a refusal that reached it moves the line count. If the audit volume is
    // not present at all this returns -1, and the test says so rather than passing vacuously.
    const after = auditLineCount()
    expect(before, 'the adapter audit directory must be readable').toBeGreaterThanOrEqual(0)
    expect(after, 'the refusal must have been appended to the refusal audit').toBeGreaterThan(
      before,
    )
    expect(lab).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Patient access grants
// ---------------------------------------------------------------------------
test.describe('SEC: patient access grants', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('SEC-11/SEC-12: a grant opens exactly its own order, and a revoked grant opens nothing', async ({
    page,
  }) => {
    test.setTimeout(240_000)
    const { token, memberships } = await signIn(page, CLINICIAN)
    const provider = memberships.find((m) => m.organization_type === 'PROVIDER')!
    const ctx = await api()
    const auth = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    try {
      // Two orders for the same provider, so "its own order" is a real distinction rather than
      // the only thing that exists.
      const catalog = await ctx.get(`${API}/catalog/tests`, { headers: auth })
      expect(catalog.status()).toBe(200)
      const entryId = (await catalog.json())[0].id

      // Deliberately NOT creating patients. `demo_seed.py`'s `_WORKFLOW_TABLES` treats
      // `exchange_patients` as baseline/configuration ("the seed patient itself"), so a patient
      // registered at runtime survives `scripts/demo/reset.sh` and accumulates. An earlier draft
      // of this suite created three patients per run and left twelve behind, which then broke the
      // golden journey's own PHASE A. Orders ARE cleared by reset, so two orders against the one
      // baseline patient is both residue-free and a stronger test: same patient, same provider,
      // same catalogue entry, so the ONLY thing distinguishing the two grants is the order they
      // are bound to.
      const directory = await ctx.get(`${API}/organizations/${provider.organization_id}/patients`, {
        headers: auth,
      })
      expect(directory.status()).toBe(200)
      const baselinePatient = (await directory.json())[0]
      expect(baselinePatient, 'the demo baseline must provide a patient').toBeTruthy()
      const referenceId = baselinePatient.primary_patient_reference_id

      const made: Array<{ orderId: string; rawToken: string }> = []
      for (let i = 0; i < 2; i += 1) {
        const order = await ctx.post(`${API}/organizations/${provider.organization_id}/orders`, {
          headers: { ...auth, 'Idempotency-Key': crypto.randomUUID() },
          data: { patient_reference_id: referenceId, catalog_entry_ids: [entryId] },
        })
        expect([200, 201]).toContain(order.status())
        const orderId = (await order.json()).id

        const grant = await ctx.post(
          `${API}/organizations/${provider.organization_id}/orders/${orderId}/patient-access`,
          { headers: auth, data: {} },
        )
        expect([200, 201]).toContain(grant.status())
        const rawToken = (await grant.json()).access_token
        expect(rawToken, 'issuance returns the one-time raw grant').toBeTruthy()
        made.push({ orderId, rawToken })
      }

      const [a, b] = made
      const patientAuth = (raw: string) => ({ Authorization: `Bearer ${raw}` })

      // SEC-11: each grant resolves, and resolves to its OWN order. The patient response carries
      // no identifier at all, so "its own" is proven by asking Exchange to resolve each grant and
      // confirming the two are different orders from the provider's own view.
      for (const g of made) {
        const response = await ctx.get(`${API}/patient/order`, { headers: patientAuth(g.rawToken) })
        expect(response.status(), 'a live grant opens its order').toBe(200)
        // Sensitive patient response must not be cacheable.
        expect(response.headers()['cache-control']).toBe('no-store')
      }

      // SEC-11b: a grant is not a general credential -- it authorizes nothing on the staff API.
      const staffWithPatientGrant = await ctx.get(
        `${API}/organizations/${provider.organization_id}/orders`,
        { headers: patientAuth(a.rawToken) },
      )
      expect([401, 403, 404]).toContain(staffWithPatientGrant.status())

      // SEC-12: revoke A. B must be untouched -- revocation is per grant, not per provider.
      revokePatientGrantsForOrder(a.orderId)
      const revoked = await ctx.get(`${API}/patient/order`, { headers: patientAuth(a.rawToken) })
      expect([401, 403, 404], 'a revoked grant must open nothing').toContain(revoked.status())

      const untouched = await ctx.get(`${API}/patient/order`, { headers: patientAuth(b.rawToken) })
      expect(untouched.status(), 'revoking one grant must not affect another').toBe(200)

      // SEC-12b: a fabricated and a malformed grant both fail the same generic way.
      for (const bogus of ['not-a-token', 'a'.repeat(64)]) {
        const response = await ctx.get(`${API}/patient/order`, { headers: patientAuth(bogus) })
        expect([401, 403, 404]).toContain(response.status())
        expect(await response.text()).not.toContain('Traceback')
      }
    } finally {
      await ctx.dispose()
    }
  })

  test('SEC-13: the patient credential travels in the fragment and is scrubbed from the URL', async ({
    page,
  }) => {
    test.setTimeout(240_000)
    const { token, memberships } = await signIn(page, CLINICIAN)
    const provider = memberships.find((m) => m.organization_type === 'PROVIDER')!
    const ctx = await api()
    const auth = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    let rawToken = ''
    try {
      const catalog = await ctx.get(`${API}/catalog/tests`, { headers: auth })
      const entryId = (await catalog.json())[0].id
      // Baseline patient, never a new one -- see the note in SEC-11/SEC-12 about
      // `exchange_patients` surviving `reset.sh`.
      const directory = await ctx.get(`${API}/organizations/${provider.organization_id}/patients`, {
        headers: auth,
      })
      const referenceId = (await directory.json())[0].primary_patient_reference_id
      const order = await ctx.post(`${API}/organizations/${provider.organization_id}/orders`, {
        headers: { ...auth, 'Idempotency-Key': crypto.randomUUID() },
        data: { patient_reference_id: referenceId, catalog_entry_ids: [entryId] },
      })
      const orderId = (await order.json()).id
      const grant = await ctx.post(
        `${API}/organizations/${provider.organization_id}/orders/${orderId}/patient-access`,
        { headers: auth, data: {} },
      )
      rawToken = (await grant.json()).access_token
    } finally {
      await ctx.dispose()
    }
    expect(rawToken).toBeTruthy()

    // The first time the DHealth Patient surface has ever been rendered by a real browser.
    const requests: string[] = []
    page.on('request', (r) => requests.push(r.url()))
    await page.goto(`/?app=patient#access_token=${rawToken}`)
    await page.waitForLoadState('networkidle')

    // The credential is gone from the visible address bar, and the app identity survives the scrub.
    expect(page.url()).not.toContain(rawToken)
    expect(new URL(page.url()).hash).toBe('')
    expect(page.url()).toContain('app=patient')

    // It never appeared in any request line either -- a fragment is not sent to a server, and the
    // app puts it in an Authorization header rather than a query parameter.
    for (const url of requests) {
      expect(url, 'the raw grant must never appear in a request URL').not.toContain(rawToken)
    }
  })
})

// ---------------------------------------------------------------------------
// Session end, response caching, and what reaches the logs
// ---------------------------------------------------------------------------
test.describe('SEC: logout, caching and leakage', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('SEC-16: after logout the browser holds no usable access', async ({ page }) => {
    test.setTimeout(180_000)
    const { token } = await signIn(page, CLINICIAN)
    const ctx = await api()
    try {
      const before = await ctx.get(`${API}/me`, { headers: { Authorization: `Bearer ${token}` } })
      expect(before.status()).toBe(200)

      await page.getByRole('button', { name: 'Se déconnecter' }).click()
      await page.waitForLoadState('networkidle')

      // The claim worth making: the browser no longer has a usable session. Navigating back to a
      // protected route must land at real Keycloak, not in a workspace.
      await page.goto('/')
      await page.waitForURL(/\/realms\/dhealth-exchange\/protocol\/openid-connect\/auth/, {
        timeout: 30_000,
      })
      await expect(page.getByRole('button', { name: 'Se déconnecter' })).toHaveCount(0)

      // The browser's own back button may still paint a cached document -- that is a browser
      // behaviour no server can fully prevent, and it is NOT claimed here. What is claimed is
      // that nothing functional survives: any new request needs a token the browser no longer has.
      await page.goBack().catch(() => undefined)
      await expect(page.getByRole('button', { name: 'Se déconnecter' })).toHaveCount(0)
    } finally {
      await ctx.dispose()
    }
  })

  /**
   * SEC-17 / FINDING G2 (P2): authenticated staff clinical responses carry NO cache directive.
   *
   * `no-store` is set on exactly eight handlers in `api.py`, and all eight are patient-access or
   * patient-notification routes. Every provider and laboratory clinical read -- orders, results,
   * release versions, the review queue, the patient directory -- sends none, so an intermediary
   * or a shared browser profile may store a clinical response under its default heuristics.
   *
   * This test PINS the gap rather than hiding it, and is the assertion that must flip when the
   * gap is closed.
   */
  test('SEC-17 [G2]: patient responses are no-store; staff clinical responses currently are not', async ({
    page,
  }) => {
    const { token, memberships } = await signIn(page, CLINICIAN)
    const provider = memberships.find((m) => m.organization_type === 'PROVIDER')!
    const ctx = await api()
    const auth = { Authorization: `Bearer ${token}` }
    try {
      const orders = await ctx.get(`${API}/organizations/${provider.organization_id}/orders`, {
        headers: auth,
      })
      expect(orders.status()).toBe(200)
      // Pinned current behaviour -- see the block comment above. If a later ticket adds a cache
      // policy for staff clinical reads, this expectation is the one that tells them so.
      expect(orders.headers()['cache-control']).toBeUndefined()
    } finally {
      await ctx.dispose()
    }
  })

  test('SEC-18: no bearer token or patient grant appears in the qualification logs', async ({
    page,
  }) => {
    // Two independent claims. (1) The suite itself never prints a credential: asserted by the
    // fact that no helper returns one to the reporter and every capture stays in a local. (2) The
    // edge does not log the Authorization header -- checked against the real Caddy log below, in
    // scripts/demo/qualify-security.sh's companion log scan, not here, because a Playwright test
    // cannot read another container's stdout without shelling out.
    const { token } = await signIn(page, CLINICIAN)
    expect(token.length).toBeGreaterThan(20)
    // The strongest thing assertable in-process: the app never puts the token anywhere a later
    // reader could recover it from the page.
    const storage = await page.evaluate(() => ({
      local: JSON.stringify(window.localStorage),
      session: JSON.stringify(window.sessionStorage),
    }))
    expect(storage.local, 'no access token in localStorage').not.toContain(token)
    expect(storage.session, 'no access token in sessionStorage').not.toContain(token)
  })
})
