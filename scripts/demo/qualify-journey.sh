#!/bin/bash
# DHEALTH-NATIVE-MVP-1.0 — the browser golden-journey qualification, against the running native demo.
#
# This does not start or stop anything. `scripts/demo/up.sh` owns the runtime and already waits on
# real readiness; this script's only job is to point a real Chromium at it and carry one synthetic
# order from clinic to laboratory to release to clinician review, clicking what a staff user clicks.
#
# It is deliberately separate from `scripts/qualify-native-auth.sh`, which qualifies the *auth
# layer* (DH-CLINIC-004B) against an ephemeral full stack it boots and tears down itself. This one
# qualifies the *clinical workflow* against the demo's own deterministic baseline.
#
# Usage:
#   scripts/demo/up.sh                 # start the native runtime and seed the baseline
#   scripts/demo/qualify-journey.sh    # run the qualification
#   scripts/demo/reset.sh              # restore the baseline before running it again
#
# The journey leaves workflow state behind on purpose -- an order, a result, a release, a review.
# Run reset.sh between runs; this script refuses to guess whether you wanted the previous run's
# state kept.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

set -a
# shellcheck disable=SC1091
source demo/native/demo.env
set +a

BASE_URL="https://app.${BASE_DOMAIN:-localhost}"
EXCHANGE_URL="https://exchange.${BASE_DOMAIN:-localhost}"

echo "DHealth Native — browser golden-journey qualification"
echo "  app:      $BASE_URL"
echo "  exchange: $EXCHANGE_URL"
echo

# Readiness, not a sleep: if the edge is not serving the application yet there is nothing to
# qualify, and a Playwright timeout thirty seconds later is a worse way to learn that.
if ! curl -sk --max-time 10 -o /dev/null "$BASE_URL/"; then
  echo "The native demo is not answering at $BASE_URL -- start it with scripts/demo/up.sh first." >&2
  exit 1
fi

cd ui
E2E_BASE_URL="$BASE_URL" \
E2E_EXCHANGE_URL="$EXCHANGE_URL" \
E2E_REPORT_JSON="${E2E_REPORT_JSON:-$REPO_ROOT/native-journey-results.json}" \
  npx playwright test --config=e2e/native-journey.config.ts "$@"
