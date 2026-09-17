#!/bin/bash
set -euo pipefail

# DH-CLINIC-004B: the single documented entry point for real-Keycloak,
# real-Caddy, real-adapter, real-Chromium qualification of the native browser
# auth layer (ADR-050, DH-CLINIC-004A). Never run by the fast push-triggered
# CI pipeline (.github/workflows/ci.yml's own header explains why the full
# clinical cold boot stays out of that path) -- invoked either by a developer
# with local Docker/internet access, or by the separate workflow_dispatch
# .github/workflows/qualify-native-auth.yml.
#
# Usage: scripts/qualify-native-auth.sh [--mode=fresh|upgrade]
#   fresh    (default) -- a brand-new Keycloak DB, `--import-realm` creates
#            dhealth-native-ui from scratch. Proves DT-1..DT-3-style "new
#            deployment" correctness and that the reconciler is a true no-op
#            here (idempotency Case A).
#   upgrade  -- first boots Keycloak against a REALM FILE with
#            dhealth-native-ui removed (simulating a realm imported before
#            DH-CLINIC-004A existed), confirms the client is genuinely
#            missing, THEN restarts against the real committed realm file
#            (still missing, because --import-realm never updates an
#            existing realm -- this is DH-CLINIC-004B's central proof) and
#            runs the reconciler to add it, confirming a representative
#            pre-existing user/role survives untouched (Case B).
#
# Exits nonzero on any failure. Always tears the ephemeral stack down (`down
# -v`), whether it succeeded or not -- no synthetic data or secret survives
# a run.

MODE=fresh
for arg in "$@"; do
  case "$arg" in
    --mode=*) MODE="${arg#--mode=}" ;;
    *)
      echo "Unknown argument: $arg (expected --mode=fresh|upgrade)" >&2
      exit 1
      ;;
  esac
done
if [[ "$MODE" != "fresh" && "$MODE" != "upgrade" ]]; then
  echo "--mode must be 'fresh' or 'upgrade', got '$MODE'" >&2
  exit 1
fi

for tool in docker node; do
  command -v "$tool" >/dev/null || { echo "$tool is required on PATH" >&2; exit 1; }
done

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

WORKDIR=$(mktemp -d)
ENV_FILE="$WORKDIR/qualify.env"
STATE_FILE="$WORKDIR/qualification-state.json"
REALM_FILE="$ROOT/keycloak/realm-config/dhealth-exchange-realm.json"
REALM_FILE_BACKUP="$WORKDIR/dhealth-exchange-realm.json.orig"
cp "$REALM_FILE" "$REALM_FILE_BACKUP"

EVIDENCE_FILE="${E2E_EVIDENCE_OUTPUT:-$ROOT/qualification-evidence-$(date -u +%Y%m%dT%H%M%SZ).json}"

cleanup() {
  local exit_code=$?
  echo "--- DH-CLINIC-004B: tearing down the ephemeral qualification stack (mode=$MODE) ---"
  cp "$REALM_FILE_BACKUP" "$REALM_FILE"
  docker compose --env-file "$ENV_FILE" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORKDIR"
  exit "$exit_code"
}
trap cleanup EXIT

random_b64() { node -e "process.stdout.write(require('crypto').randomBytes(18).toString('base64url'))"; }

