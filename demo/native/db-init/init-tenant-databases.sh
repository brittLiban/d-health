#!/bin/bash
# DH-DEMO-001 — native demo's own PostgreSQL bootstrap.
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

echo "[dhealth-native-demo] creating Keycloak and adapter demo databases"

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

# DH-DEMO-001 §"RESET SAFETY IS P0": one of three independent facts `demo_seed.py` checks before
# any destructive statement -- a marker table that only ever exists in a database this exact
# script created. Deliberately raw DDL, never an Alembic migration: a demo-only marker must never
# appear in a real deployment's schema, and this script never runs against one. Created directly
# inside the adapter's own database (not the `postgres` bootstrap database) so the seed script,
# which already connects there, can check it with the same connection it uses for everything else.
# GRANT SELECT explicitly: created by $POSTGRES_USER (the bootstrap superuser), but read by the
# adapter connecting as $ADAPTER_DB_USER -- caught by this ticket's own real-PostgreSQL
# qualification (2026-09-18): a table's creator does not implicitly grant other roles any
# privilege on it, so without this the adapter's own safety check would fail with a permission
# error instead of confirming the marker, refusing to run at all even in a genuine demo.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$ADAPTER_DB_NAME" <<-SQL
	CREATE TABLE IF NOT EXISTS dhealth_demo_environment (marker text PRIMARY KEY);
	INSERT INTO dhealth_demo_environment (marker) VALUES ('dhealth-native-demo')
	  ON CONFLICT DO NOTHING;
	GRANT SELECT ON dhealth_demo_environment TO ${ADAPTER_DB_USER};
SQL

echo "[dhealth-native-demo] done: ${KEYCLOAK_DB_NAME} (owner ${KEYCLOAK_DB_USER}), ${ADAPTER_DB_NAME} (owner ${ADAPTER_DB_USER})"
