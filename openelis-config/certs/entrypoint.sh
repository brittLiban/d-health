#!/bin/sh
# DH-021 — one job, two outputs that must agree with each other: the keystore
# material and the properties file that carries its password. Splitting them
# across two services would let a deployment end up with a regenerated keystore
# and a stale password, which is precisely the failure this ticket hit.
set -eu

/usr/local/bin/generate-certs
/usr/local/bin/render-properties
