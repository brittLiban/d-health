#!/bin/sh
# =============================================================================
# Daher Health — nightly backup of BOTH database engines and the data volumes.
#
# ⚠️ THIS SCRIPT IS NOT THE DELIVERABLE. DH-015 IS.
# A backup nobody has restored is not a backup, it is a file. This script exists
# to be exercised by the restore drill; until that drill has actually been
# performed and its output recorded, the pinned P0 item is NOT done. See AGENTS.md.
#
# WHY BOTH ENGINES, EVERY TIME (ADR-001, ARCHITECTURE §10)
# The stack has two: MariaDB holds every patient record; PostgreSQL holds the
# laboratory, Keycloak and the adapter ledger. A restore that covers only
# PostgreSQL comes back with a working lab system and NO PATIENT RECORDS. That
# failure mode is the whole reason this is written as one script over both.
#
# Exit codes: 0 all components succeeded · 1 at least one FAILED.
# Partial success is a failure. A backup missing one engine is not a backup.
# =============================================================================
set -eu

TS="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="${BACKUP_WORK_DIR:-/tmp/daher-backup}/${TS}"
OUT="${BACKUP_OUTPUT_DIR:-/backup}"
STATUS_FILE="${OUT}/last-run.status"
FAILED=0

log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }
fail() { log "FAILED: $*"; FAILED=1; }

mkdir -p "$WORK" "$OUT"
log "backup ${TS} starting"

# --- 1. MariaDB: OpenMRS. The patient records. -------------------------------
# --single-transaction keeps it consistent without locking out a live clinic.
if mariadb-dump \
      --host="${OMRS_DB_HOST:-openmrs-db}" \
      --user=root --password="${OMRS_DB_ROOT_PASSWORD:?OMRS_DB_ROOT_PASSWORD not set}" \
      --single-transaction --routines --triggers --events \
      "${OMRS_DB_NAME:-openmrs}" > "${WORK}/openmrs.sql" 2>"${WORK}/openmrs.err"; then
  log "  mariadb openmrs: $(wc -c < "${WORK}/openmrs.sql") bytes"
else
  fail "mariadb-dump openmrs — $(head -1 "${WORK}/openmrs.err" 2>/dev/null)"
fi

# --- 2. PostgreSQL: all three co-tenant databases ----------------------------
export PGPASSWORD="${OE_POSTGRES_PASSWORD:?OE_POSTGRES_PASSWORD not set}"
for DB in "${OE_DB_NAME:-clinlims}" "${KEYCLOAK_DB_NAME:-keycloak}" "${ADAPTER_DB_NAME:-adapter}"; do
  if pg_dump --host="${OE_DB_HOST:-openelis-db}" --username=postgres \
             --format=custom --file="${WORK}/${DB}.dump" "$DB" 2>"${WORK}/${DB}.err"; then
    log "  postgres ${DB}: $(wc -c < "${WORK}/${DB}.dump") bytes"
  else
    fail "pg_dump ${DB} — $(head -1 "${WORK}/${DB}.err" 2>/dev/null)"
  fi
done
unset PGPASSWORD

# --- 3. Data volumes ----------------------------------------------------------
# Not optional: OpenMRS stores complex obs (uploaded files, images) on disk, not
# in the database. A database-only restore silently loses them, and nothing
# complains until a clinician opens a chart and the attachment is gone.
for V in ${BACKUP_VOLUME_PATHS:-/volumes/openmrs-data /volumes/adapter-audit}; do
  NAME="$(basename "$V")"
  if [ -d "$V" ]; then
    if tar -czf "${WORK}/${NAME}.tar.gz" -C "$(dirname "$V")" "$NAME" 2>"${WORK}/${NAME}.err"; then
      log "  volume ${NAME}: $(wc -c < "${WORK}/${NAME}.tar.gz") bytes"
    else
      fail "tar ${NAME}"
    fi
  else
    log "  volume ${NAME}: not mounted, skipped"
  fi
done

# --- 4. Manifest --------------------------------------------------------------
# Written so a restore can tell WHAT it is restoring before it starts, and so a
# silently-shrinking backup is visible rather than discovered during an incident.
{
  echo "backup_timestamp=${TS}"
  echo "components:"
  for f in "${WORK}"/*.sql "${WORK}"/*.dump "${WORK}"/*.tar.gz; do
    [ -f "$f" ] && echo "  $(basename "$f") $(wc -c < "$f") bytes"
  done
} > "${WORK}/MANIFEST.txt"

# --- 5. Encrypt ---------------------------------------------------------------
# Patient data leaving the VM is encrypted before it leaves, never after.
ARCHIVE="${OUT}/daher-${TS}.tar.gz"
tar -czf "$ARCHIVE" -C "$(dirname "$WORK")" "$(basename "$WORK")"

if [ -n "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ]; then
  if openssl enc -aes-256-cbc -pbkdf2 -salt \
       -in "$ARCHIVE" -out "${ARCHIVE}.enc" \
       -pass env:BACKUP_ENCRYPTION_PASSPHRASE 2>/dev/null; then
    rm -f "$ARCHIVE"; ARCHIVE="${ARCHIVE}.enc"
    log "  encrypted: $(basename "$ARCHIVE")"
  else
    fail "encryption"
  fi
else
  # Loud, because an unencrypted archive of a maternity clinic's records is the
  # kind of thing that should never happen quietly.
  log "  ⚠️ BACKUP_ENCRYPTION_PASSPHRASE not set — archive is UNENCRYPTED"
  FAILED=1
fi

# --- 6. Off-site upload -------------------------------------------------------
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  if aws s3 cp "$ARCHIVE" "s3://${BACKUP_S3_BUCKET}/$(basename "$ARCHIVE")" \
        ${BACKUP_S3_ENDPOINT:+--endpoint-url "$BACKUP_S3_ENDPOINT"} >/dev/null 2>&1; then
    log "  uploaded to ${BACKUP_S3_BUCKET}"
  else
    fail "s3 upload"
  fi
else
  log "  BACKUP_S3_BUCKET not set — local copy only, NOT off-site"
fi

# --- 7. Prune -----------------------------------------------------------------
find "$OUT" -name 'daher-*.tar.gz*' -mtime "+${BACKUP_RETENTION_DAYS:-30}" -delete 2>/dev/null || true

# --- 8. Status marker ---------------------------------------------------------
rm -rf "$WORK"
if [ "$FAILED" -eq 0 ]; then
  echo "SUCCESS ${TS}" > "$STATUS_FILE"; log "backup ${TS} SUCCESS"; exit 0
else
  echo "FAILED ${TS}" > "$STATUS_FILE"; log "backup ${TS} FAILED"; exit 1
fi
