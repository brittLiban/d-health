import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-LAB-OFFERINGS-001 — laboratory service availability against the real stack.
 *
 * A twelfth config alongside its siblings, for the reason each of those gives: every suite answers a
 * different question and earns a separately reportable claim, and folding two together would make
 * one green run stand for both and change what the existing numbers mean.
 *
 * ── Why not widen `native-journey.config.ts` ──
 *
 * The golden journey's claim is that the happy path works end to end. This one's claim is the
 * opposite shape: that turning something **off** stops new work and changes nothing historical. The
 * journey would have to disable a service it then depends on, and the two claims would be entangled.
 *
 * ── This suite restores what it changes, and the one thing it cannot ──
 *
 * The offering is left **active**: `OF-F` re-enables it explicitly rather than relying on
 * `reset.sh`, because the golden journey and every sibling laboratory suite need it accepting. Only
 * a crashed run can leave it off, and `OF-A` fails loudly on the next run if that happened.
 *
 * It cannot restore the lab orders it routes -- nothing deletes a lab order, by design --
 * so it leaves two behind (one accepted, one awaiting a laboratory) plus one unassigned from the
 * refusal phase. `scripts/demo/reset.sh` clears them as workflow state, which is the supported way
 * back to the baseline.
 *
 * Zero mocking: real Chromium, real Caddy, real Keycloak 26.7.1, real Exchange adapter, real
 * PostgreSQL 16. No token is fabricated and no session injected. The direct `/exchange/v1` calls in
 * the spec assert refusals the browser cannot demonstrate on its own, or read authoritative state --
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
  testMatch: /15-lab-offerings.*\.spec\.ts/,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  // Serial, and not negotiable. The phases are one configuration change carried forward: B routes an
  // order while the analysis is accepted, C turns it off, D proves the new order is refused *and*
  // that B's order survived, F restores it. Running them in parallel would leave the central
  // assertion with nothing to assert.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.E2E_REPORT_JSON || 'native-offerings-results.json' }],
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
