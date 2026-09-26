/**
 * DHEALTH-NATIVE-SEC-001: the extra real-admin operations the security qualification needs, on
 * top of `adminApi.ts` (DH-CLINIC-004B, reused unchanged wherever it already covers something).
 *
 * Every Keycloak operation here goes through the **real Admin REST API** of the **real Keycloak**
 * the stack is running. Nothing fabricates a token, forges a session, or writes Keycloak's
 * database directly.
 *
 * One deliberate exception, and it is the most important thing in this file to understand:
 * `setMembershipStatus` and `removeRole` write **SQL directly against the Exchange database**.
 *
 * ── Why, when this was written, and what changed ──
 *
 * ~~That is not a shortcut — it is a finding. DHealth's product has no revocation path at all:
 * `set_inactive` and `remove` both exist and **neither has a single caller** in `api.py` or the UI
 * (confirmed by grep at 3d058c6).~~
 *
 * **Corrected 2026-09-26, DHEALTH-NATIVE-ACCESS-001: that gap is closed.** Both methods now have
 * production callers, reached by a facility administrator through four endpoints and an
 * `Équipe et accès` screen. It was a real finding (DHEALTH-NATIVE-SEC-001's P1/F1) and it is now
 * recorded as closed in `docs/NATIVE-SECURITY-QUALIFICATION.md` §5.1.
 *
 * **These two helpers stay, and SEC-6/SEC-7 keep using them, deliberately.** Rewriting those tests
 * to revoke through the new API would quietly change what they prove. Their claim is *"the backend
 * fails closed **given** a withdrawn membership or role"* — a property of the authorization engine
 * that must hold however the state arose, including a state no endpoint produces. The separate
 * claim, *"an administrator can **reach** that state through the product"*, belongs to
 * `07-access-lifecycle.spec.ts`, which uses the real endpoints and no SQL to cause anything.
 * Neither suite subsumes the other, so both exist.
 */
import { execFileSync } from 'node:child_process'

export interface KeycloakAdminEnv {
  keycloakUrl: string
  realm: string
  adminUser: string
  adminPassword: string
}

const DB_CONTAINER = process.env.SEC_DB_CONTAINER || 'dhealth-native-demo-dhealth-db-1'
const DB_USER = process.env.SEC_DB_USER || 'adapter'
const DB_NAME = process.env.SEC_DB_NAME || 'dhealth_native_demo'

/** Runs one statement in the real Exchange database, inside its own container. Returns trimmed
 * stdout. Used ONLY for the revocation states the product cannot itself reach (see file header). */
export function exchangeSql(sql: string): string {
  return execFileSync(
    'sudo',
    ['docker', 'exec', '-i', DB_CONTAINER, 'psql', '-U', DB_USER, '-d', DB_NAME, '-tAc', sql],
    { encoding: 'utf-8', timeout: 30_000 },
  ).trim()
}

async function adminFetch(
  env: KeycloakAdminEnv,
  token: string,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(`${env.keycloakUrl}/admin/realms/${env.realm}${path}`, {
    ...init,
    headers: {
      ...(init.headers || {}),
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
  })
}

export async function findUserIdByUsername(
  env: KeycloakAdminEnv,
  adminToken: string,
  username: string,
): Promise<string> {
  const response = await adminFetch(
    env,
    adminToken,
    `/users?username=${encodeURIComponent(username)}&exact=true`,
  )
  if (!response.ok) throw new Error(`lookup ${username} failed: ${response.status}`)
  const users = (await response.json()) as Array<{ id: string; username: string }>
  const match = users.find((u) => u.username === username)
  if (!match) throw new Error(`Keycloak user ${username} not found`)
  return match.id
}

/** Disables (or re-enables) a real Keycloak account. Disabling does NOT invalidate an
 * already-issued access token — that is precisely the limitation SEC-5 exists to observe and
 * state honestly, rather than assume JWT revocation is instantaneous. */
export async function setUserEnabled(
  env: KeycloakAdminEnv,
  adminToken: string,
  userId: string,
  enabled: boolean,
): Promise<void> {
  const response = await adminFetch(env, adminToken, `/users/${userId}`, {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  })
  if (!response.ok) {
    throw new Error(`set enabled=${enabled} on ${userId} failed: ${response.status}`)
  }
}

/**
 * Creates a qualification-only confidential client that can mint real tokens by password grant
 * AND carries the same `dhealth-exchange-api` audience mapper the production clients carry.
 *
 * The mapper is the part that matters: `adminApi.ts`'s own `createSetupClient` omits it, so a
 * token minted from that client has no `aud` claim Exchange accepts and would be refused for a
 * reason unrelated to whatever the test believed it was proving. That helper has never been run
 * against a live Keycloak (DH-CLINIC-004B never executed), which is how the gap survived.
 *
 * `accessTokenLifespan` is set as a **per-client attribute**, never on the realm: the realm's own
 * 300s production default is left exactly as shipped, so a crashed run cannot leave the
 * deployment weaker than it found it.
 */
export async function createShortLivedTokenClient(
  env: KeycloakAdminEnv,
  adminToken: string,
  lifespanSeconds: number,
): Promise<{ uuid: string; clientId: string; secret: string }> {
  const clientId = `dhealth-sec001-shortlived-${Date.now()}`
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
      serviceAccountsEnabled: false,
      description:
        'DHEALTH-NATIVE-SEC-001 qualification-harness-only client. Short access-token lifespan, ' +
        'per-client, so the realm default is untouched. Deleted at the end of the run.',
      attributes: { 'access.token.lifespan': String(lifespanSeconds) },
      protocolMappers: [
        {
          name: 'exchange-api-audience',
          protocol: 'openid-connect',
          protocolMapper: 'oidc-audience-mapper',
          config: {
            'included.client.audience': 'dhealth-exchange-api',
            'access.token.claim': 'true',
            'id.token.claim': 'false',
          },
        },
      ],
    }),
  })
  if (!createResponse.ok) {
    const body = await createResponse.text().catch(() => '')
    throw new Error(`create short-lived client failed: ${createResponse.status} ${body}`)
  }
  const uuid = createResponse.headers.get('Location')?.split('/').pop()
  if (!uuid) throw new Error('create short-lived client: no client uuid in Location')

  const secretResponse = await adminFetch(env, adminToken, `/clients/${uuid}/client-secret`)
  const secret = ((await secretResponse.json()) as { value: string }).value
  return { uuid, clientId, secret }
}

