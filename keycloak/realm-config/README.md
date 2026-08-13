# Keycloak realm configuration (DH-007)

`daher-realm.json` is imported at first boot via `--import-realm`. It is committed so the identity
configuration is code, not admin-console clicking.

**Why this README exists:** JSON has no comments, and Keycloak **rejects unknown fields outright**.
An earlier version of the realm carried `_comment` keys and the import failed with
`Unrecognized field "_comment" (class RealmRepresentation), not marked as ignorable` — while the
container still reported healthy, because a failed import does not stop Keycloak. So the reasoning
lives here instead. **Do not add comment keys back to the JSON.**

## No secrets in that file

Client secrets are `${PLACEHOLDER}` values resolved from the container's environment at import
time. That is what makes the file safe to commit. **If a literal secret ever appears in it, treat
it as leaked and rotate it.**

Set in `.env`: `KEYCLOAK_OPENMRS_CLIENT_SECRET`, `KEYCLOAK_OPENELIS_CLIENT_SECRET`,
`KEYCLOAK_ADAPTER_CLIENT_SECRET`, plus `KEYCLOAK_ADMIN_PASSWORD` and `BASE_DOMAIN`.

## Decisions encoded here

**French is the default locale** (`defaultLocale: fr`, English selectable). Djibouti's working
medical and administrative language — a project non-negotiable, not a localisation afterthought.

**Both applications use OIDC.** OpenELIS also supports SAML, but OIDC was confirmed supported at
DH-007 by inspecting the deployed webapp (`spring-security-oauth2-client-6.2.8.jar`;
`SecurityConfig` reads `org.itech.login.oauth.clientID` / `.clientSecret` / `.config`). One protocol
for both apps is deliberate — two would double the surface that has to keep working across upgrades.

⚠️ Note for DH-008: OpenELIS's shipped `common.properties` contains only the
`org.itech.login.saml.*` keys. The OIDC keys must be **added**. Reading that file alone will suggest
SAML is the only option; it is not.

**The adapter is not a user.** It gets a service account (client credentials) so its writes into
OpenMRS are attributable to a named machine identity in the audit trail rather than borrowing a
clinician's account. That distinction is what makes DH-010's audit log meaningful in an incident
review.

**No users are committed.** Real staff accounts are created by an administrator against the
facility's actual staff list. Usernames in a git repository would be both a security and a
clinical-records problem. The break-glass local admin lives in OpenMRS itself, deliberately outside
SSO, so that Keycloak failing does not lock the clinic out of its own records.

## ⚠️ Not decided — session lifetimes

`ssoSessionIdleTimeout` (1h), `ssoSessionMaxLifespan` (10h) and `accessTokenLifespan` (5m) are
**starting values, not decisions**. They are a clinical-workflow question as much as a security one:
a midwife mid-labour must not be logged out, but a shared ward terminal must not stay open all
night. Flagged for Liban in `PROJECT_STATE.md` rather than presented as settled.

## Verifying an import actually worked

A healthy container does **not** mean the realm imported. Check explicitly:

```bash
docker compose logs keycloak | grep -iE "Failed to run import|Imported realm"
docker exec daher-health-keycloak-1 /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 --realm master --user admin --password "$KEYCLOAK_ADMIN_PASSWORD"
docker exec daher-health-keycloak-1 /opt/keycloak/bin/kcadm.sh get realms/daher \
  --fields realm,defaultLocale
```
