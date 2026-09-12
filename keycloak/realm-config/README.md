# Keycloak realm configuration (DH-007)

`daher-realm.json` is imported at first boot via `--import-realm`. It is committed so the identity
configuration is code, not admin-console clicking.

`dhealth-exchange-realm.json` (DH-EX-008) is a second, completely separate realm imported the same
way, alongside this one, into the same Keycloak container. See "The DHealth Exchange realm" below —
it is never merged with `daher` and everything in this file up to that section describes `daher`
only.

**Why this README exists:** JSON has no comments, and Keycloak **rejects unknown fields outright**.
An earlier version of the realm carried `_comment` keys and the import failed with
`Unrecognized field "_comment" (class RealmRepresentation), not marked as ignorable` — while the
container still reported healthy, because a failed import does not stop Keycloak. So the reasoning
lives here instead. **Do not add comment keys back to the JSON.**

## No secrets in that file

Client secrets are `${PLACEHOLDER}` values resolved from the container's environment at import
time. That is what makes the file safe to commit. **If a literal secret ever appears in it, treat
it as leaked and rotate it.**

Verified at DH-007: the imported client secrets matched their corresponding container environment
values. DH-008 removed the no-longer-applicable OpenELIS OIDC secret when that client moved to
SAML. The check compared values in-process and printed only match/mismatch status; no secret value
was written to logs or documentation.

Set in `.env`: `KEYCLOAK_OPENMRS_CLIENT_SECRET`, `KEYCLOAK_ADAPTER_CLIENT_SECRET`,
`KEYCLOAK_ADMIN_PASSWORD`, and `BASE_DOMAIN`. OpenELIS uses SAML and therefore has no committed or
environment-provided client secret.

## Decisions encoded here

**French is the default locale** (`defaultLocale: fr`, English selectable). Djibouti's working
medical and administrative language — a project non-negotiable, not a localisation afterthought.

**The applications use different federation protocols against one Keycloak realm.** OpenMRS uses
OIDC through official OAuth2 Login 1.5.0. OpenELIS uses SAML. This is not aesthetic preference:
exact OpenELIS 3.2.1.11 source shows its OIDC success path calls a form handler that does not accept
an `OidcUser`, clears authentication, and redirects back to `LoginPage`. Its SAML path calls the
working SSO handler and consumes the upstream-native `Role` attribute.

The role mapper fragments live in `../mappers/`, deliberately outside this directory. Keycloak
interprets every JSON file under `/opt/keycloak/data/import` as a complete realm; placing a partial
mapper document here makes startup fail with `Unrecognized field "name" (RealmRepresentation)`.

**The adapter is not a user.** It gets a service account (client credentials) so its writes into
OpenMRS are attributable to a named machine identity in the audit trail rather than borrowing a
clinician's account. That distinction is what makes DH-010's audit log meaningful in an incident
review.

**No users are committed.** Real staff accounts are created by an administrator against the
facility's actual staff list. Usernames in a git repository would be both a security and a
clinical-records problem. The break-glass local admin lives in OpenMRS itself, deliberately outside
SSO, so that Keycloak failing does not lock the clinic out of its own records.

## Session lifetimes — accepted for the pilot

`ssoSessionIdleTimeout` (1h), `ssoSessionMaxLifespan` (10h) and `accessTokenLifespan` (5m) are
approved by Liban for the pilot (ADR-013). They balance continuity during labour with exposure on
a shared ward terminal. Revisit after observing pilot workflow; do not silently tune them as inert
technical constants.

## Fresh import and existing-realm reconciliation

`--import-realm` uses `IGNORE_EXISTING`; it does not apply client changes to an already populated
Keycloak database. The `keycloak-sso-config` one-shot therefore updates the two application clients
in place and reconciles the named protocol mappers without deleting clients, roles, or users. It
runs before either application and is safe to repeat.

## Verifying an import actually worked

