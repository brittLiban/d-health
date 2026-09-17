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
    // (and /openmrs/*, /OpenELIS-Global/*) to their respective backends;
    // everything else falls through to its catch-all `handle { reverse_proxy
    // daher-ui:80 }`. daher-ui is an SPA server (ui/nginx.conf: `try_files
    // $uri $uri/ /index.html`), so an unmatched path returns 200 + index.html
    // -- NOT a 404 -- which means the real negative-control assertion is on
    // BODY/content-type (never the adapter's own /health/ready JSON), not on
    // status code. Getting this wrong (asserting 404) would make the test
    // fail even when the actual security property holds.
    const response = await request.get(`${process.env.E2E_BASE_URL}/health/ready`, {
      failOnStatusCode: false,
    })
    const body = await response.text()
    expect(body).not.toContain('"status"')
    expect(body).not.toContain('"up"')
    expect(response.headers()['content-type'] ?? '').toContain('text/html')
  })

  test('E2E-7: the auth gate blocks any /exchange/v1/* call until authentication completes', async ({
    page,
  }) => {
    // A vacuous version of this test would just check the array is empty
    // right after goto() -- true whether or not the gate works, since
    // nothing has had time to fire yet. The real, discriminating claim is
    // ORDER: the first real network event must be the redirect to Keycloak,
    // never an /exchange/v1/* call -- and no /exchange/v1/* call may appear
    // before that redirect anywhere in the recorded sequence. If
    // ExchangeAuthGate.tsx were broken and rendered ClinicApp's data layer
    // before status === 'authenticated', ClinicProvider's /me call would
    // race the redirect instead of strictly following it.
    const events: Array<{ type: 'auth-redirect' | 'exchange-request'; url: string }> = []
    page.on('request', (req) => {
      if (req.url().includes('/protocol/openid-connect/auth')) {
        events.push({ type: 'auth-redirect', url: req.url() })
      } else if (req.url().includes('/exchange/v1/')) {
        events.push({ type: 'exchange-request', url: req.url() })
      }
    })

    const state = readState()
    await page.goto('/?app=clinic')
    await loginViaRealKeycloak(page, state.clinicianA.email, state.clinicianA.password)
    await expect(page.getByText('Accueil Clinique')).toBeVisible({ timeout: 20_000 })

    const firstAuthRedirectIndex = events.findIndex((e) => e.type === 'auth-redirect')
    const firstExchangeRequestIndex = events.findIndex((e) => e.type === 'exchange-request')
    expect(firstAuthRedirectIndex).toBeGreaterThanOrEqual(0)
    expect(firstExchangeRequestIndex).toBeGreaterThan(firstAuthRedirectIndex)
  })
})
