import { expect, test } from '@playwright/test'
import { getMasterAdminToken, readSetupEnv, revokeAllSessionsForUser } from '../support/adminApi'
import { loginViaRealKeycloak } from '../support/login'
import { readState } from '../support/state'

test.describe('session lifecycle against the real Keycloak session (E2E-8..E2E-11)', () => {
  test('E2E-8: a real token refresh keeps the session usable past the short test-only expiry', async ({
    page,
  }) => {
    const state = readState()
    const lifespan = state.shortAccessTokenLifespanSeconds
    if (!lifespan) {
      test.skip(true, 'requires E2E_SHORT_TOKEN_LIFESPAN_SECONDS to have been set for this qualification run')
      return
    }

    await page.goto('/?app=clinic')
    await loginViaRealKeycloak(page, state.clinicianA.email, state.clinicianA.password)
    await expect(page.getByText('Accueil Clinique')).toBeVisible({ timeout: 20_000 })

    // Outlive the short test-only access-token lifespan (never the
    // production realm's 300s default — see adminApi.ts's
    // setShortAccessTokenLifespan docstring) WITHOUT reloading the page: a
    // reload would re-run check-sso from scratch, which proves nothing about
    // mid-session refresh-token-grant behavior. Instead trigger a second
    // in-SPA authenticated call — exchangeTokenProvider.ts's
    // getAuthToken/updateToken(30) now has <30s of validity left and must
    // perform a REAL refresh_token-grant call against the real Keycloak
    // before this new request can carry a valid Authorization header.
    await page.waitForTimeout((lifespan + 5) * 1000)

    const ordersResponse = page.waitForResponse((r) => r.url().includes('/exchange/v1/organizations/'))
    await page.getByRole('button', { name: 'Ordres de laboratoire' }).first().click()
    const response = await ordersResponse
    expect(response.status()).toBe(200)
    await expect(page.getByText('Ordres de laboratoire', { exact: true })).toBeVisible({
      timeout: 20_000,
    })
  })

  test('E2E-9: revoking the Keycloak session server-side fails the next refresh closed', async ({
    page,
  }) => {
    const state = readState()
    const env = readSetupEnv()
    const lifespan = state.shortAccessTokenLifespanSeconds
    if (!lifespan) {
      test.skip(true, 'requires E2E_SHORT_TOKEN_LIFESPAN_SECONDS to have been set for this qualification run')
      return
    }

    await page.goto('/?app=clinic')
    await loginViaRealKeycloak(page, state.clinicianA.email, state.clinicianA.password)
    await expect(page.getByText('Accueil Clinique')).toBeVisible({ timeout: 20_000 })

    const adminToken = await getMasterAdminToken(env)
    await revokeAllSessionsForUser(env, adminToken, state.clinicianA.id)

    // Deliberately no page.reload() here: a reload would re-run check-sso
    // from scratch and simply redirect to a fresh login, which proves
    // nothing about refresh-time failure handling. Instead: outlive the
    // short-lived access token WITHOUT reloading, then trigger a fresh
    // authenticated action in the same SPA session. exchangeTokenProvider.ts's
    // updateToken(30) now attempts a real refresh against the revoked
    // Keycloak session, which Keycloak rejects -- onRefreshFailure fires,
    // returning null (never a stale token), and ExchangeAuthProvider reports
    // status 'error' with a truthful re-authenticate message.
    await page.waitForTimeout((lifespan + 5) * 1000)
    await page.getByRole('button', { name: 'Ordres de laboratoire' }).first().click()

    await expect(
      page.getByText(/session .* a expiré|Connexion impossible|Session .* terminée/i),
    ).toBeVisible({ timeout: 20_000 })
  })

  test('E2E-10: logout ends only the Exchange SSO session, never navigates to OpenMRS/OpenELIS paths', async ({
    page,
  }) => {
    const state = readState()
    await page.goto('/?app=clinic')
    await loginViaRealKeycloak(page, state.clinicianA.email, state.clinicianA.password)
    await expect(page.getByText('Accueil Clinique')).toBeVisible({ timeout: 20_000 })

    const navigations: string[] = []
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame()) navigations.push(frame.url())
    })

    // ClinicShell.tsx's real "Se déconnecter" button -- wired by App.tsx to
    // auth.logout (Exchange-only), never to
    // logoutLocalSessions/navigateToKeycloakEndSession (that pair belongs to
    // the separate Maternity/reference-connector shell's own logout button).
    await page.getByRole('button', { name: 'Se déconnecter' }).click()
    await page.waitForURL(/\/realms\/dhealth-exchange\/protocol\/openid-connect\/logout/, {
      timeout: 20_000,
    })

    for (const url of navigations) {
      expect(url).not.toMatch(/\/openmrs\//i)
      expect(url).not.toMatch(/OpenELIS-Global/i)
      if (url.includes('/realms/')) {
        expect(url).toContain('/realms/dhealth-exchange/')
      }
    }
  })

  test('E2E-11: switching away from and back to Clinic reuses the SSO session (no second login redirect)', async ({
    page,
  }) => {
    const state = readState()
    await page.goto('/?app=clinic')
    await loginViaRealKeycloak(page, state.clinicianA.email, state.clinicianA.password)
    await expect(page.getByText('Accueil Clinique')).toBeVisible({ timeout: 20_000 })

    // App.tsx: ClinicShell's "Mode Maternité (OpenMRS)" button only unmounts
    // ExchangeAuthProvider (onSwitchApp) — it must never call
    // keycloak.logout(). Re-entering Clinic via the Maternity shell's own
    // "DHealth Clinique (Natif)" button must reuse the still-live Keycloak
    // session through check-sso, silently, with no second visible redirect
    // to the login form.
    let sawSecondLoginRedirect = false
    page.on('request', (req) => {
      if (req.url().includes('/protocol/openid-connect/auth')) sawSecondLoginRedirect = true
    })

    await page.getByRole('button', { name: 'Mode Maternité (OpenMRS)' }).click()
    await page.getByRole('button', { name: 'DHealth Clinique (Natif)' }).click()
    await expect(page.getByText('Accueil Clinique')).toBeVisible({ timeout: 20_000 })

    expect(sawSecondLoginRedirect).toBe(false)
  })
})