A healthy container does **not** mean the realm imported. Check explicitly:

```bash
docker compose logs keycloak | grep -iE "Failed to run import|Imported realm"
docker exec daher-health-keycloak-1 /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 --realm master --user admin --password "$KEYCLOAK_ADMIN_PASSWORD"
docker exec daher-health-keycloak-1 /opt/keycloak/bin/kcadm.sh get realms/daher \
  --fields realm,defaultLocale
```

## DH-007 verification record

Verified against the running stack on 2026-08-13:

- the `daher` realm imported successfully after the invalid JSON comment fields were removed;
- default locale is `fr`, with `fr` and `en` supported;
- realm roles `clinician`, `lab-tech`, and `sys-admin` exist;
- `openmrs` is an OIDC browser-flow client and `openelis` is a SAML browser-flow client;
- `adapter` has service accounts enabled and browser/direct grants disabled;
- the adapter client completed a real client-credentials grant against the local token endpoint;
- the realm contains no committed staff users;
- the then-configured client-secret placeholders resolved to the expected environment values; and
- after restarting only Keycloak, readiness returned healthy and import correctly reported
  `Realm 'daher' already exists. Import skipped` followed by `Import finished successfully`.

DH-008 later verified the reconciliation repeatedly, the exact live client protocols and unique
mapper names, a clean realm-import restart, and a fresh network-isolated import into an ephemeral
H2 database. Application activation remains off until the native-role mapping and OpenMRS-provider
rule receive human approval; the one-login acceptance demonstration has therefore not yet been
claimed.

## The DHealth Exchange realm (DH-EX-008)

`dhealth-exchange-realm.json` defines a **second, independent realm** — `dhealth-exchange` — for
the DHealth Exchange API (DH-EX-002), imported the same way as `daher` and mounted from the same
directory. It is not a client added to `daher`; it is a whole separate realm, with its own roles,
its own single client, and its own secret. The two realms are never merged, never share a client
secret, and a role in one confers nothing in the other.

Set in `.env`: `KEYCLOAK_EXCHANGE_API_CLIENT_SECRET` — a distinct value from
`KEYCLOAK_OPENMRS_CLIENT_SECRET`/`KEYCLOAK_ADAPTER_CLIENT_SECRET` above, substituted into the
`dhealth-exchange-api` client's `${KEYCLOAK_EXCHANGE_API_CLIENT_SECRET}` placeholder at import time,
by the same `--import-realm` substitution mechanism `daher-realm.json` already uses. The same
"no secrets in that file" and "verified in-process, printed only match/mismatch" rules above apply
here unchanged.

**No grant type in the committed realm can currently issue a token to anything.** The
`dhealth-exchange-api` client has `directAccessGrantsEnabled: false` and
`serviceAccountsEnabled: false`, and `"users": []`. This is deliberate for DH-EX-008's scope — the
realm's *shape* (roles, client, audience mapper) is what this ticket proves deploys correctly, not
a working login. A real end-to-end token — correct issuer, correct audience, real signature,
platform-admin role behavior, an authorized org membership — needs either a browser-facing
authorization-code flow (a future portal) or service-account credentials added to this client,
which is a product/security decision, not something DH-EX-008 assumed on its own. That
authenticated qualification is reserved for a follow-up ticket (DH-EX-008R); do not add real users
or grant types to this realm merely to make an end-to-end demonstration possible.

Because `IGNORE_EXISTING` import behavior is per-realm (see "Fresh import and existing-realm
reconciliation" above), `dhealth-exchange` imports correctly whether or not `daher` already exists
in the same Keycloak database — each realm's presence is judged independently. There is currently
no `dhealth-exchange` counterpart to `keycloak-sso-config`: the single client's configuration comes
entirely from the committed realm JSON at first import, with nothing left for a reconciliation job
to apply in place. Add one if this client ever needs updates applied to an already-populated
database without a fresh import — same reasoning as the "Fresh import" section above.
