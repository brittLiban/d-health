#!/bin/sh
# Nightly backup for both database engines, live configuration, and data volumes.
# DH-015 (a performed restore) remains the actual pilot-ready gate.
set -eu
umask 077

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${BACKUP_OUTPUT_DIR:-/backup}"
WORK_ROOT="${BACKUP_WORK_DIR:-/tmp/daher-backup}"
STATUS_FILE="${OUT}/last-run.status"
FAILED=0

log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }
fail() { log "FAILED: $*"; FAILED=1; }
# Written to a temp file and renamed into place, not truncated in place (DH-027H): `>
# "$STATUS_FILE"` opens with O_TRUNC before the write, so a reader landing between the
# truncate and the write completing would see an empty file — and the control plane now
# reads this file, not just a human running `cat` by hand. `mv` within the same directory is
# a rename(2), atomic on POSIX, so a reader always sees either the previous complete status
# or the new one, never neither.
status() {
  printf '%s %s\n' "$1" "$TS" > "${STATUS_FILE}.tmp"
  mv -f -- "${STATUS_FILE}.tmp" "$STATUS_FILE"
}

: "${BACKUP_ENCRYPTION_PASSPHRASE:?BACKUP_ENCRYPTION_PASSPHRASE not set}"
: "${OMRS_DB_ROOT_PASSWORD:?OMRS_DB_ROOT_PASSWORD not set}"
: "${OE_POSTGRES_PASSWORD:?OE_POSTGRES_PASSWORD not set}"
: "${KEYCLOAK_ADMIN_PASSWORD:?KEYCLOAK_ADMIN_PASSWORD not set}"

case "${BACKUP_RETENTION_DAYS:-30}" in
  ''|*[!0-9]*) echo "BACKUP_RETENTION_DAYS must be a non-negative integer" >&2; exit 1 ;;
esac
case "${BACKUP_REQUIRE_OFFSITE:-true}" in
  true|false) ;;
  *) echo "BACKUP_REQUIRE_OFFSITE must be true or false" >&2; exit 1 ;;
esac

