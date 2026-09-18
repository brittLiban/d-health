#!/bin/sh
# DH-DEMO-001 — revokes PUBLIC's default CONNECT/ALL privilege on the two demo-tenant databases,
# same property `openelis-config/postgres-init/harden-tenancy.sh` proves for the maternity track's
# own co-tenant Postgres (ADR-001/DH-020), reused as an independent script rather than a shared
# one so a change to either track's hardening never risks the other. Runs as a one-shot job
# (service name `openelis-db-hardening`, matching the real compose file's own naming so `keycloak`/
# `adapter`'s existing `depends_on` blocks -- reused verbatim via `extends:` -- resolve against
# this demo-only service without any override) so it also converges an existing demo volume.
set -eu

: "${PGHOST:?harden-tenancy.sh: PGHOST not set}"
: "${PGUSER:?harden-tenancy.sh: PGUSER not set}"
: "${PGPASSWORD:?harden-tenancy.sh: PGPASSWORD not set}"
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

harden_database "$KEYCLOAK_DB_NAME" "$KEYCLOAK_DB_USER"
harden_database "$ADAPTER_DB_NAME" "$ADAPTER_DB_USER"

echo "[dhealth-native-demo] PostgreSQL tenant database grants hardened"
