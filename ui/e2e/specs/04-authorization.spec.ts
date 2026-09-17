import { expect, test } from '@playwright/test'
import {
  getMasterAdminToken,
  mintClientCredentialsToken,
  mintPasswordGrantToken,
  readSetupEnv,
} from '../support/adminApi'
import { loginViaRealKeycloak } from '../support/login'
import { readState } from '../support/state'

test.describe('E2E-12: authenticated-but-unassigned identity yields a truthful no_clinics state', () => {
  test('an identity with zero ACTIVE PROVIDER/CLINICIAN memberships sees the honest empty state, never an error or auto-privilege', async ({
    page,
  }) => {
    const state = readState()
    await page.goto('/?app=clinic')
    await loginViaRealKeycloak(page, state.unassigned.email, state.unassigned.password)

    await expect(
      page.getByText(
        "Votre compte n'est rattaché à aucune organisation de soins avec le rôle prescripteur (CLINICIAN).",
      ),
    ).toBeVisible({ timeout: 20_000 })
    // Never the authenticated dashboard, never a generic error screen.
    await expect(page.getByText('Établissement prescripteur')).not.toBeVisible()
    await expect(page.getByText('Connexion impossible')).not.toBeVisible()
  })
})

/**
 * DH-CLINIC-004B security negative-control checklist: these run against the
 * live adapter with real, Keycloak-issued tokens (never a fabricated JWT),
 * obtained via the ephemeral setup client's own grants -- see adminApi.ts's
 * module docstring for why that is a legitimate substitute for the browser's
 * PKCE flow in a raw-API context.
 */
test.describe('authorization negative controls (real adapter, real tokens)', () => {
  test('NC-1: no token is rejected with 401', async ({ request }) => {
    const response = await request.get(`${process.env.E2E_ADAPTER_BASE_URL}/me`, {
      failOnStatusCode: false,
    })
    expect(response.status()).toBe(401)
  })

  test('NC-2: a structurally invalid token is rejected with 401', async ({ request }) => {
    const response = await request.get(`${process.env.E2E_ADAPTER_BASE_URL}/me`, {
      headers: { Authorization: 'Bearer not-a-real-jwt' },
      failOnStatusCode: false,
    })
    expect(response.status()).toBe(401)
  })

  test('NC-3: a real token minted for the wrong audience is rejected', async ({ request }) => {
    const env = readSetupEnv()

    // A second, temporary, wrong-audience client is created here (never
    // committed to keycloak/realm-config/, never left in the realm after
    // this test — global-teardown.ts only tracks the ONE setup client, so
    // this test cleans up its own). A fresh admin token is minted rather than
    // reusing global-setup's, which may have expired by the time this
    // (possibly later-running) test executes.
    const masterToken = await getMasterAdminToken(env)
    const clientId = `dhealth-e2e-wrong-audience-${Date.now()}`
    const createResponse = await request.post(
      `${env.keycloakUrl}/admin/realms/${env.realm}/clients`,
      {
        headers: { Authorization: `Bearer ${masterToken}`, 'Content-Type': 'application/json' },
        data: {
          clientId,
          protocol: 'openid-connect',
          enabled: true,
          publicClient: false,
          standardFlowEnabled: false,
          serviceAccountsEnabled: true,
          description: 'DH-CLINIC-004B NC-3 temporary client, deleted at the end of this test.',
        },
      },
    )
    expect(createResponse.ok()).toBe(true)
    const location = createResponse.headers()['location']
    const uuid = location.split('/').pop()!
    const secretResponse = await request.get(
      `${env.keycloakUrl}/admin/realms/${env.realm}/clients/${uuid}/client-secret`,
      { headers: { Authorization: `Bearer ${masterToken}` } },
    )
    const { value: secret } = (await secretResponse.json()) as { value: string }

    try {
      // This client carries NO dhealth-exchange-api audience mapper, so its
      // client_credentials token's `aud` will never contain
      // dhealth-exchange-api -- the exact real-world condition the
      // adapter's TokenValidator must reject.
      const wrongAudienceToken = await mintClientCredentialsToken(env, clientId, secret)
      const response = await request.get(`${env.adapterBaseUrl}/me`, {
        headers: { Authorization: `Bearer ${wrongAudienceToken}` },
        failOnStatusCode: false,
      })
      expect(response.status()).toBe(401)
    } finally {
      await request.delete(`${env.keycloakUrl}/admin/realms/${env.realm}/clients/${uuid}`, {
        headers: { Authorization: `Bearer ${masterToken}` },
      })
    }
  })

  test('NC-4: a real clinician token cannot read a different provider organization\'s orders', async ({
    request,
  }) => {
    const env = readSetupEnv()
    const state = readState()

    const clinicianAToken = await mintPasswordGrantToken(
      env,
      state.setupClient.clientId,
      state.setupClient.secret,
      state.clinicianA.email,
      state.clinicianA.password,
    )

    // DH-EX-002 anti-enumeration: a real organization clinician A has no
    // membership in must 404, indistinguishable from a nonexistent org.
    const response = await request.get(
      `${env.adapterBaseUrl}/organizations/${state.providerOrgB.id}/orders`,
      { headers: { Authorization: `Bearer ${clinicianAToken}` }, failOnStatusCode: false },
    )
    expect(response.status()).toBe(404)
  })
})
