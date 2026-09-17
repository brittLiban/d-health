#!/bin/bash
set -euo pipefail

# DH-CLINIC-004B: `--import-realm` (docker-compose.yml's `keycloak` service) correctly refuses to
# overwrite an already-existing realm. That is safe persistence behavior for the `dhealth-exchange`
# realm's users, organizations-facing roles, and the confidential `dhealth-exchange-api` client --
# but it also means DH-CLINIC-004A's new public `dhealth-native-ui` browser client (ADR-050) would
# never reach a deployment whose `dhealth-exchange` realm was imported before that client existed
# in the committed JSON. This one-shot reconciler closes that gap the same way
# `reconcile-sso.sh` already closes it for the `daher` realm's OpenMRS/OpenELIS clients: idempotent,
# additive, admin-API convergence -- never a realm delete/recreate, never a DB reset, never a
# users/roles wipe.
#
# Deliberately touches ONLY `dhealth-native-ui`. `dhealth-exchange-api` (confidential, holds the
# backend's own secret) is never read or written by this script -- ADR-050's "same audience,
# unmodified backend validation" decision depends on that client's configuration staying exactly
# as imported.

realm=dhealth-exchange
server=${KEYCLOAK_INTERNAL_URL:-http://keycloak.openelis.org:8080}
config=/tmp/kcadm-exchange.config
kcadm=/opt/keycloak/bin/kcadm.sh

: "${KEYCLOAK_ADMIN_USER:?set KEYCLOAK_ADMIN_USER}"
: "${KEYCLOAK_ADMIN_PASSWORD:?set KEYCLOAK_ADMIN_PASSWORD}"
: "${BASE_DOMAIN:?set BASE_DOMAIN}"

daher_app_url=${DAHER_APP_PUBLIC_URL:-https://app.${BASE_DOMAIN}}

if [[ ! "$daher_app_url" =~ ^[A-Za-z0-9][A-Za-z0-9:./_-]*$ ]]; then
    echo "Unsafe URL value in Exchange SSO configuration" >&2
    exit 1
fi

trap 'rm -f "$config"' EXIT

"$kcadm" config credentials --config "$config" --server "$server" \
    --realm master --user "$KEYCLOAK_ADMIN_USER" \
    --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null

client_id_count() {
    local client_id=$1
    "$kcadm" get clients --config "$config" -r "$realm" \
        -q "clientId=$client_id" -F id --format csv --noquotes | grep -c . || true
}

client_uuid() {
    local client_id=$1
    local -a ids=()
    mapfile -t ids < <("$kcadm" get clients --config "$config" -r "$realm" \
        -q "clientId=$client_id" -F id --format csv --noquotes)
    if [[ ${#ids[@]} -ne 1 || -z "${ids[0]}" ]]; then
        echo "Expected exactly one Keycloak client named $client_id" >&2
        exit 1
    fi
    printf '%s' "${ids[0]}"
}

reconcile_mapper() {
    local client_id=$1
    local mapper_name=$2
    local mapper_file=$3
    local id name mapper_id
    local -a mapper_ids=()

    while IFS=, read -r id name; do
        if [[ "$name" = "$mapper_name" ]]; then
            mapper_ids+=("$id")
        fi
    done < <("$kcadm" get "clients/$client_id/protocol-mappers/models" \
        --config "$config" -r "$realm" -F id,name --format csv --noquotes)

    if [[ ${#mapper_ids[@]} -gt 1 ]]; then
        echo "Duplicate Keycloak mapper: $mapper_name" >&2
        exit 1
    fi

    mapper_id=${mapper_ids[0]:-}
    if [[ -n "$mapper_id" ]]; then
        "$kcadm" update "clients/$client_id/protocol-mappers/models/$mapper_id" \
            --config "$config" -r "$realm" -f "$mapper_file" \
            -s "id=$mapper_id" >/dev/null
    else
        "$kcadm" create "clients/$client_id/protocol-mappers/models" \
            --config "$config" -r "$realm" -f "$mapper_file" >/dev/null
    fi
}

# DH-CLINIC-004B: the only step `reconcile-sso.sh` never needed -- every client it reconciles is
# guaranteed present by the `daher` realm's own import. `dhealth-native-ui` is not guaranteed
# present on an upgraded `dhealth-exchange` realm, so it must be created here if missing, using
# only the fields Keycloak requires at creation time. Every security-relevant field (including
# these same two) is then converged unconditionally below, so a partially-created client from an
# interrupted prior run still ends up correct.
if [[ "$(client_id_count dhealth-native-ui)" -eq 0 ]]; then
    "$kcadm" create clients --config "$config" -r "$realm" \
        -s clientId=dhealth-native-ui \
        -s protocol=openid-connect >/dev/null
fi

native_ui_id=$(client_uuid dhealth-native-ui)

# ADR-050: public Authorization Code + PKCE S256 client, no secret, no implicit/direct-grant/
# service-account flows. Matches keycloak/realm-config/dhealth-exchange-realm.json's
# `dhealth-native-ui` client field-for-field -- this is the single source of truth this script
# converges an existing realm's client toward, never invents independently.
"$kcadm" update "clients/$native_ui_id" --config "$config" -r "$realm" \
    -s protocol=openid-connect \
    -s enabled=true \
    -s publicClient=true \
    -s bearerOnly=false \
    -s standardFlowEnabled=true \
    -s implicitFlowEnabled=false \
    -s directAccessGrantsEnabled=false \
    -s serviceAccountsEnabled=false \
    -s "redirectUris=[\"$daher_app_url/*\"]" \
    -s "webOrigins=[\"$daher_app_url\"]" \
    -s 'attributes."pkce.code.challenge.method"=S256' \
    -s "attributes.\"post.logout.redirect.uris\"=$daher_app_url/*" >/dev/null

reconcile_mapper "$native_ui_id" "exchange-api-audience" \
    /opt/daher/mappers/exchange-api-audience-mapper.json

native_ui_protocol=$("$kcadm" get "clients/$native_ui_id" --config "$config" -r "$realm" \
    -F protocol --format csv --noquotes)
native_ui_public=$("$kcadm" get "clients/$native_ui_id" --config "$config" -r "$realm" \
    -F publicClient --format csv --noquotes)

[[ "$native_ui_protocol" = openid-connect ]]
[[ "$native_ui_public" = true ]]

# DH-CLINIC-004B/ADR-050: proves this run never touched the confidential API client -- if it is
# missing entirely (a realm that somehow imported without it) this script still must not create
# it; that client's secret is operator-provisioned, not something this reconciler is allowed to
# generate or guess.
if [[ "$(client_id_count dhealth-exchange-api)" -eq 0 ]]; then
    echo "dhealth-exchange-api client is absent from this realm -- not created by this script (its secret is operator-provisioned, see docker-compose.yml)" >&2
fi

echo "Keycloak Exchange clients reconciled: dhealth-native-ui public PKCE browser client"
