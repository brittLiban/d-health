#!/bin/sh
# DHealth Native — restore an encrypted backup into a clean deployment, and prove the result.
# DHEALTH-NATIVE-RECOVERY-001.
#
# Three subcommands:
#
#   verify <archive>          decrypt, check the manifest and re-compute every hash. Touches no
#                             database. Safe to run against a live deployment.
#   verify-tenancy            prove PostgreSQL cross-database isolation over TCP with real
#                             passwords. Separate on purpose: it is also the check that must be run
#                             BETWEEN a restore and the next `docker compose up`, because `up`
#                             re-runs `dhealth-db-hardening` and would mask a restore that failed
#                             to re-harden.
#   restore <archive> --confirm-destroy-database <name>
#                             everything `verify` does, and then -- only if all of it passed --
#                             drops and recreates the two databases and restores them.
#
# The ordering is the whole design: EVERY check happens against staging, and the first statement
# that can change the target is not issued until all of them have passed. "Decryption succeeded" is
# not integrity, and a manifest that only agrees with itself is not integrity either -- which is why
# the three required components are named in this script and not read from the manifest.
#
# What a restore must NOT do, learned from the 2026-08-13 maternity drill (backup/restore-drill.md
# §3): `DROP DATABASE; CREATE DATABASE; pg_restore --no-privileges` silently discards tenant
# hardening, because CREATE DATABASE grants CONNECT to PUBLIC by default. That drill found it after
# the fact. Here the re-hardening is part of the restore transaction sequence and is then verified
# over TCP before the command will report success.
set -eu
umask 077

FORMAT='dhealth-native-backup/1'
REQUIRED_COMPONENTS='exchange.dump keycloak.dump adapter-audit.tar.gz'
ALLOWED_MEMBERS='MANIFEST exchange.dump keycloak.dump adapter-audit.tar.gz'

STAGING="${NATIVE_BACKUP_STAGING_DIR:-/tmp/native-restore}"
AUDIT_DST="${NATIVE_BACKUP_AUDIT_DIR:-/audit}"
MIGRATIONS_DIR="${NATIVE_MIGRATIONS_DIR:-/migrations/versions}"

log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }
die() { echo "REFUSED: $*" >&2; exit 1; }

