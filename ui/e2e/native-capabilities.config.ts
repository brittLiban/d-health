import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-ACCESS-CAPABILITIES-001 — capability-driven access against the real stack.
 *
 * A sixth config alongside `native-journey.config.ts` (the diagnostic exchange workflow),
 * `native-recovery.config.ts` (a just-restored deployment), `native-security.config.ts`
 * (adversarial identity/session/route behaviour), `native-access.config.ts` (the staff access
 * lifecycle) and `native-visit.config.ts` (the clinic consultation), for the same reason those five
 * are separate from one another: each answers a different question, and each earns a claim that
 * should be separately reportable.
 *
 * ── Why not widen `native-visit.config.ts` ──
 *
 * That suite's claim is that a *clinician* can run a consultation. This one's is that a *cashier*
 * can take money — a different person, a different authority, and deliberately one who can reach
 * none of the screens that suite exercises. Folding them together would make one green run stand
 * for two claims and would quietly require a single account to hold both authorities, which is the
 * separation ADR-066 §2 exists to keep.
 *
 * ── This suite is not idempotent, and cannot be ──
 *
 * An invoice and a payment have no delete endpoint, deliberately, because financial history is
 * append-only. So unlike `07-access-lifecycle.spec.ts` this suite cannot restore what it created.
 * `scripts/demo/reset.sh` clears the financial tables as workflow state
 * (`demo_seed._WORKFLOW_TABLES`, extended by this ticket), which is the supported way back to the
 * baseline before a re-run. BILL-L1 asserts that the laboratory's first invoice number ends in
 * `000001`, so a re-run *without* a reset will fail there — correctly, and loudly, rather than
 * quietly passing against leftover state.
 *
 * Zero mocking, exactly like its five siblings: real Chromium, real Caddy, real Keycloak 26.7.1,
 * real Exchange adapter, real PostgreSQL 16. No token is fabricated, no session injected, no auth
 * state seeded into browser storage. Every financial act is performed by clicking the product as a
 * cashier who signed in through a real Authorization Code + PKCE flow.
 */
const baseURL = process.env.E2E_BASE_URL
if (!baseURL) {
  throw new Error(
    'E2E_BASE_URL is required (e.g. https://app.localhost) -- this suite refuses to guess an origin.',
  )
}

export default defineConfig({
  testDir: './native',
  testMatch: /10-capabilities.*\.spec\.ts/,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  // Serial, and not negotiable. The phases are one facility's day carried forward: BILL-P reprices
  // the service BILL-C1 invoiced, BILL-S bills the order BILL-L1 created. Running them in parallel
  // would not merely race — it would be meaningless.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.E2E_REPORT_JSON || 'native-capabilities-results.json' }],
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