export async function deleteClientByUuid(
  env: KeycloakAdminEnv,
  adminToken: string,
  uuid: string,
): Promise<void> {
  await adminFetch(env, adminToken, `/clients/${uuid}`, { method: 'DELETE' })
}

/** Real password grant against the real token endpoint. Used only where the claim is about the
 * API rather than the browser; every browser claim goes through real Authorization Code + PKCE. */
export async function mintPasswordToken(
  env: KeycloakAdminEnv,
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
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`password grant for ${username} failed: ${response.status} ${body}`)
  }
  return ((await response.json()) as { access_token: string }).access_token
}

// --- Exchange-side revocation states the product cannot reach (see file header) ---

export function findMembershipId(organizationId: string, identityId: string): string {
  return exchangeSql(
    `SELECT id FROM exchange_organization_memberships
      WHERE organization_id = '${organizationId}' AND identity_id = '${identityId}'`,
  )
}

export function setMembershipStatus(membershipId: string, status: 'ACTIVE' | 'INACTIVE'): void {
  exchangeSql(
    `UPDATE exchange_organization_memberships SET status = '${status}' WHERE id = '${membershipId}'`,
  )
}

export function listRoles(membershipId: string): string[] {
  const out = exchangeSql(
    `SELECT role FROM exchange_organization_role_assignments WHERE membership_id = '${membershipId}'`,
  )
  return out ? out.split('\n').map((r) => r.trim()).filter(Boolean) : []
}

export function removeRole(membershipId: string, role: string): void {
  exchangeSql(
    `DELETE FROM exchange_organization_role_assignments
      WHERE membership_id = '${membershipId}' AND role = '${role}'`,
  )
}

export function restoreRole(membershipId: string, role: string): void {
  exchangeSql(
    `INSERT INTO exchange_organization_role_assignments (id, membership_id, role, created_at)
      SELECT gen_random_uuid(), '${membershipId}', '${role}', now()
      WHERE NOT EXISTS (
        SELECT 1 FROM exchange_organization_role_assignments
         WHERE membership_id = '${membershipId}' AND role = '${role}')`,
  )
}

/** Revokes a patient-access grant the way the product's own revoke path does: by stamping
 * `revoked_at`. Used by SEC-12. */
export function revokePatientGrantsForOrder(labOrderId: string): void {
  exchangeSql(
    `UPDATE exchange_patient_access_grants SET revoked_at = now()
      WHERE lab_order_id = '${labOrderId}' AND revoked_at IS NULL`,
  )
}

/**
 * Number of lines currently in the adapter's file-backed **refusal** audit
 * (`exchange/audit.py`'s writer, `/var/lib/daher/audit/exchange-audit.jsonl`). Returns -1 when the
 * file cannot be read at all, so a test asserting "the count went up" cannot pass vacuously
 * against a missing volume.
 *
 * Read through the container rather than the host: the file is mode 0600 under the adapter's own
 * uid inside a named volume, which is exactly the durability property DHEALTH-NATIVE-RECOVERY-001
 * backs up and this ticket must not weaken.
 */
export function auditLineCount(): number {
  try {
    const out = execFileSync(
      'sudo',
      [
        'docker',
        'exec',
        '-i',
        process.env.SEC_ADAPTER_CONTAINER || 'dhealth-native-demo-adapter-1',
        'sh',
        '-c',
        'wc -l < /var/lib/daher/audit/exchange-audit.jsonl',
      ],
      { encoding: 'utf-8', timeout: 30_000 },
    )
    return Number.parseInt(out.trim(), 10)
  } catch {
    return -1
  }
}

/**
 * Adds the `dhealth-exchange-api` audience mapper to an existing client.
 *
 * This is finding H1's fix, applied at the call site rather than by editing `adminApi.ts`:
 * `createSetupClient` there builds a perfectly good service-account client and omits the mapper,
 * so tokens minted from it carry no audience Exchange accepts and are refused for a reason
 * unrelated to whatever the test believed it was checking. That helper has never run against a
 * live Keycloak (DH-CLINIC-004B never executed), which is how it survived. Any test that mints a
 * token from it must call this first.
 */
export async function addExchangeAudienceMapper(
  env: KeycloakAdminEnv,
  adminToken: string,
  clientUuid: string,
): Promise<void> {
  const response = await adminFetch(
    env,
    adminToken,
    `/clients/${clientUuid}/protocol-mappers/models`,
    {
      method: 'POST',
      body: JSON.stringify({
        name: 'exchange-api-audience',
        protocol: 'openid-connect',
        protocolMapper: 'oidc-audience-mapper',
        config: {
          'included.client.audience': 'dhealth-exchange-api',
          'access.token.claim': 'true',
          'id.token.claim': 'false',
        },
      }),
    },
  )
  if (!response.ok && response.status !== 409) {
    const body = await response.text().catch(() => '')
    throw new Error(`add audience mapper failed: ${response.status} ${body}`)
  }
}
