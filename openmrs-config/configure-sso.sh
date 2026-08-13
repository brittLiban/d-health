#!/bin/sh
set -eu

enabled=${SSO_ENABLED:-false}
properties=/openmrs/data/oauth2.properties
temporary="${properties}.tmp.$$"
module_source=/openmrs/distribution/openmrs_modules/oauth2login-1.5.0.omod
module_directory=/openmrs/data/modules
module_target=$module_directory/oauth2login-1.5.0.omod
module_temporary="${module_target}.tmp.$$"

case "$enabled" in
    true|false) ;;
    *)
        echo "SSO_ENABLED must be exactly true or false" >&2
        exit 1
        ;;
esac

umask 077
trap 'rm -f "$temporary" "$module_temporary"' EXIT

# The upstream startup script seeds /openmrs/data/modules only when needed;
# an existing persistent volume does not automatically receive a newly added
# distribution OMOD. Install this exact, image-built release atomically on
# every boot so fresh and upgraded deployments behave identically.
mkdir -p "$module_directory"
for installed in "$module_directory"/oauth2login-*.omod; do
    if [ -e "$installed" ] && [ "$installed" != "$module_target" ]; then
        echo "Unexpected OAuth2 Login module version: $installed" >&2
        exit 1
    fi
done
if [ ! -f "$module_target" ] || ! cmp -s "$module_source" "$module_target"; then
    cp "$module_source" "$module_temporary"
    chmod 644 "$module_temporary"
    mv "$module_temporary" "$module_target"
fi

if [ "$enabled" = false ]; then
    # Do not persist the client secret when the module is deliberately
    # disabled. Its activator reads this flag and unloads itself at startup,
    # restoring the local OpenMRS login for break-glass use.
    printf '%s\n' 'oauth2.enabled=false' > "$temporary"
else
    : "${KEYCLOAK_OPENMRS_CLIENT_SECRET:?set KEYCLOAK_OPENMRS_CLIENT_SECRET}"
    : "${KEYCLOAK_PUBLIC_URL:?set KEYCLOAK_PUBLIC_URL}"
    : "${KEYCLOAK_INTERNAL_URL:?set KEYCLOAK_INTERNAL_URL}"

    for value in "$KEYCLOAK_OPENMRS_CLIENT_SECRET" "$KEYCLOAK_PUBLIC_URL" "$KEYCLOAK_INTERNAL_URL"; do
        without_line_breaks=$(printf '%s' "$value" | tr -d '\r\n')
        if [ "$without_line_breaks" != "$value" ]; then
            echo "SSO configuration values must be single-line" >&2
            exit 1
        fi
    done

    cat > "$temporary" <<EOF
oauth2.enabled=true
clientId=openmrs
clientSecret=$KEYCLOAK_OPENMRS_CLIENT_SECRET
userAuthorizationUri=$KEYCLOAK_PUBLIC_URL/realms/daher/protocol/openid-connect/auth
accessTokenUri=$KEYCLOAK_INTERNAL_URL/realms/daher/protocol/openid-connect/token
userInfoUri=$KEYCLOAK_INTERNAL_URL/realms/daher/protocol/openid-connect/userinfo
logoutUri=$KEYCLOAK_PUBLIC_URL/realms/daher/protocol/openid-connect/logout?id_token_hint=[token]
keysUrl=$KEYCLOAK_INTERNAL_URL/realms/daher/protocol/openid-connect/certs
scope=openid,profile,email
openmrs.mapping.user.username=preferred_username
openmrs.mapping.person.givenName=given_name
openmrs.mapping.person.familyName=family_name
openmrs.mapping.user.email=email
openmrs.mapping.user.systemId=sub
openmrs.mapping.user.roles=roles
openmrs.mapping.user.provider=provider
EOF
fi

chmod 600 "$temporary"
mv "$temporary" "$properties"
trap - EXIT

exec /openmrs/startup.sh
