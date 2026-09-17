/**
 * DH-CLINIC-004B: helpers used ONLY by the qualification harness (global
 * setup/teardown and a few negative-control specs) to provision and inspect
 * synthetic test data through real, already-supported Exchange/Keycloak admin
 * paths -- never a database bypass, never a fabricated token, never a change
 * to Exchange's authorization enforcement.
 *
 * Every token minted here is a REAL token issued by the REAL Keycloak for the
 * `dhealth-exchange` realm. The only thing that differs from the production
 * browser flow is the OAuth *grant type* used to obtain it (client_credentials
 * or password, via a temporary setup-only client -- see createSetupClient
 * below) instead of Authorization Code + PKCE through a real browser. The
 * interactive PKCE browser flow itself is exercised for real, with zero
 * shortcuts, in e2e/specs/01-login-flow.spec.ts.
 */

export interface SetupEnv {
  keycloakUrl: string
  realm: string
  adminUser: string
  adminPassword: string
  adapterBaseUrl: string
}

export function readSetupEnv(): SetupEnv {
  const keycloakUrl = process.env.E2E_KEYCLOAK_URL
  const realm = process.env.E2E_REALM || 'dhealth-exchange'
  const adminUser = process.env.KEYCLOAK_ADMIN_USER
  const adminPassword = process.env.KEYCLOAK_ADMIN_PASSWORD
  const adapterBaseUrl = process.env.E2E_ADAPTER_BASE_URL
  if (!keycloakUrl || !adminUser || !adminPassword || !adapterBaseUrl) {
    throw new Error(
      'E2E_KEYCLOAK_URL, KEYCLOAK_ADMIN_USER, KEYCLOAK_ADMIN_PASSWORD and E2E_ADAPTER_BASE_URL are all required for the qualification harness setup step.',
    )
  }
  return { keycloakUrl, realm, adminUser, adminPassword, adapterBaseUrl }
}

async function jsonOrThrow(response: Response, context: string): Promise<unknown> {
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`${context} failed: ${response.status} ${response.statusText} ${body}`)
  }
  const text = await response.text()
  return text ? JSON.parse(text) : null
}

/** Master-realm admin token via Keycloak's built-in `admin-cli` client -- the
 * standard, unmodified way to drive the Admin REST API non-interactively.
 * Grants zero Exchange-realm access on its own; it only authorizes calls
 * against Keycloak's own admin endpoints (realm/client/user management). */
