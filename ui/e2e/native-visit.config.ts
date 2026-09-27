import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-CLINIC-VISIT-001 — the clinic visit lifecycle against the real stack.
 *
 * A fifth config alongside `native-journey.config.ts` (the diagnostic exchange workflow),
 * `native-recovery.config.ts` (a just-restored deployment), `native-security.config.ts` (adversarial
 * identity/session/route behaviour) and `native-access.config.ts` (the staff access lifecycle), for
 * the same reason those four are separate from one another: each answers a different question, and
 * each earns a claim that should be separately reportable.
 *
 * ── Why not widen `native-journey.config.ts` ──
 *
 * That suite's claim is about the *exchange*: a provider orders, a laboratory accepts, enters,
 * releases, and a patient reads a result. This one is about the *clinic*: a person arrives, a
 * clinician sees them, writes something down, and finishes. The two overlap at exactly one point —
 * an order raised during a visit — and folding them together would make one green run stand for two
 * different claims while changing what the journey suite's own number means.
 *
 * ── This suite is not idempotent, and cannot be ──
 *
 * Every foreign key pointing at a visit is `RESTRICT`, deliberately: a completed consultation is not
 * erasable, so unlike `07-access-lifecycle.spec.ts` this suite cannot restore what it created.
 * `scripts/demo/reset.sh` clears visits and visit notes as workflow state
 * (`demo_seed._WORKFLOW_TABLES`), which is the supported way back to the baseline before a re-run.
 * Reasons for visit are made unique per run so that a database carrying earlier runs still yields
 * unambiguous selectors.
 *
 * Zero mocking, exactly like its four siblings: real Chromium, real Caddy, real Keycloak 26.7.1,
 * real Exchange adapter, real PostgreSQL 16. No token is fabricated, no session injected, no auth
 * state seeded into browser storage. Every clinical act is performed by clicking the product as a
 * clinician who signed in through a real Authorization Code + PKCE flow.
 */
const baseURL = process.env.E2E_BASE_URL
if (!baseURL) {
  throw new Error(
    'E2E_BASE_URL is required (e.g. https://app.localhost) -- this suite refuses to guess an origin.',
  )
}

export default defineConfig({
  testDir: './native',
  testMatch: /08-clinic-visit.*\.spec\.ts/,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  // Serial, and not negotiable. The phases are one consultation carried forward: VISIT-C takes the
  // visit VISIT-B created, VISIT-E orders inside it, VISIT-F finishes it. Running them in parallel
  // would not merely race -- it would be meaningless.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.E2E_REPORT_JSON || 'native-visit-results.json' }],
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
