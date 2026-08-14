#!/bin/sh
# DH-021 — render common.properties with the real SSL passwords, without ever
# committing them.
#
# Why this exists: OpenELIS reads its keystore/truststore passwords from
# common.properties, NOT from CATALINA_OPTS. That file was committed to the
# repository because it held only upstream's published values. Generating a real
# keystore therefore cannot work while that file still says `kspass` — the Spring
# context fails with "keystore password was incorrect" and every page becomes a
# redirect loop (observed 2026-08-13).
#
# Upstream delivers this file as a Docker secret, which must be a host path, and
# a host path cannot be produced by a container that runs during the same `up`.
# So the rendered file is written into a named volume instead and mounted at
# /run/secrets, preserving the path the application expects.
set -eu
umask 077

TEMPLATE="${OE_PROPERTIES_TEMPLATE:-/template/common.properties.template}"
OUT_DIR="${OE_PROPERTIES_OUT_DIR:-/output}"
OUT="${OUT_DIR}/common.properties"

: "${OE_KEYSTORE_PASSWORD:?OE_KEYSTORE_PASSWORD not set}"
: "${OE_TRUSTSTORE_PASSWORD:?OE_TRUSTSTORE_PASSWORD not set}"

log() { echo "[props] $*"; }

[ -f "$TEMPLATE" ] || { echo "[props] template not found at $TEMPLATE" >&2; exit 1; }
mkdir -p "$OUT_DIR"

# `sed` rather than envsubst: the JRE base has no gettext, and a password may
# contain characters sed would treat as delimiters or backreferences. Escape the
# replacement text for sed's RHS (\, &, and the / delimiter) before substituting.
escape() { printf '%s' "$1" | sed -e 's/[\\&/]/\\&/g'; }

KS_ESC="$(escape "$OE_KEYSTORE_PASSWORD")"
TS_ESC="$(escape "$OE_TRUSTSTORE_PASSWORD")"

TMP="$(mktemp)"
trap 'rm -f -- "$TMP"' EXIT

sed -e "s/@OE_KEYSTORE_PASSWORD@/${KS_ESC}/g" \
    -e "s/@OE_TRUSTSTORE_PASSWORD@/${TS_ESC}/g" \
    "$TEMPLATE" >"$TMP"

# Fail loudly rather than shipping a file that still contains placeholders — a
# half-rendered properties file is the same outage as no rendering at all.
if grep -q '@OE_[A-Z_]*@' "$TMP"; then
  echo "[props] unsubstituted placeholders remain:" >&2
  grep -n '@OE_[A-Z_]*@' "$TMP" >&2
  exit 1
fi

# The application reads this file; it must be readable by the webapp's user.
# 0444 matches the mode Docker gives a file-based secret.
install -m 0444 "$TMP" "$OUT"
log "rendered $(basename "$OUT") ($(wc -c <"$OUT") bytes) with SSL passwords substituted"