export async function getMasterAdminToken(env: SetupEnv): Promise<string> {
  const response = await fetch(`${env.keycloakUrl}/realms/master/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'password',
      client_id: 'admin-cli',
      username: env.adminUser,
      password: env.adminPassword,
    }),
  })
  const data = (await jsonOrThrow(response, 'master admin token')) as { access_token: string }
  return data.access_token
}

async function adminFetch(
  env: SetupEnv,
  adminToken: string,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(`${env.keycloakUrl}/admin/realms/${env.realm}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${adminToken}`,
      'Content-Type': 'application/json',
      ...(init.headers as Record<string, string> | undefined),
    },
  })
}

/**
 * DH-CLINIC-004B: creates a TEMPORARY confidential client used exclusively by
 * this qualification harness to mint real tokens for synthetic test-data
 * setup (organization/membership creation, session-termination checks, the
 * wrong-audience negative control). Never committed to
 * keycloak/realm-config/dhealth-exchange-realm.json, never present in a real
 * deployment's realm import, always deleted by global-teardown.ts at the end
 * of the qualification run. `directAccessGrantsEnabled: true` here is scoped
 * to THIS throwaway client only -- it does not touch, and cannot weaken,
 * `dhealth-native-ui`'s production posture (directAccessGrantsEnabled: false,
 * enforced by both the committed realm JSON and reconcile-exchange.sh).
 */
export async function createSetupClient(
  env: SetupEnv,
  adminToken: string,
): Promise<{ uuid: string; clientId: string; secret: string }> {
  const clientId = `dhealth-e2e-setup-${Date.now()}`
  const createResponse = await adminFetch(env, adminToken, '/clients', {
    method: 'POST',
    body: JSON.stringify({
      clientId,
      protocol: 'openid-connect',
      enabled: true,
      publicClient: false,
      standardFlowEnabled: false,
      implicitFlowEnabled: false,
      directAccessGrantsEnabled: true,
      serviceAccountsEnabled: true,
      description:
        'DH-CLINIC-004B qualification-harness-only client. Mints test-setup tokens; never shipped to a real deployment. Deleted by global-teardown.ts.',
    }),
  })
  if (!createResponse.ok) {
    const body = await createResponse.text().catch(() => '')
    throw new Error(`create setup client failed: ${createResponse.status} ${body}`)
  }
  const location = createResponse.headers.get('Location')
  if (!location) throw new Error('create setup client: no Location header returned')
  const uuid = location.split('/').pop()
  if (!uuid) throw new Error('create setup client: could not parse client uuid from Location')

  const secretResponse = await adminFetch(env, adminToken, `/clients/${uuid}/client-secret`)
  const secretData = (await jsonOrThrow(secretResponse, 'read setup client secret')) as {
    value: string
  }

  return { uuid, clientId, secret: secretData.value }
}

export async function deleteClient(env: SetupEnv, adminToken: string, uuid: string): Promise<void> {
  await adminFetch(env, adminToken, `/clients/${uuid}`, { method: 'DELETE' })
}

/** Grants the setup client's own service-account user the realm role that
 * DH-EX-002/authorization.py treats as platform admin -- the same realm role
 * `dhealth-exchange-realm.json` declares and the adapter's `get_principal`
 * dependency checks via `token.has_role(platform_admin_role)`. This is a real
 * realm-role grant through the real Admin API, not a bypass of the
 * authorization service. */
export async function grantPlatformAdminToServiceAccount(
  env: SetupEnv,
  adminToken: string,
  clientUuid: string,
  clientId: string,
): Promise<void> {
  const usersResponse = await adminFetch(
    env,
    adminToken,
    `/clients/${clientUuid}/service-account-user`,
  )
  const serviceAccountUser = (await jsonOrThrow(usersResponse, 'read service account user')) as {
    id: string
  }

  const roleResponse = await adminFetch(env, adminToken, '/roles/dhealth-platform-admin')
  const role = (await jsonOrThrow(roleResponse, 'read dhealth-platform-admin role')) as {
    id: string
    name: string
  }

  const assignResponse = await adminFetch(
    env,
    adminToken,
    `/users/${serviceAccountUser.id}/role-mappings/realm`,
    { method: 'POST', body: JSON.stringify([role]) },
  )
  if (!assignResponse.ok) {
    const body = await assignResponse.text().catch(() => '')
    throw new Error(
      `grant platform-admin to ${clientId}'s service account failed: ${assignResponse.status} ${body}`,
    )
  }
}

export interface SyntheticUser {
  id: string
  email: string
  password: string
}

/** Creates a synthetic, clearly-labeled, disposable Keycloak user in the
 * `dhealth-exchange` realm -- an `.example.invalid` address (RFC 2606
 * reserved, guaranteed non-routable/non-real), a random password never
 * logged or committed, and a `dh-clinic-004b-synthetic` realm attribute so a
 * later operator can always distinguish this from any real account. */
export async function createSyntheticUser(
  env: SetupEnv,
  adminToken: string,
  emailLocalPart: string,
): Promise<SyntheticUser> {
  const email = `${emailLocalPart}@example.invalid`
  const password = randomPassword()
  const createResponse = await adminFetch(env, adminToken, '/users', {
    method: 'POST',
    body: JSON.stringify({
      username: email,
      email,
      enabled: true,
      emailVerified: true,
      firstName: 'DH-CLINIC-004B',
      lastName: 'Synthetic test identity',
      attributes: { 'dh-clinic-004b-synthetic': ['true'] },
      credentials: [{ type: 'password', value: password, temporary: false }],
    }),
  })
  if (!createResponse.ok) {
    const body = await createResponse.text().catch(() => '')
    throw new Error(`create synthetic user ${email} failed: ${createResponse.status} ${body}`)
  }
  const location = createResponse.headers.get('Location')
  const id = location?.split('/').pop()
  if (!id) throw new Error(`create synthetic user ${email}: could not parse user id`)
  return { id, email, password }
}