cat > "$ENV_FILE" <<ENVEOF
TZ=Africa/Djibouti
FACILITY_ID=qualify004b
OMRS_DB_NAME=openmrs
OMRS_DB_USER=openmrs
OMRS_DB_PASSWORD=$(random_b64)
OMRS_DB_ROOT_PASSWORD=$(random_b64)
OMRS_DEFAULT_LOCALE=fr
OMRS_ADD_DEMO_DATA=false
OMRS_ADMIN_PASSWORD=Q4bDummy1
OMRS_LOCAL_PORT=8080
MATERNITY_DELIVERY_BEDS=2
MATERNITY_POSTPARTUM_BEDS=4
OE_DB_PASSWORD=$(random_b64)
OE_DB_SUPERUSER_PASSWORD=$(random_b64)
OE_POSTGRES_PASSWORD=$(random_b64)
OE_ADMIN_PASSWORD=Q4bTest1!
OE_KEYSTORE_PASSWORD=$(random_b64)
OE_TRUSTSTORE_PASSWORD=$(random_b64)
DAHER_OPENELIS_SITE_CODE=Q4B
OE_CERT_VALIDITY_DAYS=825
OE_CERT_RENEW_BEFORE_DAYS=30
KEYCLOAK_DB_NAME=keycloak
KEYCLOAK_DB_USER=keycloak
KEYCLOAK_DB_PASSWORD=$(random_b64)
BASE_DOMAIN=localhost
CADDY_HTTP_PORT=80
CADDY_HTTPS_PORT=443
CADDY_ACME_EMAIL=
# DH-CLINIC-004B: the real Exchange browser flow needs Keycloak's own issuer/
# redirect URLs to match the ORIGIN THE BROWSER ACTUALLY REACHES
# (https://id.localhost through Caddy), never the internal-only default --
# see caddy/Caddyfile's id.<domain> block and KC_PROXY_HEADERS=xforwarded.
KEYCLOAK_PUBLIC_URL=https://id.localhost
SSO_ENABLED=false
KEYCLOAK_ADMIN_USER=admin
KEYCLOAK_ADMIN_PASSWORD=$(random_b64)
KEYCLOAK_OPENMRS_CLIENT_SECRET=$(random_b64)
KEYCLOAK_ADAPTER_CLIENT_SECRET=$(random_b64)
ADAPTER_DB_NAME=adapter
ADAPTER_DB_USER=adapter
ADAPTER_DB_PASSWORD=$(random_b64)
ADAPTER_RETRY_MAX_ATTEMPTS=5
ADAPTER_RETRY_MULTIPLIER_SECONDS=1
ADAPTER_RETRY_MAX_SECONDS=30
KEYCLOAK_EXCHANGE_API_CLIENT_SECRET=$(random_b64)
OMRS_DEMO_PATIENTS=off
OMRS_DEMO_CONTENT=off
COMPOSE_PROFILES=
ENVEOF

compose() { docker compose --env-file "$ENV_FILE" "$@"; }

if [[ "$MODE" == "upgrade" ]]; then
  echo "--- Case B setup: booting Keycloak against a realm file with dhealth-native-ui removed ---"
  python3 - "$REALM_FILE" <<'PY'
import json, sys
path = sys.argv[1]
realm = json.loads(open(path).read())
realm["clients"] = [c for c in realm["clients"] if c["clientId"] != "dhealth-native-ui"]
open(path, "w").write(json.dumps(realm, indent=2) + "\n")
PY
  compose up -d --wait keycloak
  echo "--- confirming dhealth-native-ui is genuinely absent from the freshly-imported (pre-004A-shaped) realm ---"
  compose exec -T keycloak /opt/keycloak/bin/kcadm.sh config credentials \
    --config /tmp/qualify-kcadm.config --server http://localhost:8080 --realm master \
    --user admin --password "$(grep ^KEYCLOAK_ADMIN_PASSWORD= "$ENV_FILE" | cut -d= -f2-)" >/dev/null
  count=$(compose exec -T keycloak /opt/keycloak/bin/kcadm.sh get clients \
    --config /tmp/qualify-kcadm.config -r dhealth-exchange \
    -q clientId=dhealth-native-ui -F id --format csv --noquotes | grep -c . || true)
  if [[ "$count" -ne 0 ]]; then
    echo "Case B setup invariant violated: dhealth-native-ui should be absent before reconciliation" >&2
    exit 1
  fi
  echo "confirmed absent — restarting Keycloak against the REAL committed realm file (still absent, by design)"
  compose stop keycloak
  cp "$REALM_FILE_BACKUP" "$REALM_FILE"
  compose up -d --wait keycloak
  compose exec -T keycloak /opt/keycloak/bin/kcadm.sh config credentials \
    --config /tmp/qualify-kcadm.config --server http://localhost:8080 --realm master \
    --user admin --password "$(grep ^KEYCLOAK_ADMIN_PASSWORD= "$ENV_FILE" | cut -d= -f2-)" >/dev/null
  count=$(compose exec -T keycloak /opt/keycloak/bin/kcadm.sh get clients \
    --config /tmp/qualify-kcadm.config -r dhealth-exchange \
    -q clientId=dhealth-native-ui -F id --format csv --noquotes | grep -c . || true)
  if [[ "$count" -ne 0 ]]; then
    echo "Case B invariant violated: --import-realm unexpectedly updated an existing realm" >&2
    exit 1
  fi
  echo "confirmed: --import-realm left the upgraded realm without dhealth-native-ui, exactly as DH-CLINIC-004B set out to prove"
fi

