import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-NATIVE-ACCESS-001 — the staff access lifecycle against the real stack.
 *
 * A fourth config alongside `native-journey.config.ts` (the clinical workflow),
 * `native-recovery.config.ts` (a just-restored deployment) and `native-security.config.ts`
 * (adversarial identity/session/route behaviour), for the same reason those three are separate
 * from one another: each answers a different question, and each earns a claim that should be
 * separately reportable.
 *
 * ── Why not simply widen `native-security.config.ts`'s `testMatch` ──
 *
 * That suite's claim is "31 security tests exercised these boundaries", and
 * `docs/NATIVE-SECURITY-QUALIFICATION.md` reports it as such. Folding this ticket's specs into it
 * would silently change what that number means and would make one green run stand for two
 * different claims. The two suites also prove genuinely different things and both are needed:
 * DHEALTH-NATIVE-SEC-001's SEC-6/SEC-7 prove the backend **fails closed given** a withdrawn
 * membership or role (producing that state with direct SQL, because nothing else could); this
 * suite proves an administrator can **reach** that state through the product. Neither subsumes the
 * other, and neither is rewritten to use the other's mechanism.
 *
 * Zero mocking, exactly like its three siblings: real Chromium, real Caddy, real Keycloak 26.7.1,
 * real Exchange adapter, real PostgreSQL 16. No token is fabricated, no session injected, no auth
 * state seeded into browser storage. Every revocation below is performed through the product's own
 * supported endpoints by a real administrator holding a real token from a real login.
 */
const baseURL = process.env.E2E_BASE_URL
if (!baseURL) {
  throw new Error(
    'E2E_BASE_URL is required (e.g. https://app.localhost) -- this suite refuses to guess an origin.',
  )
}

export default defineConfig({
  testDir: './native',
  testMatch: /07-access.*\.spec\.ts/,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  // Serial, and not negotiable. Every test here mutates the same two organizations' membership and
  // role state; two running at once would race each other's setup and teardown, and a failure
  // would be indistinguishable from the product misbehaving.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.E2E_REPORT_JSON || 'native-access-results.json' }],
  ],
  use: {
    baseURL,
    // Caddy's internal CA for a non-public BASE_DOMAIN, exactly as the sibling configs explain.
    // Accepting it is not a claim about public certificate issuance, which nothing here tests.
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    ...devices['Desktop Chrome'],
  },
})
