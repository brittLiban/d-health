#!/bin/bash
set -euo pipefail

# DH-DEMO-001: creates/reconciles the fixed-username DHealth Native Demo login accounts in the
# `dhealth-exchange` realm -- idempotent, additive, admin-API convergence, the identical idiom
# `reconcile-exchange.sh`/`reconcile-sso.sh` already use. Never used against the real
# docker-compose.yml stack; the demo compose file is the only caller.
#
# These users carry NO Keycloak realm role. `dhealth-platform-admin` is the only realm role this
# realm defines and none of the three demo personas need it -- organization-scoped authority
# (CLINICIAN/LAB_TECH/LAB_RESULT_RELEASER) is an Exchange-database concept
# (OrganizationMembership + OrganizationRoleAssignment), granted by `demo_seed.py` directly, never
# by anything stored in Keycloak. This script's only job is that three real, real-password-login
# accounts exist; `demo_seed.py` discovers each one's own Keycloak-assigned `id` (the JWT `sub` a
# real login will carry) by asking Keycloak directly rather than assuming a fixed value survives
# a fresh volume (Keycloak's Admin REST API does not guarantee honoring a caller-supplied `id` on
# create across every version).

realm=dhealth-exchange
server=${KEYCLOAK_INTERNAL_URL:-http://keycloak.openelis.org:8080}
config=/tmp/kcadm-demo-users.config
kcadm=/opt/keycloak/bin/kcadm.sh

: "${KEYCLOAK_ADMIN_USER:?set KEYCLOAK_ADMIN_USER}"
: "${KEYCLOAK_ADMIN_PASSWORD:?set KEYCLOAK_ADMIN_PASSWORD}"
: "${DEMO_CLINICIAN_USERNAME:?set DEMO_CLINICIAN_USERNAME}"
: "${DEMO_CLINICIAN_PASSWORD:?set DEMO_CLINICIAN_PASSWORD}"
: "${DEMO_LAB_TECH_USERNAME:?set DEMO_LAB_TECH_USERNAME}"
: "${DEMO_LAB_TECH_PASSWORD:?set DEMO_LAB_TECH_PASSWORD}"
: "${DEMO_RELEASER_USERNAME:?set DEMO_RELEASER_USERNAME}"
: "${DEMO_RELEASER_PASSWORD:?set DEMO_RELEASER_PASSWORD}"

trap 'rm -f "$config"' EXIT

# Same "healthy but admin API still bootstrapping" retry as reconcile-exchange.sh's own login
# loop -- observed directly in that ticket's own CI run, not a theoretical concern.
login_attempt=0
until "$kcadm" config credentials --config "$config" --server "$server" \
    --realm master --user "$KEYCLOAK_ADMIN_USER" \
    --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null 2>/tmp/reconcile-demo-users-login.err; do
    login_attempt=$((login_attempt + 1))
    if [[ "$login_attempt" -ge 10 ]]; then
        echo "Could not authenticate to Keycloak's admin API after $login_attempt attempts:" >&2
        cat /tmp/reconcile-demo-users-login.err >&2
        exit 1
    fi
    sleep 3
done

# DHEALTH-NATIVE-MVP-1.0 (2026-09-25): first/last name and an e-mail address are not decoration
# here. The `dhealth-exchange` realm has Keycloak's VERIFY_PROFILE required action enabled, so a
# user without them is stopped after authenticating by "Mettre à jour les informations du compte"
# and never reaches the application -- which is what every browser attempt at this demo did, until
# a real browser was finally pointed at it. This is the narrow carve-out the staff-identity
# decision of 2026-09-24 anticipated: "email is optional unless authentication technically requires
# it". Here it does.
#
# `@demo.invalid` by construction: `.invalid` is reserved by RFC 2606 and can never be delivered
# to, so no synthetic demo account can ever resemble or collide with a real staff address.
reconcile_user() {
    local username=$1
    local password=$2
    local first_name=$3
    local last_name=$4
    local -a ids=()

    mapfile -t ids < <("$kcadm" get users --config "$config" -r "$realm" \
        -q "username=$username" -F id --format csv --noquotes)

    if [[ ${#ids[@]} -eq 0 || -z "${ids[0]}" ]]; then
        "$kcadm" create users --config "$config" -r "$realm" \
            -s "username=$username" \
            -s enabled=true \
            -s emailVerified=true \
            -s "email=$username@demo.invalid" \
            -s "firstName=$first_name" \
            -s "lastName=$last_name" >/dev/null
        mapfile -t ids < <("$kcadm" get users --config "$config" -r "$realm" \
            -q "username=$username" -F id --format csv --noquotes)
    fi

    if [[ ${#ids[@]} -ne 1 || -z "${ids[0]}" ]]; then
        echo "Expected exactly one Keycloak user named $username, found ${#ids[@]}" >&2
        exit 1
    fi

    local user_id=${ids[0]}
    # Converges enabled/emailVerified unconditionally, then resets the password -- same
    # non-destructive "existing rows never deleted, fields converged toward the committed
    # contract" discipline as reconcile-exchange.sh's own client update below its create step.
    "$kcadm" update "users/$user_id" --config "$config" -r "$realm" \
        -s enabled=true \
        -s emailVerified=true \
        -s "email=$username@demo.invalid" \
        -s "firstName=$first_name" \
        -s "lastName=$last_name" >/dev/null
    "$kcadm" set-password --config "$config" -r "$realm" \
        --userid "$user_id" --new-password "$password" >/dev/null

    # Clears any brute-force lockout from a mistyped demo password in a prior session -- a
    # "reset" that restores every other baseline fact but leaves a demo login still locked out
    # would not actually be a full reset. Ignored if brute-force protection never tripped (delete
    # of a non-existent record is not an error worth failing this script over).
    "$kcadm" delete "attack-detection/brute-force/users/$user_id" --config "$config" -r "$realm" \
        >/dev/null 2>&1 || true

    echo "$username -> $user_id"
}

reconcile_user "$DEMO_CLINICIAN_USERNAME" "$DEMO_CLINICIAN_PASSWORD" "Démo" "Clinicien"
reconcile_user "$DEMO_LAB_TECH_USERNAME" "$DEMO_LAB_TECH_PASSWORD" "Démo" "Technicien"
reconcile_user "$DEMO_RELEASER_USERNAME" "$DEMO_RELEASER_PASSWORD" "Démo" "Libérateur"

echo "DHealth Native Demo users reconciled: $DEMO_CLINICIAN_USERNAME, $DEMO_LAB_TECH_USERNAME, $DEMO_RELEASER_USERNAME"
