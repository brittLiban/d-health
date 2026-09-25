#!/bin/bash
# DHealth Native Demo — the marker that says "this database is a demo" (DH-DEMO-001's
# "RESET SAFETY IS P0", split out of init-tenant-databases.sh by DHEALTH-NATIVE-DEPLOY-001).
#
# **Why this is its own file now.** The tenant-creation script it used to live in became a shared
# deployment primitive (`deploy/native/db-init/init-tenant-databases.sh`) when the canonical
# production topology started using it. A marker asserting "this is a demo database" must never be
# written by anything a production deployment runs -- so it is mounted only by
# `docker-compose.native-demo.yml`, and `docker-compose.native.yml` has no line that could
# reference it.
#
# This is one of the three independent facts `demo_seed.py` checks before it will clear a single
# row; the other two are the database's own name and `ADAPTER_DEMO_ENVIRONMENT=1`. All three must
# hold. A production Native deployment satisfies none of them.
#
# Deliberately raw DDL, never an Alembic migration: a demo-only marker must never appear in a real
# deployment's schema, and the migration chain runs in both.
#
# Created inside the adapter's own database (not the `postgres` bootstrap database) so the seed
# script can check it on the connection it already has. `GRANT SELECT` is explicit and
# load-bearing: the table is created by the bootstrap superuser but read by the adapter's own
# role, and a table's creator does not implicitly grant other roles any privilege on it. Without
# the grant the adapter's safety check fails with a permission error and refuses to run even in a
# genuine demo -- found by real-PostgreSQL qualification, 2026-09-18.
#
# Runs after init-tenant-databases.sh (Postgres runs /docker-entrypoint-initdb.d/* in lexical
# order, hence the `zz-` prefix on the mount path), and only on a fresh volume.
set -euo pipefail

: "${ADAPTER_DB_NAME:?mark-demo-environment.sh: ADAPTER_DB_NAME not set}"
: "${ADAPTER_DB_USER:?mark-demo-environment.sh: ADAPTER_DB_USER not set}"

echo "[dhealth-native-demo] marking ${ADAPTER_DB_NAME} as a demo environment"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$ADAPTER_DB_NAME" <<-SQL
	CREATE TABLE IF NOT EXISTS dhealth_demo_environment (marker text PRIMARY KEY);
	INSERT INTO dhealth_demo_environment (marker) VALUES ('dhealth-native-demo')
	  ON CONFLICT DO NOTHING;
	GRANT SELECT ON dhealth_demo_environment TO ${ADAPTER_DB_USER};
SQL

echo "[dhealth-native-demo] demo marker present in ${ADAPTER_DB_NAME}"
