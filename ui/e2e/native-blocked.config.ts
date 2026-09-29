import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-LAB-BLOCKED-001 — the laboratory cannot-proceed workflow against the real stack.
 *
 * A sixth config alongside `native-journey.config.ts` (the diagnostic exchange workflow),
 * `native-recovery.config.ts`, `native-security.config.ts`, `native-access.config.ts`,
 * `native-visit.config.ts`, `native-billing.config.ts` and `native-capabilities.config.ts`, for the
 * same reason those are separate from one another: each answers a different question, and each
 * earns a claim that should be separately reportable.
 *
 * ── Why not widen `native-journey.config.ts` ──
 *
 * That suite's claim is the happy path: a provider orders, a laboratory accepts, enters, releases,
 * and a clinician reviews. This one's claim is the *unhappy* path and its recovery — an order that
 * stops, is honestly recorded as stopped, refuses to advance while it stands, and then resumes.
 * Folding them together would make one green run stand for two different claims and would change
 * what the journey suite's own number means.
 *
 * ── This suite is not idempotent, and cannot be ──
 *
 * A block episode is never deleted: `resolve` resolves it and no delete path exists, by design.
 * `scripts/demo/reset.sh` clears `exchange_lab_order_blocks` as workflow state
 * (`demo_seed._WORKFLOW_TABLES`, extended by this ticket), which is the supported way back to the
 * baseline before a re-run. The block note is made unique per run so a database carrying earlier
 * runs still yields unambiguous selectors.
 *
 * Zero mocking, exactly like its siblings: real Chromium, real Caddy, real Keycloak 26.7.1, real
 * Exchange adapter, real PostgreSQL 16. No token is fabricated and no session injected. The two
 * direct `/exchange/v1` calls in the spec assert a *refusal* the browser cannot demonstrate on its
 * own — see that file's own header for why.
 */
const baseURL = process.env.E2E_BASE_URL
if (!baseURL) {
  throw new Error(
    'E2E_BASE_URL is required (e.g. https://app.localhost) -- this suite refuses to guess an origin.',
  )
}

export default defineConfig({
  testDir: './native',
  testMatch: /11-lab-blocked.*\.spec\.ts/,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  // Serial, and not negotiable. The phases are one order carried forward: B blocks what A created,
  // C asserts refusals against that block, E resolves it, F completes it. Running them in parallel
  // would not merely race -- it would be meaningless.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.E2E_REPORT_JSON || 'native-blocked-results.json' }],
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
