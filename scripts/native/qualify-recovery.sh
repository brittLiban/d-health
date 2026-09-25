#!/bin/bash
# DHealth Native — the destructive recovery drill (DHEALTH-NATIVE-RECOVERY-001).
#
# Proves that DHealth Native survives the loss of its durable runtime state, by actually losing it:
#
#   populate through the real product -> encrypted backup -> DESTROY the volumes -> clean deployment
#   -> restore -> prove tenancy -> real Keycloak login -> find the SAME records in a real browser
#   -> perform a new clinical workflow afterwards
#
# "pg_restore exited 0" is not the standard and this script cannot report success on it. Every leg
# below either proves something about the real application or fails the run.
#
#   scripts/native/qualify-recovery.sh --yes-destroy-this-deployment
#
# It refuses to run against `.env.native` -- the production env file -- unless
# NATIVE_RECOVERY_DRILL_ALLOW_PRODUCTION=1 is also set. A drill that can be pointed at a real
# deployment by forgetting one flag is not a safety boundary.
#
# The qualification environment is the canonical Native topology plus the demo overlay: the overlay
# supplies synthetic staff identities and a deterministic baseline that a production deployment
# deliberately does not have (it starts with zero clinical data and no way to mint a staff account
# without a human). The BACKUP AND RESTORE TOOLING under test is the production tooling --
# `docker-compose.native.yml`'s own `native-backup`/`native-restore` services, reading the production
# env contract. Nothing in the overlay changes what is captured or how it is restored.
set -uo pipefail

# shellcheck source=scripts/native/_compose.sh
# NOTE: `_compose.sh` declares `set -euo pipefail`, so from here on this script also runs under
# `-e` whether or not the line above asked for it. That is kept rather than undone -- a drill that
# stops at the first unexpected failure is the right default -- but it means any command whose
# *success* case is a non-zero exit (a grep that should find nothing, a count that may be empty)
# needs an explicit `|| true`. One missing instance ended a whole run with no message at all.
source "$(dirname "${BASH_SOURCE[0]}")/_compose.sh"

CONFIRMED=0
for arg in "$@"; do
  [ "$arg" = "--yes-destroy-this-deployment" ] && CONFIRMED=1
done

HARNESS="${NATIVE_RECOVERY_WORK_DIR:-/tmp/dh-native-recovery}"
SENTINELS="${HARNESS}/sentinels.json"
GRANT_FILE="${HARNESS}/grant.json"
EVIDENCE="${HARNESS}/evidence.txt"
TIMINGS="${HARNESS}/timings.txt"

# `|| true` on both: an env file that leaves these at their Compose defaults has no such line, and
# grep reports "no match" as exit 1 -- which under the `-e` inherited from _compose.sh would end the
# drill here, before it had said anything. The defaults below are what that case is meant to use.
EXCHANGE_DB="$(grep -E '^ADAPTER_DB_NAME=' "$NATIVE_ENV_FILE" | tail -1 | cut -d= -f2- || true)"
EXCHANGE_DB="${EXCHANGE_DB:-dhealth_exchange}"
BASE_DOMAIN_VALUE="$(grep -E '^BASE_DOMAIN=' "$NATIVE_ENV_FILE" | tail -1 | cut -d= -f2- || true)"
BASE_DOMAIN_VALUE="${BASE_DOMAIN_VALUE:-localhost}"

if [ "$CONFIRMED" -ne 1 ]; then
  cat >&2 <<EOF
