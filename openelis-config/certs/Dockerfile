# DH-021 — replaces `itechuw/certgen`, which ships a pre-built keystore whose
# private key is publicly downloadable and whose certificate had already expired.
# This image generates the material on the deployment instead.
FROM eclipse-temurin:17-jre-alpine@sha256:02320dd4ce20e243dfb915c686089cf9315c763084fafbb12d5c9993aee18b57

# keytool ships with the JRE; openssl is used for the expiry check that decides
# whether an existing certificate may be left alone.
RUN command -v keytool >/dev/null && command -v openssl >/dev/null

COPY generate-certs.sh /usr/local/bin/generate-certs
RUN chmod 0755 /usr/local/bin/generate-certs

ENTRYPOINT ["/usr/local/bin/generate-certs"]
