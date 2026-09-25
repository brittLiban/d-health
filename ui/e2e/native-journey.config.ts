import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-NATIVE-MVP-1.0 — the golden journey, in a real browser, against the real native stack.
 *
 * Separate from `playwright.config.ts` on purpose. That config belongs to DH-CLINIC-004B, which
 * qualifies the *auth layer* and provisions its own synthetic organizations and identities through
 * a Keycloak service account in `globalSetup`. This one qualifies the *clinical workflow* against
 * the DH-DEMO-001 native demo, which already seeds its own deterministic baseline — one provider,
 * one laboratory, one catalogue entry, one synthetic patient, three staff accounts — so a second
 * provisioning pass here would fight it.
 *
 * Zero mocking, same as its sibling: `baseURL` must be a real Caddy-fronted `app.<domain>` origin,
 * every login goes through the real Keycloak form, and every clinical transition is performed by
 * clicking the real UI.
 */
const baseURL = process.env.E2E_BASE_URL
if (!baseURL) {
  throw new Error(
    'E2E_BASE_URL is required (e.g. https://app.localhost) -- this suite refuses to guess an origin.',
  )
}

export default defineConfig({
  testDir: './native',
  // Narrowed by DHEALTH-NATIVE-RECOVERY-001, which added `03-`/`04-` specs to the same directory:
  // this config means "the golden journey and its authorization boundaries", and must keep meaning
  // exactly that. The recovery specs have their own config (`native-recovery.config.ts`) because
  // they are only meaningful against a deployment that has just been restored.
  testMatch: /0[12]-.*\.spec\.ts/,
  // A full clinic -> lab -> release -> review -> patient journey through a real browser, real
  // Keycloak round trips and real database writes is not a 30-second test.
  timeout: 240_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: process.env.E2E_REPORT_JSON || 'native-journey-results.json' }]],
  use: {
    baseURL,
    // Caddy issues from its own internal CA for a non-public BASE_DOMAIN, exactly as the demo's
    // own Caddyfile warns. Accepting it here is accepting *that* CA for a local origin -- it is
    // not a claim about public certificate issuance, which no part of this qualification tests.
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    ...devices['Desktop Chrome'],
  },
})
