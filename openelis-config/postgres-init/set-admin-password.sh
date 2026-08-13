#!/bin/sh
# Make the configured OpenELIS administrator password authoritative.
#
# The upstream image ignores DEFAULT_PW and seeds a published bcrypt hash into
# clinlims.login_user. This one-shot runs only after the final PostgreSQL server
# and tenant hardening are ready, updates exactly one existing admin row, and
# verifies the resulting hash before the frontend may start.
set -eu

: "${PGHOST:?set-admin-password.sh: PGHOST not set}"
: "${PGUSER:?set-admin-password.sh: PGUSER not set}"
: "${PGPASSWORD:?set-admin-password.sh: PGPASSWORD not set}"
: "${OE_DB_NAME:?set-admin-password.sh: OE_DB_NAME not set}"
: "${OE_ADMIN_PASSWORD:?set-admin-password.sh: OE_ADMIN_PASSWORD not set}"

if ! printf '%s' "$OE_ADMIN_PASSWORD" | grep -Eq '^[A-Za-z0-9*#$!]{7,}$' \
    || ! printf '%s' "$OE_ADMIN_PASSWORD" | grep -Eq '[*#$!]'; then
    echo 'OE_ADMIN_PASSWORD must be at least 7 allowed characters and include one of * $ # !' >&2
    exit 1
fi

# Keep the secret out of argv. PostgreSQL accepts namespaced settings at session
# startup; SQL can read this one without interpolating or quoting the value.
PGOPTIONS="-c daher.admin_password=$OE_ADMIN_PASSWORD"
export PGOPTIONS

psql -X -v ON_ERROR_STOP=1 --dbname "$OE_DB_NAME" <<-'SQL'
	BEGIN;
	CREATE EXTENSION IF NOT EXISTS pgcrypto;

	SELECT count(*) = 1 AS exactly_one_admin
	  FROM clinlims.login_user
	 WHERE login_name = 'admin' \gset
	\if :exactly_one_admin
	\else
	  \echo 'Expected exactly one OpenELIS admin login row'
	  \quit 1
	\endif

	UPDATE clinlims.login_user
	   SET password = crypt(current_setting('daher.admin_password'), gen_salt('bf', 12))
	 WHERE login_name = 'admin';

	SELECT count(*) = 1 AS configured_hash_matches
	  FROM clinlims.login_user
	 WHERE login_name = 'admin'
	   AND password = crypt(current_setting('daher.admin_password'), password) \gset
	\if :configured_hash_matches
	\else
	  \echo 'Configured OpenELIS admin password did not verify after update'
	  \quit 1
	\endif
	COMMIT;
SQL

echo "[daher] OpenELIS admin password configured and verified"
