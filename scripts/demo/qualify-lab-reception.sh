#!/bin/bash
# DHEALTH-LAB-RECEPTION-001 — the laboratory work-intake qualification, against the running stack.
#
# Like scripts/demo/qualify-blocked.sh, this starts and stops nothing: scripts/demo/up.sh owns the
# runtime. Its job is to ask one question no adapter test suite can: **can a laboratory worker see the
# diagnostic work actually routed to their laboratory, identify whose it is, answer it, and hand the
# accepted work to a colleague** -- while remaining unable to release a result?
#
# adapter/tests/test_dhealthlabreception001_intake.py proves the authority, privacy and tenant rules
# through the HTTP edge (23 tests), and the PostgreSQL 16 qualification proves the concurrency half
# (8 more, where the row lock is real rather than a SQLite no-op). Neither can prove that a laboratory
# worker reaches any of it through a real browser against real Keycloak. That is this file.
#
# The persona is `demo-lab-clerk`, who holds `BILLING_CLERK` and nothing else, composed into a
# reception worker by granting `lab.queue.work` -- one capability -- through `Équipe et accès`.
# Deliberately not a purpose-built reception account: the ticket forbids inventing a LAB_RECEPTIONIST
# role, and a laboratory cashier handed one capability answers that more convincingly. If any control
# or any gate keyed off a job title, the suite could not get past LR-B3.
#
# `lab.result.release` is deliberately NOT granted, and LR-B7 has the server refuse a release to the
# same person who just accepted the work. ADR-044 keeps those two authorities separate and this is
# that sentence demonstrated rather than described. ADR-066's `billing.operate` is asserted intact
# both after the grant and after the withdrawal.
#
# Usage:
#   scripts/demo/up.sh                      # start the native runtime and seed the baseline
#   scripts/demo/qualify-lab-reception.sh    # run the work-intake qualification
#   scripts/demo/reset.sh                    # restore the baseline before running it again
#
# ── Run it from a reset baseline, and rebuild the adapter first ──
#
# LR-B4 asserts the incoming queue holds **exactly** this run's two routed orders, so a run against
# state an aborted earlier run left behind fails there -- correctly, and loudly, rather than quietly
# passing. That assertion is also what keeps LR-B5's ordering claim meaningful: an incoming queue is
# capped at 100 rows, and a large stale backlog sorted oldest-first would push this run's own orders
# off the page entirely. That is correct queue behaviour and a meaningless test run.
#
# `scripts/demo/up.sh` runs `up -d --build`, which matters more here than for most suites: LR-B5
# asserts an ordering that only exists in the adapter this ticket changed. A container still running
# a pre-722877d image fails that phase -- or, worse, could pass it by accident. Verify with
#   docker exec dhealth-native-demo-adapter-1 \
#     grep -c selected_at.asc /app/src/daher_adapter/exchange/lab_workflow.py
# which must print 1.
#
# ── What the suite leaves behind, and the one thing it removes itself ──
#
# Two real lab orders: one accepted and collected, one refused. Nothing deletes a lab order, by
# design, and reset.sh clears them as workflow state -- that is the supported way back.
#
# The capability grant is different. `exchange_membership_capability_overrides` is NOT in
# `demo_seed._WORKFLOW_TABLES`, so reset.sh leaves a leftover grant standing, and
# ui/e2e/native/09-billing.spec.ts asserts what this same laboratory cashier can reach. The spec's
# own beforeAll and afterAll both delete the override, targeted and scoped to the demo
# organizations, and LR-B1 fails loudly if one survived a crash. **After a crashed run, reset.sh
# alone is not enough** -- re-run this suite, or delete that row, before trusting 09-billing.
#
# No real patient data is used: every order is raised for the demo baseline's synthetic patient.
#
# Passing this suite does not make laboratory reception usable. Nobody who works at a laboratory
# counter has tried it, and that judgement is a human one no script can make.
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

echo "DHealth Native — laboratory work-intake qualification"
echo "  app:      $BASE_URL"
echo "  exchange: $EXCHANGE_URL"
echo "  identity: $KC_URL"
echo

# Readiness, not a sleep. The Docker daemon in some environments restarts every container at once;
# Caddy then answers 502 for roughly half a minute while the adapter and Keycloak bind their ports.
# A 502 looks exactly like "the edge refuses this path" to a status-code assertion, so the whole
# suite is worthless until the edge genuinely answers. The spec re-checks this itself too -- this is
# the fast failure for an operator who simply has not started the stack.
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

echo "Edge ready. Running the laboratory work-intake qualification."

# Caddy issues from its own internal CA for a non-public BASE_DOMAIN. Playwright's browser and its
# request contexts are told to accept it via `ignoreHTTPSErrors`, but anything the suite drives
# through Node's global `fetch` honours neither that flag nor the system store by default.
#
# The fix is to TRUST that specific CA, not to switch verification off: exporting the root and
# pointing NODE_EXTRA_CA_CERTS at it keeps certificate validation fully enabled and scoped to this
# one authority. `NODE_TLS_REJECT_UNAUTHORIZED=0` would have been one line and would have made every
# TLS assertion in the suite meaningless.
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
DEMO_LAB_ADMIN_USERNAME="${DEMO_LAB_ADMIN_USERNAME:-demo-lab-admin}" \
DEMO_LAB_ADMIN_PASSWORD="${DEMO_LAB_ADMIN_PASSWORD:?DEMO_LAB_ADMIN_PASSWORD must come from demo/native/demo.env}" \
DEMO_LAB_CLERK_USERNAME="${DEMO_LAB_CLERK_USERNAME:-demo-lab-clerk}" \
DEMO_LAB_CLERK_PASSWORD="${DEMO_LAB_CLERK_PASSWORD:?DEMO_LAB_CLERK_PASSWORD must come from demo/native/demo.env}" \
SEC_DB_CONTAINER="${SEC_DB_CONTAINER:-dhealth-native-demo-dhealth-db-1}" \
SEC_DB_USER="${ADAPTER_DB_USER:-adapter}" \
SEC_DB_NAME="${ADAPTER_DB_NAME:-dhealth_native_demo}" \
E2E_REPORT_JSON="${E2E_REPORT_JSON:-$REPO_ROOT/native-reception-results.json}" \
  npx playwright test --config=e2e/native-reception.config.ts "$@"
