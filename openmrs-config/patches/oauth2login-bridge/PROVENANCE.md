# oauth2login patches — provenance

This directory records the patch source and proof for every Daher-owned change to the pinned
`oauth2login` module, applied by `openmrs-config/Dockerfile`'s `oauth2login-build` stage.

- **The bridge patch** (`OAuth2LoginBridgeFilter.java`, `config.xml.patch`) — UI-007/DH-008B, wired
  into the Dockerfile by UI-008/DH-008C. Makes `/oauth2login` reachable at all.
- **The provider-role-derivation patch** (`OAuth2LoginController.java.patch`) — ADR-017E2/DH-008G,
  wired into the Dockerfile the same ticket. Makes Provider activation derive from the OpenMRS
  client-role claim instead of a separate, fail-open provider claim. See its own section below.
- **The first-login authentication patch** (`OAuth2UserInfoAuthenticationScheme.java.patch`) —
  Bug #25, wired into the Dockerfile with focused CI coverage. Preserves the authoritative `User`
  returned by OpenMRS's daemon creation contract instead of rereading through the caller's older
  transaction snapshot. See its own section below.

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

---

## `OAuth2LoginController.java.patch` — provenance (ADR-017E2 / DH-008G)

Also applied on top of the same pinned source/commit/toolchain above (both patches apply to the
same clone, in sequence — bridge filter first, then this one).

## Patch contents

- `OAuth2LoginController.java.patch` — unified diff against
  `omod/src/main/java/org/openmrs/module/oauth2login/web/controller/OAuth2LoginController.java`.
  Adds a `DAHER_PROVIDER_ROLE_NAME` constant and a package-private `isProviderRoleGranted(UserInfo)`
  method; replaces the single line in `login()` that read
  `userInfo.getString(UserInfo.PROP_PROVIDER, "true")` with a call to it. No other method is
  touched — `activateProviderAccount`/`deactivateProviderAccount` and their existing per-Person
  duplicate-prevention logic are unmodified; only *whether* they are called changed.
- `test-verification/ProviderRoleDerivationTest.java` — new file, focused unit tests for the
  decision method (10 cases). **Not applied by the Dockerfile** — the build always runs with
  `-Dmaven.test.skip=true`, so this is verification-only, kept for anyone re-proving the patch.
- `test-verification/ProviderAttributeUserInfoTest.java.patch` — unified diff updating an existing
  upstream integration test whose old fixture asserted the pre-patch (fail-open-adjacent) behavior;
  also verification-only, not applied by the Dockerfile.

## Checksum

`.omod` produced by the real `openmrs-config/Dockerfile` `oauth2login-build` stage, both patches
applied (`daher-health/openmrs-backend:adr017e2-test` image, 2026-08-25):

`ae148634ec06817efcc99fdf9c0a1a4e05565ef76292c8eb7899b3b4aec8966b`

As with the bridge patch above, this is not expected to be byte-reproducible across rebuilds
(Maven jar-entry timestamps) — reproducibility is verified at the source/patch/toolchain level:
the exact pinned clone, the exact patch content (applied successfully via both `git apply` and
plain `patch -p1`, independently), the exact base image digest.

## How this was proven

See `DECISIONS.md`'s `ADR-017E2` entry and `PROJECT_STATE.md`'s `ADR-017E2 / DH-008G` entry for
the full account: unit coverage for all 9 required decision-table cases plus one extra, a live
fresh-synthetic-clinician OIDC round trip with every identifier read back and cross-checked,
repeated-login idempotence, reception/zero-role/broad-non-Provider-role negative controls, an
admission re-proof, and a provider-ambiguity control run on an isolated throwaway database clone
(never the canonical disposable database). All testing was against a disposable Keycloak +
OpenMRS + MariaDB stack with synthetic data only; no real facility SSO was enabled and no
production file was modified to run these tests.

---

## `OAuth2UserInfoAuthenticationScheme.java.patch` — provenance (Bug #25)

