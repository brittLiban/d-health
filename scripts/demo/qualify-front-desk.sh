#!/bin/bash
# DHEALTH-FRONT-DESK-001 — the front-desk arrival qualification, against the running stack.
#
# Like scripts/demo/qualify-visit.sh, this starts and stops nothing: scripts/demo/up.sh owns the
# runtime. Its job is to ask one question no adapter test suite can: **can a facility worker who
# holds no clinical role, and no role named for a front desk, actually walk a patient from the door
# into a clinician's consultation** -- and is that same person genuinely unable to read the clinical
# record afterwards?
#
# `test_dhealthfrontdesk001_arrival.py` proves every rule the workflow has (44 tests) and the
# PostgreSQL qualification proves the concurrency and migration halves (11 more). Neither can prove
# that a receptionist can reach any of it through a real browser against real Keycloak. That is this
# file.
#
# The persona is `demo-clerk`, who holds `BILLING_CLERK` and nothing else, composed into a
# receptionist by granting the three ADR-069 §1 capabilities through `Équipe et accès`. Deliberately
# not a purpose-built reception account: §26 asks for a member who is *not* relying on a front-desk
# role, and a cashier handed three capabilities answers that more convincingly -- while also
# exercising §28's "one employee does reception and the till" for real.
#
# Usage:
#   scripts/demo/up.sh                    # start the native runtime and seed the baseline
#   scripts/demo/qualify-front-desk.sh    # run the front-desk arrival qualification
#   scripts/demo/reset.sh                 # restore the baseline before running it again
#
# ── This suite restores everything it creates, and that is not optional ──
#
# The spec's own afterAll removes its capability overrides, its visits, and the patient FD-B5
# registers. The patient is the one that matters and the least obvious: `demo_seed reset`
# deliberately keeps `exchange_patients` as baseline, the patient list is ordered newest-first, and
# both 01-golden-journey and 11-lab-blocked select a patient with `.first()`. A patient left behind
# here silently redirects those suites onto it, and they then fail looking for the baseline patient --
# which is exactly how it was found, by running the whole browser regression after this suite.
#
# FD-B3 additionally asserts the waiting queue is empty before it checks anybody in, so a run against
# state an aborted earlier run left behind fails there -- correctly, and loudly, rather than quietly
# passing. scripts/demo/reset.sh is still the supported way back to a known baseline, and patient and
# note text are unique per run so a polluted database yields unambiguous selectors.
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
KC_URL="${KEYCLOAK_PUBLIC_URL:-https://id.${BASE_DOMAIN:-localhost}}"

echo "DHealth Native — front-desk arrival qualification"
echo "  app:      $BASE_URL"
echo "  exchange: $EXCHANGE_URL"
echo "  identity: $KC_URL"
echo

# Readiness, not a sleep. The Docker daemon in some environments restarts every container at once;
# Caddy then answers 502 for roughly half a minute while the adapter and Keycloak bind their ports.
# A 502 looks exactly like "the edge refuses this path" to a status-code assertion, so the whole
# suite is worthless until the edge genuinely answers. The specs re-check this per describe block
# too -- this is the fast failure for an operator who simply has not started the stack.
ready=0
for _ in $(seq 1 60); do
  me_code="$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' "$BASE_URL/exchange/v1/me" || true)"
  kc_code="$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' \
    "$KC_URL/realms/dhealth-exchange/.well-known/openid-configuration" || true)"
  if [ "$me_code" = "401" ] && [ "$kc_code" = "200" ]; then
    ready=1
    break
  fi
  sleep 5
done

if [ "$ready" -ne 1 ]; then
  echo "The native stack is not serving $BASE_URL and $KC_URL yet." >&2
  echo "Start it with scripts/demo/up.sh, or wait for a restarted daemon to settle." >&2
  exit 1
fi

echo "Edge ready. Running the front-desk arrival qualification."

