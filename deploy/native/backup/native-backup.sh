#!/bin/sh
# DHealth Native — encrypted backup of everything a Native deployment cannot rebuild from Git.
# DHEALTH-NATIVE-RECOVERY-001.
#
# This is deliberately NOT `backup/backup.sh`. That script belongs to the maternity/reference-
# connector track and understands MariaDB, OpenMRS, OpenELIS's `clinlims`, and a live Keycloak
# admin export. Native's durable state is three things, so this script does three things — and
# requires no OMRS_*/OE_* variable, no MariaDB client, and no OpenELIS anything. Both scripts share
# the repository's established primitives (umask 077, staging then encrypt, AES-256-CBC/PBKDF2 with
# the passphrase passed by environment, an atomically renamed status file) without sharing code, so
# a change to Native recovery can never reach the historical deployment.
#
# Durable-state inventory it implements (docs/NATIVE-RECOVERY.md holds the reasoning):
#
#   MUST BACK UP     the Exchange database    -> exchange.dump          (pg_dump -Fc)
#                    the Keycloak database    -> keycloak.dump          (pg_dump -Fc)
#                    the adapter audit volume -> adapter-audit.tar.gz   (file-backed, authoritative)
#   RECONSTRUCTIBLE  topology, migrations, realm/client definition, Caddy config and its ACME
#                    cache, PostgreSQL roles -- all from source control plus operator secrets
#   OPERATOR-SUPPLIED  every password and this passphrase
#
# Why no Keycloak admin REST realm export (the historical script takes one): for Native the realm
# and its clients are reconstructed from `keycloak/realm-config/dhealth-exchange-realm.json` plus
# `keycloak/reconcile-exchange.sh`, both in Git. What is NOT reproducible is the user rows, and
# those live in the Keycloak database this script dumps. Skipping the export also keeps client
# secrets out of the archive entirely.
set -eu
umask 077

# The archive format. `native-restore.sh` refuses anything it does not recognise exactly, so this
# is a compatibility gate and not decoration.
FORMAT='dhealth-native-backup/1'

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${NATIVE_BACKUP_OUTPUT_DIR:-/backup}"
STAGING="${NATIVE_BACKUP_STAGING_DIR:-/tmp/native-backup}"
STATUS_FILE="${OUT}/last-run.status"
FAILED=0

log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }
fail() { log "FAILED: $*"; FAILED=1; }

# Written to a temp file and renamed, never truncated in place: a reader landing between an
# O_TRUNC and the write completing would otherwise see an empty status. Same reasoning, and the
# same fix, as DH-027H applied to the historical sidecar.
status() {
  printf '%s %s\n' "$1" "$TS" > "${STATUS_FILE}.tmp"
  mv -f -- "${STATUS_FILE}.tmp" "$STATUS_FILE"
}

# The passphrase is required and comes from the environment at runtime. It is deliberately absent
# from every Compose file in this repository -- Compose interpolates profile-gated services too, so
# a `${BACKUP_ENCRYPTION_PASSPHRASE:?}` there would break `docker compose config` for every
# operator who has never taken a backup. `adapter/tests/test_native_recovery_tooling.py` asserts
# that absence.
: "${BACKUP_ENCRYPTION_PASSPHRASE:?BACKUP_ENCRYPTION_PASSPHRASE not set -- a backup nobody can decrypt is not a backup}"
: "${PGHOST:?PGHOST not set}"
: "${PGPASSWORD:?PGPASSWORD not set}"
: "${ADAPTER_DB_NAME:?ADAPTER_DB_NAME not set}"
: "${KEYCLOAK_DB_NAME:?KEYCLOAK_DB_NAME not set}"
PGUSER="${PGUSER:-postgres}"
PGPORT="${PGPORT:-5432}"
export PGUSER PGPORT

case "${BACKUP_REQUIRE_OFFSITE:-true}" in
  true|false) ;;
  *) echo "BACKUP_REQUIRE_OFFSITE must be true or false" >&2; exit 1 ;;