export async function deleteUser(env: SetupEnv, adminToken: string, userId: string): Promise<void> {
  await adminFetch(env, adminToken, `/users/${userId}`, { method: 'DELETE' })
}

/** DT/E2E session-termination-then-fail-closed: revokes every session
 * Keycloak currently holds open for this user, via the real admin API --
 * never a database write, never a client-side "pretend the token is gone." */
export async function revokeAllSessionsForUser(
  env: SetupEnv,
  adminToken: string,
  userId: string,
): Promise<void> {
  const response = await adminFetch(env, adminToken, `/users/${userId}/logout`, { method: 'POST' })
  if (!response.ok && response.status !== 204 && response.status !== 200) {
    const body = await response.text().catch(() => '')
    throw new Error(`revoke sessions for ${userId} failed: ${response.status} ${body}`)
  }
}

/** DH-CLINIC-004B: temporarily lowers the realm's access-token lifespan so
 * the real-token-refresh scenario (E2E-9) does not require a multi-minute
 * wait. Applied ONLY to the ephemeral qualification-run realm (imported
 * fresh into a throwaway Postgres/Keycloak instance that is destroyed at the
 * end of the CI job) -- never applied to, and never committed as, production
 * configuration. `accessTokenLifespan` is the only field this touches. */
export async function setShortAccessTokenLifespan(
  env: SetupEnv,
  adminToken: string,
  seconds: number,
): Promise<void> {
  const response = await adminFetch(env, adminToken, '', {
    method: 'PUT',
    body: JSON.stringify({ accessTokenLifespan: seconds }),
  })
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`set short access token lifespan failed: ${response.status} ${body}`)
  }
}

export async function mintClientCredentialsToken(
  env: SetupEnv,
  clientId: string,
  clientSecret: string,
): Promise<string> {
  const response = await fetch(
    `${env.keycloakUrl}/realms/${env.realm}/protocol/openid-connect/token`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'client_credentials',
        client_id: clientId,
        client_secret: clientSecret,
      }),
    },
  )
  const data = (await jsonOrThrow(response, 'client_credentials token')) as { access_token: string }
  return data.access_token
}

export async function mintPasswordGrantToken(
  env: SetupEnv,
  clientId: string,
  clientSecret: string,
  username: string,
  password: string,
): Promise<string> {
  const response = await fetch(
    `${env.keycloakUrl}/realms/${env.realm}/protocol/openid-connect/token`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'password',
        client_id: clientId,
        client_secret: clientSecret,
        username,
        password,
      }),
    },
  )
  const data = (await jsonOrThrow(response, `password grant token for ${username}`)) as {
    access_token: string
  }
  return data.access_token
}

export interface MeResponse {
  identity_id: string
  issuer: string
  subject: string
  memberships: Array<{ organization_id: string; organization_name: string; roles: string[] }>
}

export async function callMe(env: SetupEnv, token: string): Promise<MeResponse> {
  const response = await fetch(`${env.adapterBaseUrl}/me`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  return (await jsonOrThrow(response, 'GET /exchange/v1/me')) as MeResponse
}

export async function createOrganization(
  env: SetupEnv,
  platformAdminToken: string,
  name: string,
): Promise<{ id: string; name: string }> {
  const response = await fetch(`${env.adapterBaseUrl}/organizations`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${platformAdminToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ name, organization_type: 'PROVIDER' }),
  })
  return (await jsonOrThrow(response, 'POST /exchange/v1/organizations')) as {
    id: string
    name: string
  }
}

export async function createMembership(
  env: SetupEnv,
  platformAdminToken: string,
  organizationId: string,
  identityId: string,
  roles: string[],
): Promise<void> {
  const response = await fetch(
    `${env.adapterBaseUrl}/organizations/${organizationId}/memberships`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${platformAdminToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ identity_id: identityId, roles }),
    },
  )
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`create membership failed: ${response.status} ${body}`)
  }
}

function randomPassword(): string {
  const bytes = new Uint8Array(24)
  crypto.getRandomValues(bytes)
  return Buffer.from(bytes).toString('base64url')
}
