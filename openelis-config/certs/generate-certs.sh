#!/bin/sh
# DH-021 — replace the publicly-downloadable OpenELIS keystore with material
# generated on this deployment.
#
# Why this exists: `itechuw/certgen` generates nothing. It ships PRE-BUILT
# keystores inside a public image, so the private key protecting the internal
# 8443 connectors can be downloaded by anyone, and the store passwords are the
# published `kspass`/`tspass`. The shipped certificate was ALSO already expired
# (notAfter 2026-07-23), which nothing on this path was validating.
#
# Structure is deliberately IDENTICAL to upstream's so nothing else has to
# change: the same three filenames, PKCS12 stores, one PrivateKeyEntry, and a
# truststore holding that same certificate as a trustedCertEntry under alias
# `oecert`. Only the material and the passwords differ. A private CA would be
# cleaner long-term, but it changes the trust structure; that is a separate
# ticket, not something to fold into a security fix.
set -eu
umask 077

KS="${OE_CERT_DIR:-/etc/openelis-global}/keystore"
CFKS="${OE_CERT_DIR:-/etc/openelis-global}/client_facing_keystore"
TS="${OE_CERT_DIR:-/etc/openelis-global}/truststore"
ALIAS="${OE_CERT_ALIAS:-1}"
TRUST_ALIAS="${OE_TRUST_ALIAS:-oecert}"
DAYS="${OE_CERT_VALIDITY_DAYS:-825}"
# Renew before expiry rather than at it, so a pilot never wakes up to a dead
# internal TLS path the way this deployment just did.
RENEW_BEFORE_DAYS="${OE_CERT_RENEW_BEFORE_DAYS:-30}"

: "${OE_KEYSTORE_PASSWORD:?OE_KEYSTORE_PASSWORD not set}"
: "${OE_TRUSTSTORE_PASSWORD:?OE_TRUSTSTORE_PASSWORD not set}"

case "$DAYS" in ''|*[!0-9]*) echo "OE_CERT_VALIDITY_DAYS must be an integer" >&2; exit 1 ;; esac
case "$RENEW_BEFORE_DAYS" in ''|*[!0-9]*) echo "OE_CERT_RENEW_BEFORE_DAYS must be an integer" >&2; exit 1 ;; esac

log() { echo "[certs] $*"; }

# Upstream's SANs, plus the names this stack actually connects to. Hostname
# verification is not enforced on these connectors today, but generating a
# certificate that would fail it is how a future strictness change turns into
# an outage.
SAN="dns:*.openelis.org,dns:openelis.org,dns:*.openelis-global.org,dns:localhost"
SAN="${SAN},dns:openelis-webapp,dns:openelis-fhir,dns:db.openelis.org,dns:fhir.openelis.org"
SAN="${SAN},dns:webapp.openelis.org,ip:127.0.0.1"

needs_generation() {
  [ -f "$KS" ] || { log "no keystore present — generating"; return 0; }
  if ! keytool -list -keystore "$KS" -storepass "$OE_KEYSTORE_PASSWORD" >/dev/null 2>&1; then
    log "existing keystore does not open with the configured password — regenerating"
    return 0
  fi
  # -checkend takes seconds. Renew early rather than exactly at expiry.
  if keytool -exportcert -rfc -alias "$ALIAS" -keystore "$KS" \
       -storepass "$OE_KEYSTORE_PASSWORD" 2>/dev/null \
     | openssl x509 -noout -checkend "$((RENEW_BEFORE_DAYS * 86400))" >/dev/null 2>&1; then
    log "existing certificate is valid for more than ${RENEW_BEFORE_DAYS} days — leaving it alone"
    return 1
  fi
  log "existing certificate is expired or within ${RENEW_BEFORE_DAYS} days of expiry — regenerating"
  return 0
}

# Public certificate in PEM, for reverse proxies that must VERIFY this backend
# rather than skip verification (DH-016 Caddy). Certificate only — never the
# private key. This runs on EVERY invocation, not only when material is
# regenerated: an idempotent run that skipped it would leave a proxy with no
# trust anchor after any restart that recreated the volume.
export_ca_pem() {
  CA_DIR="${OE_CA_OUT_DIR:-/ca}"
  [ -d "$CA_DIR" ] || return 0
  keytool -exportcert -rfc -alias "$ALIAS" -keystore "$KS" \
    -storepass "$OE_KEYSTORE_PASSWORD" 2>/dev/null >"${CA_DIR}/.openelis-ca.pem.tmp"
  install -m 0444 "${CA_DIR}/.openelis-ca.pem.tmp" "${CA_DIR}/openelis-ca.pem"
  rm -f "${CA_DIR}/.openelis-ca.pem.tmp"
  log "exported openelis-ca.pem for reverse-proxy trust"
}

if ! needs_generation; then
  keytool -list -v -keystore "$KS" -storepass "$OE_KEYSTORE_PASSWORD" 2>/dev/null \
    | grep -E '^Valid from' | sed 's/^/[certs] /'
  export_ca_pem
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

log "generating a 4096-bit RSA key and self-signed certificate valid ${DAYS} days"
keytool -genkeypair \
  -alias "$ALIAS" \
  -keyalg RSA -keysize 4096 -sigalg SHA256withRSA \
  -validity "$DAYS" \
  -dname "CN=openelis.org, OU=Daher Health, O=Daher Health, L=Djibouti, C=DJ" \
  -ext "san=${SAN}" \
  -ext "bc=ca:false" \
  -ext "ku=digitalSignature,keyEncipherment" \
  -ext "eku=serverAuth,clientAuth" \
  -keystore "$TMP/keystore" -storetype PKCS12 \
  -storepass "$OE_KEYSTORE_PASSWORD" -keypass "$OE_KEYSTORE_PASSWORD" >/dev/null

# serverAuth AND clientAuth both matter: the webapp presents this same
# certificate as a CLIENT when it calls the FHIR server over mutual TLS.
keytool -exportcert -alias "$ALIAS" -keystore "$TMP/keystore" \
  -storepass "$OE_KEYSTORE_PASSWORD" -rfc -file "$TMP/cert.pem" >/dev/null

log "building truststore trusting exactly that certificate"
keytool -importcert -noprompt -alias "$TRUST_ALIAS" -file "$TMP/cert.pem" \
  -keystore "$TMP/truststore" -storetype PKCS12 \
  -storepass "$OE_TRUSTSTORE_PASSWORD" >/dev/null

# Upstream ships a third store used for the client-facing connector. Keep the
# file present with the same material so no consumer loses a path it expects.
cp "$TMP/keystore" "$TMP/client_facing_keystore"

# Verify BEFORE overwriting anything in place. A half-written keystore would
# take both 8443 connectors down.
keytool -list -keystore "$TMP/keystore"   -storepass "$OE_KEYSTORE_PASSWORD"   >/dev/null
keytool -list -keystore "$TMP/truststore" -storepass "$OE_TRUSTSTORE_PASSWORD" >/dev/null
openssl x509 -in "$TMP/cert.pem" -noout -checkend 0 >/dev/null

install -m 0644 "$TMP/keystore"                "$KS"
install -m 0644 "$TMP/client_facing_keystore"  "$CFKS"
install -m 0644 "$TMP/truststore"              "$TS"

export_ca_pem

log "installed keystore, client_facing_keystore, truststore"
openssl x509 -in "$TMP/cert.pem" -noout -subject -dates | sed 's/^/[certs] /'