esac

mkdir -p "$OUT" "$STAGING"
WORK="$(mktemp -d "${STAGING}/run-${TS}-XXXXXX")"
PLAIN="${STAGING}/plain-${TS}.tar.gz"

# Cleanup on every exit path, success or failure. The staging directory is a tmpfs mount in the
# Compose service, so decrypted material never reaches a disk at all -- but a `docker cp` or a
# changed mount must not turn that into a silent regression, hence the explicit removal too.
cleanup() { rm -rf -- "$WORK"; rm -f -- "$PLAIN"; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

log "native backup ${TS} starting"
log "  exchange database: ${ADAPTER_DB_NAME}"
log "  keycloak database: ${KEYCLOAK_DB_NAME}"

# The Alembic revision the Exchange database is actually at, read from the database rather than
# from the repository -- a backup must record the schema it contains, not the schema the source
# tree happens to be on. `native-restore.sh` refuses a revision it cannot find in `migrations/`.
REVISION="$(psql -X -At -d "$ADAPTER_DB_NAME" \
  -c 'SELECT version_num FROM alembic_version' 2>/dev/null || true)"
[ -n "$REVISION" ] || fail "could not read alembic_version from ${ADAPTER_DB_NAME}"
SERVER_VERSION="$(psql -X -At -d postgres -c 'SHOW server_version' 2>/dev/null || echo unknown)"

# Two hot logical dumps, back to back. Each `pg_dump` is internally consistent (it runs in one
# repeatable-read snapshot), but the two are separate snapshots taken seconds apart: there is no
# cluster-wide snapshot across databases in PostgreSQL. Both start timestamps go in the manifest so
# the consistency boundary is a recorded fact rather than an assumption. This is acceptable for MVP
# because the only cross-database relationship is "an Exchange identity references a Keycloak
# subject", and a subject that appears in the later dump but not the earlier one is a staff account
# created inside the window -- it reappears at the next backup and blocks nobody else's care.
# Clinical operations are NOT frozen: doing so would trade a real cost for a theoretical one.
EXCHANGE_STARTED="$(date -u +%Y%m%dT%H%M%SZ)"
if pg_dump --format=custom --file="${WORK}/exchange.dump" "$ADAPTER_DB_NAME" 2>"${WORK}/exchange.err"; then
  log "  exchange.dump: $(wc -c <"${WORK}/exchange.dump") bytes"
else
  fail "pg_dump ${ADAPTER_DB_NAME} -- $(head -1 "${WORK}/exchange.err" 2>/dev/null)"
fi

KEYCLOAK_STARTED="$(date -u +%Y%m%dT%H%M%SZ)"
if pg_dump --format=custom --file="${WORK}/keycloak.dump" "$KEYCLOAK_DB_NAME" 2>"${WORK}/keycloak.err"; then
  log "  keycloak.dump: $(wc -c <"${WORK}/keycloak.dump") bytes"
else
  fail "pg_dump ${KEYCLOAK_DB_NAME} -- $(head -1 "${WORK}/keycloak.err" 2>/dev/null)"
fi

# The adapter's audit trail is file-backed, not a table (ADAPTER_AUDIT_PATH), so it is durable
# state that no database dump contains. A missing mount is a failure, never a silently smaller
# archive: an archive that looks tidy and cannot restore the audit history is worse than one that
# refuses to be written.
AUDIT_SRC="${NATIVE_BACKUP_AUDIT_DIR:-/audit}"
if [ ! -d "$AUDIT_SRC" ]; then
  fail "required audit directory ${AUDIT_SRC} is not mounted"
elif tar -czf "${WORK}/adapter-audit.tar.gz" --numeric-owner -C "$(dirname "$AUDIT_SRC")" \
       "$(basename "$AUDIT_SRC")" 2>"${WORK}/audit.err"; then
  log "  adapter-audit.tar.gz: $(wc -c <"${WORK}/adapter-audit.tar.gz") bytes"
else
  fail "tar ${AUDIT_SRC} -- $(head -1 "${WORK}/audit.err" 2>/dev/null)"
fi

rm -f -- "${WORK}"/*.err

if [ "$FAILED" -ne 0 ]; then
  status FAILED
  log "native backup ${TS} FAILED before archive creation; nothing was written"
  exit 1
fi

# The manifest. Every field a restore needs to decide whether it may proceed, and no secret:
# no password, no passphrase, no token, no client secret, no patient-identifying value.
{
  echo "format=${FORMAT}"
  echo "created_utc=${TS}"
  echo "exchange_database=${ADAPTER_DB_NAME}"
  echo "keycloak_database=${KEYCLOAK_DB_NAME}"
  echo "exchange_owner=${ADAPTER_DB_USER:-adapter}"
  echo "keycloak_owner=${KEYCLOAK_DB_USER:-keycloak}"
  echo "alembic_revision=${REVISION}"
  echo "postgres_server_version=${SERVER_VERSION}"
  echo "exchange_dump_started_utc=${EXCHANGE_STARTED}"
  echo "keycloak_dump_started_utc=${KEYCLOAK_STARTED}"
  echo "consistency=per-database-snapshot"
  for F in exchange.dump keycloak.dump adapter-audit.tar.gz; do
    printf 'component %s %s %s\n' \
      "$F" "$(wc -c <"${WORK}/${F}")" "$(sha256sum "${WORK}/${F}" | cut -d' ' -f1)"
  done
} >"${WORK}/MANIFEST"
log "  manifest: revision ${REVISION}, 3 components"

# Flat archive: every member sits at the archive root with a plain basename, which is what lets
# the restore side reject anything else outright rather than having to reason about a prefix.
if ! tar -czf "$PLAIN" -C "$WORK" MANIFEST exchange.dump keycloak.dump adapter-audit.tar.gz; then
  fail "archive creation"
  status FAILED
  exit 1
fi

ARCHIVE="${OUT}/dhealth-native-${TS}.tar.gz.enc"
# AES-256-CBC with PBKDF2 and a salt, passphrase read from the environment and never placed on a
# command line -- the same mechanism ADR-025 established for this repository's backups. Changing it
# would be a decision, not a refactor, so it is unchanged here.
if ! openssl enc -aes-256-cbc -pbkdf2 -salt -in "$PLAIN" -out "$ARCHIVE" \
     -pass env:BACKUP_ENCRYPTION_PASSPHRASE 2>/dev/null; then
  fail "encryption"
  rm -f -- "$ARCHIVE"
  status FAILED
  exit 1
fi
rm -f -- "$PLAIN"
log "  encrypted: $(basename "$ARCHIVE") ($(wc -c <"$ARCHIVE") bytes)"

# Off-site is a production requirement this image cannot satisfy: it carries no uploader, and
# building one was out of scope for DHEALTH-NATIVE-RECOVERY-001. The contract is kept honest rather
# than quietly relaxed -- the default still REQUIRES off-site, so an unconfigured production run
# ends FAILED with its encrypted archive retained, and an operator who sets a bucket is told the
# truth instead of being allowed to believe a copy left the host.
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  fail "off-site upload is not implemented in the Native backup image (BACKUP_S3_BUCKET is set); the encrypted local archive was written and retained"
elif [ "${BACKUP_REQUIRE_OFFSITE:-true}" = "true" ]; then
  fail "BACKUP_REQUIRE_OFFSITE=true but no off-site destination exists for Native; encrypted local archive retained, no off-site copy was made"
else
  log "  off-site explicitly disabled for this run (BACKUP_REQUIRE_OFFSITE=false)"
fi

if [ "$FAILED" -eq 0 ]; then
  status SUCCESS
  log "native backup ${TS} SUCCESS"
  exit 0
fi

status FAILED
log "native backup ${TS} FAILED"
exit 1
