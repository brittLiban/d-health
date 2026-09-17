import { expect, test } from '@playwright/test'
import { loginViaRealKeycloak } from '../support/login'
import { readState } from '../support/state'

test.describe('adapter validation, routing, and the auth gate (E2E-4..E2E-7)', () => {
  test('E2E-4: the adapter accepts the real, JWKS-validated token (not a static test key)', async ({
    page,
  }) => {
    const state = readState()
    await page.goto('/?app=clinic')
    await loginViaRealKeycloak(page, state.clinicianA.email, state.clinicianA.password)

    // The adapter's TokenValidator (identity_auth.py) is deployed in this
    // qualification stack wired to EXCHANGE_OIDC_JWKS_URL against the real
    // Keycloak (docker-compose.yml), never a StaticKeySource -- so a
    // successful authenticated render here is only possible if the adapter
    // fetched the real JWKS and verified the real signature against it.
    await expect(page.getByText('Accueil Clinique')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByText(state.providerOrgA.name)).toBeVisible()
  })

  test('E2E-5: /exchange/v1/* is reached same-origin under app.<domain>, no CORS involved', async ({
    page,
  }) => {
    const state = readState()
    const meResponses: number[] = []
    const exchangeRequestMethods: string[] = []
    page.on('response', (response) => {
      if (response.url().includes('/exchange/v1/me')) meResponses.push(response.status())
    })
    page.on('request', (req) => {
      if (req.url().includes('/exchange/v1/')) exchangeRequestMethods.push(req.method())
    })

    await page.goto('/?app=clinic')
    await loginViaRealKeycloak(page, state.clinicianA.email, state.clinicianA.password)
    await expect(page.getByText('Accueil Clinique')).toBeVisible({ timeout: 20_000 })

    expect(meResponses).toContain(200)
    // A same-origin GET never triggers a browser CORS preflight; a
    // cross-origin call (e.g. against exchange.<domain>) would have forced
    // Chromium to send an OPTIONS request first. It never does here.
    expect(exchangeRequestMethods).not.toContain('OPTIONS')
    expect(new URL(page.url()).hostname).toBe(new URL(process.env.E2E_BASE_URL!).hostname)
  })

  test('E2E-6: negative control — an unrelated adapter endpoint is not exposed through app.<domain>', async ({
    request,
  }) => {
    // caddy/Caddyfile's app.{$BASE_DOMAIN} block scopes ONLY /exchange/v1/*
    // to the adapter; the adapter's own /health/ready must not be reachable
    // through this vhost, or a future adapter route could become public by
    // omission the same way ci.yml's DH-EX-008 static check already guards.
    const response = await request.get(`${process.env.E2E_BASE_URL}/health/ready`, {
      failOnStatusCode: false,
    })
    expect(response.status()).toBe(404)
  })

  test('E2E-7: the auth gate blocks any /exchange/v1/* call until authentication completes', async ({
    page,
  }) => {
    const exchangeRequestsBeforeAuth: string[] = []
    let authenticated = false
    page.on('request', (req) => {
      if (!authenticated && req.url().includes('/exchange/v1/')) {
        exchangeRequestsBeforeAuth.push(req.url())
      }
    })

    const state = readState()
    await page.goto('/?app=clinic')
    // At this point the gate must be showing the loading/redirecting state,
    // never the authenticated Clinic UI, and no /exchange/v1/* call may have
    // fired yet (ExchangeAuthGate.tsx renders children only once
    // status === 'authenticated').
    expect(exchangeRequestsBeforeAuth).toEqual([])

    await loginViaRealKeycloak(page, state.clinicianA.email, state.clinicianA.password)
    await expect(page.getByText('Accueil Clinique')).toBeVisible({ timeout: 20_000 })
    authenticated = true

    expect(exchangeRequestsBeforeAuth).toEqual([])
  })
})
