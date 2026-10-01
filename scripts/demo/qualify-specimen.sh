#!/bin/bash
# DHEALTH-SPECIMEN-001 — the physical specimen qualification, against the running stack.
#
# Like scripts/demo/qualify-blocked.sh and qualify-lab-reception.sh, this starts and stops nothing:
# scripts/demo/up.sh owns the runtime. Its job is to ask the one question no adapter test suite can:
# **can a laboratory worker record a physical specimen by clicking the product, be forced to stop for
# an unusable sample, and then record a second specimen without the first one disappearing?**
#
# adapter/tests/test_dhealthspecimen001_specimens.py proves the domain, authority, privacy and audit
# rules through the HTTP edge (38 tests), and
# adapter/tests/integration/test_dhealthspecimen001_postgres_qualification.py proves the concurrency
# and constraint half (15 more, where the row lock is real rather than a SQLite no-op). Neither can
# prove that a laboratory worker reaches any of it through a real browser against real Keycloak.
# That is this file.
#
# ── Rebuild the adapter AND the ui image first, and verify it ──
#
# DHEALTH-LAB-RECEPTION-001 was bitten by a stale running adapter image. This suite is more exposed
# than that one was, because it depends on changes in *both* services: a new `/specimens` route and a
# new table in the adapter, and a new specimen-history panel in the frontend. A container running a
# pre-751308e image fails SPEC-B -- or, worse, could appear to pass an earlier phase.
#
# `scripts/demo/up.sh` runs `up -d --build`. Verify both halves actually took, afterwards:
#
#   docker exec dhealth-native-demo-adapter-1 \
#     grep -c "exchange_lab_specimens" /app/src/daher_adapter/exchange/models.py      # >= 1
#   docker exec dhealth-native-demo-dhealth-db-1 \
#     psql -U adapter -d dhealth_native_demo -c '\d exchange_lab_specimens'           # table exists
#   curl -sk -o /dev/null -w '%{http_code}' \
#     "https://exchange.localhost/exchange/v1/organizations/$(uuidgen)/lab-orders/$(uuidgen)/specimens"
#                                                                                      # 401, not 404
#
# The third check is the one that distinguishes "the route exists and refused me" from "there is no
# such route" -- a 404 from an unauthenticated call would mean the running image predates this
# ticket. The adapter entrypoint runs `alembic upgrade head`, so a rebuilt adapter brings 0039 with
# it; the psql check is how you confirm that rather than assume it.
#
# ── Run it from a reset baseline ──
#
# Usage:
#   scripts/demo/up.sh                 # start the native runtime, rebuild images, seed the baseline
#   scripts/demo/reset.sh              # restore the deterministic baseline
#   scripts/demo/qualify-specimen.sh   # run the physical specimen qualification
#
# SPEC-A2 asserts a freshly accepted order has **zero** specimens, so a run against state an aborted
# earlier run left behind fails there -- correctly, and loudly, rather than quietly passing against
# somebody else's specimen rows.
#
# Note that `reset.sh` does NOT clear `exchange_membership_capability_overrides`. This suite never
# writes that table, so a leftover grant from a crashed `qualify-lab-reception.sh` run cannot make
# this suite pass -- but it can make SPEC-D fail, because SPEC-D asserts that `demo-lab-clerk` is
# refused, and a surviving `lab.queue.work` grant would make that person legitimately authorized.
# If SPEC-D fails, check for a leftover override before suspecting the code:
#   docker exec dhealth-native-demo-dhealth-db-1 psql -U adapter -d dhealth_native_demo \
#     -c 'select count(*) from exchange_membership_capability_overrides'      # expect 0
#
# ── What the suite leaves behind ──
#
# One lab order carried to IN_PROGRESS, with two specimen rows and one resolved block episode.
# Nothing deletes a lab order, a specimen or a block episode, by design; reset.sh clears all three as
# workflow state, which is the supported way back.
#
# No real patient data is used: the order is raised for the demo baseline's synthetic patient, and
# the block note the suite types is obviously synthetic.
#
# Passing this suite does not make specimen collection usable, and does not make the product
# pilot-ready. Nobody who works at a laboratory bench has tried it, and that judgement is a human one
# no script can make.
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

