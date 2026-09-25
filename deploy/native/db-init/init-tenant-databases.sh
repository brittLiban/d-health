#!/bin/bash
# DHealth Native — PostgreSQL tenant bootstrap (DH-DEMO-001, generalized by
# DHEALTH-NATIVE-DEPLOY-001 into a shared deployment primitive used by BOTH the canonical
# production topology and the demo overlay).
#
# Unlike `openelis-config/postgres-init/zz-daher-extra-databases.sh` (which co-tenants Keycloak
# and the adapter on OpenELIS's own clinlims database and is part of the preserved
# maternity/reference-connector track), this script exists purely so the native-only demo does
# not need OpenELIS's database image at all. Same two tenants (Keycloak, the adapter), same
# create-role-then-database idempotent shape, deliberately not shared code with that script: the
# maternity track's own init scripts stay untouched by this ticket, per its own explicit
# instruction, and this one carries no OE_*/clinlims-specific requirement to fight past.
#
# Runs once per fresh volume (Postgres's own initdb.d contract) — the compose file also runs it
# as a one-shot job (`native-demo-db-init`) so an existing volume converges too, matching the
# `zz-daher-extra-databases.sh` / `harden-tenancy.sh` precedent's own reasoning for why both paths
# exist.
set -euo pipefail

: "${KEYCLOAK_DB_NAME:?init-tenant-databases.sh: KEYCLOAK_DB_NAME not set}"
: "${KEYCLOAK_DB_USER:?init-tenant-databases.sh: KEYCLOAK_DB_USER not set}"
: "${KEYCLOAK_DB_PASSWORD:?init-tenant-databases.sh: KEYCLOAK_DB_PASSWORD not set}"
: "${ADAPTER_DB_NAME:?init-tenant-databases.sh: ADAPTER_DB_NAME not set}"
: "${ADAPTER_DB_USER:?init-tenant-databases.sh: ADAPTER_DB_USER not set}"
: "${ADAPTER_DB_PASSWORD:?init-tenant-databases.sh: ADAPTER_DB_PASSWORD not set}"

echo "[dhealth-native] creating the Keycloak and Exchange tenant databases"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	DO \$\$
	BEGIN
	  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${KEYCLOAK_DB_USER}') THEN
	    CREATE ROLE ${KEYCLOAK_DB_USER} LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}';
	  ELSE
	    ALTER ROLE ${KEYCLOAK_DB_USER} LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}';
	  END IF;

	  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${ADAPTER_DB_USER}') THEN
	    CREATE ROLE ${ADAPTER_DB_USER} LOGIN PASSWORD '${ADAPTER_DB_PASSWORD}';
	  ELSE
	    ALTER ROLE ${ADAPTER_DB_USER} LOGIN PASSWORD '${ADAPTER_DB_PASSWORD}';
	  END IF;
	END
	\$\$;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	SELECT 'CREATE DATABASE ${KEYCLOAK_DB_NAME} OWNER ${KEYCLOAK_DB_USER}'
	 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${KEYCLOAK_DB_NAME}')\gexec

	SELECT 'CREATE DATABASE ${ADAPTER_DB_NAME} OWNER ${ADAPTER_DB_USER}'
	 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${ADAPTER_DB_NAME}')\gexec
SQL

# DHEALTH-NATIVE-DEPLOY-001: the demo-environment marker table used to be written here. It moved
# to demo/native/db-init/mark-demo-environment.sh when this script became a shared deployment
# primitive -- a marker asserting "this is a demo database" must never be written by anything the
# canonical production deployment runs, and docker-compose.native.yml no longer has a line that
# could reference it.

echo "[dhealth-native] done: ${KEYCLOAK_DB_NAME} (owner ${KEYCLOAK_DB_USER}), ${ADAPTER_DB_NAME} (owner ${ADAPTER_DB_USER})"
