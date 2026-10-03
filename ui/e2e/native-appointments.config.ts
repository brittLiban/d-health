import { defineConfig, devices } from '@playwright/test'

/**
 * DHEALTH-APPOINTMENTS-001 — the version-bound printable released report against the real stack.
 *
 * A thirteenth config alongside its siblings, for the reason each of those gives: every suite answers
 * a different question and earns a separately reportable claim, and folding two together would make
 * one green run stand for both and change what the existing numbers mean.
 *
 * ── Why not widen `native-journey.config.ts` ──
 *
 * The golden journey's claim is that the happy path works end to end, and it ends at the clinician's
 * review of a single released result. This one's claim is about what happens to an *earlier* released
 * version once a later one exists — it has to amend and release twice, which the journey deliberately
 * does not do. Entangling them would mean a journey failure and a report failure were the same
 * number.
 *
 * ── Why not widen `native-offerings.config.ts` ──
 *
 * RP-I does switch the analysis off, which looks like the offerings suite's territory. It is not the
 * same claim: offerings proves that disabling stops *new* ordering, and RP-I proves that it does not
 * hide an *already released* report. One is about the future of the catalogue and the other about the
 * permanence of a document.
 *
 * ── This suite restores what it changes, and the one thing it cannot ──
 *
 * The laboratory's offering is left **active**: RP-I re-enables it explicitly rather than relying on
 * `reset.sh`, because the golden journey and every sibling laboratory suite need it accepting.
 * `exchange_lab_offerings` is baseline configuration rather than workflow state and is deliberately
 * absent from `reset.sh`'s `_WORKFLOW_TABLES`, so only a crashed run can leave it off.
 *
 * It cannot restore the lab order it routes — nothing deletes a lab order, by design — so it leaves
 * one behind, released twice. `scripts/demo/reset.sh` clears it as workflow state, which is the
 * supported way back to the baseline.
 *
 * The PDFs RP-D2 and RP-J generate are written to the OS temp directory and deleted by the test that
 * made them. The screenshots RP-G takes land in `ui/test-results/`, which is gitignored: they are
 * there for a human to look at, and nothing asserts on them.
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
  testMatch: /17-clinic-appointments.*\.spec\.ts/,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  // Serial, and not negotiable. The phases are one clinical object carried forward: A routes and
  // processes an order, C releases Version 1, E prepares an amendment against it, F releases
  // Version 2, and F2 is the whole point — Version 1's report after Version 2 exists. Running them
  // in parallel would leave the central assertion with nothing to assert.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    // Into the already-gitignored `ui/test-results/`: a run's report is an artifact for a human to
    // read, never something that rides into a commit.
    [
      'json',
      {
        outputFile:
          process.env.E2E_REPORT_JSON || 'test-results/native-appointments-results.json',
      },
    ],
  ],
  use: {
    baseURL,
    // Caddy's internal CA for a non-public BASE_DOMAIN, exactly as the sibling configs explain.
    // Accepting it is not a claim about public certificate issuance, which nothing here tests.
    ignoreHTTPSErrors: true,
    // A bounded action timeout, deliberately. Without one, a click on an element that does not
    // exist waits out the whole 300s test timeout, and the error that surfaces is
    // `browserContext.close` from the `finally` -- which names neither the locator nor the step.
    // Four separate locator mistakes in this suite each cost a five-minute run to find for that
    // reason. At 30s a missing element fails fast and says what it was looking for.
    actionTimeout: 30_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    ...devices['Desktop Chrome'],
  },
})
