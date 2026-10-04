import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-FACILITY-PROFILE-001 — each facility's own timezone against the real stack.
 *
 * A fourteenth config alongside its siblings, for the reason each of those gives: every suite
 * answers a different question and earns a separately reportable claim, and folding two together
 * would make one green run stand for both and change what the existing numbers mean.
 *
 * ── Why not widen `native-appointments.config.ts` ──
 *
 * That suite's claim is that a clinic can book, move, cancel and arrive a patient. This one's claim
 * is that changing the facility's timezone leaves every one of those bookings exactly where it was
 * while changing how all of them read. It has to *mutate deployment configuration* mid-run, which
 * the appointments suite deliberately never does — and a configuration change that leaked would
 * redden the appointments suite for reasons that have nothing to do with appointments.
 *
 * ── Why not widen `native-capabilities.config.ts` ──
 *
 * FP-F does assert who cannot reach the section, which looks like the capabilities suite's
 * territory. It is a different claim: capabilities proves the grant mechanism, and FP-F proves that
 * *this* surface is gated on `facility.configure` **alone** rather than on the
 * `facility.configure || billing.configure_prices` disjunction its neighbour uses. One is about how
 * authority is granted, the other about which authority a specific screen demands.
 *
 * ── This suite restores what it changes, and must ──
 *
 * `exchange_facility_profiles` is **configuration**, so it is deliberately absent from
 * `demo_seed._WORKFLOW_TABLES` and `scripts/demo/reset.sh` does not truncate it. FP-E therefore puts
 * the clinic back on `Africa/Djibouti` **by clicking** and asserts it, because every sibling suite's
 * wall-clock assertion depends on that zone. `demo_seed` also overwrites a differing value at the
 * next `up.sh`, which is a second line of defence rather than the first.
 *
 * If a crashed run leaves Vancouver behind, put it back before suspecting any other suite:
 *   docker exec dhealth-native-demo-dhealth-db-1 psql -U adapter -d dhealth_native_demo \
 *     -c "update exchange_facility_profiles set timezone_name = 'Africa/Djibouti'"
 *
 * The booking FP-B creates is workflow state; `reset.sh` clears it. FP-G's screenshots land in
 * `ui/test-results/`, which is gitignored: they are there for a human to look at, and nothing
 * asserts on them.
 *
 * Zero mocking: real Chromium, real Caddy, real Keycloak 26.7.1, real Exchange adapter, real
 * PostgreSQL 16. No token is fabricated and no session injected. The direct `/exchange/v1` calls in
 * the spec assert refusals the browser cannot demonstrate on its own, or read authoritative state —
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
  testMatch: /18-facility-profile.*\.spec\.ts/,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  // Serial, and not negotiable. The phases carry one facility forward: A reads its profile, B books
  // under the home zone, C changes the zone and asserts the booking did not move, E puts the zone
  // back. Running them in parallel would leave C asserting against a booking that does not exist
  // and E racing the assertion it exists to protect.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    [
      'json',
      {
        outputFile:
          process.env.E2E_REPORT_JSON || 'test-results/native-facility-profile-results.json',
      },
    ],
  ],
  use: {
    baseURL,
    // Caddy's internal CA for a non-public BASE_DOMAIN, exactly as the sibling configs explain.
    // Accepting it is not a claim about public certificate issuance, which nothing here tests.
    ignoreHTTPSErrors: true,
    // A bounded action timeout, for the reason the appointments config records: without one, a
    // click on an element that does not exist waits out the whole 300s test timeout and the error
    // that surfaces names neither the locator nor the step.
    actionTimeout: 30_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    ...devices['Desktop Chrome'],
  },
})
