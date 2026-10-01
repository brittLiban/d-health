import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-SPECIMEN-001 — physical specimen collection against the real stack.
 *
 * An eleventh config alongside `native-journey.config.ts`, `native-recovery.config.ts`,
 * `native-security.config.ts`, `native-access.config.ts`, `native-visit.config.ts`,
 * `native-billing.config.ts`, `native-capabilities.config.ts`, `native-blocked.config.ts`,
 * `native-front-desk.config.ts` and `native-reception.config.ts`, for the same reason those are
 * separate from one another: each answers a different question and earns a separately reportable
 * claim.
 *
 * ── Why not widen `native-blocked.config.ts` ──
 *
 * The blocked suite's claim is that a laboratory can say "we cannot continue this" and be believed.
 * This one's claim starts where that ends and is the thing that was previously impossible: that a
 * *second* physical specimen can be recorded without the first one disappearing. The blocked
 * workflow appears here as a means rather than as the subject -- it is how the suite reaches a
 * legitimate second collection without inventing any semantics. Folding them together would make one
 * green run stand for two different claims and would change what the existing numbers mean.
 *
 * ── This suite is not idempotent, and says so ──
 *
 * It leaves one lab order behind, carried to `IN_PROGRESS`, with two specimen rows and one resolved
 * block episode. Nothing deletes a lab order, a specimen or a block episode, by design --
 * `scripts/demo/reset.sh` clears all three as workflow state (`demo_seed._WORKFLOW_TABLES`, extended
 * by this ticket for `exchange_lab_specimens`), which is the supported way back to the baseline.
 *
 * Unlike `native-reception.config.ts` this suite grants no capability and therefore has nothing to
 * take back: every act is performed by `demo-lab-tech`, who already holds `lab.queue.work`, and the
 * one authorization refusal it asserts uses `demo-lab-clerk` exactly as the baseline seeds them.
 * `exchange_membership_capability_overrides` is never written here.
 *
 * Zero mocking: real Chromium, real Caddy, real Keycloak 26.7.1, real Exchange adapter, real
 * PostgreSQL 16. No token is fabricated and no session injected. The direct `/exchange/v1` calls in
 * the spec read authoritative state or assert refusals the browser cannot demonstrate on its own --
 * see that file's own header for which is which.
 */
const baseURL = process.env.E2E_BASE_URL
if (!baseURL) {
  throw new Error(
    'E2E_BASE_URL is required (e.g. https://app.localhost) -- this suite refuses to guess an origin.',
  )
}

export default defineConfig({
  testDir: './native',
  testMatch: /14-lab-specimen.*\.spec\.ts/,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  // Serial, and not negotiable. The phases are one physical sample's life carried forward: A routes
  // the work, A2 accepts it, B collects, F blocks for an unusable sample, F2 resolves, F3 collects
  // again and asserts BOTH survive. Running them in parallel would not merely race -- the central
  // assertion would have nothing to assert.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.E2E_REPORT_JSON || 'native-specimen-results.json' }],
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
