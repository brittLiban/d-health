# oauth2login bridge patch — provenance (UI-007 / DH-008B)

Not wired into `openmrs-config/Dockerfile`. This directory records the patch source and its
proof so it can be applied later, once the blockers in `PROJECT_STATE.md`'s UI-007 entry are
resolved by a human decision. Applying it today would be inert anyway: even with the dispatch
fixed, the `authentication` module collision (see PROJECT_STATE.md) stops real login from
completing.

## Upstream

- Repo: https://github.com/openmrs/openmrs-module-oauth2login
- Tag: `1.5.0`
- Commit: `2770233627343a53719ec981e997d94b1d90b7ad`
- Same clone/build commands as `openmrs-config/Dockerfile`'s existing `oauth2login-build` stage.

## Toolchain

- Build image: `maven:3.9.11-eclipse-temurin-17@sha256:e4a7ace3dc0d645ed97f8d9ad0b0d3f0b14fa8d150138f27f116d7105a639b82`
  (identical to the Dockerfile's existing `oauth2login-build` stage)
- Build command: `mvn -B -f pom.xml -Dmaven.test.skip=true package`
- Resulting artifact: `omod/target/oauth2login-1.5.0.omod`

## Patch contents

- `OAuth2LoginBridgeFilter.java` — new file, added at
  `omod/src/main/java/org/openmrs/module/oauth2login/web/filter/OAuth2LoginBridgeFilter.java`.
- `config.xml.patch` — unified diff against `omod/src/main/resources/config.xml`, registering
  the new filter against the exact literal path `/oauth2login` only.

Total patch size: one new class (~25 lines excluding license header/Javadoc), one config.xml
addition (14 lines). No existing file's behavior is changed; no other class is touched. The
filter does not construct authorization URLs, parse tokens, or implement any OAuth2 logic of
its own — it looks up the module's own unmodified `oauth2ClientContextFilter` Spring bean by
name (`Context.getRegisteredComponent("oauth2ClientContextFilter", Filter.class)`) and forwards
through it to `/ws/oauth2login`, which the core `openmrs` servlet's existing `/ws/*` mapping
already routes to `OAuth2LoginController`.

## Checksums

Three builds of the *identical* source (no edits between them) produced three different
SHA-256 checksums:

| When | SHA-256 |
|---|---|
| First build | `00bcef57408d94867f97f7ff74a8fe929e62b700fd04128c6d123078493bcacf` |
| Second rebuild (after an unrelated diagnostic-build detour) | `213e162a288f260ba95222ce7e81194e92087f7cf326bf5dcfd5b59bba89ca52` |
| Final rebuild (canonical, used for the last confirmation run) | `65181782d1e08cbaefebffd9932835f248bdfaf4145259eec590101f7c7658cd` |

This is expected, not drift: Maven-produced JARs embed a per-entry last-modified timestamp,
so rebuilding unchanged source from a fresh `mvn package` invocation is not byte-reproducible
with this toolchain. Functional equivalence was verified directly, not assumed: the full OIDC
round trip (see PROJECT_STATE.md's UI-007 entry) was re-run end-to-end against the **final**
`65181782…` artifact and passed identically to the earlier runs against `00bcef57…`.

**`65181782d1e08cbaefebffd9932835f248bdfaf4145259eec590101f7c7658cd` is the canonical checksum
for this patch as recorded here.**

Baseline (unmodified upstream, same commit, same toolchain) checksum, for the control
comparison in §7 of the ticket: `541e0bbcd6149b2ba0b69cbfe26b4893133f94ad94886ec36f913a3c46559335`.

## How this was proven

See `PROJECT_STATE.md`'s UI-007 / DH-008B entry for the full account: the baseline-vs-patched
control, the dispatch-forward measurement, the complete synthetic OIDC round trip (including
provider identity, restart, and Keycloak-outage fail-closed behavior), and the negative
controls that were and were not able to be run. All testing was against a disposable
Keycloak + OpenMRS + MariaDB stack with synthetic data only; no real facility SSO was enabled
and no production file was modified to run these tests.
