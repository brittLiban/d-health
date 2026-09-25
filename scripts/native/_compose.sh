#!/bin/bash
# Shared Compose invocation for the Native operator scripts (DHEALTH-NATIVE-RECOVERY-001).
# Sourced, never executed.
#
# One definition of "which deployment are we talking to", so backup, restore and the recovery drill
# cannot drift apart about it. The default is the canonical production deployment:
#
#     docker compose -f docker-compose.native.yml --env-file .env.native
#
# Two hooks, both for environments that are not a plain production host:
#
#   NATIVE_ENV_FILE        the env file to read (default `.env.native`)
#   NATIVE_COMPOSE_EXTRA   extra `-f` files, space separated, applied AFTER the canonical one
#
# `NATIVE_COMPOSE_EXTRA` is how the recovery drill points these scripts at the demo overlay -- which
# supplies synthetic staff identities and a deterministic baseline that a production deployment
# deliberately does not have. It composes Compose files and nothing else: it cannot disable
# authentication, grant reset authority, or change what the backup captures. Every path it can name
# is in this repository.
set -euo pipefail

NATIVE_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$NATIVE_REPO_ROOT"

NATIVE_ENV_FILE="${NATIVE_ENV_FILE:-.env.native}"
if [ ! -f "$NATIVE_ENV_FILE" ]; then
  cat >&2 <<EOF
No env file at '${NATIVE_ENV_FILE}'.

For a production deployment:   cp deploy/native/native.env.example .env.native   # then fill it in
For the demo/qualification:    NATIVE_ENV_FILE=demo/native/demo.env ...
EOF
  exit 1
fi

NATIVE_COMPOSE=(docker compose -f docker-compose.native.yml)
for extra in ${NATIVE_COMPOSE_EXTRA:-}; do
  NATIVE_COMPOSE+=(-f "$extra")
done
NATIVE_COMPOSE+=(--env-file "$NATIVE_ENV_FILE")

# Where archives live. A bind mount, never a named volume: an archive stored in a volume of the
# project it protects dies with that project. Default keeps a first run working; production points
# this at separate storage, and `docs/NATIVE-RECOVERY.md` says so where an operator will read it.
NATIVE_BACKUP_DIR="${NATIVE_BACKUP_DIR:-./backup/native-archives}"
export NATIVE_BACKUP_DIR
mkdir -p "$NATIVE_BACKUP_DIR"

# The passphrase is never read from a file in the repository, never written to a Compose file, and
# never placed on a command line. It is passed to the container by NAME (`-e VAR`, no value), so it
# travels through the Docker API rather than through this host's process table.
native_require_passphrase() {
  if [ -z "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ]; then
    cat >&2 <<'EOF'
BACKUP_ENCRYPTION_PASSPHRASE is not set.

Supply it from your own secret custody, in this shell only, e.g.

    read -rs BACKUP_ENCRYPTION_PASSPHRASE && export BACKUP_ENCRYPTION_PASSPHRASE

A backup whose only decryption secret lives on the host that was destroyed is not a
disaster-recovery backup (ADR-025). Keep a recoverable copy somewhere else.
EOF
    exit 1
  fi
}