WORK=''
cleanup() { [ -z "$WORK" ] || rm -rf -- "$WORK"; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

# ---------------------------------------------------------------------------
# tenancy verification -- six cases over TCP with real password authentication.
#
# Unix-socket `trust` proves nothing about tenancy: it authorizes by local uid. Every probe below
# therefore goes through the TCP listener with a password, which is the path Keycloak and the
# adapter actually use.
# ---------------------------------------------------------------------------
probe() {
  # probe <role> <password> <database> <EXPECT ALLOWED|REFUSED> <label>
  _role=$1; _pw=$2; _db=$3; _expect=$4; _label=$5
  if PGPASSWORD="$_pw" psql -X -q -w -h "$PGHOST" -p "${PGPORT:-5432}" \
       -U "$_role" -d "$_db" -c 'SELECT 1' >/dev/null 2>&1; then
    _actual=ALLOWED
  else
    _actual=REFUSED
  fi
  if [ "$_actual" = "$_expect" ]; then
    printf '  %-52s %-8s OK\n' "$_label" "$_actual"
  else
    printf '  %-52s %-8s WRONG (expected %s)\n' "$_label" "$_actual" "$_expect"
    TENANCY_FAILED=$((TENANCY_FAILED + 1))
  fi
}

verify_tenancy() {
  : "${KEYCLOAK_DB_PASSWORD:?KEYCLOAK_DB_PASSWORD not set}"
  : "${ADAPTER_DB_PASSWORD:?ADAPTER_DB_PASSWORD not set}"
  TENANCY_FAILED=0
  log "tenant isolation over TCP with password authentication"
  probe "${KEYCLOAK_DB_USER:-keycloak}" "$KEYCLOAK_DB_PASSWORD" "$KEYCLOAK_DB_NAME" \
    ALLOWED "keycloak role -> keycloak database"
  probe "${KEYCLOAK_DB_USER:-keycloak}" "$KEYCLOAK_DB_PASSWORD" "$ADAPTER_DB_NAME" \
    REFUSED "keycloak role -> exchange database"
  probe "${ADAPTER_DB_USER:-adapter}" "$ADAPTER_DB_PASSWORD" "$ADAPTER_DB_NAME" \
    ALLOWED "adapter role  -> exchange database"
  probe "${ADAPTER_DB_USER:-adapter}" "$ADAPTER_DB_PASSWORD" "$KEYCLOAK_DB_NAME" \
    REFUSED "adapter role  -> keycloak database"
  probe "${KEYCLOAK_DB_USER:-keycloak}" 'wrong-password-not-the-real-one' "$KEYCLOAK_DB_NAME" \
    REFUSED "keycloak role with a wrong password"
  probe "${ADAPTER_DB_USER:-adapter}" 'wrong-password-not-the-real-one' "$ADAPTER_DB_NAME" \
    REFUSED "adapter role  with a wrong password"
  if [ "$TENANCY_FAILED" -ne 0 ]; then
    die "${TENANCY_FAILED} of 6 tenant-isolation cases are wrong -- the deployment is NOT tenant-isolated"
  fi
  log "tenant isolation: 6/6 correct"
}

# ---------------------------------------------------------------------------
# staging: decrypt, extract under an allowlist, verify the manifest and every hash.
# Nothing here writes to any database.
# ---------------------------------------------------------------------------
stage_and_verify() {
  ARCHIVE=$1
  : "${BACKUP_ENCRYPTION_PASSPHRASE:?BACKUP_ENCRYPTION_PASSPHRASE not set}"
  [ -f "$ARCHIVE" ] || die "archive not found: ${ARCHIVE}"

  mkdir -p "$STAGING"
  WORK="$(mktemp -d "${STAGING}/verify-XXXXXX")"

  log "decrypting $(basename "$ARCHIVE") ($(wc -c <"$ARCHIVE") bytes)"
  # A wrong passphrase, a truncated file or a flipped byte in the ciphertext all land here. openssl
  # is given no chance to write a partial plaintext anyone might then trust: failure removes it.
  if ! openssl enc -d -aes-256-cbc -pbkdf2 -salt -in "$ARCHIVE" -out "${WORK}/plain.tar.gz" \
       -pass env:BACKUP_ENCRYPTION_PASSPHRASE 2>"${WORK}/openssl.err"; then
    rm -f -- "${WORK}/plain.tar.gz"
    die "decryption failed -- wrong passphrase or corrupt archive. Nothing was read from it and no database was touched."
  fi

  # Member allowlist, checked before a single byte is extracted. This is stronger than filtering
  # `..` or a leading `/`: a member whose name is not exactly one of four expected basenames is
  # refused, so there is no path for an archive to place a file anywhere but where it belongs.
  log "inspecting archive members"
  MEMBERS="$(tar -tzf "${WORK}/plain.tar.gz" 2>/dev/null)" \
    || die "archive is not a readable gzip tar after decryption"
  for M in $MEMBERS; do
    _ok=0
    for A in $ALLOWED_MEMBERS; do [ "$M" = "$A" ] && _ok=1; done
    [ "$_ok" -eq 1 ] || die "archive contains an unexpected member '${M}' -- refusing to extract"
  done
  tar -xzf "${WORK}/plain.tar.gz" -C "$WORK" || die "extraction failed"
  rm -f -- "${WORK}/plain.tar.gz"

  [ -f "${WORK}/MANIFEST" ] || die "archive has no MANIFEST"

  # Format gate. An archive from a future or unknown writer is refused rather than guessed at.
  M_FORMAT="$(sed -n 's/^format=//p' "${WORK}/MANIFEST")"
  [ "$M_FORMAT" = "$FORMAT" ] \
    || die "unsupported backup format '${M_FORMAT}' (this tool restores '${FORMAT}' only)"

  M_REVISION="$(sed -n 's/^alembic_revision=//p' "${WORK}/MANIFEST")"
  M_EXCHANGE_DB="$(sed -n 's/^exchange_database=//p' "${WORK}/MANIFEST")"
  M_KEYCLOAK_DB="$(sed -n 's/^keycloak_database=//p' "${WORK}/MANIFEST")"
  M_EXCHANGE_OWNER="$(sed -n 's/^exchange_owner=//p' "${WORK}/MANIFEST")"
  M_KEYCLOAK_OWNER="$(sed -n 's/^keycloak_owner=//p' "${WORK}/MANIFEST")"
  M_CREATED="$(sed -n 's/^created_utc=//p' "${WORK}/MANIFEST")"
  [ -n "$M_REVISION" ] || die "manifest declares no alembic revision"
  [ -n "$M_EXCHANGE_DB" ] && [ -n "$M_KEYCLOAK_DB" ] || die "manifest names no databases"

  log "manifest: format ${M_FORMAT}, created ${M_CREATED}, revision ${M_REVISION}"
  log "  databases: ${M_EXCHANGE_DB} (owner ${M_EXCHANGE_OWNER}), ${M_KEYCLOAK_DB} (owner ${M_KEYCLOAK_OWNER})"

  # Required components are named HERE, not taken from the manifest. A backup writer that forgot a
  # component would otherwise produce a manifest that agrees with itself perfectly.
  for C in $REQUIRED_COMPONENTS; do
    [ -f "${WORK}/${C}" ] || die "required component '${C}' is missing from the archive"
    grep -q "^component ${C} " "${WORK}/MANIFEST" \
      || die "required component '${C}' is present but absent from the manifest"
  done

  # Independently re-computed, then compared. Decryption proving the passphrase was right says
  # nothing about whether the plaintext is the plaintext that was written.
  log "verifying component integrity"
  MISMATCH=0
  while read -r _kw NAME SIZE HASH; do
    [ "$_kw" = component ] || continue
    [ -f "${WORK}/${NAME}" ] || { log "  ${NAME}: MISSING"; MISMATCH=$((MISMATCH + 1)); continue; }
    ACTUAL_SIZE="$(wc -c <"${WORK}/${NAME}")"
    ACTUAL_HASH="$(sha256sum "${WORK}/${NAME}" | cut -d' ' -f1)"
    if [ "$ACTUAL_SIZE" = "$SIZE" ] && [ "$ACTUAL_HASH" = "$HASH" ]; then
      printf '  %-24s %12s bytes  sha256 OK\n' "$NAME" "$SIZE"
    else
      printf '  %-24s MISMATCH (manifest %s/%s, actual %s/%s)\n' \
        "$NAME" "$SIZE" "$(echo "$HASH" | cut -c1-12)" "$ACTUAL_SIZE" "$(echo "$ACTUAL_HASH" | cut -c1-12)"
      MISMATCH=$((MISMATCH + 1))
    fi
  done <"${WORK}/MANIFEST"
  [ "$MISMATCH" -eq 0 ] || die "${MISMATCH} component(s) failed integrity verification -- no database was touched"

  # A backup from a newer codebase cannot be restored safely: the application about to serve it
  # would have no migration for its schema. The check needs the repository's own migration
  # directory, so an unmounted directory is a refusal rather than a skip.
  [ -d "$MIGRATIONS_DIR" ] \
    || die "migration directory ${MIGRATIONS_DIR} is not mounted -- cannot check that revision ${M_REVISION} is known to this codebase"
  # Matches the quoted revision string anywhere in the migration tree, deliberately tolerant of how
  # it is declared: this repository writes `revision: str = "0032_patient_notifications"`, and a
  # pattern that assumed `revision = "..."` refused a revision that was in fact present. Observed
  # while building this, on a real archive -- which is the kind of thing a guard must get wrong in a
  # drill rather than during a recovery.
  if ! grep -rqF "\"${M_REVISION}\"" "$MIGRATIONS_DIR" 2>/dev/null \
     && ! grep -rqF "'${M_REVISION}'" "$MIGRATIONS_DIR" 2>/dev/null; then
    die "archive was taken at Alembic revision '${M_REVISION}', which this codebase does not contain -- restoring it would leave a schema no application here can migrate"
  fi
  log "  revision ${M_REVISION} is known to this codebase"
  log "VERIFIED: archive is complete, intact and restorable by this deployment"
}

# ---------------------------------------------------------------------------
# apply: the only part that can change anything.
# ---------------------------------------------------------------------------
restore_database() {
  # restore_database <dbname> <owner> <dumpfile>
  _db=$1; _owner=$2; _dump=$3

  # Fail closed on a live target. Plain DROP DATABASE (never WITH FORCE) so an application still
  # holding a session causes a refusal instead of having its connection killed underneath it.
  _sessions="$(psql -X -At -d postgres \
    -c "SELECT count(*) FROM pg_stat_activity WHERE datname = '${_db}'" 2>/dev/null || echo unknown)"
  [ "$_sessions" = "0" ] \
    || die "database '${_db}' has ${_sessions} active session(s) -- stop keycloak and the adapter before restoring"

  log "  dropping and recreating ${_db} (owner ${_owner})"
  psql -X -q -v ON_ERROR_STOP=1 -d postgres \
    -c "DROP DATABASE IF EXISTS \"${_db}\"" \
    -c "CREATE DATABASE \"${_db}\" OWNER \"${_owner}\"" \
    || die "could not recreate ${_db}"

  # CREATE DATABASE hands PUBLIC a CONNECT privilege. Re-hardening immediately, inside the same
  # sequence, is what keeps the window between "the database exists" and "the database is
  # tenant-isolated" closed. `dhealth-db-hardening` also converges this on the next `up`; both
  # layers exist deliberately, and `verify-tenancy` is how either one is proven rather than assumed.
  psql -X -q -v ON_ERROR_STOP=1 -d postgres \
    -c "REVOKE ALL PRIVILEGES ON DATABASE \"${_db}\" FROM PUBLIC" \
    -c "GRANT ALL PRIVILEGES ON DATABASE \"${_db}\" TO \"${_owner}\"" \
    || die "could not re-harden ${_db}"

  # Ownership is preserved, not stripped: the roles exist (recreated from source control plus the
  # operator's passwords), so `--no-owner` would hand every table to the superuser and leave the
  # tenant role unable to write its own data. `--exit-on-error` makes any single failed statement
  # fail the restore instead of leaving a half-populated database that looks fine.
  log "  restoring ${_db} from $(basename "$_dump")"
  pg_restore --dbname="${_db}" --exit-on-error --no-password "$_dump" \
    || die "pg_restore into ${_db} failed"
}

# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------
: "${PGHOST:?PGHOST not set}"
: "${ADAPTER_DB_NAME:?ADAPTER_DB_NAME not set}"
: "${KEYCLOAK_DB_NAME:?KEYCLOAK_DB_NAME not set}"
PGUSER="${PGUSER:-postgres}"
PGPORT="${PGPORT:-5432}"
export PGUSER PGPORT

COMMAND="${1:-}"
case "$COMMAND" in
  verify)
    [ $# -ge 2 ] || die "usage: native-restore verify <archive>"
    : "${PGPASSWORD:?PGPASSWORD not set}"
    stage_and_verify "$2"
    ;;

  verify-tenancy)
    : "${PGPASSWORD:?PGPASSWORD not set}"
    verify_tenancy
    ;;

  restore)
    [ $# -ge 2 ] || die "usage: native-restore restore <archive> --confirm-destroy-database <name>"
    : "${PGPASSWORD:?PGPASSWORD not set}"
    ARCHIVE_ARG=$2
    shift 2
    CONFIRM=''
    while [ $# -gt 0 ]; do
      case "$1" in
        --confirm-destroy-database) CONFIRM="${2:-}"; shift 2 ;;
        *) die "unknown argument '$1'" ;;
      esac
    done
    # Two independent facts, one of which must name the actual database by hand. A restore is
    # destructive on arrival -- it drops the target before it writes -- so it is not something an
    # operator should be able to start by getting a shell-history arrow key wrong.
    [ -n "$CONFIRM" ] \
      || die "restore requires --confirm-destroy-database <exchange database name>. This command DROPS the target databases."
    [ "$CONFIRM" = "$ADAPTER_DB_NAME" ] \
      || die "--confirm-destroy-database '${CONFIRM}' does not name this deployment's Exchange database ('${ADAPTER_DB_NAME}'). Refusing an ambiguous target."

    stage_and_verify "$ARCHIVE_ARG"

    # The archive's own idea of which databases it holds must match the deployment being restored
    # into. Restoring a demo archive onto production, or the reverse, is exactly the accident this
    # catches -- and it is why the manifest records the names at all.
    [ "$M_EXCHANGE_DB" = "$ADAPTER_DB_NAME" ] \
      || die "archive holds Exchange database '${M_EXCHANGE_DB}' but this deployment is '${ADAPTER_DB_NAME}'"
    [ "$M_KEYCLOAK_DB" = "$KEYCLOAK_DB_NAME" ] \
      || die "archive holds Keycloak database '${M_KEYCLOAK_DB}' but this deployment is '${KEYCLOAK_DB_NAME}'"

    log "APPLYING -- every check above passed; the target is about to be replaced"
    restore_database "$M_KEYCLOAK_DB" "${KEYCLOAK_DB_USER:-keycloak}" "${WORK}/keycloak.dump"
    restore_database "$M_EXCHANGE_DB" "${ADAPTER_DB_USER:-adapter}" "${WORK}/exchange.dump"

    # The file-backed audit trail. Extracted over the live volume as root with numeric ownership
    # preserved, so the adapter's own uid still owns what it must append to after this.
    if [ -d "$AUDIT_DST" ]; then
      log "  restoring the adapter audit volume into ${AUDIT_DST}"
      tar -xzf "${WORK}/adapter-audit.tar.gz" --numeric-owner -C "$(dirname "$AUDIT_DST")" \
        || die "could not restore the adapter audit volume"
    else
      die "audit destination ${AUDIT_DST} is not mounted -- refusing a restore that would silently drop the audit trail"
    fi

    # Post-restore verification, before this command is allowed to report success.
    log "verifying the restored databases"
    R_REVISION="$(psql -X -At -d "$M_EXCHANGE_DB" -c 'SELECT version_num FROM alembic_version')"
    [ "$R_REVISION" = "$M_REVISION" ] \
      || die "restored Exchange database is at revision '${R_REVISION}', the archive declared '${M_REVISION}'"
    log "  alembic revision: ${R_REVISION}"

    # The sibling of the grant defect the maternity drill found: a restore that strips ownership
    # leaves every table belonging to the superuser, and the tenant role unable to write its own data.
    #
    # Checked against the ARCHIVE'S OWN ownership, read out of the dump's table of contents, rather
    # than against "everything should belong to the tenant role". A first version asserted the
    # latter and was wrong: the demo overlay's `dhealth_demo_environment` marker table is
    # deliberately owned by the superuser and merely granted SELECT to the adapter, so that the
    # application cannot write the marker that authorizes a destructive demo reset. A check that
    # forced every table to the tenant role would have quietly required that property to be given up.
    pg_restore --list "${WORK}/exchange.dump" \
      | grep -E '^[0-9]+; [0-9]+ [0-9]+ TABLE ' | grep -v ' TABLE DATA ' \
      | awk '{print $(NF-1) "|" $NF}' | sort >"${WORK}/expected-owners.txt"
    psql -X -At -d "$M_EXCHANGE_DB" \
      -c "SELECT tablename || '|' || tableowner FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename" \
      | sort >"${WORK}/actual-owners.txt"
    if ! diff -q "${WORK}/expected-owners.txt" "${WORK}/actual-owners.txt" >/dev/null; then
      log "  ownership differs from the archive:"
      diff "${WORK}/expected-owners.txt" "${WORK}/actual-owners.txt" | sed 's/^/    /' | head -20
      die "restored table ownership does not match the archive"
    fi
    log "  table ownership matches the archive exactly ($(wc -l <"${WORK}/actual-owners.txt") tables)"

    verify_tenancy
    log "RESTORED: ${M_EXCHANGE_DB} and ${M_KEYCLOAK_DB} from $(basename "$ARCHIVE_ARG")"
    ;;

  *)
    cat >&2 <<'USAGE'
usage:
  native-restore verify <archive>
  native-restore verify-tenancy
  native-restore restore <archive> --confirm-destroy-database <exchange database name>
USAGE
    exit 1
    ;;
esac
