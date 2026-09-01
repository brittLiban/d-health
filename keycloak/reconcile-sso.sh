#!/bin/bash
set -euo pipefail

realm=daher
server=${KEYCLOAK_INTERNAL_URL:-http://keycloak.openelis.org:8080}
config=/tmp/kcadm.config
kcadm=/opt/keycloak/bin/kcadm.sh

: "${KEYCLOAK_ADMIN_USER:?set KEYCLOAK_ADMIN_USER}"
: "${KEYCLOAK_ADMIN_PASSWORD:?set KEYCLOAK_ADMIN_PASSWORD}"
: "${KEYCLOAK_OPENMRS_CLIENT_SECRET:?set KEYCLOAK_OPENMRS_CLIENT_SECRET}"
: "${BASE_DOMAIN:?set BASE_DOMAIN}"

openmrs_public_url=${OPENMRS_PUBLIC_URL:-https://emr.${BASE_DOMAIN}}
openelis_public_url=${OPENELIS_PUBLIC_URL:-https://lab.${BASE_DOMAIN}}
# UI-004 (ADR-027): the Daher UI's own origin, added to the openmrs client's
# existing redirect/web-origin lists — never replacing emr.'s entries, never
# a second OpenMRS client. UI-005 measured OpenELIS's SAML ACS as
# per-request, app-origin-derived, conditional on the openelis client
# accepting that origin — UI-011 (DH-008F) applies that same addition here,
# additively, alongside lab.'s existing entries, never replacing them.
daher_app_url=${DAHER_APP_PUBLIC_URL:-https://app.${BASE_DOMAIN}}

for value in "$BASE_DOMAIN" "$openmrs_public_url" "$openelis_public_url" "$daher_app_url"; do
    if [[ ! "$value" =~ ^[A-Za-z0-9][A-Za-z0-9:./_-]*$ ]]; then
        echo "Unsafe URL/domain value in SSO configuration" >&2
        exit 1
    fi
done

trap 'rm -f "$config"' EXIT

"$kcadm" config credentials --config "$config" --server "$server" \
    --realm master --user "$KEYCLOAK_ADMIN_USER" \
    --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null

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

# ADR-017E2/DH-008G: deletes a superseded mapper by name if it is still present
# (existing realms reconciled before this ticket), no-op on a realm that never
# had it (fresh imports, or realms already reconciled since). Mirrors
# reconcile_mapper's own list-and-filter idiom so the same duplicate-detection
# guard applies on the way out as on the way in.
remove_mapper_if_present() {
    local client_id=$1
    local mapper_name=$2
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
        "$kcadm" delete "clients/$client_id/protocol-mappers/models/$mapper_id" \
            --config "$config" -r "$realm" >/dev/null
    fi
}

# ADR-017B: the openmrs client's "client roles" mapper (above) relays every
# client role the authenticated user holds on this client into the JWT roles
# claim; the unmodified oauth2login module then assigns any OpenMRS role
# whose name matches a claim value exactly (config/oauth2.properties.template,
# openmrs.mapping.user.roles=roles) — no separate translation table exists.
# This means the Keycloak-side artifact for an OpenMRS role must be a client
# role on `openmrs` named identically to the OpenMRS role, or the sync
# silently grants nothing (proven, not assumed: ADR-017B evidence log).
reconcile_client_role() {
    local client_id=$1
    local role_name=$2
    local role_description=$3
    local id name role_id
    local -a role_ids=()

    # kcadm does not URL-encode path segments, so a name-in-path lookup
    # ("roles/$role_name") breaks outright on a space (e.g. "Daher
    # Reception") instead of 404ing — list-and-filter avoids that, and
    # roles-by-id (a Keycloak-native id-keyed alias) avoids it on update too.
    while IFS=, read -r id name; do
        if [[ "$name" = "$role_name" ]]; then
            role_ids+=("$id")
        fi
    done < <("$kcadm" get "clients/$client_id/roles" \
        --config "$config" -r "$realm" -F id,name --format csv --noquotes)

    if [[ ${#role_ids[@]} -gt 1 ]]; then
        echo "Duplicate Keycloak client role: $role_name" >&2
        exit 1
    fi

    role_id=${role_ids[0]:-}
    if [[ -n "$role_id" ]]; then
        # roles-by-id is a realm-root endpoint, not nested under the client
        # (unlike every other resource this script touches) — role ids are
        # globally unique within a realm.
        "$kcadm" update "roles-by-id/$role_id" \
            --config "$config" -r "$realm" \
            -s "description=$role_description" >/dev/null
    else
        "$kcadm" create "clients/$client_id/roles" \
            --config "$config" -r "$realm" \
            -s "name=$role_name" -s "description=$role_description" >/dev/null
    fi
}

openmrs_id=$(client_uuid openmrs)
"$kcadm" update "clients/$openmrs_id" --config "$config" -r "$realm" \
    -s protocol=openid-connect \
    -s enabled=true \
    -s publicClient=false \
    -s bearerOnly=false \
    -s standardFlowEnabled=true \
    -s directAccessGrantsEnabled=false \
    -s serviceAccountsEnabled=false \
    -s "secret=$KEYCLOAK_OPENMRS_CLIENT_SECRET" \
    -s "rootUrl=$openmrs_public_url" \
    -s "baseUrl=$openmrs_public_url" \
    -s "redirectUris=[\"$openmrs_public_url/*\",\"$daher_app_url/*\",\"http://127.0.0.1:8080/*\",\"http://openmrs-gateway/*\"]" \
    -s "webOrigins=[\"$openmrs_public_url\",\"$daher_app_url\",\"http://127.0.0.1:8080\",\"http://openmrs-gateway\"]" \
    -s 'attributes."post.logout.redirect.uris"=+' >/dev/null

reconcile_mapper "$openmrs_id" "client roles" \
    /opt/daher/mappers/openmrs-client-roles-mapper.json

# ADR-017E2/DH-008G: superseded. The patched oauth2login module (see
# openmrs-config/patches/oauth2login-bridge/) no longer reads a "provider"
# claim at all -- Provider activation is derived from the same "roles" claim
# above (exact "Provider" client-role membership), removing the fail-open
# absent-claim default and the two-competing-mappers risk a separate boolean
# claim would otherwise introduce. Deletes the mapper on any realm that still
# has it from before this ticket; no-op everywhere else.
remove_mapper_if_present "$openmrs_id" "provider disabled until approved"

# ADR-017B: registration-only reception role. Additive — clinician/lab-tech/
# sys-admin's own client roles (created and assigned manually per ADR-017,
# same as this one) are untouched. Does not assign the role to any staff
# account; that stays a separate, manual, roster-driven step (ADR-017).
reconcile_client_role "$openmrs_id" "Daher Reception" \
    "ADR-017B: patient registration only. Maps 1:1 to the OpenMRS role of the same name."

# ADR-017D/ADR-034/ADR-035/ADR-036: reception cannot admit or place orders. Admission and the
# three explicitly approved order-path privileges are clinician-only, via a
# second, separate, self-contained Daher-owned role — never granted to
# Daher Reception, no inheritance either direction. Additive: the reception
# reconciliation above, and every other client/mapper call, is untouched.
reconcile_client_role "$openmrs_id" "Daher Clinician Admission" \
    "ADR-017D/ADR-034/ADR-035/ADR-036: admission plus approved Add Orders, Get Concepts, and Get Care Settings; clinician-only. Maps 1:1 to OpenMRS."

openelis_id=$(client_uuid openelis)
"$kcadm" update "clients/$openelis_id" --config "$config" -r "$realm" \
    -s protocol=saml \
    -s enabled=true \
    -s publicClient=false \
    -s bearerOnly=false \
    -s standardFlowEnabled=true \
    -s directAccessGrantsEnabled=false \
    -s serviceAccountsEnabled=false \
    -s "rootUrl=$openelis_public_url" \
    -s "baseUrl=$openelis_public_url" \
    -s "redirectUris=[\"$openelis_public_url/*\",\"https://oe.openelis.org:8443/OpenELIS-Global/*\",\"$daher_app_url/*\"]" \
    -s "webOrigins=[\"$openelis_public_url\",\"$daher_app_url\"]" \
    -s 'attributes."saml.assertion.signature"=false' \
    -s 'attributes."saml.force.post.binding"=true' \
    -s 'attributes."saml.encrypt"=false' \
    -s 'attributes."saml.server.signature"=true' \
    -s 'attributes."saml.server.signature.keyinfo.ext"=false' \
    -s 'attributes."saml.signature.algorithm"=RSA_SHA256' \
    -s 'attributes."saml_force_name_id_format"=false' \
    -s 'attributes."saml.client.signature"=false' \
    -s 'attributes."saml.authnstatement"=true' \
    -s 'attributes."saml_name_id_format"=username' >/dev/null

reconcile_mapper "$openelis_id" "role list" \
    /opt/daher/mappers/openelis-role-list-mapper.json

openmrs_protocol=$("$kcadm" get "clients/$openmrs_id" --config "$config" -r "$realm" \
    -F protocol --format csv --noquotes)
openelis_protocol=$("$kcadm" get "clients/$openelis_id" --config "$config" -r "$realm" \
    -F protocol --format csv --noquotes)

[[ "$openmrs_protocol" = openid-connect ]]
[[ "$openelis_protocol" = saml ]]

echo "Keycloak SSO clients reconciled: OpenMRS OIDC, OpenELIS SAML"
