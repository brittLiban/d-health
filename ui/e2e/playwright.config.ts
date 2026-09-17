import { defineConfig, devices } from '@playwright/test'

/**
 * DH-CLINIC-004B: real-Keycloak, real-Caddy, real-adapter, real-Chromium
 * qualification of the native browser auth layer (ADR-050, DH-CLINIC-004A).
 *
 * This suite is never run by the fast push-triggered CI pipeline (see
 * .github/workflows/ci.yml's own header on why the full clinical stack boot
 * stays out of that path). It is invoked only by
 * scripts/qualify-native-auth.sh, itself invoked either by a developer
 * locally against a real docker compose stack, or by the separate
 * workflow_dispatch-triggered .github/workflows/qualify-native-auth.yml.
 *
 * Zero mocking: `baseURL` must point at a real Caddy-fronted `app.<domain>`
 * origin, never Vite's dev server directly and never a stub server.
 */
const baseURL = process.env.E2E_BASE_URL
if (!baseURL) {
  throw new Error(
    'E2E_BASE_URL is required (e.g. https://app.localhost) -- this suite refuses to guess an origin.',
  )
}

export default defineConfig({
  testDir: '.',
  testMatch: /specs\/.*\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.E2E_REPORT_JSON || 'e2e-results.json' }],
  ],
  globalSetup: require.resolve('./support/global-setup.ts'),
  globalTeardown: require.resolve('./support/global-teardown.ts'),
  use: {
    baseURL,
    // Caddy's Caddyfile issues from its OWN internal CA for a non-public
    // BASE_DOMAIN (documented in caddy/Caddyfile) -- Chromium has no reason to
    // trust that CA, so this suite trusts it explicitly for the qualification
    // run rather than silently accepting any certificate. This is a
    // documented, deliberate transport-trust relaxation for the qualification
    // harness only -- see AC17/evidence log in the ticket handoff, never
    // shipped to a real client and never present outside this test config.
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
