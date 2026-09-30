import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-LAB-RECEPTION-001 — laboratory work intake against the real stack.
 *
 * A tenth config alongside `native-journey.config.ts`, `native-recovery.config.ts`,
 * `native-security.config.ts`, `native-access.config.ts`, `native-visit.config.ts`,
 * `native-billing.config.ts`, `native-capabilities.config.ts`, `native-blocked.config.ts` and
 * `native-front-desk.config.ts`, for the same reason those are separate from one another: each
 * answers a different question, and each earns a claim that should be separately reportable.
 *
 * ── Why not widen `native-journey.config.ts` or `native-blocked.config.ts` ──
 *
 * The journey suite's claim is the happy path end to end, performed by people who already hold the
 * authority for each step. This one's claim is narrower and earlier: that the *beginning* of a
 * laboratory day works, and that it works for somebody whose job title says nothing about a
 * laboratory — a cashier handed exactly `lab.queue.work` and nothing else. The blocked suite owns
 * the unhappy path after acceptance. Folding any of them together would make one green run stand for
 * several different claims and would change what the existing numbers mean.
 *
 * ── This suite is not idempotent, and says so ──
 *
 * It leaves two real lab orders behind, one accepted and collected and one refused, because nothing
 * deletes a lab order by design. `scripts/demo/reset.sh` clears them as workflow state, which is the
 * supported way back to the baseline — and LR-B4 asserts the incoming queue holds exactly this run's
 * two orders, so a re-run without a reset fails loudly rather than passing against leftover state.
 *
 * The capability grant is the one thing the suite removes itself, in `afterAll` and again in
 * `beforeAll`: `exchange_membership_capability_overrides` is absent from
 * `demo_seed._WORKFLOW_TABLES`, so a reset would leave it standing and `09-billing.spec.ts` would
 * then be asserting against a cashier this suite had quietly promoted.
 *
 * Zero mocking, exactly like its siblings: real Chromium, real Caddy, real Keycloak 26.7.1, real
 * Exchange adapter, real PostgreSQL 16. No token is fabricated and no session injected. The two
 * direct `/exchange/v1` assertions in the spec assert *refusals* the browser cannot demonstrate on
 * its own — see that file's own header for why.
 */
const baseURL = process.env.E2E_BASE_URL
if (!baseURL) {
  throw new Error(
    'E2E_BASE_URL is required (e.g. https://app.localhost) -- this suite refuses to guess an origin.',
  )
}

export default defineConfig({
  testDir: './native',
  testMatch: /13-lab-reception.*\.spec\.ts/,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  // Serial, and not negotiable. The phases are one working day carried forward: B2 routes the work
  // B3 grants the authority for, B4 finds it, B5 orders it, B6 accepts it, B8 declines the other,
  // B9 continues the first and B10 takes the authority back. Running them in parallel would not
  // merely race -- it would be meaningless.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.E2E_REPORT_JSON || 'native-reception-results.json' }],
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
