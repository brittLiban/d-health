#!/bin/bash
# =============================================================================
# Daher Health — Keycloak and adapter databases, co-tenants of OpenELIS's
# PostgreSQL instance (ADR-001).
#
# WHY THIS IS A SHELL SCRIPT AND NOT PLAIN .sql
# Passwords come from the environment. A plain .sql file in initdb.d cannot read
# environment variables, and hard-coding credentials in a committed file is not
# an option, so this is a .sh that psql's them in.
#
# WHY THE FILENAME STARTS WITH zz-
# Postgres's entrypoint runs everything in /docker-entrypoint-initdb.d in sorted
# order. OpenELIS ships OpenELIS-Global.sql, init.sh (which creates the clinlims
# and admin roles), and siteInfo.sql. `zz-` sorts last, so this runs only after
# OpenELIS has finished provisioning its own schema. Do not rename it to sort
# earlier.
#
# ⚠️ THIS RUNS EXACTLY ONCE PER VOLUME — Postgres's entrypoint only executes
# initdb.d on an EMPTY data directory. Adding a database here later, against an
# existing volume, does nothing. That is the one-shot the DH-005 work had to plan
# around; on an existing deployment, create the database by hand and record it in
# the runbook.
#
# It is written to be idempotent anyway. Not because it is expected to run twice,
# but because "the script was safe to re-run" is a much better position to be in
# during a restore drill (DH-015) than discovering it was not.
# =============================================================================
set -euo pipefail

: "${KEYCLOAK_DB_NAME:?zz-daher-extra-databases.sh: KEYCLOAK_DB_NAME not set}"
: "${KEYCLOAK_DB_USER:?zz-daher-extra-databases.sh: KEYCLOAK_DB_USER not set}"
: "${KEYCLOAK_DB_PASSWORD:?zz-daher-extra-databases.sh: KEYCLOAK_DB_PASSWORD not set}"
: "${ADAPTER_DB_NAME:?zz-daher-extra-databases.sh: ADAPTER_DB_NAME not set}"
: "${ADAPTER_DB_USER:?zz-daher-extra-databases.sh: ADAPTER_DB_USER not set}"
: "${ADAPTER_DB_PASSWORD:?zz-daher-extra-databases.sh: ADAPTER_DB_PASSWORD not set}"

echo "[daher] creating Keycloak and adapter databases as co-tenants (ADR-001)"

# Roles first: a database's OWNER must exist before the database is created.
# CREATE ROLE has no IF NOT EXISTS, hence the DO block.
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

# CREATE DATABASE cannot run inside a transaction block or a DO block, so it is
# guarded by checking first and using gexec to run only what is missing.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	SELECT 'CREATE DATABASE ${KEYCLOAK_DB_NAME} OWNER ${KEYCLOAK_DB_USER}'
	 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${KEYCLOAK_DB_NAME}')\gexec

	SELECT 'CREATE DATABASE ${ADAPTER_DB_NAME} OWNER ${ADAPTER_DB_USER}'
	 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${ADAPTER_DB_NAME}')\gexec
SQL

# Each tenant owns its own database. Co-tenancy is a deployment convenience
# (ADR-001), not a trust boundary being relaxed.
#
# WHAT THIS ACTUALLY ACHIEVES — measured at DH-005, not assumed:
#   ✅ keycloak cannot connect to `adapter`      ("permission denied for database")
#   ✅ adapter cannot read OpenELIS clinical data ("permission denied for schema clinlims")
#   ⚠️ adapter CAN still connect to `clinlims` and create objects in its `public`
#      schema — PostgreSQL grants CONNECT and (in 14) CREATE-on-public to PUBLIC
#      by default, and the REVOKEs below only cover the two databases we create.
#
# The gap is deliberately left open here rather than closed blind: OpenELIS keeps
# 3 objects in that `public` schema, so revoking on `clinlims` risks breaking the
# laboratory system in a way that would surface later, and OpenELIS's database is
# not ours to harden as a side effect of creating two other databases.
# Tracked as DH-020 for review. No clinical data is exposed by this gap.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	REVOKE ALL ON DATABASE ${KEYCLOAK_DB_NAME} FROM PUBLIC;
	REVOKE ALL ON DATABASE ${ADAPTER_DB_NAME}  FROM PUBLIC;
	GRANT ALL PRIVILEGES ON DATABASE ${KEYCLOAK_DB_NAME} TO ${KEYCLOAK_DB_USER};
	GRANT ALL PRIVILEGES ON DATABASE ${ADAPTER_DB_NAME}  TO ${ADAPTER_DB_USER};
SQL

echo "[daher] done: ${KEYCLOAK_DB_NAME} (owner ${KEYCLOAK_DB_USER}), ${ADAPTER_DB_NAME} (owner ${ADAPTER_DB_USER})"