echo "DHealth Native — physical specimen qualification"
echo "  app:      $BASE_URL"
echo "  exchange: $EXCHANGE_URL"
echo "  identity: $KC_URL"
echo

# Readiness, not a sleep. The Docker daemon in some environments restarts every container at once;
# Caddy then answers 502 for roughly half a minute while the adapter and Keycloak bind their ports.
# A 502 looks exactly like "the edge refuses this path" to a status-code assertion, so the whole
# suite is worthless until the edge genuinely answers.
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

# The running image actually contains this ticket's code. Checked rather than assumed, because
# DHEALTH-LAB-RECEPTION-001 lost a qualification run to a stale adapter image and the next ticket
# should not have to rediscover that.
#
# An UNAUTHENTICATED call to the specimen route must answer 401 ("the route exists and you are not
# authorized") and never 404 ("there is no such route"). A 404 here means the container predates
# DHEALTH-SPECIMEN-001 and every assertion below would be meaningless.
probe_order="00000000-0000-4000-8000-000000000000"
probe_code="$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' \
  "$EXCHANGE_URL/exchange/v1/organizations/$probe_order/lab-orders/$probe_order/specimens" || true)"
if [ "$probe_code" != "401" ]; then
  echo "The running adapter does not serve the specimen route (got HTTP $probe_code, expected 401)." >&2
  echo "The container is almost certainly running a pre-DHEALTH-SPECIMEN-001 image." >&2
  echo "Rebuild it: scripts/demo/up.sh   (which runs 'up -d --build')" >&2
  exit 1
fi
echo "Running adapter serves /specimens (HTTP 401 unauthenticated, as expected)."

# And the migration actually ran. The adapter entrypoint runs `alembic upgrade head`, so this
# confirms rather than performs it -- an adapter that started against a database still at 0038 would
# fail every write with a missing-table error, which is a confusing way to learn this.
DB_CONTAINER="${SEC_DB_CONTAINER:-dhealth-native-demo-dhealth-db-1}"
if ! ${SEC_DOCKER:-docker} exec -i "$DB_CONTAINER" \
      psql -U "${ADAPTER_DB_USER:-adapter}" -d "${ADAPTER_DB_NAME:-dhealth_native_demo}" \
      -tAc "select 1 from information_schema.tables where table_name = 'exchange_lab_specimens'" \
      2>/dev/null | grep -q 1; then
  echo "exchange_lab_specimens is absent from the running database." >&2
  echo "The adapter's 'alembic upgrade head' has not applied 0039_lab_specimens." >&2
  exit 1
fi
echo "Running database has exchange_lab_specimens (migration 0039 applied)."
echo
echo "Edge ready. Running the physical specimen qualification."

# Caddy issues from its own internal CA for a non-public BASE_DOMAIN. Playwright's browser and its
# request contexts are told to accept it via `ignoreHTTPSErrors`, but anything the suite drives
# through Node's global `fetch` honours neither that flag nor the system store by default.
#
# The fix is to TRUST that specific CA, not to switch verification off: exporting the root and
# pointing NODE_EXTRA_CA_CERTS at it keeps certificate validation fully enabled and scoped to this
# one authority. `NODE_TLS_REJECT_UNAUTHORIZED=0` would have been one line and would have made every
# TLS assertion in the suite meaningless.
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
DEMO_LAB_CLERK_USERNAME="${DEMO_LAB_CLERK_USERNAME:-demo-lab-clerk}" \
DEMO_LAB_CLERK_PASSWORD="${DEMO_LAB_CLERK_PASSWORD:?DEMO_LAB_CLERK_PASSWORD must come from demo/native/demo.env}" \
SEC_DB_CONTAINER="${SEC_DB_CONTAINER:-dhealth-native-demo-dhealth-db-1}" \
SEC_DB_USER="${ADAPTER_DB_USER:-adapter}" \
SEC_DB_NAME="${ADAPTER_DB_NAME:-dhealth_native_demo}" \
E2E_REPORT_JSON="${E2E_REPORT_JSON:-$REPO_ROOT/native-specimen-results.json}" \
  npx playwright test --config=e2e/native-specimen.config.ts "$@"