This drill DESTROYS the durable state of the deployment it is pointed at.

  compose:   docker-compose.native.yml ${NATIVE_COMPOSE_EXTRA:-}
  env file:  ${NATIVE_ENV_FILE}
  database:  ${EXCHANGE_DB}
  it will:   run a synthetic golden journey, take an encrypted backup, then
             \`down -v\` the project -- deleting the PostgreSQL volume, the Keycloak
             realm data inside it, the adapter audit volume and Caddy's data --
             and then restore from that backup and prove the result.

Re-run with --yes-destroy-this-deployment if that is what you want.
EOF
  exit 1
fi

if [ "$NATIVE_ENV_FILE" = ".env.native" ] && [ "${NATIVE_RECOVERY_DRILL_ALLOW_PRODUCTION:-0}" != "1" ]; then
  echo "REFUSED: ${NATIVE_ENV_FILE} is the production env file. Set NATIVE_RECOVERY_DRILL_ALLOW_PRODUCTION=1 as well if you really mean it." >&2
  exit 1
fi

native_require_passphrase

mkdir -p "$HARNESS"
chmod 700 "$HARNESS"
: >"$EVIDENCE"
: >"$TIMINGS"

STEP=0

step() { STEP=$((STEP + 1)); printf '\n========== STEP %d — %s\n' "$STEP" "$1"; echo "STEP ${STEP}: $1" >>"$EVIDENCE"; }
record() { echo "$*" >>"$EVIDENCE"; echo "  $*"; }
time_it() { printf '%-44s %ss\n' "$1" "$2" >>"$TIMINGS"; record "timing: $1 = $2s"; }
die() { echo "DRILL FAILED at step ${STEP}: $*" >&2; echo "FAILED at step ${STEP}: $*" >>"$EVIDENCE"; exit 1; }

compose() { "${NATIVE_COMPOSE[@]}" "$@"; }
psql_x() { compose exec -T dhealth-db psql -X -At -U postgres -d "$EXCHANGE_DB" "$@"; }

# Waits on the five long-lived services only. The one-shots are `restart: "no"` jobs that exit 0,
# which Compose's own `--wait` can read as "not ready"; the long-lived services `depends_on` them
# with `service_completed_successfully`, so they still run to completion first.
bring_up() { compose up -d --wait --wait-timeout 420 dhealth-db keycloak adapter daher-ui caddy; }

export E2E_BASE_URL="https://app.${BASE_DOMAIN_VALUE}"
export E2E_EXCHANGE_URL="https://exchange.${BASE_DOMAIN_VALUE}"
export E2E_GRANT_TOKEN_FILE="$GRANT_FILE"
export RECOVERY_SENTINELS="$SENTINELS"
set -a
# shellcheck disable=SC1090
source "$NATIVE_ENV_FILE"
set +a

# ---------------------------------------------------------------------------
step "validate the canonical Native Compose files"
compose config -q || die "compose does not render"
record "both compose files render"

# ---------------------------------------------------------------------------
step "start a clean Native deployment"
T0=$(date -u +%s)
compose down -v --remove-orphans >/dev/null 2>&1 || true
# Only the recovery tooling image is built here. The application images are the deployment's own and
# are expected to exist already -- which is true of any deployment you could be recovering, and keeps
# this drill from depending on a registry being reachable at the moment somebody wants to rehearse a
# recovery. `up` builds a missing application image on its own if it has to.
compose --profile recovery build native-backup native-restore \
  || die "could not build the recovery tooling image"
bring_up || die "the stack did not come up healthy"
compose exec -T adapter python -m daher_adapter.exchange.demo_seed reset \
  || die "could not seed the synthetic baseline"
time_it "clean source deployment up + seeded" "$(( $(date -u +%s) - T0 ))"
record "baseline: $(psql_x -c 'SELECT count(*) FROM exchange_lab_orders') order(s) before the journey"

# ---------------------------------------------------------------------------
step "create the source clinical state through the real product"
T0=$(date -u +%s)
( cd ui && E2E_REPORT_JSON="${HARNESS}/source-journey.json" \
    npx playwright test --config=e2e/native-journey.config.ts ) || die "the source golden journey failed"
time_it "source golden journey (real browser)" "$(( $(date -u +%s) - T0 ))"

# ---------------------------------------------------------------------------
step "add the durable state the journey does not create: patient access + notification"
( cd ui && E2E_REPORT_JSON="${HARNESS}/source-extras.json" \
    npx playwright test --config=e2e/native-recovery.config.ts e2e/native/03-recovery-source.spec.ts ) \
  || die "could not create the patient-access and notification state"
# Drain the notification queue once, on the provider that contacts nothing, so the archive contains
# a notification with a terminal state and an attempt row -- which is what makes "a restore does not
# re-send history" a checkable claim rather than an aspiration.
compose exec -T adapter python -m daher_adapter.notify.cli run --once >/dev/null 2>&1 \
  || record "note: the notification dispatcher returned non-zero (recorded, not fatal -- the durable obligation rows are what this drill needs)"
record "notification queue: $(compose exec -T adapter python -m daher_adapter.notify.cli status 2>&1 | tr '\n' ' ' | tr -d '\r')"
record "notification rows: $(psql_x -c 'SELECT count(*) FROM patient_notifications')"
record "notification attempts: $(psql_x -c 'SELECT count(*) FROM patient_notification_attempts')"
record "patient access grants: $(psql_x -c 'SELECT count(*) FROM exchange_patient_access_grants')"

# A synthetic DHealth Connect installation. Inserted with SQL and disclosed as such: this deployment
# exposes no interface for registering a connector, and DHEALTH-NATIVE-RECOVERY-001 was explicitly
# not to build a vendor adapter. Its only job is to stop "Connect durable state survives" being a
# vacuous claim about an empty table. Synthetic, obviously so, and in the same database -- which is
# the actual property under test, since the backup dumps whole databases and selects no tables.
psql_x -c "INSERT INTO connector_installations (id, organization_id, connector_key, connector_version, contract_version, source_system, interface_type, configuration_fingerprint, mapping_fingerprint, status, created_at, updated_at) SELECT gen_random_uuid(), o.id, 'synthetic-recovery-drill', '0.0.0-synthetic', '1', 'SYNTHETIC_DRILL_SOURCE', 'REST_API', 'fp-config-synthetic', 'fp-mapping-synthetic', 'REGISTERED', now(), now() FROM exchange_organizations o ORDER BY o.id LIMIT 1" >/dev/null \
  || record "note: synthetic Connect row could not be inserted (recorded, not fatal)"
record "connector installations: $(psql_x -c 'SELECT count(*) FROM connector_installations')"

# ---------------------------------------------------------------------------
step "record the recovery sentinels"
bash "$(dirname "${BASH_SOURCE[0]}")/recovery-sentinels.sh" capture "$SENTINELS" \
  || die "could not capture sentinels"
# A full table census, so "nothing was selectively dropped" is provable across every table in the
# schema rather than only the ones this drill happens to name.
#
# EXACT counts, not `pg_stat_user_tables.n_live_tup`. The first version of this used n_live_tup and
# the drill failed on its own instrument: that column is a statistics-collector estimate that lags
# until autovacuum or ANALYZE catches up, so the source under-reported the grant and notification
# rows created seconds earlier while the restored side -- freshly COPYed -- reported them correctly.
# A census that can disagree with itself about an unchanged database is not evidence.
census() {
  psql_x -c "SELECT relname || ' ' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', schemaname, relname), false, true, '')))[1]::text FROM pg_stat_user_tables ORDER BY relname"
}
census >"${HARNESS}/source-tables.txt" 2>/dev/null
psql_x -c "SELECT count(*) FROM pg_stat_user_tables" | tr -d '\r' >"${HARNESS}/source-table-count.txt"
record "source table census: $(wc -l <"${HARNESS}/source-tables.txt") tables"
record "audit file bytes: $(compose exec -T adapter sh -c 'cat /var/lib/daher/audit/*.jsonl 2>/dev/null | wc -c' | tr -d '\r')"

# ---------------------------------------------------------------------------
step "take the encrypted backup"
T0=$(date -u +%s)
BACKUP_REQUIRE_OFFSITE=false bash "$(dirname "${BASH_SOURCE[0]}")/backup.sh" \
  || die "the backup failed"
time_it "encrypted backup" "$(( $(date -u +%s) - T0 ))"
ARCHIVE="$(find "$NATIVE_BACKUP_DIR" -maxdepth 1 -name 'dhealth-native-*.tar.gz.enc' -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2-)"
[ -n "$ARCHIVE" ] || die "no encrypted archive was produced"
ARCHIVE_NAME="$(basename "$ARCHIVE")"
ARCHIVE_SIZE="$(wc -c <"$ARCHIVE")"
record "archive: ${ARCHIVE_NAME} (${ARCHIVE_SIZE} bytes)"

# ---------------------------------------------------------------------------
step "prove the archive is encrypted and no plaintext was left behind"
# `file`-free check: an AES-256-CBC salted openssl stream begins with the literal bytes "Salted__",
# and contains none of the SQL a plaintext dump would.
head -c 8 "$ARCHIVE" | grep -q 'Salted__' || die "the archive does not look like an openssl salted stream"
if grep -qa 'CREATE TABLE\|PGDMP\|exchange_lab_orders' "$ARCHIVE"; then
  die "the archive contains recognisable plaintext"
fi
record "archive begins with an openssl salt header and contains no recognisable plaintext"
LEFTOVER="$(find "$NATIVE_BACKUP_DIR" -type f ! -name '*.tar.gz.enc' ! -name 'last-run.status' | wc -l)"
[ "$LEFTOVER" = "0" ] || die "${LEFTOVER} non-archive file(s) left in ${NATIVE_BACKUP_DIR}"
record "no plaintext, dumps or manifests left in the backup directory"
bash "$(dirname "${BASH_SOURCE[0]}")/restore.sh" verify "$ARCHIVE_NAME" \
  || die "the archive does not verify"

# ---------------------------------------------------------------------------
step "DESTROY the durable Native state"
T0=$(date -u +%s)
# Every volume this project owns: the PostgreSQL data directory holding BOTH databases, the adapter's
# file-backed audit trail, and Caddy's data. Deliberately `down -v`, not a truncate: the point is to
# lose the storage, not to empty it politely.
compose down -v --remove-orphans || die "could not tear the deployment down"
time_it "destruction (down -v)" "$(( $(date -u +%s) - T0 ))"

# ---------------------------------------------------------------------------
step "prove the durable state is really gone"
REMAINING_CONTAINERS="$(compose ps -a -q | wc -l | tr -d ' ')"
REMAINING_VOLUMES="$(docker volume ls --format '{{.Name}}' | grep -cE '^dhealth-native(-demo)?_' || true)"
record "containers remaining in this project: ${REMAINING_CONTAINERS}"
record "Native/demo volumes remaining on the host: ${REMAINING_VOLUMES}"
[ "$REMAINING_CONTAINERS" = "0" ] || die "containers survived the destruction"
[ "$REMAINING_VOLUMES" = "0" ] || die "volumes survived the destruction -- this drill would prove nothing"
# Not only by project name: the archive is the ONLY place the data now exists, which is the property
# that matters, and it lives outside every volume this project owns.
record "the encrypted archive is outside the destroyed volume set: ${NATIVE_BACKUP_DIR}/${ARCHIVE_NAME}"
[ -f "${NATIVE_BACKUP_DIR}/${ARCHIVE_NAME}" ] || die "the archive did not survive the destruction"

# ---------------------------------------------------------------------------
step "build a clean Native deployment from source control"
T0=$(date -u +%s)
bring_up || die "the clean deployment did not come up healthy"
time_it "clean target deployment up (no restore yet)" "$(( $(date -u +%s) - T0 ))"
record "clean target alembic revision: $(psql_x -c 'SELECT version_num FROM alembic_version')"
record "clean target orders: $(psql_x -c 'SELECT count(*) FROM exchange_lab_orders')"
record "clean target organizations: $(psql_x -c 'SELECT count(*) FROM exchange_organizations')"

# ---------------------------------------------------------------------------
step "prove the clean target does NOT contain the source state"
bash "$(dirname "${BASH_SOURCE[0]}")/recovery-sentinels.sh" assert-absent "$SENTINELS" \
  || die "the target is not clean -- a restore into it would prove nothing"

# ---------------------------------------------------------------------------
step "stop the application, leaving only the database running"
# The restore tool refuses a target with live sessions, which is the right behaviour and also means
# the application has to be stopped first. Caddy and the UI go too: nothing should be able to accept
# a clinical request against a half-restored database.
compose stop adapter keycloak caddy daher-ui || die "could not stop the application"
record "adapter, keycloak, caddy and the UI are stopped; dhealth-db is still up"

# ---------------------------------------------------------------------------
step "restore the encrypted backup into the clean target"
T0=$(date -u +%s)
bash "$(dirname "${BASH_SOURCE[0]}")/restore.sh" restore "$ARCHIVE_NAME" \
  --confirm-destroy-database "$EXCHANGE_DB" || die "the restore failed"
RESTORE_SECONDS=$(( $(date -u +%s) - T0 ))
time_it "restore (verify + apply + tenancy proof)" "$RESTORE_SECONDS"

# ---------------------------------------------------------------------------
step "prove tenant isolation over TCP before anything is started"
# Deliberately BEFORE `up`. Bringing the stack up re-runs `dhealth-db-hardening`, which converges the
# grants -- so a restore that failed to re-harden would be masked by the very next command. Running
# the probe here is what makes the restore's own hardening the thing under test.
bash "$(dirname "${BASH_SOURCE[0]}")/restore.sh" verify-tenancy || die "tenant isolation is not intact after the restore"

# ---------------------------------------------------------------------------
step "start the restored Native deployment"
T0=$(date -u +%s)
bring_up || die "the restored deployment did not come up healthy"
READY_SECONDS=$(( $(date -u +%s) - T0 ))
time_it "restore complete -> application ready" "$READY_SECONDS"
for url in "https://app.${BASE_DOMAIN_VALUE}/" "https://id.${BASE_DOMAIN_VALUE}/realms/dhealth-exchange" "https://exchange.${BASE_DOMAIN_VALUE}/exchange/v1/me"; do
  record "edge ${url} -> $(curl -sk -o /dev/null -w '%{http_code}' --max-time 15 "$url")"
done

# ---------------------------------------------------------------------------
step "prove the SAME persisted objects returned"
bash "$(dirname "${BASH_SOURCE[0]}")/recovery-sentinels.sh" assert-present "$SENTINELS" \
  || die "the restored deployment does not contain the same objects"
census >"${HARNESS}/restored-tables.txt" 2>/dev/null
RESTORED_TABLE_COUNT="$(psql_x -c 'SELECT count(*) FROM pg_stat_user_tables' | tr -d '\r')"
SOURCE_TABLE_COUNT="$(cat "${HARNESS}/source-table-count.txt")"
if [ "$RESTORED_TABLE_COUNT" != "$SOURCE_TABLE_COUNT" ]; then
  die "the restored schema has ${RESTORED_TABLE_COUNT} tables, the source had ${SOURCE_TABLE_COUNT}"
fi
record "every table returned: ${RESTORED_TABLE_COUNT} of ${SOURCE_TABLE_COUNT}"
# Row-count census over EVERY table, which is how notification, patient-access, Connect and audit
# state are covered without the drill having to enumerate them.
if ! diff -u "${HARNESS}/source-tables.txt" "${HARNESS}/restored-tables.txt" >"${HARNESS}/table-diff.txt"; then
  record "table census differs (recorded in ${HARNESS}/table-diff.txt):"
  sed -n '1,40p' "${HARNESS}/table-diff.txt" | sed 's/^/    /'
  die "at least one table came back with a different row count"
fi
record "the whole-database row census is identical, table for table"
record "restored audit file bytes: $(compose exec -T adapter sh -c 'cat /var/lib/daher/audit/*.jsonl 2>/dev/null | wc -c' | tr -d '\r')"
# `state`, not `status` -- the column is `state` (the first version of this line named `status`, the
# query errored, and the evidence line came out silently empty. A `record` line cannot fail the run,
# which is exactly why a wrong one has to be caught by reading the output).
record "notifications after restore (id + state): $(psql_x -c "SELECT string_agg(left(id::text,8) || ':' || state, ',') FROM patient_notifications")"
record "notification delivered rows after restore (must be 0 -- only a signed provider receipt writes DELIVERED): $(psql_x -c "SELECT count(*) FROM patient_notifications WHERE state = 'DELIVERED'")"
record "notification attempts after restore: $(psql_x -c 'SELECT count(*) FROM patient_notification_attempts')"
record "connector installations after restore: $(psql_x -c "SELECT string_agg(connector_key || ':' || status, ',') FROM connector_installations")"

# ---------------------------------------------------------------------------
step "prove recovery through the real application, then write new clinical work"
T0=$(date -u +%s)
( cd ui && E2E_REPORT_JSON="${HARNESS}/recovery-proof.json" \
    npx playwright test --config=e2e/native-recovery.config.ts e2e/native/04-recovery-proof.spec.ts ) \
  || die "the browser recovery proof failed"
time_it "browser recovery proof + post-recovery journey" "$(( $(date -u +%s) - T0 ))"

# ---------------------------------------------------------------------------
step "confirm the restored release was not rewritten by the new work"
bash "$(dirname "${BASH_SOURCE[0]}")/recovery-sentinels.sh" assert-present "$SENTINELS" \
  || die "the restored release changed while new work was performed"
record "orders after recovery: $(psql_x -c 'SELECT count(*) FROM exchange_lab_orders') (1 restored + 1 new)"
record "release versions after recovery: $(psql_x -c 'SELECT count(*) FROM exchange_release_versions')"
record "reviews after recovery: $(psql_x -c 'SELECT count(*) FROM exchange_doctor_reviews WHERE reviewed_at IS NOT NULL')"
record "notification attempts after the new work: $(psql_x -c 'SELECT count(*) FROM patient_notification_attempts')"

# ---------------------------------------------------------------------------
step "clean up every secret and every plaintext artifact this drill created"
# The one real secret the drill handles is the raw patient-access grant: the product stores only its
# SHA-256 digest, so the raw value had to be captured at issue time to test it after the restore.
# It is deleted here, unconditionally, whether the drill passed or failed.
rm -f -- "$GRANT_FILE"
[ ! -f "$GRANT_FILE" ] || die "the captured patient grant is still on disk"
record "the captured patient grant has been deleted"
# Nothing decrypted, no dump and no manifest may survive. Staging is a tmpfs inside `run --rm`
# containers, so this asserts a property rather than performing a cleanup -- if it ever fails, the
# staging mount has changed and plaintext reached a disk.
PLAINTEXT="$(find "$NATIVE_BACKUP_DIR" -type f ! -name '*.tar.gz.enc' ! -name 'last-run.status' | wc -l)"
[ "$PLAINTEXT" = "0" ] || die "${PLAINTEXT} plaintext artefact(s) in ${NATIVE_BACKUP_DIR}"
# `|| true` is load-bearing: finding nothing is the SUCCESS case here, and grep reports that as exit
# 1. Sourcing `_compose.sh` turns on `set -e` (it declares `set -euo pipefail`), so without this the
# drill exits silently at the exact moment it proves no plaintext leaked -- which is how the first
# clean run of this step ended, with no error message at all.
LEAKED="$(grep -rlaE 'PGDMP|CREATE TABLE exchange_' "$HARNESS" 2>/dev/null | wc -l || true)"
[ "$LEAKED" = "0" ] || die "${LEAKED} file(s) under ${HARNESS} contain database plaintext"
record "no decrypted dump, manifest or passphrase file survives; ${NATIVE_BACKUP_DIR} holds only encrypted archives"
record "the sentinel and evidence files that remain contain identifiers and counts, no secrets"

# The stack is deliberately left RUNNING so the recovered deployment can be inspected by hand after
# a drill -- which is most of the value of having run one. `--teardown` removes it, volumes and all.
if printf '%s\n' "$@" | grep -qx -- --teardown; then
  compose down -v --remove-orphans >/dev/null 2>&1
  record "qualification environment torn down (--teardown): containers and volumes removed"
else
  record "the restored deployment is left running for inspection; pass --teardown to remove it"
fi

# ---------------------------------------------------------------------------
step "summary"
{
  echo
  echo "MEASURED TIMINGS (one workstation, one run -- not an RTO)"
  cat "$TIMINGS"
  echo
  echo "archive: ${ARCHIVE_NAME}  ${ARCHIVE_SIZE} bytes"
} | tee -a "$EVIDENCE"

echo
echo "RECOVERY DRILL PASSED."
echo "  We destroyed the DHealth Native durable state, rebuilt the deployment from source control,"
echo "  restored the encrypted backup, proved tenant isolation over TCP, signed in with the"
echo "  pre-backup identity through real Keycloak, found the same order, release version and review"
echo "  through the real product, and then completed a new clinical workflow on top of it."
echo
echo "  Evidence: ${EVIDENCE}"
echo "  NOT claimed: off-site recovery, production deployment, human usability, clinical sign-off,"
echo "  real provider messaging. See docs/NATIVE-RECOVERY.md."
exit 0
