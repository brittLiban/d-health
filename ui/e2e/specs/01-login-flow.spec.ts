import { expect, test } from '@playwright/test'
import { decodeJwtClaimsOnly } from '../support/jwt'
import { captureFirstExchangeBearerToken, loginViaRealKeycloak } from '../support/login'
import { readState } from '../support/state'

/**
 * DH-CLINIC-004B E2E-1/E2E-2/E2E-3: the real Authorization Code + PKCE S256
 * browser round trip against a real Keycloak, with zero mocking, zero token
 * injection, and zero localStorage seeding. This is the single most
 * important proof in this suite -- everything else in this directory
 * verifies a property of a session established exactly this way.
 */
test.describe('real browser login (E2E-1, E2E-2, E2E-3)', () => {
  test('E2E-1: unauthenticated visit redirects to a real Keycloak login and returns authenticated', async ({
    page,
  }) => {
    const state = readState()

    const authRequest = page.waitForRequest((req) =>
      req.url().includes('/realms/dhealth-exchange/protocol/openid-connect/auth'),
    )

    await page.goto('/?app=clinic')

    // E2E-2: the real authorization request Keycloak received -- inspected
    // from the actual outgoing browser request, not reconstructed/assumed.
    const request = await authRequest
    const authUrl = new URL(request.url())
    expect(authUrl.searchParams.get('response_type')).toBe('code')
    expect(authUrl.searchParams.get('client_id')).toBe('dhealth-native-ui')
    expect(authUrl.searchParams.get('code_challenge_method')).toBe('S256')
    expect(authUrl.searchParams.get('code_challenge')).toBeTruthy()
    // A secret-bearing confidential-client param must never appear on a
    // public PKCE client's authorization request.
    expect(authUrl.searchParams.has('client_secret')).toBe(false)

    await loginViaRealKeycloak(page, state.clinicianA.email, state.clinicianA.password)

    await expect(page.getByText('Accueil Clinique')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByText(state.providerOrgA.name)).toBeVisible()
  })

  test('E2E-3: the real issued token carries the expected issuer/audience/azp/subject', async ({
    page,
  }) => {
    const state = readState()
    const tokenPromise = captureFirstExchangeBearerToken(page)

    await page.goto('/?app=clinic')
    await loginViaRealKeycloak(page, state.clinicianA.email, state.clinicianA.password)
    await expect(page.getByText('Accueil Clinique')).toBeVisible({ timeout: 20_000 })

    const token = await tokenPromise
    const { payload } = decodeJwtClaimsOnly(token)

    expect(String(payload.iss)).toMatch(/\/realms\/dhealth-exchange$/)
    expect(payload.azp).toBe('dhealth-native-ui')
    // DH-CLINIC-004A/ADR-050: a different client (azp) from dhealth-exchange-api,
    // but the SAME audience via the realm's audience mapper -- the exact
    // property adapter/tests/test_exchange_token_validator.py proved at the
    // unit level; this is that same claim, on a real Keycloak-issued token.
    const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud]
    expect(aud).toContain('dhealth-exchange-api')
    expect(payload.sub).toBeTruthy()
    // Never assert on, log, or return the token itself beyond this point.
  })
})