# Caddy issues from its own internal CA for a non-public BASE_DOMAIN. Playwright's browser and its
# request contexts are told to accept it via `ignoreHTTPSErrors`, but the suite also drives
# Keycloak's Admin REST API through Node's global `fetch`, which honours neither that flag nor the
# system store by default.
#
# The fix is to TRUST that specific CA, not to switch verification off: exporting the root and
# pointing NODE_EXTRA_CA_CERTS at it keeps certificate validation fully enabled and scoped to this
# one authority. `NODE_TLS_REJECT_UNAUTHORIZED=0` would have been one line and would have made
# every TLS assertion in the suite meaningless.
# DHEALTH_CADDY_ROOT_CA may be supplied by the caller (useful where `docker` needs a wrapper);
# otherwise the root is read straight out of the running Caddy container.
if [ -n "${DHEALTH_CADDY_ROOT_CA:-}" ] && [ -s "${DHEALTH_CADDY_ROOT_CA}" ]; then
  CA_FILE="$DHEALTH_CADDY_ROOT_CA"
else
  CA_FILE="$(mktemp -t dhealth-caddy-root-XXXXXX.crt)"
  trap 'rm -f "$CA_FILE"' EXIT
  CADDY_CONTAINER="${SEC_CADDY_CONTAINER:-dhealth-native-demo-caddy-1}"
  if ! ${SEC_DOCKER:-docker} exec -i "$CADDY_CONTAINER" \
        cat /data/caddy/pki/authorities/local/root.crt > "$CA_FILE" 2>/dev/null; then
    echo "Could not export Caddy's internal root CA from $CADDY_CONTAINER." >&2
    echo "Start the stack (scripts/demo/up.sh), or set DHEALTH_CADDY_ROOT_CA to the exported root." >&2
    exit 1
  fi
fi
export NODE_EXTRA_CA_CERTS="$CA_FILE"
echo "Trusting Caddy's internal root CA for this run: $CA_FILE"
echo

cd ui
E2E_BASE_URL="$BASE_URL" \
E2E_EXCHANGE_URL="$EXCHANGE_URL" \
E2E_KEYCLOAK_PUBLIC_URL="$KC_URL" \
E2E_KEYCLOAK_URL="$KC_URL" \
E2E_ADAPTER_BASE_URL="$EXCHANGE_URL/exchange/v1" \
E2E_REALM="dhealth-exchange" \
KEYCLOAK_ADMIN_USER="${KEYCLOAK_ADMIN_USER:-admin}" \
KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:?KEYCLOAK_ADMIN_PASSWORD must come from demo/native/demo.env}" \
DEMO_CLINICIAN_USERNAME="${DEMO_CLINICIAN_USERNAME:-demo-clinician}" \
DEMO_CLINICIAN_PASSWORD="${DEMO_CLINICIAN_PASSWORD:?}" \
DEMO_LAB_TECH_USERNAME="${DEMO_LAB_TECH_USERNAME:-demo-lab-tech}" \
DEMO_LAB_TECH_PASSWORD="${DEMO_LAB_TECH_PASSWORD:?}" \
DEMO_RELEASER_USERNAME="${DEMO_RELEASER_USERNAME:-demo-releaser}" \
DEMO_RELEASER_PASSWORD="${DEMO_RELEASER_PASSWORD:?}" \
DEMO_ADMIN_USERNAME="${DEMO_ADMIN_USERNAME:-demo-admin}" \
DEMO_ADMIN_PASSWORD="${DEMO_ADMIN_PASSWORD:?DEMO_ADMIN_PASSWORD must come from demo/native/demo.env}" \
DEMO_LAB_ADMIN_USERNAME="${DEMO_LAB_ADMIN_USERNAME:-demo-lab-admin}" \
DEMO_LAB_ADMIN_PASSWORD="${DEMO_LAB_ADMIN_PASSWORD:?DEMO_LAB_ADMIN_PASSWORD must come from demo/native/demo.env}" \
DEMO_CLERK_USERNAME="${DEMO_CLERK_USERNAME:-demo-clerk}" \
DEMO_CLERK_PASSWORD="${DEMO_CLERK_PASSWORD:?DEMO_CLERK_PASSWORD must come from demo/native/demo.env}" \
SEC_DB_CONTAINER="${SEC_DB_CONTAINER:-dhealth-native-demo-dhealth-db-1}" \
SEC_DB_USER="${ADAPTER_DB_USER:-adapter}" \
SEC_DB_NAME="${ADAPTER_DB_NAME:-dhealth_native_demo}" \
E2E_REPORT_JSON="${E2E_REPORT_JSON:-$REPO_ROOT/native-front-desk-results.json}" \
  npx playwright test --config=e2e/native-front-desk.config.ts "$@"
