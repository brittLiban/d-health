#!/bin/sh
# Small PID-1 scheduler: retains container environment (including secrets),
# unlike distro cron implementations whose environment behavior varies.
set -eu

HOUR="${BACKUP_HOUR_UTC:-0}"
case "$HOUR" in ''|*[!0-9]*) echo "BACKUP_HOUR_UTC must be 0-23" >&2; exit 1;; esac
[ "$HOUR" -le 23 ] || { echo "BACKUP_HOUR_UTC must be 0-23" >&2; exit 1; }
: "${BACKUP_ENCRYPTION_PASSPHRASE:?BACKUP_ENCRYPTION_PASSPHRASE not set}"

CHILD=''
stop() { [ -z "$CHILD" ] || kill "$CHILD" 2>/dev/null || true; exit 0; }
trap stop HUP INT TERM

while :; do
  NOW="$(date -u +%s)"
  MIDNIGHT="$(date -u -d 'today 00:00:00' +%s)"
  NEXT=$((MIDNIGHT + HOUR * 3600))
  [ "$NEXT" -gt "$NOW" ] || NEXT=$((NEXT + 86400))
  DELAY=$((NEXT - NOW))
  echo "[$(date -u +%H:%M:%SZ)] next backup at $(date -u -d "@${NEXT}" +%Y-%m-%dT%H:%M:%SZ)"
  sleep "$DELAY" & CHILD=$!
  wait "$CHILD" || true
  CHILD=''
  /usr/local/bin/daher-backup || true
done
