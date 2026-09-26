import { expect, test } from '@playwright/test'
import { waitForEdge } from '../support/edgeReadiness'

/**
 * DHEALTH-NATIVE-SEC-001 — the public edge, the transport, and what an unauthenticated stranger
 * can learn. Nothing here logs in; that is the point. These are the questions an internet caller
 * can ask before holding any credential at all.
 *
 * Every assertion is against the **real Caddy edge** in front of the **real adapter**, not against
 * the Caddyfile's text. A routing claim read out of a config file is a claim about intent; this
 * file only records what the edge actually answered.
 */

const APP = process.env.E2E_BASE_URL ?? 'https://app.localhost'
const EXCHANGE = process.env.E2E_EXCHANGE_URL ?? 'https://exchange.localhost'
const ID = process.env.E2E_KEYCLOAK_PUBLIC_URL ?? 'https://id.localhost'

/**
 * The Docker daemon in this environment has bounced mid-session at least once, restarting all
 * five containers at once; Caddy then answers 502 for ~35s while the adapter and Keycloak bind
 * their ports. A 502 is indistinguishable from "the edge refuses this path" if you only look at
 * the status code, so every describe block gates on the edge genuinely answering first rather than
 * trusting that a previous block left it healthy.
 */

test.describe.configure({ mode: 'serial' })

test.describe('SEC-EDGE: what the public edge publishes', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('SEC-14: the adapter internal health endpoints are not public on the API vhost', async ({
    request,
  }) => {
    // These exist and are healthy inside the Docker network -- `docker compose --wait` gates the
    // whole stack on them. The claim is that the edge does not publish them.
    for (const path of ['/health/live', '/health/ready', '/health/exchange-ready']) {
      const response = await request.get(`${EXCHANGE}${path}`)
      expect(response.status(), `${EXCHANGE}${path} must not be published`).toBe(404)
      // Caddy's own 404, with no body -- the request never reached the adapter at all.
      expect((await response.body()).length).toBe(0)
    }
  })

  test('SEC-14b: no API documentation, schema or metrics surface is published', async ({
    request,
  }) => {
    for (const path of ['/docs', '/redoc', '/openapi.json', '/metrics', '/']) {
      const response = await request.get(`${EXCHANGE}${path}`)
      expect(response.status(), `${EXCHANGE}${path} must not be published`).toBe(404)
      expect((await response.body()).length).toBe(0)
    }
  })

  test('SEC-15: the notification namespace is published at exactly one path, not as a prefix', async ({
    request,
  }) => {
    // The exact Meta callback path is published and fails closed on its own authority (403: no
    // app secret / verify token configured in this deployment). That 403 is the evidence it
    // reached the adapter.
    const exact = await request.get(`${EXCHANGE}/notifications/v1/meta/webhook`)
    expect(exact.status()).toBe(403)

    // Everything else in the namespace is invisible at the edge. Publishing the prefix would hand
    // the edge every future route in it by omission -- DH-NOTIFY-META-001R's explicit reasoning.
    for (const path of [
      '/notifications/v1/typo',
      '/notifications/v1/',
      '/notifications/v1/meta/webhook/extra',
      '/notifications/v1/enrollments',
    ]) {
      const response = await request.get(`${EXCHANGE}${path}`)
      expect(response.status(), `${EXCHANGE}${path} must not be published`).toBe(404)
      expect((await response.body()).length).toBe(0)
    }
  })

  test('SEC-15b: the Connect runtime namespace is not published at the edge', async ({
    request,
  }) => {
    for (const path of ['/connect/v1/deliveries', '/connect/v1/', '/connect/v1/installations']) {
      const response = await request.get(`${EXCHANGE}${path}`)
      expect(response.status()).toBe(404)
      expect((await response.body()).length).toBe(0)
    }
  })

  test('SEC-EDGE-1: the API vhost refuses an unauthenticated Exchange call without leaking why', async ({
    request,
  }) => {
    const response = await request.get(`${EXCHANGE}/exchange/v1/me`)
    expect(response.status()).toBe(401)
    const body = await response.text()
    // One generic message for every authentication failure: `identity_auth.py` collapses missing
    // header, bad signature, wrong issuer, wrong audience and expiry into one exception type
    // precisely so the response cannot be used as a validation oracle.
    expect(body).toContain('invalid or missing credentials')
    for (const leak of ['Traceback', 'SELECT', 'psycopg', 'sqlalchemy', '/app/', 'jwt.', 'Keycloak']) {
      expect(body, `401 body must not leak ${leak}`).not.toContain(leak)
    }
  })
})

