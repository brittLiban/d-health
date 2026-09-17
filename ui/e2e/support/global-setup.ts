import {
  callMe,
  createMembership,
  createOrganization,
  createSetupClient,
  createSyntheticUser,
  getMasterAdminToken,
  grantPlatformAdminToServiceAccount,
  mintClientCredentialsToken,
  mintPasswordGrantToken,
  readSetupEnv,
  setShortAccessTokenLifespan,
} from './adminApi'
import { writeState, type QualificationState } from './state'

/**
 * DH-CLINIC-004B qualification harness setup. Runs once before the real
 * Playwright specs. Every write here goes through a real Keycloak Admin API
 * call or a real `/exchange/v1/*` HTTP request carrying a real,
 * validly-issued token -- there is no database bypass and no fabricated
 * authorization anywhere in this file. See adminApi.ts's module docstring
 * for why the grant type differs from the browser's PKCE flow here without
 * that flow itself ever being faked.
 */
export default async function globalSetup(): Promise<void> {
  const env = readSetupEnv()

  const masterAdminToken = await getMasterAdminToken(env)

  const setupClient = await createSetupClient(env, masterAdminToken)
  await grantPlatformAdminToServiceAccount(
    env,
    masterAdminToken,
    setupClient.uuid,
    setupClient.clientId,
  )

  let shortAccessTokenLifespanSeconds: number | null = null
  if (process.env.E2E_SHORT_TOKEN_LIFESPAN_SECONDS) {
    shortAccessTokenLifespanSeconds = Number(process.env.E2E_SHORT_TOKEN_LIFESPAN_SECONDS)
    await setShortAccessTokenLifespan(env, masterAdminToken, shortAccessTokenLifespanSeconds)
  }

  const platformAdminToken = await mintClientCredentialsToken(
    env,
    setupClient.clientId,
    setupClient.secret,
  )

  const clinicianAUser = await createSyntheticUser(env, masterAdminToken, 'clinic-e2e-a')
  const clinicianBUser = await createSyntheticUser(env, masterAdminToken, 'clinic-e2e-b')
  const unassignedUser = await createSyntheticUser(env, masterAdminToken, 'clinic-e2e-unassigned')

  // Materializes each identity's ExchangeIdentity row (get_or_create on first
  // authenticated /me call) and captures the resulting identity_id -- the
  // exact, only, legitimate way to learn it (see adapter api.py's own
  // MembershipCreateRequest docstring on why it is not otherwise discoverable).
  const clinicianAToken = await mintPasswordGrantToken(
    env,
    setupClient.clientId,
    setupClient.secret,
    clinicianAUser.email,
    clinicianAUser.password,
  )
  const clinicianAMe = await callMe(env, clinicianAToken)

  const clinicianBToken = await mintPasswordGrantToken(
    env,
    setupClient.clientId,
    setupClient.secret,
    clinicianBUser.email,
    clinicianBUser.password,
  )
  const clinicianBMe = await callMe(env, clinicianBToken)

  const unassignedToken = await mintPasswordGrantToken(
    env,
    setupClient.clientId,
    setupClient.secret,
    unassignedUser.email,
    unassignedUser.password,
  )
  const unassignedMe = await callMe(env, unassignedToken)

  const providerOrgA = await createOrganization(
    env,
    platformAdminToken,
    'DH-CLINIC-004B E2E Provider A',
  )
  const providerOrgB = await createOrganization(
    env,
    platformAdminToken,
    'DH-CLINIC-004B E2E Provider B',
  )

  await createMembership(env, platformAdminToken, providerOrgA.id, clinicianAMe.identity_id, [
    'CLINICIAN',
  ])
  await createMembership(env, platformAdminToken, providerOrgB.id, clinicianBMe.identity_id, [
    'CLINICIAN',
  ])
  // unassignedUser deliberately receives no membership -- this is E2E-11's
  // truthful no_clinics identity.

  const state: QualificationState = {
    adminToken: masterAdminToken,
    setupClient,
    providerOrgA,
    providerOrgB,
    clinicianA: { ...clinicianAUser, identityId: clinicianAMe.identity_id },
    clinicianB: { ...clinicianBUser, identityId: clinicianBMe.identity_id },
    unassigned: { ...unassignedUser, identityId: unassignedMe.identity_id },
    shortAccessTokenLifespanSeconds,
  }
  writeState(state)

  // eslint-disable-next-line no-console
  console.log(
    `DH-CLINIC-004B qualification setup complete: 2 provider orgs, 3 synthetic identities (1 unassigned), setup client ${setupClient.clientId}.`,
  )
}
