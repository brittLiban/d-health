import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-NATIVE-RECOVERY-001 — the recovery specs, in a real browser, against a real stack.
 *
 * Separate from `native-journey.config.ts` because these two specs are only meaningful at specific
 * moments in a destructive drill, and running them at any other time proves nothing:
 *
 *   03-recovery-source.spec.ts   against the SOURCE deployment, after the golden journey and before
 *                                the backup. Creates the durable grant/notification state.
 *   04-recovery-proof.spec.ts    against a deployment that has just been RESTORED from that backup.
 *                                Finds the same clinical records, then writes new ones.
 *
 * `scripts/native/qualify-recovery.sh` owns that ordering. Same no-mocking contract as its sibling:
 * a real Caddy-fronted origin, the real Keycloak login form, real database writes.
 */
const baseURL = process.env.E2E_BASE_URL
if (!baseURL) {
  throw new Error(
    'E2E_BASE_URL is required (e.g. https://app.localhost) -- this suite refuses to guess an origin.',
  )
}

export default defineConfig({
  testDir: './native',
  testMatch: /0[34]-recovery.*\.spec\.ts/,
  // The post-recovery leg drives a whole second golden journey through five role changes, each one
  // a real Keycloak round trip.
  timeout: 300_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.E2E_REPORT_JSON || 'native-recovery-results.json' }],
  ],
  use: {
    baseURL,
    // Caddy's internal CA for a non-public BASE_DOMAIN. Accepting it is accepting THAT CA for a
    // local origin; it is not a claim about public certificate issuance, and a recovery drill that
    // recreates the Caddy volume gets a freshly generated CA, which is itself part of what this
    // ticket records as reconstructible rather than backed up.
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    ...devices['Desktop Chrome'],
  },
})
