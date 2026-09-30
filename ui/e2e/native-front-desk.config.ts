import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-FRONT-DESK-001 — walk-in arrival against the real stack.
 *
 * An eighth config alongside `native-journey`, `native-recovery`, `native-security`,
 * `native-access`, `native-visit`, `native-billing`, `native-capabilities` and `native-blocked`, for
 * the same reason those are separate from one another: each answers a different question and each
 * earns a claim that should be separately reportable.
 *
 * ── Why not widen `native-visit.config.ts` ──
 *
 * That suite's claim is that a **clinician** can run a consultation. This one's is that a member who
 * holds **no clinical role and no role named for a front desk** can walk a patient from the door into
 * that clinician's hands — and is genuinely unable to read what the clinician then writes. Folding
 * them together would make one green run stand for two claims, and would require a single account to
 * hold both authorities, which is the separation ADR-069 §2 exists to keep.
 *
 * ── This suite restores everything it creates, and that is not optional ──
 *
 * It removes its capability overrides, its visits, and the patient FD-B5 registers. The patient
 * matters most and is the least obvious: `demo_seed reset` deliberately keeps `exchange_patients` as
 * baseline, the patient list is ordered newest-first, and both `01-golden-journey` and
 * `11-lab-blocked` select a patient with `.first()`. A patient left behind here therefore silently
 * redirects those suites onto it, and they fail looking for the baseline patient — which is exactly
 * how this was found, by running the whole browser regression after this suite.
 *
 * FD-B3 additionally asserts the waiting queue is empty before it checks anybody in, so a run against
 * state some earlier abort left behind fails there — correctly, and loudly — rather than quietly
 * passing. `scripts/demo/reset.sh` is still the supported way back to a known baseline.
 *
 * Zero mocking, exactly like its siblings: real Chromium, real Caddy, real Keycloak 26.7.1, real
 * Exchange adapter, real PostgreSQL 16. No token is fabricated, no session injected, no auth state
 * seeded into browser storage. Every arrival is created by clicking the product as a member who
 * signed in through a real Authorization Code + PKCE flow.
 */
const baseURL = process.env.E2E_BASE_URL
if (!baseURL) {
  throw new Error(
    'E2E_BASE_URL is required (e.g. https://app.localhost) -- this suite refuses to guess an origin.',
  )
}

export default defineConfig({
  testDir: './native',
  testMatch: /12-front-desk.*\.spec\.ts/,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  // Serial, and not negotiable. The phases are one patient's morning carried forward: FD-B7 starts
  // the visit FD-B3 created, FD-B8 completes it. Running them in parallel would not merely race --
  // it would be meaningless.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.E2E_REPORT_JSON || 'native-front-desk-results.json' }],
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
