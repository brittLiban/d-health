#!/bin/sh
# Restrict each PostgreSQL co-tenant database to its named owner.
#
# This is separate from zz-daher-extra-databases.sh because initdb.d runs only
# for an empty data directory. Compose also runs this script as a one-shot job,
# so an existing volume receives the same idempotent grants on upgrade.
set -eu

: "${PGHOST:?harden-tenancy.sh: PGHOST not set}"
: "${PGUSER:?harden-tenancy.sh: PGUSER not set}"
: "${PGPASSWORD:?harden-tenancy.sh: PGPASSWORD not set}"
: "${OE_DB_NAME:?harden-tenancy.sh: OE_DB_NAME not set}"
: "${OE_DB_OWNER:?harden-tenancy.sh: OE_DB_OWNER not set}"
: "${KEYCLOAK_DB_NAME:?harden-tenancy.sh: KEYCLOAK_DB_NAME not set}"
: "${KEYCLOAK_DB_USER:?harden-tenancy.sh: KEYCLOAK_DB_USER not set}"
: "${ADAPTER_DB_NAME:?harden-tenancy.sh: ADAPTER_DB_NAME not set}"
: "${ADAPTER_DB_USER:?harden-tenancy.sh: ADAPTER_DB_USER not set}"

harden_database() {
	database_name=$1
	database_owner=$2

	psql -X -v ON_ERROR_STOP=1 --dbname postgres \
		--set=database_name="$database_name" \
		--set=database_owner="$database_owner" <<-'SQL'
		SELECT format(
		  'REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC',
		  :'database_name'
		) \gexec
		SELECT format(
		  'GRANT ALL PRIVILEGES ON DATABASE %I TO %I',
		  :'database_name',
		  :'database_owner'
		) \gexec
	SQL
}

harden_database "$OE_DB_NAME" "$OE_DB_OWNER"
harden_database "$KEYCLOAK_DB_NAME" "$KEYCLOAK_DB_USER"
harden_database "$ADAPTER_DB_NAME" "$ADAPTER_DB_USER"

psql -X -v ON_ERROR_STOP=1 --dbname postgres \
	--set=oe_db_name="$OE_DB_NAME" \
	--set=keycloak_db_name="$KEYCLOAK_DB_NAME" \
	--set=adapter_db_name="$ADAPTER_DB_NAME" <<-'SQL'
	SELECT NOT EXISTS (
	    SELECT 1
	      FROM pg_database AS database,
	           LATERAL aclexplode(
	             coalesce(database.datacl, acldefault('d', database.datdba))
	           ) AS grant_row
	     WHERE database.datname IN (
	       :'oe_db_name', :'keycloak_db_name', :'adapter_db_name'
	     )
	       AND grant_row.grantee = 0
	) AS grants_hardened \gset
	\if :grants_hardened
	\else
	  \echo 'PUBLIC still has privileges on a tenant database'
	  \quit 1
	\endif
SQL

echo "[daher] PostgreSQL tenant database grants hardened"