mkdir -p "$OUT" "$WORK_ROOT"
WORK="$(mktemp -d "${WORK_ROOT}/run-${TS}-XXXXXX")"
PLAIN="${WORK_ROOT}/daher-${TS}.tar.gz"
cleanup() {
  rm -rf -- "$WORK"
  rm -f -- "$PLAIN"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

log "backup ${TS} starting"

# MariaDB contains the patient record. A logically consistent, non-locking dump.
if mariadb-dump --host="${OMRS_DB_HOST:-openmrs-db}" --user=root \
     --password="$OMRS_DB_ROOT_PASSWORD" --single-transaction --routines --triggers --events \
     "${OMRS_DB_NAME:-openmrs}" >"${WORK}/openmrs.sql" 2>"${WORK}/openmrs.err"; then
  log "  mariadb openmrs: $(wc -c <"${WORK}/openmrs.sql") bytes"
else
  fail "mariadb-dump openmrs — $(head -1 "${WORK}/openmrs.err" 2>/dev/null)"
fi

# PostgreSQL contains OpenELIS, Keycloak, and the adapter ledger/dead letters.
export PGPASSWORD="$OE_POSTGRES_PASSWORD"
for DB in "${OE_DB_NAME:-clinlims}" "${KEYCLOAK_DB_NAME:-keycloak}" "${ADAPTER_DB_NAME:-adapter}"; do
  if pg_dump --host="${OE_DB_HOST:-openelis-db}" --username=postgres --format=custom \
       --file="${WORK}/${DB}.dump" "$DB" 2>"${WORK}/${DB}.err"; then
    log "  postgres ${DB}: $(wc -c <"${WORK}/${DB}.dump") bytes"
  else
    fail "pg_dump ${DB} — $(head -1 "${WORK}/${DB}.err" 2>/dev/null)"
  fi
done
unset PGPASSWORD

# A live, portable Keycloak configuration export supplements (never replaces)
# the authoritative database dump. It may contain credentials and is encrypted
# with the rest of the staging directory before leaving this container.
KC_BASE="${KEYCLOAK_INTERNAL_URL:-http://keycloak.openelis.org:8080}"
if TOKEN="$(curl -fsS --data-urlencode "username=${KEYCLOAK_ADMIN_USER:-admin}" \
     --data-urlencode "password=$KEYCLOAK_ADMIN_PASSWORD" \
     -d client_id=admin-cli -d grant_type=password \
     "${KC_BASE}/realms/master/protocol/openid-connect/token" | jq -er .access_token)"; then
  if curl -fsS -X POST -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
       -d '{}' "${KC_BASE}/admin/realms/daher/partial-export?exportClients=true&exportGroupsAndRoles=true" \
       >"${WORK}/keycloak-realm.json"; then
    log "  keycloak realm: $(wc -c <"${WORK}/keycloak-realm.json") bytes"
  else
    fail "Keycloak partial realm export"
  fi
  unset TOKEN
else
  fail "Keycloak admin token"
fi

# Missing mounts are failures. Silently omitting attachments or audit history
# would create a tidy-looking archive that cannot restore the clinical record.
for V in ${BACKUP_VOLUME_PATHS:-/volumes/openmrs-data /volumes/adapter-audit}; do
  NAME="$(basename "$V")"
  if [ ! -d "$V" ]; then
    fail "required volume ${NAME} is not mounted"
  elif tar -czf "${WORK}/${NAME}.tar.gz" -C "$(dirname "$V")" "$NAME" \
       2>"${WORK}/${NAME}.err"; then
    log "  volume ${NAME}: $(wc -c <"${WORK}/${NAME}.tar.gz") bytes"
  else
    fail "tar ${NAME}"
  fi
done

if [ "$FAILED" -ne 0 ]; then
  status FAILED
  log "backup ${TS} FAILED before archive creation; nothing uploaded"
  exit 1
fi

{
  echo "backup_timestamp=${TS}"
  echo "components:"
  for F in "${WORK}"/*.sql "${WORK}"/*.dump "${WORK}"/*.json "${WORK}"/*.tar.gz; do
    [ -f "$F" ] || continue
    echo "  $(basename "$F") $(wc -c <"$F") bytes sha256=$(sha256sum "$F" | cut -d' ' -f1)"
  done
} >"${WORK}/MANIFEST.txt"

if ! tar -czf "$PLAIN" -C "$(dirname "$WORK")" "$(basename "$WORK")"; then
  fail "archive creation"
  status FAILED
  exit 1
fi
ARCHIVE="${OUT}/daher-${TS}.tar.gz.enc"
if ! openssl enc -aes-256-cbc -pbkdf2 -salt -in "$PLAIN" -out "$ARCHIVE" \
     -pass env:BACKUP_ENCRYPTION_PASSPHRASE 2>/dev/null; then
  fail "encryption"
  rm -f -- "$ARCHIVE"
  status FAILED
  exit 1
fi
rm -f -- "$PLAIN"
log "  encrypted: $(basename "$ARCHIVE")"

if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  if [ -n "${BACKUP_S3_ENDPOINT:-}" ]; then
    aws s3 cp "$ARCHIVE" "s3://${BACKUP_S3_BUCKET}/$(basename "$ARCHIVE")" \
      --endpoint-url "$BACKUP_S3_ENDPOINT" >/dev/null 2>&1 || fail "S3 upload"
  else
    aws s3 cp "$ARCHIVE" "s3://${BACKUP_S3_BUCKET}/$(basename "$ARCHIVE")" \
      >/dev/null 2>&1 || fail "S3 upload"
  fi
  [ "$FAILED" -ne 0 ] || log "  uploaded to ${BACKUP_S3_BUCKET}"
elif [ "${BACKUP_REQUIRE_OFFSITE:-true}" = "true" ]; then
  fail "BACKUP_S3_BUCKET not set — encrypted local copy retained, but no off-site copy"
else
  log "  off-site upload explicitly disabled for local verification"
fi

if [ "$FAILED" -eq 0 ]; then
  if ! find "$OUT" -type f -name 'daher-*.tar.gz.enc' \
       -mtime "+${BACKUP_RETENTION_DAYS:-30}" -delete; then
    fail "retention prune"
  fi
fi

if [ "$FAILED" -eq 0 ]; then
  status SUCCESS
  log "backup ${TS} SUCCESS"
  exit 0
fi

status FAILED
log "backup ${TS} FAILED"
exit 1
