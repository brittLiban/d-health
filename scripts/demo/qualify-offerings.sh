#!/bin/bash
# DHEALTH-LAB-OFFERINGS-001 — the laboratory service-availability qualification.
#
# Like its siblings this starts and stops nothing: scripts/demo/up.sh owns the runtime. Its job is to
# ask the one question no adapter test suite can: **can a laboratory administrator turn an analysis
# off by clicking the product, does a clinic then stop being able to order it, and is the order the
# clinic already placed untouched?**
#
# adapter/tests/test_dhealthlabofferings001_offerings.py qualifies the domain, authority, history and
# separation rules through the HTTP edge (27 tests), and
# adapter/tests/integration/test_dhealthlabofferings001_postgres_qualification.py qualifies the row
# lock, both races and the constraint set on real PostgreSQL (11 more). Neither can prove a laboratory
# administrator reaches any of it through a real browser against real Keycloak. That is this file.
#
# ── Rebuild BOTH images first, and verify it ──
#
# This ticket changed the frontend (a new `Analyses réalisées` panel, two new client methods, one
# navigation rule) and one backend file (`catalog.py`'s row lock). A container running a pre-6c32ad2
# ui image fails OF-A immediately; a pre-87ddba7 adapter would still serve the endpoints, so its
# staleness would be *invisible* here and would only show up as a missing lock in the PostgreSQL
# suite. Verify both rather than assuming:
#
#   docker exec dhealth-native-demo-adapter-1 \
#     grep -c with_for_update /app/src/daher_adapter/exchange/catalog.py          # >= 1
#   docker exec dhealth-native-demo-daher-ui-1 \
#     sh -c 'grep -rl "Analyses réalisées" /usr/share/nginx/html/assets/*.js'      # one match
#
# The second check is the one that matters most: the whole suite is about a panel, and a stale bundle
# means every phase is testing the previous ticket's product.
#
# ── Run it from a reset baseline ──
#
# Usage:
#   scripts/demo/up.sh                  # start the runtime, rebuild images, seed the baseline
#   scripts/demo/reset.sh               # restore the deterministic baseline
#   scripts/demo/qualify-offerings.sh   # run the availability qualification
#
# OF-A asserts the demo laboratory's one synthetic analysis starts **accepting**. That is the seeded
# baseline (`demo_seed` creates the offering, and `create_offering` defaults to active), so a failure
# there means an earlier crashed run left it switched off -- OF-F re-enables it explicitly for exactly
# that reason, and `reset.sh` does **not** restore it, because `exchange_lab_offerings` is baseline
# configuration rather than workflow state and is deliberately absent from `_WORKFLOW_TABLES`.
#
# If OF-A fails on a fresh reset, switch it back on before suspecting the code:
#   docker exec dhealth-native-demo-dhealth-db-1 psql -U adapter -d dhealth_native_demo \
#     -c 'update exchange_lab_offerings set active = true'
#
# ── What the suite leaves behind ──
#
# The offering **active**, restored by OF-F, because the golden journey and every sibling laboratory
# suite need it. Three lab orders: one accepted, one awaiting a laboratory from the refusal phase, and
# one routed at the end. Nothing deletes a lab order by design; reset.sh clears them.
#
# No real patient data is used: every order is raised for the demo baseline's synthetic patient, and
# the only analysis involved is the synthetic `Analyse de démonstration A`.
#
# Passing this suite does not make laboratory catalogue management usable, and does not make the
# product pilot-ready. Nobody who administers a laboratory has tried it, and that judgement is a human
# one no script can make.
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

echo "DHealth Native — laboratory service-availability qualification"
echo "  app:      $BASE_URL"
echo "  exchange: $EXCHANGE_URL"
echo "  identity: $KC_URL"
echo

# Readiness, not a sleep. The Docker daemon in some environments restarts every container at once;
# Caddy then answers 502 for roughly half a minute while the adapter and Keycloak bind their ports,
# and a 502 looks exactly like "the edge refuses this path" to a status-code assertion.
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

# The running images actually contain this ticket's code. Checked rather than assumed, because
# DHEALTH-LAB-RECEPTION-001 lost a qualification run to a stale adapter image and this suite is more
# exposed: it is entirely about a frontend panel.
UI_CONTAINER="${SEC_UI_CONTAINER:-dhealth-native-demo-daher-ui-1}"
if ! ${SEC_DOCKER:-docker} exec -i "$UI_CONTAINER" \
      sh -c 'grep -rlq "Analyses" /usr/share/nginx/html/assets/*.js' 2>/dev/null; then
  echo "The running ui bundle does not contain the offerings panel." >&2
  echo "It is almost certainly a pre-DHEALTH-LAB-OFFERINGS-001 image." >&2
  echo "Rebuild it: scripts/demo/up.sh   (which runs 'up -d --build')" >&2
  exit 1
fi
echo "Running ui bundle contains the offerings panel."

ADAPTER_CONTAINER="${SEC_ADAPTER_CONTAINER:-dhealth-native-demo-adapter-1}"
if ! ${SEC_DOCKER:-docker} exec -i "$ADAPTER_CONTAINER" \
      grep -q with_for_update /app/src/daher_adapter/exchange/catalog.py 2>/dev/null; then
  echo "The running adapter's catalog.py has no row lock in set_offering_active." >&2
  echo "It predates this ticket's fix. Rebuild it: scripts/demo/up.sh" >&2
  exit 1
fi
echo "Running adapter carries the set_offering_active row lock."
echo
echo "Edge ready. Running the laboratory service-availability qualification."

# Caddy issues from its own internal CA for a non-public BASE_DOMAIN. Playwright's browser and its
# request contexts accept it via `ignoreHTTPSErrors`, but anything driven through Node's global
# `fetch` honours neither that flag nor the system store by default.
#
# The fix is to TRUST that specific CA, not to switch verification off: exporting the root and
# pointing NODE_EXTRA_CA_CERTS at it keeps certificate validation fully enabled and scoped to this one
# authority. `NODE_TLS_REJECT_UNAUTHORIZED=0` would have been one line and would have made every TLS
# assertion in the suite meaningless.
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
DEMO_LAB_ADMIN_USERNAME="${DEMO_LAB_ADMIN_USERNAME:-demo-lab-admin}" \
DEMO_LAB_ADMIN_PASSWORD="${DEMO_LAB_ADMIN_PASSWORD:?DEMO_LAB_ADMIN_PASSWORD must come from demo/native/demo.env}" \
SEC_DB_CONTAINER="${SEC_DB_CONTAINER:-dhealth-native-demo-dhealth-db-1}" \
SEC_DB_USER="${ADAPTER_DB_USER:-adapter}" \
SEC_DB_NAME="${ADAPTER_DB_NAME:-dhealth_native_demo}" \
E2E_REPORT_JSON="${E2E_REPORT_JSON:-$REPO_ROOT/native-offerings-results.json}" \
  npx playwright test --config=e2e/native-offerings.config.ts "$@"
