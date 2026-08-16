#!/bin/sh
# Generate the demonstration's TLS material (ADR-022).
#
# A private CA, a server certificate for `api` signed by it, and — critically — a SECOND
# unrelated CA plus a certificate for the WRONG hostname. Those two exist so the
# demonstration can prove verification is actually happening rather than being skipped.
#
# A TLS test that only ever presents the correct certificate proves nothing: `verify=False`
# would pass it identically. The controls are the test.
set -eu
umask 077

OUT="${1:-/shared/tls}"
mkdir -p "$OUT"

gen_ca() {
  name="$1"; cn="$2"
  # basicConstraints AND keyUsage are both required. OpenSSL 3 rejects a signing CA that
  # omits keyUsage with "CA cert does not include key usage extension" — a failure the
  # plain-HTTP demo could never have surfaced, and the first thing the TLS leg found.
  openssl req -x509 -newkey rsa:2048 -nodes -days 3 \
    -keyout "$OUT/${name}-ca.key" -out "$OUT/${name}-ca.pem" \
    -subj "/CN=${cn}" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" >/dev/null 2>&1
}

gen_server() {
  name="$1"; cn="$2"; ca="$3"
  openssl req -newkey rsa:2048 -nodes \
    -keyout "$OUT/${name}.key" -out "$OUT/${name}.csr" \
    -subj "/CN=${cn}" >/dev/null 2>&1
  # SAN matters: modern clients ignore CN, so a certificate without a matching SAN fails
  # hostname verification even when the CA is trusted. That is one of the controls below.
  {
    printf 'subjectAltName=DNS:%s\n' "$cn"
    printf 'basicConstraints=CA:FALSE\n'
    printf 'keyUsage=critical,digitalSignature,keyEncipherment\n'
    printf 'extendedKeyUsage=serverAuth\n'
  } > "$OUT/${name}.ext"
  openssl x509 -req -in "$OUT/${name}.csr" -days 3 \
    -CA "$OUT/${ca}-ca.pem" -CAkey "$OUT/${ca}-ca.key" -CAcreateserial \
    -extfile "$OUT/${name}.ext" -out "$OUT/${name}.pem" >/dev/null 2>&1
}

# The real material the agent should accept.
gen_ca real "Daher Demo CA"
gen_server api api real

# Control 1: an unrelated CA the agent must NOT accept.
gen_ca bogus "Not The Real CA"

# Control 2: a certificate signed by the REAL CA but for the WRONG hostname, so the only
# thing that can reject it is hostname verification.
gen_server wronghost not-the-api real

chmod 0644 "$OUT"/*.pem
chmod 0600 "$OUT"/*.key
echo "[certs] generated real CA, api cert, bogus CA, and a wrong-hostname cert in $OUT"