Also applied on top of the same pinned source/commit/toolchain above, after the bridge and
provider-role patches.

## Patch contents

- `OAuth2UserInfoAuthenticationScheme.java.patch` — unified diff against
  `api/src/main/java/org/openmrs/module/oauth2login/authscheme/OAuth2UserInfoAuthenticationScheme.java`.
  Changes the private `createUser` wrapper to return the `User` produced by
  `ContextDAO.createUser`, and assigns that return value in `authenticate`. It removes the single
  same-transaction post-create username reread. No transaction annotation, isolation level,
  lookup key, role mapping, Provider reconciliation, retry, delay, cache, or session behavior is
  otherwise changed.
- `test-verification/FirstLoginAuthenticationTest.java` — three focused JUnit 4 cases that execute
  the patched authentication method against a `ContextDAO` whose username lookup deliberately
  remains blind after creation while `createUser` returns the authoritative committed `User`.
  They prove first-login authentication uses that return value, prove there is exactly one
  pre-create lookup and no old-snapshot reread, and prove creation failure still propagates.
  The test avoids upstream Mockito 1/CGLIB, which cannot initialize on the pinned JDK 17 toolchain.

## Root-cause evidence

The pinned OpenMRS 2.8.7 `Daemon.createUser` implementation creates the Person/User/roles in a
separate daemon thread and Hibernate session, waits on `Future.get()`, and returns the created
`User`. Instrumentation in the disposable stack measured the outer
`OAuth2UserInfoAuthenticationScheme.authenticate` transaction as active at MariaDB
`REPEATABLE-READ`: its initial lookup returned null; the awaited daemon call returned a populated
User; an immediate reread in the same outer transaction still returned null. At that point an
independent SQL connection already saw the committed User/Person/roles, and the next HTTP request's
fresh transaction saw the same User. The original method therefore constructed
`BasicAuthenticated` with a null User, causing the first callback to report unauthenticated and
skip controller-level Provider reconciliation.

Before the fix, ten completely fresh valid-`@example.com` clinicians failed their genuine first
OIDC callback this way: **10 failures / 10 attempts**. Each had one User, one Person, the exact two
roles, and zero Providers; the following request authenticated and reconciled exactly one Provider.
This is a measured 100% reproduction series, not a claim that every possible deployment schedule
must reproduce it.

## Verification

- Patched focused Maven tests: `FirstLoginAuthenticationTest` **3/3** and the unchanged
  `ProviderRoleDerivationTest` **10/10**, using the exact pinned clone and build image.
- Negative control on unpatched upstream: the first-login suite ran 3 tests and failed the two
  properties the patch changes (returned authenticated User was null; username lookup count was
  two rather than one). The creation-failure propagation case remained green as expected.
- Static guard rejects missing Dockerfile wiring, failure to consume the daemon result, and
  retry/sleep/global-cache-flush workarounds. A throwaway mutation removing the assignment failed
  that guard for the intended reason.
- The real `openmrs-config/Dockerfile` `off` target built successfully with all three patches.
- Disposable live proof used two fresh clinicians. Each authenticated on the first callback with
  exactly one Person/User/Provider, the exact ADR-017F roles and nine privileges, and stable UUIDs
  across two further logins. A fresh Reception user authenticated on its first callback with no
  Provider; registration succeeded and admission was denied. A deliberately invalid email produced
  zero User, Person, Provider, and role-assignment rows.

The live proof artifact's OMOD SHA-256 was
`5f2a7e064e490668f8311097e3e9cc94e9ff6a179621a31e14c091cb140b12f7`. As above, Maven archive
timestamps mean that checksum is evidence for the tested artifact, not a byte-reproducibility
promise.

All authentication writes and experiments were confined to the disposable
`openmrs-adr017f`/`mariadb-ui009`/`kc-ui009` environment with synthetic data. The real facility
backend was not restarted or written, its SSO remained disabled, and no real staff or patients
were used.