test.describe('SEC-EDGE: the application vhost and SPA fallback', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('SEC-EDGE-2: same-origin Exchange routing is live on the app vhost', async ({ request }) => {
    // The property gate 2 rests on: the browser origin serves both the UI and the API, so no
    // cross-origin request -- and therefore no CORS grant -- is needed for the product to work.
    const response = await request.get(`${APP}/exchange/v1/me`)
    expect(response.status()).toBe(401)
    expect(response.headers()['content-type']).toContain('application/json')
  })

  test('SEC-EDGE-3: a typo under the proxied API prefix is a JSON 404, never the SPA', async ({
    request,
  }) => {
    const response = await request.get(`${APP}/exchange/v1/typo`)
    expect(response.status()).toBe(404)
    expect(response.headers()['content-type']).toContain('application/json')
  })

  /**
   * FINDING G1 (P2, disclosed not fixed) — this test PINS current behaviour rather than asserting
   * a property we are happy with.
   *
   * `app.<domain>` ends in a catch-all that serves the SPA, so any API-shaped path that is not
   * under `/exchange/v1/*` returns **200 text/html** -- the 462-byte application shell. The
   * Caddyfile predicts this in its own comments ("a routing mistake wearing the disguise of a
   * working endpoint") and it is exactly the class of defect DH-NOTIFY-META-001R found when a
   * correctly-configured Meta callback terminated at a proxy that answered instead of refusing.
   *
   * It discloses no data: every response is the same public shell an anonymous visitor already
   * gets at `/`. The harm is operational -- an operator who points a callback, a probe or a
   * monitor at the wrong host sees 200 and concludes it works. Recorded as a finding, pinned here
   * so that if anyone later adds a denylist the change is deliberate and this test says so.
   */
  test('SEC-EDGE-4 [G1]: API-shaped paths on the app vhost currently return the SPA shell', async ({
    request,
  }) => {
    for (const path of [
      '/health/ready',
      '/health/live',
      '/notifications/v1/meta/webhook',
      '/api/unknown',
      '/admin/whatever',
    ]) {
      const response = await request.get(`${APP}${path}`)
      expect(response.status(), `${path}: pinned current behaviour`).toBe(200)
      expect(response.headers()['content-type']).toContain('text/html')
    }
    // The shell and nothing but the shell: identical to the anonymous root document, so no
    // additional information is disclosed by any of the paths above.
    const root = await request.get(`${APP}/`)
    const typo = await request.get(`${APP}/api/unknown`)
    expect(await typo.text()).toBe(await root.text())
  })
})

test.describe('SEC-EDGE: effective security headers', () => {
  test.beforeAll(async () => {
    test.setTimeout(300_000)
    await waitForEdge()
  })

  test('SEC-EDGE-5: the edge sets the four headers it claims, on every vhost', async ({
    request,
  }) => {
    for (const url of [`${APP}/`, `${EXCHANGE}/exchange/v1/me`, `${ID}/realms/dhealth-exchange`]) {
      const response = await request.get(url)
      const headers = response.headers()
      expect(headers['strict-transport-security'], url).toBe(
        'max-age=31536000; includeSubDomains',
      )
      expect(headers['x-content-type-options'], url).toBe('nosniff')
      expect(headers['referrer-policy'], url).toBe('same-origin')
      expect(headers['x-frame-options'], url).toBe('SAMEORIGIN')
      // `-Server` in the Caddyfile: the edge does not advertise what it is.
      expect(headers['server'], `${url} must not advertise a server banner`).toBeUndefined()
    }
  })

  test('SEC-EDGE-6: Referrer-Policy is same-origin, so no patient credential can leak outward', async ({
    request,
  }) => {
    // The patient credential travels in the URL *fragment*, which browsers never send in a
    // Referer at all; `same-origin` is the second, independent control that also prevents a path
    // or query from reaching a third-party origin. Both together are what SEC-13 rests on.
    const response = await request.get(`${APP}/`)
    expect(response.headers()['referrer-policy']).toBe('same-origin')
  })

  /**
   * FINDING G4 (P3) — no Content-Security-Policy and no Permissions-Policy on the application
   * origin. Pinned as a deliberate, recorded decision rather than silently absent: §19 of the
   * ticket forbids guessing a CSP from theory, and the resource inventory needed to write a
   * correct one is a separate piece of work. Keycloak sets its own CSP on its own origin
   * (`frame-src 'self'; frame-ancestors 'self'; object-src 'none'`) and that is observed below.
   */
  test('SEC-EDGE-7 [G4]: the app origin has no CSP today, and Keycloak governs its own', async ({
    request,
  }) => {
    const app = await request.get(`${APP}/`)
    expect(app.headers()['content-security-policy']).toBeUndefined()
    expect(app.headers()['permissions-policy']).toBeUndefined()

    // Keycloak's own framing policy is intact and is NOT overridden by the edge. The
    // authorization endpoint called without parameters answers 400 with Keycloak's own error
    // page, and that page carries the policy -- which is the thing being asserted. Adding
    // parameters would move this to a redirect and assert nothing.
    const kc = await request.get(`${ID}/realms/dhealth-exchange/protocol/openid-connect/auth`)
    expect(kc.status()).toBe(400)
    expect(kc.headers()['content-security-policy']).toContain("frame-ancestors 'self'")
  })

  test('SEC-EDGE-8: framing is refused for a third-party origin by X-Frame-Options', async ({
    request,
  }) => {
    // SAMEORIGIN on every vhost. The UI never frames Keycloak -- `ExchangeAuthProvider` sets
    // `checkLoginIframe: false`, so the silent-SSO iframe that would otherwise need
    // app.<domain> to frame id.<domain> is not used at all, and the two policies cannot conflict.
    const response = await request.get(`${APP}/`)
    expect(response.headers()['x-frame-options']).toBe('SAMEORIGIN')
  })

  test('SEC-EDGE-9: the Exchange grants no CORS access to a foreign origin', async ({ request }) => {
    // Native is same-origin by construction (SEC-EDGE-2). The correct result is that a
    // cross-origin caller is granted nothing -- no ACAO header, and certainly not with
    // credentials. An attacker origin must not be echoed back.
    const response = await request.get(`${EXCHANGE}/exchange/v1/me`, {
      headers: { Origin: 'https://attacker.example' },
    })
    const headers = response.headers()
    expect(headers['access-control-allow-origin']).toBeUndefined()
    expect(headers['access-control-allow-credentials']).toBeUndefined()

    const preflight = await request.fetch(`${EXCHANGE}/exchange/v1/me`, {
      method: 'OPTIONS',
      headers: {
        Origin: 'https://attacker.example',
        'Access-Control-Request-Method': 'GET',
        'Access-Control-Request-Headers': 'authorization',
      },
    })
    expect(preflight.headers()['access-control-allow-origin']).toBeUndefined()
  })
})
