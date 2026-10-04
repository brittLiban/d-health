#!/bin/bash
# DHEALTH-FACILITY-PROFILE-001 — the per-facility timezone qualification.
#
# Like its siblings this starts and stops nothing: scripts/demo/up.sh owns the runtime. Its job is to
# ask the one question no adapter test suite can: **can a clinic administrator change their
# facility's timezone by clicking the product, does the clinic's schedule immediately read in the new
# zone, and do the bookings that already existed stay exactly where they were?**
#
# adapter/tests/test_dhealthfacilityprofile001_facility_profile.py qualifies the projection, the
# authority, the validation and the DST refusal through the HTTP edge (63 tests), and
# adapter/tests/integration/test_dhealthfacilityprofile001_postgres_qualification.py qualifies the
# lock races, the per-facility day boundaries and the migration on real PostgreSQL (11 more).
# Neither can prove that a human reaches the screen, that the confirmation is two steps, or that a
# booking made before the change displays differently and stores identically afterwards. That is
# this file.
#
# ── Rebuild BOTH images first, and verify it ──
#
# This ticket changed the backend (a new table, a new repository, two routes, the appointment
# conversion path) and the frontend (a new `Établissement` section, two client methods, one
# navigation gate). A container running a pre-0041 adapter image returns 404 for both profile routes
# and 500 for every booking, which would look like a product bug; a stale ui image has no
# `Établissement` section at all. The checks below are run by this script, not left to the reader.
#
# ── Run it from a reset baseline ──
#
# Usage:
#   scripts/demo/up.sh                        # start the runtime, rebuild images, seed the baseline
#   scripts/demo/reset.sh                     # restore the deterministic baseline
#   scripts/demo/qualify-facility-profile.sh  # run the facility-profile qualification
#
# ── What the suite leaves behind, and the one thing it must clean up itself ──
#
# One appointment, a week out, for the demo baseline's synthetic patient. That is workflow state and
# reset.sh clears it.
#
# **The facility timezone is different.** `exchange_facility_profiles` is baseline *configuration*,
# deliberately absent from `demo_seed._WORKFLOW_TABLES`, so reset.sh does **not** put it back. FP-E
# restores `Africa/Djibouti` by clicking and asserts it, because every sibling suite's wall-clock
# assertion depends on that zone. If a crashed run left Vancouver behind, put it back before
# suspecting any other suite:
#   docker exec dhealth-native-demo-dhealth-db-1 psql -U adapter -d dhealth_native_demo \
#     -c "update exchange_facility_profiles set timezone_name = 'Africa/Djibouti'"
#
# FP-G's screenshots land in ui/test-results/ (gitignored) for a human to look at; nothing asserts
# on them.
#
# No real patient data is used: the one booking is for the demo baseline's synthetic patient.
#
# Passing this suite does not make the screen usable, and does not make the product pilot-ready. No
# clinic administrator has looked at it and said it is their facility's record. That judgement is a
# human one no script can make, and it has not been made.
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

echo "DHealth Native — facility-profile (timezone) qualification"
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

# The running images actually contain this ticket's code. Checked rather than assumed.
UI_CONTAINER="${SEC_UI_CONTAINER:-dhealth-native-demo-daher-ui-1}"
if ! ${SEC_DOCKER:-docker} exec -i "$UI_CONTAINER" \
      sh -c 'grep -rlq "Changer le fuseau horaire" /usr/share/nginx/html/assets/*.js' 2>/dev/null; then
  echo "The running ui bundle has no Établissement section." >&2
  echo "It is almost certainly a pre-DHEALTH-FACILITY-PROFILE-001 image." >&2
  echo "Rebuild it: scripts/demo/up.sh   (which runs 'up -d --build')" >&2
  exit 1
fi
echo "Running ui bundle contains the facility-profile screen."

ADAPTER_CONTAINER="${SEC_ADAPTER_CONTAINER:-dhealth-native-demo-adapter-1}"
if ! ${SEC_DOCKER:-docker} exec -i "$ADAPTER_CONTAINER" \
      grep -q FacilityProfileRepository /app/src/daher_adapter/exchange/api.py 2>/dev/null; then
  echo "The running adapter has no FacilityProfileRepository wired in api.py." >&2
  echo "It predates this ticket. Rebuild it: scripts/demo/up.sh" >&2
  exit 1
