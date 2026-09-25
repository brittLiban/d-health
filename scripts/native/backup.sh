#!/bin/bash
# DHealth Native — take an encrypted backup now (DHEALTH-NATIVE-RECOVERY-001).
#
#   read -rs BACKUP_ENCRYPTION_PASSPHRASE && export BACKUP_ENCRYPTION_PASSPHRASE
#   scripts/native/backup.sh
#
# Runs against a LIVE deployment: both dumps are hot and clinical work is not interrupted. See
# docs/NATIVE-RECOVERY.md for the consistency boundary that buys.
#
#   scripts/native/backup.sh --status     print the last run's result and list the archives
set -euo pipefail

# shellcheck source=scripts/native/_compose.sh
source "$(dirname "${BASH_SOURCE[0]}")/_compose.sh"

if [ "${1:-}" = "--status" ]; then
  echo "Backup directory: ${NATIVE_BACKUP_DIR}"
  if [ -f "${NATIVE_BACKUP_DIR}/last-run.status" ]; then
    echo -n "Last run:         "; cat "${NATIVE_BACKUP_DIR}/last-run.status"
  else
    echo "Last run:         (no run has been recorded here)"
  fi
  echo "Archives:"
  # Only the encrypted archives are listed, and nothing is decrypted to describe them. Reading an
  # archive's manifest is `scripts/native/restore.sh verify`, which needs the passphrase.
  find "$NATIVE_BACKUP_DIR" -maxdepth 1 -name 'dhealth-native-*.tar.gz.enc' -printf '  %f  %s bytes  %TY-%Tm-%TdT%TH:%TM\n' \
    2>/dev/null | sort || true
  exit 0
fi

native_require_passphrase

echo "DHealth Native — encrypted backup"
echo "  compose:  docker-compose.native.yml ${NATIVE_COMPOSE_EXTRA:-}"
echo "  env file: ${NATIVE_ENV_FILE}"
echo "  archives: ${NATIVE_BACKUP_DIR}"
echo

# `run --rm` in the `recovery` profile: a one-shot that leaves no container behind and whose tmpfs
# staging area disappears with it, so plaintext cannot outlive the run even on a crash. `-e VAR`
# with no value passes the passphrase by name from this shell's environment.
"${NATIVE_COMPOSE[@]}" --profile recovery run --rm \
  -e BACKUP_ENCRYPTION_PASSPHRASE \
  native-backup

echo
echo -n "Status: "; cat "${NATIVE_BACKUP_DIR}/last-run.status"
