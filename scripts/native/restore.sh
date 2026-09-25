#!/bin/bash
# DHealth Native — verify or restore an encrypted backup (DHEALTH-NATIVE-RECOVERY-001).
#
#   scripts/native/restore.sh verify <archive-name>
#       Decrypt, check the manifest, re-compute every component hash. Touches no database. Safe
#       against a live deployment.
#
#   scripts/native/restore.sh verify-tenancy
#       Prove PostgreSQL cross-database isolation over TCP with the real passwords. Run this
#       BETWEEN a restore and the next `up`: `up` re-runs dhealth-db-hardening and would mask a
#       restore that failed to re-harden.
#
#   scripts/native/restore.sh restore <archive-name> --confirm-destroy-database <name>
#       DESTRUCTIVE. Drops and recreates both databases, then restores them. Requires the Exchange
#       database to be named by hand, and refuses while Keycloak or the adapter hold a session.
#
# The order of a real recovery is in docs/NATIVE-RECOVERY.md. In short: stop the application, start
# only the database, restore, verify tenancy, then bring the stack up.
set -euo pipefail

# shellcheck source=scripts/native/_compose.sh
source "$(dirname "${BASH_SOURCE[0]}")/_compose.sh"

COMMAND="${1:-}"
case "$COMMAND" in
  verify|restore|verify-tenancy) ;;
  *)
    sed -n '3,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
    exit 1
    ;;
esac

native_require_passphrase_if_needed() {
  [ "$COMMAND" = "verify-tenancy" ] || native_require_passphrase
}
native_require_passphrase_if_needed

ARGS=("$COMMAND")
if [ "$COMMAND" != "verify-tenancy" ]; then
  ARCHIVE="${2:-}"
  if [ -z "$ARCHIVE" ]; then
    echo "Which archive? Available in ${NATIVE_BACKUP_DIR}:" >&2
    find "$NATIVE_BACKUP_DIR" -maxdepth 1 -name 'dhealth-native-*.tar.gz.enc' -printf '  %f\n' | sort >&2
    exit 1
  fi
  # Basename only: the archive is read from the container's own read-only /backup mount, so a path
  # from outside it cannot be reached and does not need to be sanitised here.
  ARGS+=("/backup/$(basename "$ARCHIVE")")
  shift 2
  ARGS+=("$@")
fi

if [ "$COMMAND" = "restore" ]; then
  cat <<EOF

⚠️  DESTRUCTIVE RESTORE
    This DROPS and recreates this deployment's two databases before writing the archive into them.
    Everything currently in them is lost. It will refuse if Keycloak or the adapter still hold a
    session, and it will refuse unless every integrity check passes first.

      compose:  docker-compose.native.yml ${NATIVE_COMPOSE_EXTRA:-}
      env file: ${NATIVE_ENV_FILE}

EOF
fi

"${NATIVE_COMPOSE[@]}" --profile recovery run --rm \
  -e BACKUP_ENCRYPTION_PASSPHRASE \
  native-restore "${ARGS[@]}"
