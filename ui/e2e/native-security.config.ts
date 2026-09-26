import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-NATIVE-SEC-001 — Native security qualification against the real stack.
 *
 * A third config alongside `native-journey.config.ts` (the clinical workflow) and
 * `native-recovery.config.ts` (a just-restored deployment), for the same reason those two are
 * separate from each other: each answers a different question and is only meaningful against a
 * particular stack state. This one answers "does the product stay secure when identities,
 * memberships, roles, sessions, tokens, grants and public routes behave adversarially", and it
 * runs against an ordinary, healthy native deployment.
 *
 * Zero mocking, exactly like its siblings: real Chromium, real Caddy, real Keycloak, real
 * Exchange, real PostgreSQL. No token is fabricated, no session injected, no auth state seeded
 * into browser storage.
 */
const baseURL = process.env.E2E_BASE_URL
if (!baseURL) {
  throw new Error(
    'E2E_BASE_URL is required (e.g. https://app.localhost) -- this suite refuses to guess an origin.',
  )
}

export default defineConfig({
  testDir: './native',
  testMatch: /0[56]-security.*\.spec\.ts/,
  // Session and expiry scenarios deliberately wait on real Keycloak lifetimes rather than
  // simulating a clock, so the budget is generous.
  timeout: 300_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.E2E_REPORT_JSON || 'native-security-results.json' }],
  ],
  use: {
    baseURL,
    // Caddy's internal CA for a non-public BASE_DOMAIN, exactly as the journey config explains.
    // Accepting it is not a claim about public certificate issuance, which nothing here tests.
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    ...devices['Desktop Chrome'],
  },
})