fi
echo "Running adapter carries the facility-profile repository."

# A migration landed in this ticket, so the running database must actually be at its head. A stale
# container would otherwise serve 500s from a missing table and look like a product defect.
DB_CONTAINER="${SEC_DB_CONTAINER:-dhealth-native-demo-dhealth-db-1}"
head_in_db="$(${SEC_DOCKER:-docker} exec -i "$DB_CONTAINER" \
  psql -U "${ADAPTER_DB_USER:-adapter}" -d "${ADAPTER_DB_NAME:-dhealth_native_demo}" \
  -tAc 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]')"
if [ "$head_in_db" != "0041_facility_profiles" ]; then
  echo "The running database is at '${head_in_db}', not 0041_facility_profiles." >&2
  echo "Run scripts/demo/up.sh so the adapter applies this ticket's migration." >&2
  exit 1
fi
echo "Running database is at 0041_facility_profiles."

# And the baseline really is on the home zone before anything changes it. A leftover Vancouver from
# a crashed earlier run would make FP-A fail on an assertion about the wrong thing, and FP-C's
# "nothing moved" would be comparing two instants that were never under the zone it names.
zone_in_db="$(${SEC_DOCKER:-docker} exec -i "$DB_CONTAINER" \
  psql -U "${ADAPTER_DB_USER:-adapter}" -d "${ADAPTER_DB_NAME:-dhealth_native_demo}" \
  -tAc "select distinct timezone_name from exchange_facility_profiles" 2>/dev/null |
  tr -d '[:space:]')"
if [ "$zone_in_db" != "Africa/Djibouti" ]; then
  echo "The demo facilities are on '${zone_in_db}', not the seeded Africa/Djibouti." >&2
  echo "A crashed earlier run of this suite probably left its change behind. Put it back:" >&2
  echo "  ${SEC_DOCKER:-docker} exec -i $DB_CONTAINER psql -U ${ADAPTER_DB_USER:-adapter} \\" >&2
  echo "    -d ${ADAPTER_DB_NAME:-dhealth_native_demo} \\" >&2
  echo "    -c \"update exchange_facility_profiles set timezone_name = 'Africa/Djibouti'\"" >&2
  exit 1
fi
echo "Demo facilities are on the seeded Africa/Djibouti."
echo
echo "Edge ready. Running the facility-profile qualification."

# Caddy issues from its own internal CA for a non-public BASE_DOMAIN. Playwright's browser and its
# request contexts accept it via `ignoreHTTPSErrors`, but anything driven through Node's global
# `fetch` honours neither that flag nor the system store by default.
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
# Note every line here is one backslash-continued environment assignment -- a comment inside the
# chain terminates the command and the suite then refuses to start, complaining that `E2E_BASE_URL`
# is unset.
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
DEMO_ADMIN_USERNAME="${DEMO_ADMIN_USERNAME:-demo-admin}" \
DEMO_ADMIN_PASSWORD="${DEMO_ADMIN_PASSWORD:?DEMO_ADMIN_PASSWORD must come from demo/native/demo.env}" \
DEMO_CLERK_USERNAME="${DEMO_CLERK_USERNAME:-demo-clerk}" \
DEMO_CLERK_PASSWORD="${DEMO_CLERK_PASSWORD:?DEMO_CLERK_PASSWORD must come from demo/native/demo.env}" \
SEC_DB_CONTAINER="${SEC_DB_CONTAINER:-dhealth-native-demo-dhealth-db-1}" \
SEC_DB_USER="${ADAPTER_DB_USER:-adapter}" \
SEC_DB_NAME="${ADAPTER_DB_NAME:-dhealth_native_demo}" \
E2E_REPORT_JSON="${E2E_REPORT_JSON:-test-results/native-facility-profile-results.json}" \
  npx playwright test --config=e2e/native-facility-profile.config.ts "$@"