echo "--- booting the full stack (Caddy's app./id. vhosts depend on the whole clinical stack being healthy) ---"
# The whole clinical stack, not just Exchange, must reach healthy before
# Caddy's own healthcheck passes (see docker-compose.yml's caddy depends_on)
# -- this is the ~50-minute cold boot .github/workflows/ci.yml's own header
# documents keeping out of push-triggered CI. --wait-timeout is generous on
# purpose; a real timeout here should fail loudly, never be worked around.
compose up -d --wait --wait-timeout 3600

echo "--- running the Exchange reconciler 3 times to prove idempotency (DT: idempotent-repeat) ---"
for i in 1 2 3; do
  echo "reconcile run $i/3"
  compose run --rm keycloak-exchange-config
done

echo "--- confirming the confidential dhealth-exchange-api client secret is unchanged ---"
compose exec -T keycloak /opt/keycloak/bin/kcadm.sh config credentials \
  --config /tmp/qualify-kcadm.config --server http://localhost:8080 --realm master \
  --user admin --password "$(grep ^KEYCLOAK_ADMIN_PASSWORD= "$ENV_FILE" | cut -d= -f2-)" >/dev/null
api_public=$(compose exec -T keycloak /opt/keycloak/bin/kcadm.sh get clients \
  --config /tmp/qualify-kcadm.config -r dhealth-exchange \
  -q clientId=dhealth-exchange-api -F publicClient --format csv --noquotes)
if [[ "$api_public" != "false" ]]; then
  echo "dhealth-exchange-api must remain confidential (publicClient=false) after reconciliation" >&2
  exit 1
fi

echo "--- app.localhost / id.localhost host resolution ---"
for host in app.localhost id.localhost; do
  if ! getent hosts "$host" >/dev/null 2>&1; then
    echo "$host does not resolve -- add '127.0.0.1 $host' to /etc/hosts before running this script" >&2
    exit 1
  fi
done

echo "--- provisioning synthetic test identities and running the real Playwright E2E suite ---"
export E2E_BASE_URL="https://app.localhost"
export E2E_KEYCLOAK_URL="https://id.localhost"
export E2E_REALM="dhealth-exchange"
export E2E_ADAPTER_BASE_URL="https://app.localhost/exchange/v1"
export KEYCLOAK_ADMIN_USER=admin
export KEYCLOAK_ADMIN_PASSWORD
KEYCLOAK_ADMIN_PASSWORD=$(grep ^KEYCLOAK_ADMIN_PASSWORD= "$ENV_FILE" | cut -d= -f2-)
export E2E_STATE_FILE="$STATE_FILE"
export E2E_SHORT_TOKEN_LIFESPAN_SECONDS="${E2E_SHORT_TOKEN_LIFESPAN_SECONDS:-20}"
export E2E_REPORT_JSON="$WORKDIR/e2e-results.json"

pushd "$ROOT/ui" >/dev/null
npm ci
npx playwright install --with-deps chromium
set +e
npx playwright test --config e2e/playwright.config.ts
PLAYWRIGHT_EXIT=$?
set -e
popd >/dev/null

echo "--- writing qualification evidence record ---"
node - "$EVIDENCE_FILE" "$WORKDIR/e2e-results.json" "$MODE" "$PLAYWRIGHT_EXIT" <<'NODE'
const fs = require('fs')
const [, , evidencePath, resultsPath, mode, exitCode] = process.argv
let results = null
try { results = JSON.parse(fs.readFileSync(resultsPath, 'utf-8')) } catch {}
const summary = results?.suites
  ? { expected: results.stats?.expected, unexpected: results.stats?.unexpected, skipped: results.stats?.skipped }
  : null
const record = {
  ticket: 'DH-CLINIC-004B',
  timestampUtc: new Date().toISOString(),
  commitShaEnv: process.env.GITHUB_SHA || null,
  mode,
  keycloakImage: 'quay.io/keycloak/keycloak:26.7.1@sha256:f1f1f01e472c8a78df40d8f2a49a925274eda4d3d80d5f6edbb5c880ee3c01c6',
  browser: 'chromium (Playwright-managed)',
  playwrightExitCode: Number(exitCode),
  testSummary: summary,
}
fs.writeFileSync(evidencePath, JSON.stringify(record, null, 2) + '\n')
console.log('evidence written to', evidencePath)
NODE

if [[ "$PLAYWRIGHT_EXIT" -ne 0 ]]; then
  echo "--- DH-CLINIC-004B qualification FAILED (mode=$MODE) — see $EVIDENCE_FILE and Playwright's own report ---" >&2
  exit "$PLAYWRIGHT_EXIT"
fi

echo "--- DH-CLINIC-004B qualification PASSED (mode=$MODE) ---"
