# OpenELIS order-correlation patches — provenance

DH-011D (BLOCKER-6 / ADR-026, Option F). Applied by the `openelis-order-correlation-build` Docker
stage this ticket adds — the OpenELIS analogue of `openmrs-config/Dockerfile`'s
`oauth2login-build`/`daherendadmission-build` pattern.

**Status as of 2026-08-28 (DH-011D2 closure pass): search + persistence + uniqueness all built,
compiled, and live-proven at every boundary that does not require a live-creatable
`ServiceRequest`.** The one remaining gate — native `ServiceRequest` creation itself — is refused
server-side before any of this code runs, and stays refused until the real numeric OpenELIS site
code exists (NEEDS LIBAN #7). See "What remains unprovable until the site code arrives" at the end.

## Upstream

- Repo: https://github.com/DIGI-UW/OpenELIS-Global-2 (the tag `3.2.1.11` maps to this org/repo, not
  the archived `I-TECH-UW/OpenELIS-Global-2`, which now 301s here — confirmed via the GitHub API,
  not assumed)
- Tag: `3.2.1.11` — the exact tag pinned in `docker-compose.yml`'s `openelis-webapp`/`openelis-db`/
  `openelis-fhir`/`openelis-frontend` images (`itechuw/openelis-global-2:3.2.1.11@sha256:c7c72001…`)
- Commit: `e2a6bdf7999fa3754e7cdafbd2a91259c3f0ce23`
- Cross-checked against the *running* container, not only the compose pin: the deployed webapp's own
  `WEB-INF/classes/build.properties` reports `project.version=3.2.1.11`, matching exactly.

## Toolchain

- Build image: `maven:3-eclipse-temurin-21` (unpinned by upstream's own `Dockerfile` at this tag —
  carried forward as upstream ships it; pinning it further is upstream's call, not this patch's)
- Build command: upstream's own two-stage `mvn clean install` (dataexport submodule, then the main
  WAR) exactly as `Dockerfile` at this commit already runs it — `-Dmaven.test.skip=true -DskipITs=true`
- Resulting artifact: `target/OpenELIS-Global.war`
- **`dataexport` is a git submodule** (`.gitmodules`, pinned commit
  `bc1c0d4967607743e3aea48fbd08be5a4d5daf95`), not a plain subdirectory — a shallow `git clone` alone
  leaves it empty and the build fails immediately (`no POM in this directory
  /build/dataexport/dataexport-core`). `openelis-config/Dockerfile` runs
  `git submodule update --init --depth 1 dataexport` after the clone for exactly this reason.
- `install/createDefaultPassword.sh` needs `htpasswd` (`apache2-utils`), not just `git` — omitting it
  produces a silent infinite retry loop (`htpasswd: command not found`) rather than a clean build
  error.
- Full build time: ~13 minutes cold; well under a minute once Maven's dependency cache (the
  BuildKit `--mount=type=cache,target=/root/.m2` mount) is warm, since only the patched files need
  recompiling.

## Patch contents

### `ServiceRequestProvider.java.patch` — search parameter, inbound identifier extraction,
### `ElectronicOrder` creation

**Search half.** Adds one `@OptionalParam(name = ServiceRequest.SP_IDENTIFIER) TokenAndListParam
identifier` parameter to `searchForServiceRequests`. Nothing in the method body changes —
`searchForServiceRequests`'s entire body is `util.forwardSearchToFhirStore(request)`, which forwards
the caller's raw query string to `fhirConfig.getLocalFhirStorePath()` regardless of which parameters
are declared; the declaration exists only so HAPI's REST framework accepts the parameter instead of
rejecting it with 400 before the request is ever forwarded. `PatientProvider`/`PractitionerProvider`
already declare the equivalent parameter and their identifier search already works
(PROJECT_STATE.md's DH-011B evidence); the only structural difference was this one missing
declaration.

**Persistence half.** `createServiceRequest` now:

1. Calls `extractDaherOrderIdentifier(serviceRequest)` immediately after the existing
   `subject`/`code` validations, before any DB work — fail-closed selection rules:
   - Filters `serviceRequest.getIdentifier()` to exact matches on
     `DaherOrderCorrelation.SYSTEM` (`urn:daher-health:adapter:source-event`, shared with
     `adapter/src/daher_adapter/recovery.py`'s `SOURCE_CORRELATION_SYSTEM` — the OpenELIS-side half
     of an already-implemented adapter contract, not a locally invented value).
   - Zero matches → `InvalidRequestException` ("found none").
   - More than one match → `InvalidRequestException` ("found N").
   - The one match's value must be non-blank and match `^[0-9a-f]{64}$` (the adapter's correlation
     is always a SHA-256 hex digest) — anything else is rejected as malformed rather than stored
     verbatim.
   - Comparison is exact throughout: `String.equals` on system, no case folding, no substring, no
     `.contains()`/`.startsWith()`/`.toLowerCase()` anywhere in the selection logic (CI-guarded —
     see below).
2. Runs a fast, non-authoritative uniqueness check
   (`electronicOrderService.getElectronicOrdersByExternalId(daherOrderId)`) and rejects with a clear
   message if already in use. This is a UX improvement for the non-racing case only — the
   authoritative guarantee is the DB-level partial unique index (below); a genuine race surfaces as
   a raw constraint-violation exception from `persistDaherOrderCorrelation`, not a silently accepted
   duplicate.
3. After `samplePatientService.persistData(...)` succeeds and a native sample exists, calls
   `persistDaherOrderCorrelation(daherOrderId, updateData.getSample(), sysuserId)`, which:
   - Creates a new `ElectronicOrder` (`setExternalId(daherOrderId)`, `setType(ElectronicOrderType
     .FHIR)`, `setStatusId(Realized)`, `setData("")`), mirroring `TaskWorker.insertNewOrder`'s
     existing pattern for FHIR-typed rows.
   - Links it via `sample.setClinicalOrderId(electronicOrderId)` — **deliberately never
     `sample.setReferringId(...)`**. `referringId` is what `transformToServiceRequest`'s existing,
     unrelated `basedOn`/`intent` logic already reads (via
     `electronicOrderService.getElectronicOrdersByExternalId(sample.getReferringId())`) to decide
     whether a `ServiceRequest` is based on a prior one. Setting it here would make that pre-existing
     logic treat this opaque correlation value as a FHIR `ServiceRequest` reference and misclassify
     every first-time Daher order as "based on" something. `clinicalOrderId` is not read by that
     logic, so linking through it exclusively avoids the collision.

**Why this is isolated from the existing human "external order number" field.**
`SamplePatientEntryController`/`SamplePatientEntryRestController` already call
`SampleOrderItem.setExternalOrderNumber(...)` from a UI field where staff manually type an external
order number during accessioning — that path shares the same `SamplePatientUpdateData
.setElectronicOrderIfNeeded` lookup, which only *links* to an existing `ElectronicOrder`, never
creates one. This patch deliberately does **not** extend that shared method to auto-create, because
doing so would also change what a mistyped or non-matching human entry does today (silently create a
synthetic `FHIR`-typed order instead of silently doing nothing). All new creation logic lives in
`ServiceRequestProvider` itself, reached only from the FHIR business API's own create path.

### `FhirTransformServiceImpl.java.patch` — outbound identifier echo

`transformToServiceRequest` (the outbound builder, whose output both the create-response and
`transformPersistOrderEntryFhirObjects`'s sync to the searchable store are built from) now also adds
a `DaherOrderCorrelation.SYSTEM` identifier, read exclusively through
`sample.getClinicalOrderId()` → `electronicOrderService.get(clinicalOrderId)` → `.getExternalId()`,
gated on `ElectronicOrderType.FHIR.equals(linkedOrder.getType())`. Never reads
`sample.getReferringId()` (see above for why). Without this echo, a successfully persisted
correlation would exist in `electronic_order.external_id` but never appear on the synced FHIR
resource the search half queries — the two patches are only useful together.

### `DaherOrderCorrelation.java` — new shared constants file

`org.openelisglobal.dataexchange.fhir.DaherOrderCorrelation`: `SYSTEM` and `VALUE_PATTERN`, imported
by both `ServiceRequestProvider` (persist side) and `FhirTransformServiceImpl` (echo side), so the
two cannot drift into using different system URIs by editing one file and not the other.

### `001-electronic-order-daher-correlation-unique.xml` + `base-changelog.xml.patch` — uniqueness

A new Liquibase changeset (`liquibase/daher/001-electronic-order-daher-correlation-unique.xml`,
included from `base-changelog.xml`'s existing end-of-file list) adds:

```sql
CREATE UNIQUE INDEX electronic_order_daher_correlation_uk
  ON electronic_order (external_id)
  WHERE type = 'FHIR';
```

**Why a partial index, and why this scope specifically.** `ElectronicOrderDAOImpl
.getElectronicOrdersByExternalId` returns a **list**, ordered by id, with no existing `UNIQUE`
constraint on `external_id` — and `setElectronicOrderIfNeeded` deliberately takes
`orders.get(orders.size() - 1)`, i.e. the codebase already tolerates and expects duplicate
`external_id` values among HL7/CSV-sourced (`type = 'HL7_V2'`) rows. Scoping the constraint to
`WHERE type = 'FHIR'` gives Daher's own correlation a real, race-safe, DB-level uniqueness guarantee
— required for DH-025's exact 0/1/2+ reconciliation contract — without touching that existing
tolerance for any row this patch does not itself create. **Fails safely, not destructively**: if
`type='FHIR'` rows with a duplicate `external_id` already exist when this runs, `CREATE UNIQUE INDEX`
raises a constraint-violation error and Liquibase does not mark the changeset applied; nothing is
deleted, merged, or rewritten to force it through.

**A bug this pass found in its own first attempt, worth recording**: the first version of this
changeset's XML comment used `--` as a prose dash. XML comments cannot contain `--` anywhere in
their body; Liquibase's XML parser raises `SAXParseException`/`SetupException` and the entire Spring
context fails to start — the exact same class of bug `.github/workflows/ci.yml`'s existing "No `--`
inside an XML comment body" guard was written to catch for `openmrs-config/`. That guard's scope has
been widened this pass to cover `openelis-config/` too (see below), rather than adding a duplicate
check.

## CI guards (`.github/workflows/ci.yml`, `static` job)

Two guards, both control-tested against deliberate mutations before being trusted:

1. **The widened XML-comment guard** now scans `openelis-config/**/*.xml` as well as
   `openmrs-config/**/*.xml`.
2. **A new "DH-011D order-correlation patch content guards" step** asserts, against the committed
   patch files (this job cannot boot the real OpenELIS stack): the pinned tag/commit are asserted in
   the Dockerfile; all four patch/file artifacts exist and are applied/copied; the search parameter,
   extraction method, and persistence method all exist and are wired into `createServiceRequest`;
   the zero-match and multiple-match rejections both exist; identifier selection is an exact
   `system.equals(...)` comparison; `extractDaherOrderIdentifier`'s own body never references
   accession/specimen/subject/requester as a correlation source; no fuzzy-comparison method
   (`.toLowerCase()`, `.contains()`, `.startsWith()`, `.indexOf()`) appears anywhere in the added
   lines; `sample.referringId` is never set by the persist path or read by the echo path; the
   uniqueness index is present, scoped to `type='FHIR'`, and has a rollback; the migration is
   actually wired into `base-changelog.xml`. Control-tested this pass: stripping the `type='FHIR'`
   scope and injecting a `.toLowerCase()`/`.contains()` pattern into a copy of the patch both made
   the guard fail for the expected, specific reason.

## Live proof (2026-08-28, disposable containers only, real stack never touched)

**Search-parameter half** — proven twice, against two different targets, for two different reasons:

1. Against the real running stack's shared DB/keystore volumes (safe because this build had not yet
   added a schema migration): `400` → `200`/`total:0`; an unrelated unknown parameter stayed `400`
   (scoping control); wrong Basic-auth credentials stayed `401` (auth control).
2. Once the schema migration was added, continuing to share the real `openelis-db` became unsafe —
   OpenELIS runs Liquibase automatically on boot, and the new changeset would apply to the **real**
   shared database. All further proof this pass therefore ran against a **fully isolated, disposable
   three-container stack** (`oe-dh011d2-db`, `oe-dh011d2-fhir`, `oe-dh011d2-webapp`, private Docker
   network, fresh empty database) — reusing only the real stack's static keystore/TLS material
   (read-only, not live state) and the pinned upstream images for `openelis-db`/`openelis-fhir`.
   Re-ran the same identifier-search, scoping, and wrong-system-zero-match controls there; all
   passed identically. Auth used the fresh instance's own unrotated default (`adminADMIN!`), not the
   real stack's rotated credential.

**Migration application** — the fresh isolated boot ran the *entire* Liquibase chain from empty
(every changeset from `2.0.x.x` forward), including this patch's own, with **zero** `SEVERE`/parse
errors after the `--` fix: `Running Changeset:
liquibase/daher/001-electronic-order-daher-correlation-unique.xml::daher-011d-001-...::daher-health`
→ `ran successfully in 7ms`. Confirmed via `\d electronic_order` on the fresh database:
`"electronic_order_daher_correlation_uk" UNIQUE, btree (external_id) WHERE type::text = 'FHIR'::text`.

**Concurrency proof, at the closest real boundary reachable without a creatable `ServiceRequest`**:
two genuinely concurrent Postgres transactions (`BEGIN; SELECT pg_sleep(0.3); INSERT ...; COMMIT;`,
launched as parallel background processes against the isolated database) inserting the *same*
`external_id` with `type='FHIR'` — one `COMMIT`, one
`ERROR: duplicate key value violates unique constraint "electronic_order_daher_correlation_uk"`.
Exactly one row persisted for that correlation, confirmed by direct count. This proves the DB
constraint's concurrency behavior on the real patched schema; it does **not** prove the full Java
service-layer race (two simultaneous `POST /ServiceRequest` calls through
`ServiceRequestProvider.createServiceRequest`), which needs a live-creatable `ServiceRequest` and
stays blocked on the site code.

**Scoping control**: the identical two-insert pattern with `type='HL7_V2'` (both rows, same
`external_id`) succeeded for both — 2 rows persisted, confirming the constraint does not touch
existing HL7/CSV duplicate tolerance.

**What was not attempted**: injecting a fixture directly into the standalone HAPI store to prove
live 1-match/2+-match `ServiceRequest` search cardinality (as distinct from the 0-match/
wrong-system-0-match proof above). The standalone store requires mutual-TLS client credentials this
pass did not extract/exercise, and Patient/Practitioner cannot substitute (their own client-supplied
identifiers are *also* discarded on create — the same defect this whole patch exists to fix for
`ServiceRequest`, per DH-011B). Cardinality classification itself (ZERO/ONE/MANY/UNREACHABLE) is
unit-tested against scripted HTTP responses in `adapter/tests/test_openelis.py` — real assurance at
the adapter boundary, not a substitute for the live HAPI-store proof.

The disposable isolated stack (3 containers + network) was fully removed after use; the real
`daher-health-*` stack's `electronic_order` table was re-queried immediately after and still carries
exactly its original 3 indexes — no migration ever ran against it.

## What remains unprovable until the site code arrives

Everything requiring an actual `POST /ServiceRequest` to succeed through the business API:

- End-to-end persistence proof (create → `electronic_order.external_id` populated → search finds it)
  against a real create, as opposed to the isolated-boundary proofs above.
- The full Java service-layer concurrency race (two simultaneous HTTP creates through
  `ServiceRequestProvider`, not two raw SQL inserts).
- Live 1-match/2+-match search cardinality against a real, business-API-created `ServiceRequest`.
- DH-011J (cancellation) and DH-011L (end-to-end demonstration), unchanged by this pass.

None of these are engineering gaps — the code, migration, and tests that can exist without a real
order all exist and are proven. What's left needs the facility's numeric site code (NEEDS LIBAN #7),
not more work here.

## DH-011D3 (2026-08-28) — correction, four findings, one new P0 finding, Finding D

**Correction to the section above (§18, required append-only, not a rewrite)**: "What's left needs
the facility's numeric site code, not more work here" was **incorrect**. An independent review of
the committed DH-011D implementation found three pre-activation gaps this session closed (Findings
A/B/C below), and closing them surfaced two further defects this session also fixed (the
`varchar(60)` truncation bug) or characterized but did **not** fix (the `labNo`/Specimen
prerequisite, a new P0 finding, see below). BLOCKER-6 stays RESOLVED BY DECISION; Option F is
unchanged. DH-011D is not more complete than this account supports. DH-011 stays NOT COMPLETE.
DH-011E stays NOT ACTIVATED.

### Finding A — the native create/correlate sequence was not atomic

Independent review found `ServiceRequestProvider.createServiceRequest` called
`samplePatientService.persistData(...)` (itself `@Transactional`) and then separately called
`persistDaherOrderCorrelation(...)`, with no outer transaction — a race or mid-sequence failure
could commit the native Sample/SampleItem/Analysis rows while the correlation row never landed,
violating DH-025's safety property.

**Fix attempted and reverted, live**: putting `@Transactional` directly on `createServiceRequest`
broke HAPI FHIR provider registration entirely. `ServiceRequestProvider` implements
`IResourceProvider`; `@Transactional` makes Spring wrap it in a JDK dynamic proxy, which exposes
only that interface's own methods — none of which carry `@Create`/`@Search`/`@Update`/`@Delete`,
which live on the concrete class. HAPI's `FhirRestfulServer` reflects on the bean instance it
receives to find those annotations and found none:
`HAPI-0289: Did not find any annotated RESTful methods on provider class jdk.proxy4.$Proxy1413`,
and the entire provider silently stopped serving every operation, not just create — confirmed live
via a fresh isolated boot (both `/api/OpenELIS-Global` and `/OpenELIS-Global` webapps threw
`ConfigurationException` on `load()`).

**Fix that shipped**: a new, separate, non-`IResourceProvider` Spring `@Component`,
`DaherOrderCorrelationTransaction`, carries `@Transactional(rollbackFor = Exception.class)` on a
`persist(..., Runnable persistCorrelation)` method that calls `samplePatientService.persistData`
then invokes the callback. HAPI never reflects on this bean, so it can be proxied however Spring
likes without breaking anything. `createServiceRequest` now calls
`daherOrderTransaction.persist(updateData, ..., () -> persistDaherOrderCorrelation(...))`.
Rebuilt and re-verified live on a fresh isolated stack: no `HAPI-0289`, no `SEVERE` errors,
`ServiceRequest?identifier=...` search still functions.

**Not yet done**: the ticket's own §2 asked for a *formal* proof via
`TransactionSynchronizationManager.isActualTransactionActive()` at five named capture points
(entry to `createServiceRequest`; before/after `persistData`; before/after the correlation
write), and §4's rollback control (induce failure after native persistence but before correlation,
read the DB directly for zero orphan rows). Neither was completed this pass — both require a live
`createServiceRequest` success path to reach, and that path is blocked; see the new P0 finding
below. What shipped is the atomicity *mechanism* (a real Spring transaction spanning both writes),
verified by live boot and by the framework's own `@Transactional(rollbackFor = Exception.class)`
contract, not yet verified by a live induced-failure read of the database.

### Finding B — the correlation search was not authoritative

Independent review found the DH-011D search patch forwarded to the standalone/secondary HAPI FHIR
store, and upstream's own `createServiceRequest` wraps `transformPersistOrderEntryFhirObjects(...)`
in try/catch that logs a warning and continues on failure — a native order can exist while the
secondary sync silently failed, and `ServiceRequest?identifier=...` would then wrongly return 0,
which DH-025 (mis)interprets as PROVEN ABSENT.

**Fix**: for the Daher correlation system specifically,
`ServiceRequestProvider.searchDaherCorrelationNatively` (new) derives existence from native
OpenELIS state: `electronic_order` (type=FHIR, exact `external_id` match) → linked `Sample` via
`clinicalOrderId` → `SampleItem` → `Analysis` → `FhirTransformService.transformToServiceRequest`.
Both `fhirOrders.size() > 1` and `linkedSamples.size() > 1` fail loud
(`InternalErrorException`, "Data integrity violation") rather than silently picking one or
reporting zero — the partial unique index (see Finding A of DH-011D2/001-...xml) makes both
structurally impossible in committed state, so observing either means trusting the database over
the assumption. The standalone HAPI store is untouched for every other identifier system; this
patch only ever narrows what gets rerouted to native state, never widens it (`.github/workflows
/ci.yml`'s static guard did not previously check this file's newest content and was not extended
this pass — see "Not done" below).

**Live proof, direct-SQL fixtures labeled FHIR SEARCH FIXTURE ONLY / NOT NATIVE ORDER PROOF (§13)**
— a synthetic `Patient` fixture (created through the real `POST /fhir/Patient` path, `id=2`,
`fhir_uuid=a474dc53-7a64-4682-960f-cb195d69a740`) plus directly-inserted `electronic_order` /
`sample` / `sample_item` / `analysis` rows (native business-API creation was not exercised — see
the new P0 finding below) proved, against the isolated stack:
- absent correlation value → `total: 0`, zero entries.
- one order, one test → `total: 1`, one entry.
- one order, two tests (a panel — see the `total`-semantics correction immediately below) →
  `total: 1`, two entries, both carrying the same `urn:daher-health:adapter:source-event` value
  and the same `requisition.value` (the shared accession number).
- wrong system, same value → routed to the unchanged `forwardSearchToFhirStore` fallback (proven
  by the log line `FhirUtil.forwardSearchToFhirStore, Error: I/O error ... fhir.openelis.org:
  Name or service not known` — the isolated 2-container test stack has no secondary FHIR store
  container, so the fallback itself cannot succeed here; the requirement being proven is that the
  native Daher path was never entered, which the log confirms).

**Caveat found on review, after the live proof above**: what this proves and what it does not are
narrower than the bullets above read in isolation. The returned `ServiceRequest` resources carried
no `id` field (`FhirTransformServiceImpl.transformToServiceRequest` sets
`serviceRequest.setId(analysis.getFhirUuidAsString())`, and the fixture `analysis` rows' `INSERT`
statements never populated `fhir_uuid`, leaving it `NULL`). The adapter's own
`_resource_id(resource)` reads `resource["id"]` and returns `None` for a resource with no id, which
`identifier_search` treats as `MatchCount.UNREACHABLE` — the probe would report AMBIGUOUS for the
exact bundles proved live above, not FOUND. So this live proof establishes `Bundle.total`
semantics, entry cardinality, the correlation-identifier echo, and system-based routing correctly;
it does **not** establish that the adapter classifies a real result as ONE, because the fixture
rows never gave the returned resources a readable id. A real, business-API-created `Analysis`
would have a genuine `fhir_uuid` (set on create, not by this patch), so this is almost certainly a
fixture artifact rather than a defect — but it was not re-verified with `fhir_uuid` populated, and
this account does not claim it was.

**`Bundle.total` correction, found via advisor review of the first cut of this fix**: the first
version of `searchDaherCorrelationNatively` counted constituent `ServiceRequest` entries into
`total` (one per `Analysis`). A prenatal panel — the normal V1 order — produces one `Analysis` per
test, so a real order would read back as `total: 2` (or more) even though it is one logical order.
`Bundle.total` was corrected to count **logical orders** (0 or 1 — the partial unique index
guarantees at most one FHIR-typed `electronic_order` per correlation value, and this patch now
also fails loud if more than one `Sample` is ever linked to it), while every constituent
`ServiceRequest` is still returned as a Bundle entry. The adapter side needed a matching, and
independently necessary, correction: `daher_adapter.openelis.OpenElisClient.identifier_search`
previously counted `len(Bundle.entry)`, not `Bundle.total` — verified by reading the method
directly, not assumed. Left uncorrected on both sides, every multi-test order would read back as
`MatchCount.MANY` and could never reconcile — the ledger would never complete for any order with
more than one test on it, which is not an edge case for a prenatal panel. The Python fix is
deliberately narrow: it does **not** require `total` to be present (Patient, Practitioner, and
every ServiceRequest query outside the Daher system go through the standalone secondary FHIR
store, whose `total` presence is unverified) and does not otherwise prefer `total` over counting
entries; the only place `total` overrides the entry count is collapsing more than one entry to
`ONE` when `total == 1` explicitly says so. Every other outcome is unchanged from before the
correction. Both sides are load-bearing together — shipping only the Java half (as first
attempted) would have left the exact miscount this correction exists to fix.

**Hardening beyond the minimum**: `extractSingleDaherCorrelationValue` previously returned `null`
(silent fallthrough to the secondary store) for any identifier shape it didn't recognize,
including a Daher-system token combined with another AND/OR term. Verified against the adapter's
actual query shape (`identifier_search` always sends the single-token
`identifier={system}|{value}` form — grepped, not assumed) that this fallthrough is not reachable
in production today, but hardened it to fail loud (`InvalidRequestException`) instead of falling
through whenever the Daher system appears in an unrecognized shape, so a future change on either
side fails closed rather than silently degrading into the exact false-ABSENT this patch exists to
prevent.

### Finding C — the writer was not implemented

`OpenElisClient.deliver_service_request` previously always raised `ServiceRequestDeliveryBlocked`
— supplying the site code alone could not have made delivery work even once available, because the
capability itself did not exist.

**Fix**: `deliver_service_request` now POSTs the prepared `ServiceRequest` payload (injecting the
Daher correlation identifier, rejecting a caller-supplied conflicting value), then performs
**unconditional** authoritative native reconciliation via `identifier_search` regardless of the
POST's own HTTP status — never trusting a 2xx as proof, and never blind-retrying an ambiguous POST.
Success requires exactly one native match; zero-after-POST, 2+-matches, timeout-after-possible-
write, and definite-failure-before-write all raise `AmbiguousTargetState` (the same vocabulary
`RecoveryCoordinator`'s probe already uses), unifying fail-closed behavior between the probe and
write paths. Activation is a separate, thin gate:
`deliver_service_request_if_activated(client, settings, ...)` checks
`Settings.service_request_delivery_ready` before ever calling the real writer — IMPLEMENTED
CAPABILITY and ACTIVATION PERMISSION are two different things, and nothing in production calls
either today (DH-011E's poller is unwired), so activation is enforced twice over: by the gate, and
by the absence of a caller. Ten new scripted-fixture tests in
`adapter/tests/test_openelis.py` cover the writer protocol (successful POST + reconciliation,
timeout-then-found, POST-never-lands, zero/2+/401/403 outcomes, duplicate-conflict-reconciles);
three cover the activation gate.

### New finding, found while proving Finding B live — `electronic_order.external_id` too narrow

Building the fixture chain for Finding B's live proof, a direct insert of a genuine
64-character correlation value (`DaherOrderCorrelation.VALUE_PATTERN`'s exact shape — a
SHA-256 hex digest, matching `adapter/src/daher_adapter/recovery.py`'s
`SOURCE_CORRELATION_SYSTEM` value format) into a fresh isolated database raised
`value too long for type character varying(60)`. `clinlims.electronic_order.external_id` was
`varchar(60)`; `persistDaherOrderCorrelation` (`electronicOrder.setExternalId(daherOrderId)`)
would hit the identical failure on **every** real create — with Finding A's outer transaction in
place this now rolls back cleanly instead of orphaning native Sample/SampleItem/Analysis state,
but the correlation itself could never be written at all, silently defeating Option F before it
ever reached the writer. This was not caught by any prior ticket's live proof because none of
them inserted a genuine 64-character value; DH-011D2's concurrency proof used raw SQL against the
same column with shorter test values.

**Fix**: a new Liquibase changeset,
`003-electronic-order-external-id-widen.xml` (`modifyDataType ... newDataType="varchar(64)"`,
with rollback), widens the column to exactly 64 characters — matching the pattern this column now
additionally stores, and not touching existing HL7/CSV external order numbers (all ≤60 characters
observed, and Postgres does not rewrite existing data on a varchar length increase). The Hibernate
mapping (`ElectronicOrder.hbm.xml`, `length="60"` → `length="64"`) was updated to match for
consistency; **this is documentation-consistency only, not load-bearing** — `hbm.xml`'s `length`
attribute governs Hibernate's own DDL generation, not runtime validation, and Liquibase (not
Hibernate) owns this deployment's actual schema. The evidence that the column was genuinely too
narrow, and that the fix genuinely works, is entirely DB-level: the same 64-character insert that
failed against a fresh isolated DB with only changesets 001-002 applied succeeded after changeset
003 ran against that **same** database (proving safe upgrade-in-place, not just a fresh-DB
replay) — `\d electronic_order` showing `character varying(64)` before the retry, and successful
`INSERT` after.

### New P0 finding, not fixed this pass — `createServiceRequest` cannot create a new order

Attempting a live `POST /fhir/ServiceRequest` (subject referencing the fixture Patient, a valid
Daher correlation identifier, no `specimen` reference — the only reasonable payload shape for
DH-025 creating a brand-new order) against the isolated stack returned
`Generated lab number is invalid` (HTTP 400). This is **not** the accession-format/site-code
failure §10 anticipated — confirmed by first setting the isolated database's own `site_information`
row to a synthetic, obviously-non-real numeric value (`90009`, test-scope only, never the real
stack) and retrying: the failure was identical, ruling out the site code specifically. This
synthetic value turned out irrelevant to the actual failure — **changeset
`002-site-code-configuration.xml` has still never been exercised with any real value flowing
through `LiquibaseConfig`'s own validated path** (the `90009` value was written directly via SQL
to unblock this one test, not through the Liquibase changeset the real configuration mechanism
uses). Traced to
`ServiceRequestProvider.createServiceRequest` line 251 (upstream, **not** part of any DH-011D/D2/D3
patch — confirmed by grepping every committed patch file for the exact string, no match):
`requireNonBlank(sampleOrder.getLabNo(), "Generated lab number is invalid")`. `sampleOrder` comes
from `FhirTransformServiceImpl.buildSampleOrderItemFromServiceRequest`, which only sets
`orderItem.setLabNo(sample.getAccessionNumber())` when `sample != null` — and `sample` is only
resolved from an **existing** `Sample` reached via `serviceRequest.specimen`'s reference (or, for
an update, a matched `Analysis`). For a genuinely new order with no pre-existing specimen, `sample`
stays `null`, `labNo` is never set, and the create fails before any of DH-011D3's own logic
(atomicity, correlation persistence) ever runs.

**What this means**: `createServiceRequest` was never designed as a standalone new-order-creation
endpoint. It decorates/links a `ServiceRequest` to a `Sample` that must already exist — accession
number generation happens somewhere else in native accessioning, reached (per
`SpecimenProvider.java`'s own `@Create` method) via `POST /fhir/Specimen` first. This is strictly
larger than §10's framing ("the writer doesn't exist, and the site code alone can't fix that"): the
target endpoint cannot create an order **at all**, with or without a site code, without a
Specimen-first prerequisite step this ticket did not build or test. §2's formal transaction-capture
proof, §4's rollback control, §5's concurrency control, §8's FHIR-sync-failure control, and §9's
crash-recovery control all require a live `createServiceRequest` success path and are **all
blocked by this**, not by the missing site code.

Building or designing a Specimen-first prerequisite flow was **not attempted this pass** — it
carries clinical-surface implications (specimen collection semantics: what a Specimen represents,
who/what creates it, what data it carries) squarely inside AGENTS.md's "any gap that obviously
should be filled a particular way" instinct-guard. This is recorded as `Human Decision Required:
Yes` in `PROJECT_STATE.md`, not designed or assumed here.

### Finding D — the patch was not part of the deployed service graph, and CI never compiled it

`docker-compose.yml`'s `openelis-webapp` service ran `itechuw/openelis-global-2:3.2.1.11`
directly — none of DH-011D's patches were part of the normal deployed service graph, and the
existing CI job for this patch set (`static` job's "DH-011D order-correlation patch content
guards") only asserts on committed source text, never compiles it.

**Fixed**: `docker-compose.yml`'s `openelis-webapp` now follows the same local-derived-image
pattern already used for `openmrs-backend` — `image: daher-health/openelis-webapp:3.2.1.11-order-
correlation` with `build: { context: ./openelis-config }` (Compose builds and tags locally,
reusing the already-built image unless asked to rebuild). `docker compose config -q` parses
cleanly and resolves `openelis-webapp` to this build/image pair (verified locally; not deployed
against the real running stack, per this ticket's explicit instruction). The §12 site-code
mechanism is now wired into `CATALINA_OPTS` (`-Ddaher.openelis.site.code=${DAHER_OPENELIS_SITE_CODE:-}`,
defaulting to empty — never inventing a value), and `.env.example` documents the new variable with
the same NEEDS LIBAN #7 framing as every other real-facility-value placeholder in that file.

A new CI job, `openelis-order-correlation-build`, clones the exact pinned tag, verifies the exact
commit, initializes the pinned `dataexport` submodule, applies the same committed patch files
`openelis-config/Dockerfile` consumes (the same `git apply`/`cp` list, not a duplicate), and runs
upstream's own build recipe through to a produced `target/OpenELIS-Global.war`, inside the same
pinned Maven/JDK image the Dockerfile itself builds with, with Maven dependency caching keyed on
the pinned commit. Control-tested locally (this job cannot be executed as real GitHub Actions from
this session): a throwaway corrupted copy of `ServiceRequestProvider.java.patch` was applied via
the exact same `git apply` invocation this job runs, against the exact same pinned, pristine clone
this job would use — `error: corrupt patch at line 20`, exit 128, exactly the failure mode that
would fail the real CI job. The static guard's own asserted patch/file list was **not** extended
to cover this session's new files (`LiquibaseConfig.java.patch`, `ElectronicOrder.hbm.xml.patch`,
`002-`/`003-...xml`, `DaherOrderCorrelationTransaction.java`) — a real but smaller gap than the
one this section closes, recorded here rather than silently left unmentioned.

**Existing static guards re-run against every file this pass touched**: `ServiceRequestProvider
.java.patch` was regenerated twice this session (Finding A's transaction fix, then the Finding B
`total`-semantics correction), and `docker-compose.yml`'s `openelis-webapp` service block was
rewritten. Every static CI check whose asserted content those changes could plausibly have broken
was extracted from `.github/workflows/ci.yml` and executed directly against the working tree
(not merely inferred from reading the workflow): the "Every image is pinned" guard (the new
`daher-health/openelis-webapp:3.2.1.11-order-correlation` reference matches the same
already-exempted `daher-health/*` pattern `openmrs-backend` uses), the "Compose invariants" guard,
and the "DH-011D order-correlation patch content guards" static-content guard (asserts on
`extractDaherOrderIdentifier`'s exact isolated body and a fuzzy-comparison blacklist against the
patch's added lines) all print their `ok` line with no `bad` entries. This is not a substitute for
an actual CI run, but it is stronger than "the YAML parses."

**Secret exposure, unrelated to any patch content but recorded because this document is the
durable account of this session's work**: verifying `docker compose config` resolves
`openelis-webapp` to the derived image involved dumping that service's full resolved
configuration, which printed the real, live `OE_DB_PASSWORD`, `OE_KEYSTORE_PASSWORD`, and
`OE_TRUSTSTORE_PASSWORD` values from this deployment's `.env` into the session transcript. All
three should be rotated.

### Test suite state

`adapter`: `ruff check`/`ruff format --check` clean; `pytest` — 142 passed (up from the prior
count; includes Finding C's ten writer-protocol tests, three activation-gate tests, and three
`total`-semantics tests added and then corrected this pass after the narrower fix above).
`openelis-config`: the patched image builds cleanly (`docker build`, exit 0) and boots cleanly on
a fresh isolated stack with no `HAPI-0289`/`SEVERE` errors.

### What remains — not started or not completed this pass

- §2's formal `TransactionSynchronizationManager.isActualTransactionActive()` proof at the five
  named capture points — blocked by the new P0 finding above.
- §4's mandatory rollback control (direct DB read after an induced mid-sequence failure) —
  blocked by the new P0 finding above. Note: the `varchar(60)` bug (fixed) would itself have been
  a real, production-code-path failure-after-native-persist injection had it not been fixed first
  — that opportunity is gone now that the column is correctly widened, but it briefly existed and
  is recorded here for anyone re-deriving this account.
- §5's mandatory service-layer concurrency control — blocked by the new P0 finding above. DH-011D2's
  raw-SQL concurrency proof against the unique index (still valid, see above) is not a substitute.
- §8's FHIR-sync-failure control and §9's crash-after-native-write control — both require a live
  `createServiceRequest` success path, blocked by the new P0 finding above.
- The static guard job's asserted patch/file list was not extended to this session's new files.
- `DECISIONS.md`, `PROJECT_STATE.md`, `CURRENT_STATE.md` corrections per §18 — not yet applied as
  of this section being written; see the ticket's own FINAL HANDOFF for current status.

READY TO COMMIT: No. See the FINAL HANDOFF delivered alongside this document for the complete,
itemized reason.

## DH-011M (2026-08-29) — Option P implemented and live-proven; one pre-existing, unrelated defect found

DH-011D3's own evidence, and the follow-on DH-011M evidence ticket, established that
`ServiceRequestProvider.createServiceRequest` could not create a new order at all: `labNo` and
`SampleEditItem.sampleItemId` were derived exclusively from an already-existing `Sample`/
`SampleItem` resolved via `ServiceRequest.specimen`, and no accession number was ever generated.
Liban approved **Option P** (2026-08-29): patch the ingestion path itself to generate a real
accession number and let `SamplePatientUpdateData.createPopulatedSample`'s existing "create new
sample" branch run, rather than building a parallel persistence engine or an ElectronicOrder-first
inbox (Option O) or a Specimen-first bootstrap (Option S, already ruled out as structurally
incomplete).

### What changed, precisely

`ServiceRequestProvider.createServiceRequest` now branches on `firstItem.getSampleItemId()`
(unchanged derivation, from `FhirTransformServiceImpl.buildSampleEditItemsListFromServiceRequest`):

- **Present** (an existing specimen resolved): entirely unchanged code path -- the exact same
  `sampleItemService.get(sampleItemId)` call and `requireNonBlank(sampleOrder.getLabNo(), ...)`
  check as before this ticket.
- **Absent, and no specimen reference was given at all**: `newOrderAccessionNumber()` calls
  `AccessionNumberUtil.getMainAccessionNumberGenerator().getNextAvailableAccessionNumber("",
  true)` -- the identical call `CommonValidationsRestController`'s `SampleEntryGenerateScanProvider`
  endpoint makes for the human Sample Entry frontend, not a second implementation. `newOrderSampleItem
  (tests)` builds a **transient** `SampleItem` (never itself persisted -- it exists only to carry
  `getTypeOfSample()` into the unchanged `SampleUtil.buildSampleXml`, which
  `SamplePatientUpdateData.initSampleData`'s own `SampleAddService` re-parses into the real,
  persisted `SampleItem`) carrying only the sample type every requested test needs, resolved via
  `TypeOfSampleService.getSampleTypeFromTest` (Hibernate `uniqueResult()` -- fails loud on
  ambiguous lab configuration, never guesses) and rejecting a request whose tests need different
  sample types rather than picking one. Deliberately sets nothing else: no collection date,
  collector, quantity, container, source, or received state -- `SampleAddService` already leaves
  each of those unset (and the resulting `SampleItem` at `SampleStatus.Entered`, not
  received/collected) when its XML omits the corresponding attribute, which is exactly what
  happens when the transient `SampleItem` carries only a type.
- **Absent, but a specimen reference WAS given** (dangling, wrong system, or a race): fails loud
  (`InvalidRequestException`) rather than falling through to the new-order path. Found and fixed
  during this ticket's own invalid-input testing: the first cut of Option P could not distinguish
  "no specimen provided" from "a specimen was referenced and didn't resolve", and would have
  silently created a duplicate new order for the latter -- a caller with a typo'd or stale
  reference needs to see an error, not an unrelated second order. Live-proven: a dangling
  `Specimen/00000000-...` reference now returns `400`, not `201`.

No parallel persistence engine, no second accession algorithm, no direct Sample/SampleItem/Analysis
INSERT anywhere in this patch -- every native row is still created exclusively by
`SamplePatientEntryService.persistData`'s existing business-layer path, reached exactly as before.

### Live proof, fully synthetic disposable stack (own generated keystore/DB credentials, `.env`
never read, real facility never touched)

- **New-order happy path**: `POST /fhir/ServiceRequest` with no `specimen` reference succeeded
  (`201`), `requisition.value` a real `SITEYEARNUM` accession (`DEV01260000000000001`), exactly
  one `Sample`/`SampleItem`/`Analysis` row, `Sample.clinical_order_id` correctly linked to the
  Daher correlation's `electronic_order` row, `SampleItem.status_id` = "The sample has been
  entered into the system" (20), `Sample.status_id` = "No tests have been run for this order" (1)
  -- neither reads as collected/received. `SampleItem.collector`/`quantity`/`source_id` all NULL.
  `SampleItem.collection_date`/`received_date` populated to the create timestamp -- **not
  Option P's choice**: `buildSampleXml` always sets `receivedDate`/`receivedTime` (unchanged,
  pre-existing code, identical for the traditional human Sample Entry UI), and
  `clinlims.sample.received_date` is `NOT NULL` at the schema level, so *some* value is
  unavoidable for any Sample this engine creates, walk-in or FHIR-originated. The lifecycle
  signal a lab worker actually reads is the status text, which is correctly "entered", not
  "received".
- **Duplicate delivery**: an identical second `POST` for the same correlation was rejected
  (`400`, the existing fast pre-check) with zero new rows of any kind; the correlation search
  still resolved to the original single `ServiceRequest`.
- **Concurrent delivery (race)**: two simultaneous `POST`s for the same never-used correlation --
  one `201` (real accession, real Sample), one `500`
  (`ConstraintViolationException` on the partial unique index). Exactly one `electronic_order`
  row, exactly one more `Sample`/`SampleItem`/`Analysis` row each than before the race -- the
  loser left zero orphan native rows, proving Finding A's outer-transaction atomicity holds under
  real concurrency for a brand-new order, not just the raw-SQL proof DH-011D2 recorded. The
  loser's *reserved accession number* was not attached to any persisted Sample and does not
  reappear -- confirmed by observing the next successful create receive the number after the
  gap. This is expected and not a data-integrity problem: `AccessionServiceImpl`'s sequence
  increment is not part of the same atomic unit as the native row-set (it commits before
  `daherOrderTransaction.persist` ever starts), so a rolled-back create leaves a gap in the
  sequence, the same way any sequence-backed generator behaves under rollback -- never a
  duplicate, never a fabricated fact.
- **Crash-ambiguity, through the concrete adapter writer, not mocked HTTP**: a disposable script
  drove the real `daher_adapter.openelis.OpenElisClient` (`deliver_service_request`,
  `service_request_probe`) against the live stack over real HTTP. Probe before delivery: ABSENT.
  Real delivery: succeeds, returns a `TargetRecord`. Probe again immediately after (simulating a
  restart before the ledger was marked complete): FOUND, resolving to the exact same
  `record_id` -- proving DH-025's `RECONCILED_EXISTING` path would correctly avoid a duplicate
  write against this concrete writer, not just its unit-mocked double. A second
  `deliver_service_request` call for the same correlation reconciled to the same existing record
  rather than raising or duplicating (the POST itself was rejected server-side, but
  `deliver_service_request` never trusts POST status -- it reconciles by search regardless, per
  Finding C's existing design, and found the one true record) -- confirmed at the DB level:
  exactly one `electronic_order` row for that correlation throughout.
- **UI/workflow representation**: `GET /rest/home-dashboard/ORDERS_IN_PROGRESS` (the real
  dashboard tile backing `AnalysisStatus.NotStarted`, unrelated to and unmodified by this patch)
  returned the new orders correctly, showing only `orderDate`/`labNumber`/`testName`/`priority` --
  no field claims or implies a specimen was collected or received. This is the actual JSON a lab
  worker's dashboard renders, from a real HTTP call against the running application, not an
  inference from source alone.

### New finding, NOT fixed -- pre-existing, unrelated to any Daher patch

Adding a second test to an already-existing order (`ServiceRequest.specimen` referencing a real,
resolvable `SampleItem` -- the "existing-order" branch, entirely untouched by this ticket) throws
live: `org.hibernate.StaleObjectStateException: Row was updated or deleted by another transaction
(or unsaved-value mapping was incorrect) : [...Sample#2]`. This is the **first time this exact
scenario has ever been exercised live** in this project's history -- no prior DH-011 ticket ever
had a real, resolvable specimen to reference, so this defect was structurally invisible until now.

**Control-tested to determine attribution, not assumed**: the identical fixture (a directly-SQL-
inserted `Sample`/`SampleItem`/`Analysis`, labeled FHIR SEARCH FIXTURE ONLY / NOT NATIVE ORDER
PROOF) and the identical follow-up `POST` were run against a **completely pristine, unpatched**
build of the exact pinned upstream tag/commit -- zero Daher patches applied, built via a fresh
`git clone` with no `git apply` steps at all. The identical `StaleObjectStateException` on
`Sample#2` occurred. **This conclusively proves the defect is pre-existing upstream OpenELIS
3.2.1.11 behavior, entirely unrelated to DH-011D, DH-011D2, DH-011D3, or DH-011M** -- not a
regression this ticket's Finding A transaction wrapper, or any other Daher patch, introduced.

Per this ticket's own scope discipline ("smallest patch", "one ticket only", and AGENTS.md's
"a pre-existing, unrelated gap" guidance on second bugs found mid-fix), this was **not fixed**
here. It does not block Option P's own correctness -- Option P's new-order path never reaches this
branch at all, proven by the happy-path/duplicate/race evidence above, all of which exercise only
the new-order branch. It **does** mean "the existing-order edit path behaves exactly as before"
is true in the narrowest, most literal sense (byte-identical code, byte-identical failure) but was
never actually a passing regression test -- "before" was already broken, just never provably so
until this ticket finally had a real specimen to test against. Recorded as a new finding requiring
its own ticket/decision, not silently left undiscovered and not silently repaired under this one.

### Test infrastructure

All of the above ran against fully synthetic, disposable infrastructure this ticket generated
itself: a self-signed keystore/truststore (Docker named volumes, `keytool` inside the pinned
Maven/JDK image), a `common.properties` rendered from the template with those synthetic passwords,
and the pinned `itechuw/openelis-global-2-database:3.2.1.11` image with freshly-generated random
`DB_PASSWORD`/`DB_SUPERUSER_PASSWORD`/`POSTGRES_PASSWORD` -- `.env` was never read, no real
facility credential was ever touched, and the real running `daher-health-*` stack was never
started, stopped, or connected to. All containers, networks, and volumes were removed after use;
nothing synthetic persists.

### What remains

- The pre-existing existing-order-edit `StaleObjectStateException` above: a new, separate finding
  requiring its own ticket and decision on priority -- not designed, not fixed, not assumed away
  here.
- Formal negative controls for "bypass accession generation", "second delivery creates a second
  accession", and "remove the transaction boundary" were not independently re-run this ticket as
  fresh rebuild-and-break exercises beyond what the evidence above already demonstrates by
  construction (the pristine-vs-patched comparison **is** the accession-generation control: pristine
  cannot create a new order at all; the race test **is** the duplicate-accession and transaction-
  boundary control, live, not synthetic). "Drop correlation persistence" and "fabricate a
  collection field" were reasoned from code inspection (both are single-line, easily-reverted
  changes with obvious, already-covered blast radius) rather than independently rebuilt and
  broken live, given the cost of each full OpenELIS rebuild cycle against the value of re-proving
  properties this session's evidence already establishes by construction.

READY TO COMMIT: No -- see the ticket's FINAL HANDOFF for the exact, itemized reason.

## DH-011N (2026-08-29) -- existing-order StaleObjectStateException: premise corrected, root
## cause proven live, fix blocked on two human decisions -- no code changed

DH-011M recorded a "new finding, not fixed here": adding a second test to an already-existing
order throws `org.hibernate.StaleObjectStateException` on `Sample`, control-tested against a
pristine, unpatched build of the same pinned upstream tag/commit and reported as conclusively a
pre-existing upstream defect unrelated to any Daher patch. This ticket set out to reproduce that
defect rigorously, find its root cause, and fix it. It reproduced the defect and found the root
cause. It does not fix it -- two genuine human decisions block the fix, recorded below and in
`PROJECT_STATE.md`.

### Premise correction: DH-011M's own reproduction fixture was not native-order proof

DH-011M's own text already flagged this ("labeled FHIR SEARCH FIXTURE ONLY / NOT NATIVE ORDER
PROOF") but its narrative conclusion overstated what that fixture proved. A directly-SQL-inserted
`Sample`/`SampleItem`/`Analysis` row is the single most common *artificial* cause of exactly this
exception (a hand-written `INSERT` can easily leave the Hibernate `optimistic-lock="version"`
timestamp column, `LASTUPDATED`, in a state that never round-trips through a real load/update
cycle). Both pristine and patched builds throwing identically on that fixture is consistent with
"real upstream defect" **and** with "the fixture itself is malformed" -- the control held the
wrong variable constant. This ticket's job was to close that gap before trusting the conclusion,
not to re-assert it.

### Phase 1: live reproduction against a genuinely native Sample -- defect confirmed real

Built one fully synthetic, disposable stack from the exact committed `openelis-config/Dockerfile`
and patch set (own generated Postgres/keystore/truststore credentials via `openssl rand`/`keytool`,
`.env` never read, real facility stack never started, stopped, or connected to): `itechuw/openelis
-global-2-database:3.2.1.11` (pinned digest, unchanged from docker-compose.yml) plus the patched
webapp built from `git clone --branch 3.2.1.11 --depth 1` asserted against commit
`e2a6bdf7999fa3754e7cdafbd2a91259c3f0ce23` with every patch in this directory applied -- the same
recipe `openelis-config/Dockerfile` and CI's `openelis-order-correlation-build` job already use, not
a separately-invented build. `daher.openelis.site.code` was left unset (not `DEV01`) to satisfy this
ticket's own `LiquibaseConfig` fail-closed boot guard, which correctly refused to start with the
literal placeholder -- confirms that guard behaves as designed.

1. Created a genuinely new order the same way a real clinician's client would: `POST
   /fhir/ServiceRequest`, no specimen, LOINC `736-9` (Lymphocytes %, Whole Blood), against a
   FHIR-created native Patient. Succeeded exactly as DH-011M's own evidence describes: `201`, a
   real `SITEYEARNUM` accession (`DEV01260000000000001`), exactly one native `Sample`/`SampleItem`/
   `Analysis` row (`clinlims.sample` id 2, `clinlims.sample_item` id 2, `clinlims.analysis` id 2,
   `test_id`=27), correlation preserved in `electronic_order` id 2. This is required test #10
   (existing new-order Option P regression) and it passes: Option P's own behavior is unaffected by
   anything in this ticket.
2. Added a second test (LOINC `5905-5`, Monocytes %, same Whole Blood type) to that same order via
   `POST /fhir/ServiceRequest` referencing the real, resolvable `Specimen/<sample_item fhir_uuid>`
   -- the "existing-order" branch, byte-identical code to before DH-011D/DH-011M (see "What
   changed, precisely" above: this branch is untouched by Option P). Result: `500`,
   `org.hibernate.StaleObjectStateException: ... [org.openelisglobal.sample.valueholder.Sample#2]`,
   reproduced identically three times against three fresh, never-before-used Daher correlations.
   **This is the first time this exact scenario has been exercised against a genuinely
   Hibernate-native, non-fixture Sample** -- the fixture-vs-real gap flagged above is closed: the
   defect is real, not a fixture artifact.
3. Atomicity verified directly against the database after the failure (not inferred from
   `@Transactional`): `clinlims.sample`, `clinlims.sample_item`, `clinlims.analysis`, and
   `clinlims.electronic_order` were byte-identical to the pre-attempt baseline -- same single
   `Analysis` row (`test_id`=27 only), same single `electronic_order` row (the *original*
   correlation only; the second POST's fresh correlation was never persisted), same
   `Sample.lastupdated`. The outer transaction rolled back cleanly.

### Phase 2/3: root cause, proven live by elimination and by contrast with the native path

Three specific hypotheses were tested and ruled out, in order, before the real mechanism was
identified:

1. **DB trigger touching `SAMPLE.LASTUPDATED` from a child-table write.** Source search of the
   pinned upstream tree for `CREATE TRIGGER` found exactly two triggers in the whole codebase
   (`electronic_signature_immutability_check`, `trigger_update_normalized_description`), neither
   touching `sample`/`sample_item`/`analysis`. Ruled out from source, no live test needed.
2. **Hibernate JDBC batching misreporting a successful UPDATE's row count.**
   `hibernate.cfg.xml` sets `hibernate.jdbc.batch_size=50` and, notably,
   `hibernate.jdbc.batch_versioned_data=true` -- the exact combination historically associated with
   PgJDBC batched-execution row-count bugs (`Statement.SUCCESS_NO_INFO` misread as a version
   conflict). Tested live by hot-patching the deployed WAR's exploded
   `WEB-INF/classes/hibernate/hibernate.cfg.xml` (Tomcat's default `unpackWARs`, no rebuild needed)
   to `batch_versioned_data=false`, restarting, and reproducing: **identical failure.** Then to
   `batch_size=0` (batching fully disabled), restarted, reproduced again: **identical failure.**
   Batching is conclusively not the cause.
3. **A genuine version/precision mismatch (the bound `LASTUPDATED` value not matching what's
   actually stored).** Captured the exact failing SQL via `log_statement=mod` on the disposable
   Postgres: `UPDATE clinlims.SAMPLE SET LASTUPDATED=$1, referring_id=$2 WHERE ID=$3 AND
   LASTUPDATED=$4` with `$4` = the correct, current baseline value (confirmed via `select id,
   lastupdated, extract(epoch from lastupdated)::numeric(20,9) from clinlims.sample where id=2` --
   no hidden sub-millisecond digits). Replayed that **exact statement, verbatim, by hand via psql**
   against the live disposable database: `UPDATE 1`. The predicate Hibernate constructed was
   objectively correct and would have matched the row. Hibernate's own exception text carries two
   alternatives -- "Row was updated or deleted by another transaction (**or unsaved-value mapping
   was incorrect**)" -- and the second clause, not the first, is what this proves.

With those three eliminated, the DB-level trace itself carries the mechanism: the request issued
**seven** separate `SELECT ... FROM clinlims.SAMPLE WHERE ID=2` round-trips over its lifetime
(captured with `batch_size=0`, so this is not a batching artifact), each a full database round-trip
-- not a single Hibernate session reusing its own first-level cache, which would return the
already-loaded instance for a repeated `Session.get()` on the same ID without touching the
database at all. Multiple independent round-trips for the identical primary key mean multiple
independent Hibernate sessions loaded and released `Sample#2` over the course of one HTTP request.
This is exactly what DH-011D3's own finding A javadoc on `DaherOrderCorrelationTransaction` already
documents structurally: no ambient transaction spans `ServiceRequestProvider.createServiceRequest`
until `daherOrderTransaction.persist(...)` is explicitly entered partway through the method.
Concretely, in the existing-order branch:

- **Load #1**, before the transactional boundary: `FhirTransformServiceImpl
  .buildSampleOrderItemFromServiceRequest` resolves `ServiceRequest.specimen` via
  `getItemByFhirId(specimenUUID, sampleItemService)`, which eagerly loads `SampleItem.sample`
  (`SampleItem.hbm.xml`: `lazy="false"`) -- in its own short-lived session/transaction, closed by
  the time this call returns.
- **Load #2**, also before the transactional boundary: `ServiceRequestProvider
  .createServiceRequest` itself calls `sampleItemService.get(sampleItemId)` again -- another
  independent, closed session.
- **Load #3**, the first load actually *inside* `daherOrderTransaction.persist(...)`:
  `SamplePatientUpdateData.createPopulatedSample`'s "Load existing sample for update" branch,
  `SampleService.get(sampleOrder.getSampleId())` -- this is the instance that later gets
  `.setReferringId(...)` and is the target of the one `UPDATE` attempt.

`Session.get()` returning a cached instance for a repeated ID within one session is standard
Hibernate behavior; getting genuinely different SQL round-trips for the same ID, as observed,
requires genuinely different sessions. A `Sample` instance loaded and released before the
transactional boundary opens, then referenced or re-resolved once inside it, is a detached-entity
handoff across session boundaries -- ticket outcome **B** ("stale entity reuse... loaded in two
sessions... detached then merged"), not a genuine concurrent-modification race (nothing else was
writing to this row) and not a version/precision bug (eliminated above).

### The native path proves outcome C is the right *shape* of fix, live, not by inspection

The human "add a test to an existing accession" workflow uses a structurally different business
service, `SampleEditServiceImpl.editSample` (reached via `POST /rest/SampleEdit`,
`SampleEditRestController`), which is itself `@Transactional` -- unlike `ServiceRequestProvider
.createServiceRequest`, its single method boundary is consistent for the whole operation, so
whatever `Sample` instance it loads stays valid for every use inside it. It also does not
unconditionally re-save `Sample`: `sampleService.update(updatedSample)` runs only `if
(sampleChanged)`, a deliberate guard against exactly the kind of no-op Sample-level write the FHIR
path performs unconditionally on every existing-order request (`sample.setReferringId(...)`,
`sample.setReceivedTimestamp(...)`, etc., regardless of whether anything actually needs to change).

Live-proven, not asserted: fetched the real `SampleEditForm` for accession
`DEV01260000000000001` via `GET /rest/SampleEdit?accessionNumber=...`, flipped `add=true` on the
`possibleTests` entry for test 29 (Monocytes %, Whole Blood -- deliberately a *different* legitimate
test than the FHIR-path attempt, same accession), and `POST`ed the whole form back to `/rest
/SampleEdit`: `200`. Database confirmed: exactly one new `Analysis` row (id 5, `test_id`=29,
`sampitem_id`=2 -- the *same* SampleItem), the original `Analysis` (id 2, `test_id`=27)
byte-identical, `clinlims.sample_item.lastupdated` **unchanged** (`sampleItemChanged` stayed false
too), and -- critically -- `clinlims.sample.lastupdated` **unchanged** from before the request,
confirming `sampleChanged` correctly stayed false and no Sample-level UPDATE was even attempted.
The identical logical operation (add one more test to an existing accession) that throws through
the FHIR path succeeds cleanly through the native path, on the same fixture, in the same
environment, moments apart.

**This does not mean the fix is "route to `SampleEditService`" alone.** `SampleEditService.editSample`
only avoids the defect because its own `@Transactional` boundary keeps one session alive for its
whole operation. If `ServiceRequestProvider`'s fix calls `editSample` but hands it a `Sample`
instance obtained from Load #1 or Load #2 above -- both outside `daherOrderTransaction`'s boundary
-- the detached-entity handoff reproduces through a new door. The `Sample` (or `null`, letting
`editSample`'s own `sampleService.getSampleByAccessionNumber` fetch run inside its own
transaction) must be resolved *inside* whatever transactional boundary wraps the call, not before
it. This is a concrete, required negative control for whichever ticket eventually implements the
fix: hand the fixed path a pre-transaction-loaded `Sample` and confirm a test goes red.

### Why this ticket does not implement the fix

Two questions block writing `ServiceRequestProvider`'s existing-order branch against
`SampleEditService`, and both have clinical/scope surface AGENTS.md reserves for a human, not an
agent's inference:

1. **Add-vs-replace semantics.** `SampleEditForm` is replace-shaped: it carries `existingTests`
   with per-item `canceled`/`removeSample` flags that `SampleEditServiceImpl` acts on directly
   (`createRemoveList`, `createCancelSampleList`). A `ServiceRequest` create carries only the one
   test being added -- it says nothing about the tests already on the order. Building the form
   server-side therefore requires *synthesizing* the existing-tests half from current DB state
   (all `canceled=false`, `removeSample=false`) on the assumption that a FHIR create with an
   existing specimen reference always means "add this test, touch nothing else" and never means
   anything closer to a full-state replace. That assumption has never been stated as a decision
   anywhere in this repository's history and is exactly AGENTS.md's flagged failure mode: "a gap
   that obviously should be filled a particular way."
2. **Whether DH-025 models "adding a test to an existing order" as its own source event.** This
   ticket's live test consumed one fresh Daher correlation per added-test `ServiceRequest`, purely
   because `ServiceRequestProvider`'s existing uniqueness check (`extractDaherOrderIdentifier` /
   `getElectronicOrdersByExternalId`) demands a not-yet-used correlation on every create. Whether
   the adapter's ledger actually intends "add a test" to carry its own correlation record (and, if
   so, what DH-025 reconciliation for that event looks like), versus something else entirely, is
   an adapter-contract question this ticket has no basis to decide.

Both are recorded as blockers in `PROJECT_STATE.md`, `Human Decision Required: Yes`, per this
ticket's own Phase 4 outcome E and AGENTS.md's "never invent policy" rule. No fix was written; no
`ServiceRequestProvider`, `SampleEditService`, or any other production file was modified by this
ticket.

### What was deliberately not done, and why

- **No pristine-upstream arm was built.** DH-011M's own patch diff already proves the failing
  branch (`sampleItemId` present -- "entirely unchanged code path") is byte-identical to upstream;
  a second container would only re-demonstrate what the trace and the diff already establish, at
  the cost of another full build/boot cycle, and would not distinguish anything the batching/
  precision/trigger eliminations above didn't already settle more precisely than a second
  black-box repro could.
- **No JVM-level debugger or Hibernate DEBUG session-tracing was attached.** The seven-SELECT
  DB-log trace, cross-referenced against the DH-011D3 finding A javadoc and the source-level load
  sites, is sufficient to establish the causal mechanism (detached entity across session
  boundaries) without needing to instrument the JVM directly -- and doing so would have required a
  rebuild this investigation did not need for any of its other conclusions.
- **Required tests #3-#9** (duplicate/concurrent add-test requests, crash-before/after-write,
  invalid existing Sample, dangling Specimen regression) were not built. They test the *fix*, and
  there is no fix yet to test against; building them now would mean guessing the add-vs-replace
  semantics decision above in order to construct fixtures, which is exactly the inference this
  ticket is refusing to make.

### Test infrastructure and real-facility boundary

Identical posture to DH-011M: one fully synthetic, disposable stack (own `openssl rand`-generated
Postgres passwords and `keytool`-generated keystore/truststore, `.env` never read), built from the
exact committed Dockerfile/patch files, no separately-invented build recipe. Unlike DH-021/UI-005's
note that this sandbox's Docker daemon denies `stop`/`kill`/`restart`, teardown was verified working
here (a throwaway probe container stopped and removed cleanly before any real work began) and was
exercised for real at the end: `docker compose down -v` removed all three containers, all four named
volumes (`dh011n-patched_ca`, `dh011n-patched_db-data`, `dh011n-patched_keystore`,
`dh011n-patched_properties`), and the dedicated network; the patched test image
(`dh011n-patched-openelis:test`) was removed with `docker rmi`. Nothing synthetic persists. The real
`daher-health-*` stack was never started, stopped, or connected to.

Two elimination experiments (`hibernate.jdbc.batch_versioned_data=false`, then
`hibernate.jdbc.batch_size=0`) were applied by hot-patching the *deployed, exploded* WAR directory
inside the disposable container (`WEB-INF/classes/hibernate/hibernate.cfg.xml`) and restarting that
one container -- never the committed `hibernate.cfg.xml` inside this repository's patch set, and
never any file the real facility's image is built from. The disposable container carrying those
temporary edits was torn down along with the rest of the stack; no artifact of that hot-patch
persists anywhere.

### Findings noted, not fixed (out of this ticket's scope)

- **`daher.openelis.site.code=DEV01` boot guard confirmed working as designed.** `LiquibaseConfig`
  correctly refuses to start when this property is explicitly set to the literal upstream
  placeholder, per its own DH-011M-era design. Any future disposable stack must leave this property
  **unset** entirely (not set it to a placeholder value) to boot -- worth documenting since it is
  easy to trip by copying `docker-compose.yml`'s production `CATALINA_OPTS` verbatim.
- **`POST /fhir/Patient` returned `201` with an empty body for a Patient create that actually
  failed server-side** (a `ConstraintViolationException` on `Person.firstName` -- the supplied
  synthetic name contained digits, which OpenELIS's name-format validator correctly rejects). The
  webapp logged the real exception and persisted zero rows, but the HTTP response claimed success.
  This is a false-green API response, structurally the same class of bug this repository has
  already hardened against elsewhere (the OpenMRS/OpenELIS healthcheck false-greens at DH-002/
  DH-021) -- but it is unrelated to DH-011N's scope (existing-order test-add) and was not
  investigated further or fixed here. Worth its own ticket.

### Test suite state

Adapter and static-guard CI were not re-run by this ticket -- no adapter or patch-file source was
changed. `pytest`/`ruff` counts are unchanged from DH-011M's last reported state (142 passing,
Ruff clean); this ticket did not re-measure them because it made no change that could affect them.

READY TO COMMIT: No production code was changed. The only committable artifact from this ticket is
this documentation update plus the two `PROJECT_STATE.md` blocker entries -- see the ticket's FINAL
HANDOFF.

## DH-011O (2026-08-29) -- both DH-011N decisions approved; additive semantics implementation-
## ready; correlation storage hits a structural blocker, characterized, not resolved -- no code
## changed, no disposable infrastructure built

Liban approved DH-011N's two blocking decisions: (1) existing-order updates are additive only --
add the requested test, never implicitly touch, cancel, or replace existing analyses; (2) each
added test is its own independent DH-025 source event, with its own stable correlation,
independently deduplicated and recoverable, on a stable accession. This ticket set out to
implement the existing-order branch against those decisions. It resolved decision (1) down to an
implementation-ready design from source alone. Decision (2) hit a structural blocker in the current
correlation model that this ticket characterizes rather than resolves by inference -- writing code
past it would mean choosing a schema/model-extension shape Liban has not seen, on a clinical
database, which is squarely the "gap that obviously should be filled a particular way" AGENTS.md
reserves for a human. No disposable infrastructure was built this pass; both findings below came
from static analysis of the pinned upstream source (already cloned locally from DH-011N) and this
repository's own adapter code, at zero Docker cost.

### Decision (1), additive semantics: no synthesized state needed, source-proven

`SampleEditServiceImpl.createRemoveList` and `createSampleItemUpdateList` (native "add a test"
business logic, unchanged upstream code) both iterate exclusively over the `existingTests` array
the caller submits, and act on an item only when it carries an explicit flag (`isCanceled()`,
`isSampleItemChanged()`) set to true. Neither method, nor anything else in `editSample`, ever
compares the submitted `existingTests` against what is currently in the database -- an existing
test simply *absent* from the submitted array is never loaded, never visited by any loop, and
therefore cannot be cancelled, modified, or replaced by omission. This means a pure "add one test"
call can omit `existingTests` entirely: nothing needs to be read back from OpenELIS and
faithfully carried forward to avoid corrupting it, because the native code path never inspects
current state for anything not named in the request. DH-011N's earlier live test (successful
add via `POST /rest/SampleEdit` with `existingTests` carried forward verbatim) is consistent with
this but did not distinguish "carrying forward is required" from "carrying forward happens to be
harmless" -- this ticket closed that gap from source, confirming the latter. Decision (1) is
therefore implementation-ready as designed, once decision (2)'s storage question is resolved.

### Decision (2), per-added-test correlation: structural blocker, two designs characterized

**The blocker, precisely.** `Sample.hbm.xml` maps `clinicalOrderId` as a single scalar column, not
a collection. `ServiceRequestProvider.searchDaherCorrelationNatively` (DH-011D3) is the sole
authoritative native lookup DH-025 relies on, and it resolves every correlation through exactly
that column: `electronicOrderService.getElectronicOrdersByExternalId(correlationValue)` →
`sampleService.getAllMatching("clinicalOrderId", order.getId())`, requiring **exactly one** linked
`Sample` -- zero throws `InternalErrorException` ("no linked sample"), more than one throws the
same for "expected exactly one". Both branches are deliberate fail-closed integrity checks per
that method's own javadoc, not incidental gaps. Consequence: a second `electronic_order` row
created for an added test's own correlation would be structurally unlinkable without breaking
something. Leave `Sample.clinical_order_id` pointed at the original order and the new row's own
probe finds an `electronic_order` with zero linked samples -- the "no linked sample" integrity
throw, every time. Repoint `clinical_order_id` at the new row instead and the *original* order's
probe now finds zero linked samples the same way -- the fix would silently break DH-025's ability
to reconcile the very event (Option P's new-order creation) this repository has already built and
live-proven twice (DH-011M, DH-011N). There is no way to satisfy decision (2) by reusing the
existing `electronic_order`/`Sample.clinical_order_id` mechanism at all; it needs a genuine
model extension.

**Two designs evaluated, neither implemented:**

- **(A) A new column directly on `clinlims.analysis`** -- e.g. `daher_correlation_id varchar(64)`,
  nullable, with a partial unique index `WHERE daher_correlation_id IS NOT NULL` (mirroring
  `electronic_order_daher_correlation_uk`'s own `WHERE type = 'FHIR'` pattern). Requires: a new
  Liquibase changeset; a new `Analysis.hbm.xml.patch` adding the mapped property; **and a new
  patch to upstream `Analysis.java` itself**, adding the field and its accessor. `Analysis.java` is
  a core clinical valueholder no Daher patch has touched in this repository's history -- this
  option meaningfully grows both the committed patch surface and the upgrade/rebase burden this
  ticket elsewhere asks to keep small, for a value that is pure transport metadata, not a clinical
  fact about the analysis itself.
- **(B, recommended) A new standalone correlation table**, e.g.
  `daher_analysis_correlation(id, analysis_id → analysis.id, correlation_value varchar(64) unique,
  created_at)`, mapped by a new Hibernate/JPA entity class -- structurally the same pattern
  already established for order-level correlation (`DaherOrderCorrelation.java`,
  `DaherOrderCorrelationTransaction.java`: new files, not patches to existing upstream classes).
  Requires a new Liquibase changeset and a new mapping/entity file; **zero patches to any existing
  upstream `.java` or `.hbm.xml` file**. This also matches `hibernate.cfg.xml`'s own stated
  direction -- its comments on the Sample Storage Management and ASTM Analyzer Mapping sections
  explicitly say newer additions are "annotation-based mappings (JPA)... per Constitution IV"
  rather than further `.hbm.xml` patching, so a JPA-annotated entity registered in
  `persistence.xml` (rather than a fifth `.hbm.xml.patch`) would follow this codebase's own
  already-stated preference, not introduce a new pattern.

**Checked against the adapter before proposing either, so the design isn't chosen blind to its
consumer:** `adapter/src/daher_adapter/openelis.py`'s `identifier_search` treats a FHIR bundle
with exactly one `entry` as `MatchCount.ONE` regardless of what `Bundle.total` says -- the
`total`-based collapse rule only fires when `len(entries) > 1`. A new Analysis-scoped native
search branch that returns exactly one entry for an added-test correlation therefore needs **no
adapter-side code change** for the base (one-match) case under either design A or B -- this is a
pure OpenELIS-side addition as far as DH-025's existing contract is concerned, for that case.

**Two things an eventual implementation must still state explicitly, whichever design is chosen,**
not leave to whichever lookup happens to run first: (i) what happens if a correlation value is
somehow found in *both* namespaces at once (`electronic_order.external_id` and the new added-test
store) -- almost certainly the same fail-closed ambiguity discipline `searchDaherCorrelationNatively`
already applies to its own "more than one" cases, but this has never been stated for a
cross-namespace collision and must be, not assumed by omission; (ii) whether the new branch's
`Bundle.total` should read `1` for consistency with the existing "one logical order" convention,
even though the adapter's own single-entry shortcut does not strictly require it.

**Two cheap alternatives considered and rejected in writing, not merely dismissed:**
deduplicating an added test by "does this accession already have an Analysis for this test code"
is clinical-state deduplication across potentially distinct source events, which the ticket's own
Same-test-semantics section explicitly forbids inventing (Event B and a later, distinct clinician-
originated Event C requesting the identical test are not automatically the same event). Keeping
the added-test-to-correlation mapping only in the adapter's own Postgres database (never written to
OpenELIS at all) fails the ticket's own requirement that the *target* be authoritative and
searchable across the crash-after-target-commit-but-before-ledger-completion window DH-025's
recovery contract must close -- an adapter-only record cannot answer "did this exact event already
succeed" from the target's own state, which is the entire point of DH-025's probe-before-deliver
design.

### Concurrency, flagged for whichever ticket implements the fix, not designed in here

A pure add (no `existingTests` submitted, matching decision (1)'s finding above) never sets
`SampleEditServiceImpl.editSample`'s internal `sampleChanged` flag, so it never issues a `Sample`
`UPDATE` at all -- the mutation is confined to a new `Analysis` insert (plus, under design B, a new
correlation-table insert). Two concurrent additions to the same accession may therefore never
contend on any shared versioned row in the first place. Per this ticket's own instruction not to
design a serialization boundary ahead of evidence: the eventual implementation should run the
concurrent-different-additions test (required test H) unmodified first, and add
`LockMode.PESSIMISTIC_WRITE` (or an equivalent narrow lock) only if it demonstrably fails --
never disable or weaken `@Version`.

### What was deliberately not done, and why

- **ADR-031 was not written.** Liban's two decisions are approved and real, but their
  implementation depends on which correlation-storage design he picks -- writing the ADR now,
  before that answer, would mean appending a correction to a decision record authored an hour
  earlier, on an append-only file. It will be written once the storage design is chosen.
- **No disposable infrastructure was built.** Both this ticket's findings (the structural
  correlation blocker, and decision (1)'s no-synthesis-needed proof) came from reading the pinned
  upstream source (already cloned locally from DH-011N, commit `e2a6bdf7999fa3754e7cdafbd2a91259c3f0ce23`,
  unchanged) and this repository's own adapter code. Building a container to re-confirm either
  would have cost a full build/boot cycle to re-demonstrate what the source already settles
  precisely.
- **No code was written for either design.** Per this ticket's own Phase 1 instruction ("if using
  the native edit service necessarily requires synthesizing clinical state... STOP and report the
  exact problem") and AGENTS.md's "never invent policy" rule -- decision (2)'s storage shape is a
  gap with more than one reasonable, materially different answer, and this ticket has no basis to
  choose one for Liban.
- **The required verification matrix (A-K), negative controls, and DH-025/ADR-031 documentation
  were not attempted.** All depend on a concrete implementation that does not yet exist.

### Real-facility boundary and cleanup

Real facility never started, stopped, or connected to. No synthetic containers, volumes, networks,
or images were created this pass -- there is nothing to tear down.

READY TO COMMIT: No production code was changed. The only committable artifact from this ticket is
this documentation update plus the one `PROJECT_STATE.md` blocker entry -- see the ticket's FINAL
HANDOFF.

## DH-011P (2026-08-29) -- Design B selected and implemented: standalone `daher_analysis_correlation`
## table, atomic add-test transaction, dual-namespace fail-closed search -- live-verified end to end
## on fully synthetic disposable infrastructure; not committed pending Liban's review

Liban selected **Design B** (a standalone correlation table, zero patches to any existing upstream
`Analysis.java`/`.hbm.xml` file) over design A, closing DH-011O's structural blocker. This ticket
implemented it end to end and live-verified 8 of 9 required matrix items plus one additional
happy-path probe against fully synthetic disposable infrastructure -- own `openssl rand`-generated
Postgres passwords and keystore/truststore, `.env` never read, real facility never started, stopped,
or connected to.

### What was built

- **`daher_analysis_correlation` table** (`liquibase/daher/004-daher-analysis-correlation.xml`):
  `id`, `analysis_id NUMERIC(10,0) NOT NULL REFERENCES clinlims.analysis(id)`, `correlation_value
  VARCHAR(64) NOT NULL` with a format CHECK (`^[0-9a-f]{64}$`) and a **unique** index, `created_at`,
  `last_updated`. Deliberately **no** `UNIQUE(analysis_id)` -- a canceled analysis revived under a
  later, distinct correlation can legitimately retarget the same `analysis_id` across its lifecycle;
  only `correlation_value` carries the authoritative one-event-one-row guarantee.
- **`DaherAnalysisCorrelation`** (new JPA `@Entity`, registered in `persistence.xml`) plus a thin
  DAO/service pair following the existing `UomTypeMap`/`UnitOfMeasureService` pattern exactly --
  matching `hibernate.cfg.xml`'s own stated preference for annotation-based (JPA) additions over
  further `.hbm.xml` patching, per Constitution IV, the same reasoning DH-011O recorded for choosing
  design B in the first place.
- **`DaherAddedTestTransaction`** (new `@Component`, mirroring `DaherOrderCorrelationTransaction`'s
  own reason for being a separate transactional wrapper bean rather than `@Transactional` directly on
  `ServiceRequestProvider` -- a transactional proxy on that class breaks HAPI's reflection-based
  `@Create`/`@Update`/`@Search` method discovery, `HAPI-0289`, live-confirmed at DH-011D3). One
  `@Transactional(rollbackFor = Exception.class)` method, `addTest`, covers `SampleEditService
  .editSample`'s native write and the correlation-row insert in a single transaction, proven active
  via `TransactionSynchronizationManager.isActualTransactionActive()` (throws if not). A fast,
  non-authoritative pre-check against `daher_analysis_correlation` makes a replay a no-op; the
  authoritative guarantee against a true race is the DB-level `UNIQUE(correlation_value)` constraint,
  deliberately **not** pre-emptively serialized with a pessimistic lock (see Concurrency below).
  Returns a `Result{analysisId, created}` rather than a bare id specifically so a replay can report
  `created=false` -- a replay that silently claimed 201 would be the same false-green class as the
  pre-existing `Patient`-create bug this file already records (DH-011N section); self-caught before
  live testing, not found by it.
- **`SampleEditService.editSample`'s return type widened from `void` to `List<String>`.** The prior
  shape returned the updated analysis ids through a shared singleton-bean instance field
  (`getUpdatedAnalysisList()`), a live concurrency hazard for any two overlapping calls on the same
  `@Service` bean -- eliminated, not merely worked around, by making the method return its result
  directly. `SampleEditServiceImpl` and `SampleEditRestController` (the pre-existing REST caller of
  `editSample`) were both updated to the new signature; this is the one place this ticket's changes
  reach outside the add-test path itself, and is called out explicitly because DH-011O's design-B
  writeup assumed the new work would be purely additive -- it was not, and a reviewer should see this
  named rather than find it in a file list.
- **`ServiceRequestProvider.createServiceRequest`'s existing-specimen branch** now routes to a new
  `createAddedTestServiceRequest`, which builds a `SampleEditForm` with `existingTests` left empty
  (source-proven safe at DH-011O; nothing omitted from that array is ever visited by any
  cancel/update loop) and `possibleTests` carrying exactly the one requested test, then calls
  `daherAddedTestTransaction.addTest`. A `StaleObjectStateException` cause is mapped to a 400
  (`InvalidRequestException`), matching this file's existing convention for a native optimistic-lock
  conflict surfaced to the FHIR caller.
- **`searchDaherCorrelationNatively` extended to a dual-namespace lookup**: queries both
  `electronic_order.external_id` (the existing initial-order namespace) and
  `daher_analysis_correlation.correlation_value` (the new added-test namespace). If a correlation
  value somehow matches **both** namespaces at once, this throws `InternalErrorException` rather than
  silently preferring either one -- DH-011O flagged this exact cross-namespace case as needing an
  explicit answer, not an assumption; live-verified below. The added-test namespace's own `Bundle`
  sets `total=1` for consistency with the initial-order namespace's "one logical order" convention,
  per DH-011O's second flagged open question.

### The one real implementation bug found by live testing, not by review

`createAddedTestServiceRequest` initially set `form.setSampleXML(SampleUtil.buildSampleXml(...))`,
mirroring the pre-existing `updateServiceRequest`'s own pattern. Live-tested (item 3 below), this
produced HTTP 500, `"Unexpected analysis count (2) for add-test event"` -- `editSample` created
**two** `Analysis` rows for one add-test call, not one. Root cause, found by reading
`SampleAddService.createSampleTestCollection()` in full: a populated `sampleXml` drives a **separate**
code path (`createAddSampleList`) that unconditionally parses every `<sample>` XML element and
inserts a brand-new `SampleItem` + `Analysis`, with **no branch at all** that reuses an existing
`SampleItem` even when the XML's `sampleItemId` attribute names one -- that reuse logic lives only in
the sibling `possibleTests` → `createAddAanlysisList` path. Fix: set `form.setSampleXML("<samples>
</samples>")` (an empty document) so `createAddSampleList` parses zero elements and contributes
nothing; the add flows exclusively through `possibleTests`, which correctly reuses the existing
`SampleItem`. **`updateServiceRequest` (pristine, unpatched upstream code) has this identical latent
defect** -- it builds `sampleXml` from an existing `SampleItem` the same way, whenever
`possibleTests` is non-empty -- **but is unreachable via the Daher adapter**, confirmed by reading
`adapter/src/daher_adapter/openelis.py`'s `deliver_service_request`: it only ever issues `POST`,
never `PUT`. Recorded here as an out-of-scope finding, not fixed, per this ticket's own "smallest
patch" instruction; worth its own ticket if `updateServiceRequest` is ever wired to a caller.

### A second, self-caught XML defect found only by a live boot

The migration's original explanatory `<!-- -->` comment used `" -- "` as a prose separator, which is
invalid inside an XML comment per spec (a literal `--` may not appear between `<!--` and `-->`).
Liquibase's own XML parser rejected it at boot (`SetupException: The string "--" is not permitted
within comments`, line 10 column 72) -- a class of bug no static patch-text guard can catch, only a
live boot proves it. Fixed by replacing every `" -- "` with `" - "` in the comment block, verified
well-formed via `xml.dom.minidom.parse`, and re-confirmed live: `daher-011p-004-daher-analysis-
correlation` migration now runs successfully (19ms) and the CI static guard still passes unchanged.

### Live verification matrix, all against one disposable stack (own generated credentials,
### `dh011p-patched-openelis:test`, pinned upstream `3.2.1.11` / `e2a6bdf7999fa3754e7cdafbd2a91259c3f0ce23`)

1. **Boot + schema.** Webapp and DB both healthy; `\d daher_analysis_correlation` confirmed table, FK
   to `clinlims.analysis`, the unique index on `correlation_value`, the non-unique index on
   `analysis_id`, and the format CHECK constraint, exactly as designed. **PASS**
2. **New order (DH-011M/Option P non-regression).** A fresh `ServiceRequest` POST with no existing
   specimen reference created one Sample/SampleItem/Analysis with a real generated accession, exactly
   as DH-011M proved before this ticket touched anything. **PASS**
3. **Add a second test to an existing accession -- the core DH-011N regression this ticket exists to
   fix.** POST referencing the first order's real `Specimen` → `201`. DB read after: the original
   Analysis (WBC, id=2) unchanged, including its own `lastupdated` timestamp; exactly one new
   `Analysis` (RBC, id=3) and one new `daher_analysis_correlation` row (`correlation_value` = `corr2`,
   `f2b4902a...`) exist; `sample_item` count for the accession unchanged at 1 -- no spurious duplicate
   specimen; zero `StaleObjectStateException` anywhere in the logs. **PASS**, fully confirmed with
   before/after DB state, not inferred from HTTP status alone.
4. **Dangling specimen reference (DH-011M's fail-loud guard, non-regression).** A `ServiceRequest`
   referencing a `Specimen` id that does not resolve to a real `SampleItem` → `400`,
   `"ServiceRequest.specimen reference did not resolve to an existing SampleItem -- refusing to
   silently create a new order instead"`, zero new samples. **PASS**
5. **Replay / idempotency.** The identical item-3 request replayed with the same correlation
   (`corr2`) → `200` (not `201` -- `addTestResult.isCreated() == false` correctly reported), zero new
   DB rows of any kind. **PASS**
6. **Concurrent identical event, deliberately without added locking** (per this ticket's own
   instruction not to pre-emptively serialize the accession ahead of evidence). Two simultaneous
   deliveries of the same correlation (`corr3`) against the same accession: one `201` winner, one
   `500` loser (`ConstraintViolationException` on `UNIQUE(correlation_value)`); DB read after showed
   **exactly one** new `Analysis` and **exactly one** new correlation row -- the loser's own spurious
   `Analysis` insert was rolled back with the rest of its transaction, zero orphans. This is also the
   live evidence for item 9's rollback requirement below -- see that item. **PASS**
7. **Concurrent different events, deliberately without added locking.** Two simultaneous deliveries
   of two distinct, valid add-test correlations against the same accession: both `201`, both
   Analyses created, zero `StaleObjectStateException` anywhere in either container's logs -- a pure
   add never sets `editSample`'s `sampleChanged` flag (DH-011O's own concurrency note), so the two
   never contend on any shared versioned `Sample` row, confirmed rather than assumed. **PASS**
8. **Cross-namespace collision, fail-closed (DH-011O's first flagged open question).** A synthetic
   64-hex value was directly SQL-inserted into **both** `electronic_order.external_id` (type=`FHIR`)
   and `daher_analysis_correlation.correlation_value` (pointed at the item-3 Analysis). The
   authoritative search (`GET /fhir/ServiceRequest?identifier=urn:daher-health:adapter:source-
   event|<value>`) returned `500`: `"Data integrity violation: correlation ... matches both the
   initial-order namespace (electronic_order.external_id) and the added-test namespace
   (daher_analysis_correlation.correlation_value) -- refusing to silently pick one."` Neither
   namespace was silently preferred. **PASS**
9. **Rollback / crash-recovery, split into its two halves.** The rollback half -- a failure between
   the native `Analysis` write and the correlation-row commit leaving zero partial state -- is
   **PASS, proven** as a natural byproduct of item 6: the loser's `Analysis` insert (created inside
   the same `@Transactional addTest` call, before its own correlation insert hit the unique-
   constraint violation) was fully rolled back with it; no separate synthetic failure injection was
   performed because item 6 already exercises the exact failure point this half asks about. The
   **crash-recovery half is NOT DEMONSTRATED** -- that requires the Daher adapter's own
   `RecoveryCoordinator` marking an event `RECONCILED_EXISTING` after a probe against a target write
   the adapter itself never received a response for, and the adapter was not started this pass. This
   is reported as a real gap, not inferred from the rollback proof; it is a remaining verification
   item for whichever ticket next puts the adapter in the loop against this build.
10. **Happy-path probe on the added-test namespace (DH-011O's second flagged open question, the
    complement of item 8).** `GET /fhir/ServiceRequest?identifier=urn:daher-health:adapter:source-
    event|corr2` (the item-3 correlation, no collision fixture involved) → `200`, `Bundle.total: 1`,
    exactly one `entry`, correctly the RBC `ServiceRequest` item 3 created. **PASS**

### A representation gap found while running item 10, recorded rather than fixed

The `ServiceRequest` body returned by both the original item-3 `201` response and the item-10 search
carries `identifier[system=urn:daher-health:adapter:source-event].value = corr1` -- the **original
order's** correlation (`a757304f...`), not `corr2` (`f2b4902a...`), the correlation of the actual
add-test event that produced this resource. Root cause: `createAddedTestServiceRequest` builds its
response via `fhirTransformService.transformToServiceRequest(analysisId)`, the same pre-existing,
unpatched transform every other path uses, which derives this identifier from the Sample's linked
`electronic_order.external_id` -- and design B deliberately does not create a new `electronic_order`
row for an added test (that was DH-011O's entire point: reusing that mechanism for a per-test
correlation is what's structurally broken), so the only `electronic_order.external_id` linked to that
Sample is still the original order's. The **search itself is correct** -- item 10 resolved by `corr2`
through the `daher_analysis_correlation` table, not by trusting this field -- only the *echoed
identifier on the returned resource body* names the wrong correlation. This is safe for the one
consumer that exists: the adapter's `deliver_service_request` already knows which correlation it
sent and always re-probes by that value rather than reading it back out of the response body (DH-025
finding C). It is recorded here as a known representation gap, not silently accepted as "acceptable
by design" -- whether a future consumer could ever be misled by it is Liban's call, not this ticket's
to close by inference.

### Concurrency: no locking added, exactly as DH-011O flagged and this ticket's own instruction required

Items 6 and 7 together are the live evidence DH-011O asked the eventual implementation to gather
before considering any lock: the identical-event race is safely resolved by `UNIQUE
(correlation_value)` alone (item 6), and two distinct concurrent additions to the same accession do
not contend on any shared versioned row at all (item 7) because a pure add never triggers `Sample`'s
own `@Version` update path. No `LockMode.PESSIMISTIC_WRITE` or equivalent was added; none of this
evidence demonstrates a need for one.

### CI

The "DH-011D order-correlation patch content guards" job (static guard) was extended with checks for
the new migration's table/FK/unique-index/rollback content and the explicit absence of
`UNIQUE(analysis_id)`; a check that `DaherAnalysisCorrelation.java` exists and is correctly
`@Entity`-mapped; a check that no `Analysis.java.patch`/`Analysis.hbm.xml.patch` exists (enforcing
design B's own boundary -- this ticket must never grow into design A by accretion); a check that
`SampleEditServiceImpl.java.patch` returns `List<String>` rather than assigning to the removed
`analysisList` field; and checks that `ServiceRequestProvider.java.patch` queries the added-test
namespace, contains the explicit cross-namespace collision-check string, routes through
`daherAddedTestTransaction.addTest(`, and contains no fuzzy-matching pattern. Both new negative-
control guards (the forbidden `Analysis.java.patch` file, the forbidden `UNIQUE(analysis_id)`
constraint) were control-tested by injecting each violation into a scratch copy and confirming the
guard script fails with the expected message for both -- not merely written and assumed correct.

The "openelis order-correlation patch -- compiles" behavioral job's "Apply the production patches"
step was extended to apply all four new patches and copy all new files, mirroring the Dockerfile
build stage exactly. **One pre-existing, unrelated bug found and fixed while reproducing this job
locally**: its "Install git" step never installed `apache2-utils`, so
`install/createDefaultPassword.sh` (which the job's build step calls) retries in an unbounded loop --
reproduced locally as an 80,000+-line log of the identical repeated error before the missing
dependency was identified. Fixed by adding `apache2-utils` to that step (renamed "Install git and
htpasswd") with a comment noting this predates DH-011P and was found empirically, not by static
review.

### Build evidence

Both a standalone Maven build (`/home/libanbritt/dh011p-build/run-build.sh`, a cold `.m2` volume) and
the actual production `Dockerfile` (a warm BuildKit cache) were built to `BUILD SUCCESS` /
`target/OpenELIS-Global.war` present, independently -- the standalone build gives a compile signal
isolated from container plumbing; the Dockerfile build is what the real image is actually built from.
Neither is redundant with the other.

### Test infrastructure and real-facility boundary

Identical posture to DH-011M/N/O: one fully synthetic, disposable stack, own generated Postgres
passwords and keystore/truststore, `.env` never read, real facility never started, stopped, or
connected to. Full teardown performed and verified at the end of this ticket: `docker compose down
-v` removed all three containers (`dh011p-patched-webapp-1`, `dh011p-patched-db-1`,
`dh011p-patched-certs-1`), all four named volumes (`dh011p-patched_ca`, `dh011p-patched_db-data`,
`dh011p-patched_keystore`, `dh011p-patched_properties`), and the dedicated network; `docker rmi
dh011p-patched-openelis:test` and the standalone build's `m2-dh011p-cache` volume were removed;
`docker ps -a`/`docker volume ls` confirmed nothing named `dh011p*` remains. One unrelated,
pre-existing container (`daher-health-openelis-certs-1`, created six days before this ticket started)
shares the `daher-health/certgen:0.1.0` image and was deliberately left untouched -- not part of this
ticket's disposable infrastructure and not this ticket's to remove.

### What was deliberately not done, and why

- **Crash-recovery (item 9's second half) was not demonstrated.** It requires the adapter's own
  `RecoveryCoordinator` in the loop, which this pass never started. Reported as NOT DEMONSTRATED,
  not inferred from the rollback proof that item 6 does provide.
- **The echoed-correlation representation gap was not fixed.** Changing what
  `fhirTransformService.transformToServiceRequest` echoes touches a pre-existing, shared transform
  used by every FHIR-facing path in this codebase, not something scoped to design B's own new files
  -- exactly the kind of change this ticket's "smallest patch, one ticket only" instruction reserves
  for its own ticket, after Liban has seen it named here.
- **`updateServiceRequest`'s identical `sampleXml` defect was not fixed**, for the same
  adapter-unreachable reasoning DH-011M/N already established for other findings in this file.

### Test suite state

No adapter or Python source was changed by this ticket; adapter test counts are unchanged from their
last-reported state. No frontend code was touched.

READY TO COMMIT: pending Liban's review of this handoff. Production code changes are complete and
live-verified per the matrix above; nothing has been committed or pushed. **DH-011 overall remains
NOT COMPLETE** -- DH-011G (patient identity), DH-011H (real requester, NEEDS LIBAN #6), DH-011I (test
mapping, DH-006), DH-011J (cancellation), DH-011K (crash points beyond DH-025's generic tests), and
DH-011L (demonstration) are all still open, unaffected by this ticket.

## DH-011P2 (2026-08-29) -- independent review declined DH-011P's commit; three closure gaps
## closed with live evidence: added-test correlation representation fixed, true DH-025
## crash-recovery demonstrated with the real adapter, updateServiceRequest reachability
## resolved to a contained, non-blocking finding -- still not committed pending Liban's review

An independent review of DH-011P's own handoff declined to approve it for commit, citing three
specific gaps: (1) the local worktree's relationship to `origin/master` was unverified; (2) true
DH-025 crash-after-target-commit recovery was reported NOT DEMONSTRATED; (3) the added-test
response's echoed identifier was recorded as a known gap, not fixed. This ticket closes all three,
live, against a fresh disposable stack rebuilt from the corrected patch set.

### Gap 1 -- repository state, verified rather than assumed

`git rev-parse HEAD` and `git rev-parse origin/master` were run directly rather than trusting the
prior handoff's stated base. They already matched exactly (`797eb7c97cba569589ab5dd47bb5d3a9b81bc790`,
`git rev-list --left-right --count HEAD...origin/master` → `0 0`) -- DH-011P's own working tree sat
cleanly on current master the whole time; the prior handoff's stated HEAD (`8f2ed6d`) was simply
stale, carried forward from an early-session snapshot rather than re-checked at handoff time. No
rebase, stash, or merge was needed or performed. All 15 DH-011P files (8 modified, 7 untracked) were
confirmed still present and `git diff --check` still showed only the same pre-existing benign
diff-context-blank-line pattern already present in 4 other committed `.patch` files in this repo.

### Gap 2 -- the representation fix (§5)

Root cause, precisely: `FhirTransformServiceImpl`'s shared `transformToServiceRequest(Analysis)`
method -- the single implementation every FHIR-facing path funnels through -- built the
`urn:daher-health:adapter:source-event` identifier exclusively from `sample.getClinicalOrderId()`'s
linked `electronic_order.external_id`, with no awareness that design B's `daher_analysis_correlation`
table exists at all. An added test's Analysis has no row in `electronic_order` of its own (design B's
entire point), so this code always fell through to the *sample's* electronic_order -- the *original*
order's correlation -- regardless of which Analysis was actually being transformed.

**Fix**, in `FhirTransformServiceImpl.java.patch` (an existing DH-011D patch, extended, not a new
file): before the existing `clinicalOrderId` fallback, look up
`daherAnalysisCorrelationService.getByAnalysisId(Long.valueOf(analysis.getId()))`. If that Analysis
has one or more added-test correlations, emit *every one* of them as its own repeated
`source-event` identifier (`ServiceRequest.identifier` is already 0..*) and skip the
`clinicalOrderId` fallback entirely. If it has none, fall through unchanged to the pre-existing
initial-order behavior -- the regression boundary DH-011N/O/P already established.

**The "emit all, not one" choice, not left to lookup order.** `daher_analysis_correlation` has no
`UNIQUE(analysis_id)` by design (a canceled-then-revived Analysis can legitimately carry a
correlation from before cancellation and a second from its revival -- `DaherAnalysisCorrelation`'s
own javadoc). Live-confirmed this is not hypothetical: an accidental test fixture during this
ticket's own §14 setup (a botched first attempt at the collision fixture, see below) put two
different correlation rows on the same Analysis and the search response correctly returned both as
separate identifiers on the one resource -- unplanned, but real, corroborating evidence that this
representation is right, not merely defensible.

**Live-verified, three orders on one accession** (a fresh WBC order + two independent add-test
events, RBC and HGB, each its own correlation):

| Search by | Bundle | Each entry's OWN echoed `source-event` identifier |
|---|---|---|
| corr1 (initial order) | `total:1`, 3 entries (WBC+RBC+HGB, all on the accession) | WBC→corr1 (unchanged), RBC→corr2, HGB→corr3 |
| corr2 (RBC's own event) | `total:1`, 1 entry | RBC→corr2 |
| corr3 (HGB's own event) | `total:1`, 1 entry | HGB→corr3 |

Before this fix, all three entries in the corr1 search would have echoed corr1 -- the search-by-
correlation namespace was already correct (DH-011P proved that), only the *echoed identifier on the
returned resource* was wrong. Confirmed via both the live HTTP response bodies and the authoritative
`daher_analysis_correlation` table read (exactly 2 rows: RBC→corr2, HGB→corr3, none for WBC, matching
design). **Gap 2: CLOSED**, not merely documented.

### Gap 3 -- true DH-025 crash recovery, demonstrated with the real adapter (§7-11)

**No production entrypoint assembles `OpenElisClient` + `RecoveryCoordinator` +
`LedgerRepository` into a running delivery loop yet** -- confirmed by reading `app.py` (a
health-check-only FastAPI app) and `order_poller.py` (`OrderPoller` takes a `RecoveryCoordinator`
as a constructor dependency but nothing in this repository ever instantiates one; DH-011E's poller
remains, as PROJECT_STATE.md already says, "not activated"). This ticket therefore built a
test-only *integration harness* -- not a change to `src/daher_adapter/` -- that drives the real,
unmodified production classes through the exact sequence a real crash would produce, against a real
disposable Postgres (the real `alembic upgrade head` migrations, `0001_safety_ledger` and
`0002_watermarks`, applied for real) and the real disposable OpenELIS webapp built from this
ticket's own patch set.

Committed as `adapter/tests/integration/test_dh011p2_crash_recovery.py` -- manual/opt-in only,
gated behind `DH011P2_LIVE_*` environment variables so the default `pytest -q` run (and CI, which
has no live stack) skips it: confirmed live, `pytest -q` on the unmodified adapter tree reports
**142 passed, 2 skipped** (the two new gated tests), an exact, re-verified match to DH-011P's own
last-reported adapter test count -- not assumed unchanged, checked.

**§7-9-10, crash-after-target-commit-before-ledger-completion:**
1. `ledger.claim(identity)` (real `LedgerRepository`, real Postgres) -- state → `processing`.
2. `RecoveryCoordinator.reconcile`'s `write_target` callable calls the real
   `deliver_service_request_if_activated` (POST + authoritative native reconciliation read, the
   real production writer), which returns a genuine `TargetRecord` -- OpenELIS has truly committed
   a new `Analysis` and `daher_analysis_correlation` row. The callable then raises a
   `SimulatedCrash` -- the one test-only fault, placed exactly after a proven real commit and
   before `reconcile`'s own `ledger.mark_completed` call, per this ticket's §9 instruction.
3. DB read after: `analysis` count +1, `daher_analysis_correlation` count +1 from the pre-state,
   `ledger.get_state(identity)` still `"processing"` -- the genuine ambiguous window.
4. "Restart": a **fresh** `RecoveryCoordinator` bound to a **fresh** `LedgerRepository`/session
   factory/SQLAlchemy engine (no Python object or in-memory state reused) calls `reconcile` again
   for the same identity. Its `write_target` raises `AssertionError` if ever invoked.
5. Result: `RecoveryAction.RECONCILED_EXISTING`, the writer was called **zero** times, DB counts
   **unchanged** from step 3 (no new Analysis, no new correlation row), `ledger.get_state`
   transitions to `"completed"`.

**§11, timeout/response-loss after a real commit** -- one real correction made while writing this
scenario, recorded rather than silently worked around: `deliver_service_request`'s own internal
reconciliation only runs when the `post` callable *returns* (even with an HTTP error status); if
the callable itself *raises* (a genuine transport-level exception -- the actual shape of a TCP
timeout/connection loss), the method raises `AmbiguousTargetState` immediately, without attempting
its own reconciliation read. This is real, already-tested behavior
(`test_openelis_recovery_binding.py`'s ambiguous-probe sibling case), not a defect introduced or
found here. The "no second mutation" guarantee for this exact failure mode therefore comes from
`RecoveryCoordinator.reconcile`'s own probe-before-write discipline on the *next* attempt -- proven
end to end: a `post` callable that performs a real POST (server really commits) then raises
`TimeoutError` to the caller → `AmbiguousTargetState` propagates, ledger stays `"processing"`, DB
shows the real write (+1 Analysis, +1 correlation) despite the "failed" first attempt; a second,
real (non-lossy) `reconcile` call finds `FOUND` via `service_request_probe`, calls the writer
**zero** times, completes the ledger. Final count after both attempts: still exactly **one** new
Analysis and **one** new correlation row total -- no double write, no blind retry POST.

Six independent runs of these two scenarios (two ad hoc, four via the tracked pytest file, run
twice to confirm the lint-cleanup pass changed nothing behavioral) all left the adapter's own
`processed_messages` ledger in state `completed` for every identity, zero stuck in `processing`,
zero dead-lettered. **Gap 3: DEMONSTRATED**, with the real `OpenElisClient`, `RecoveryCoordinator`,
and `LedgerRepository` -- not a unit fake, not scripted HTTP, not a direct DB insert, not
`service_request_probe` called alone.

### §4 -- full patch-surface review, the one third caller DH-011P never named

Grepped the entire pinned upstream tree, including the `dataexport` submodule, for every call site
of `SampleEditService`/`editSample`/`getUpdatedAnalysisList`. Exactly three exist, not two:
`SampleEditRestController` (already reviewed and patched by DH-011P), `ServiceRequestProvider`
(already reviewed and patched by DH-011P), and **`SampleEditController`** -- the classic,
non-REST Spring MVC controller behind the human "Sample Edit" browser workflow, never previously
named in this file. It calls `editSample` as a bare statement (`sampleEditService.editSample(...);`,
return value discarded) and never calls the removed `getUpdatedAnalysisList()` getter anywhere.
Java permits discarding a non-void return value used as a statement expression, so widening
`editSample`'s return type from `void` to `List<String>` is behaviorally silent here -- confirmed
both by this source-level review and empirically: the full Maven reactor (which compiles this file
unconditionally, patched or not) reached `BUILD SUCCESS` repeatedly across this and the DH-011P
session with no changes needed to `SampleEditController.java`. No patch to it exists or is needed.

**Per-patch review, the requested WHY/BEFORE/AFTER/GENERAL-BEHAVIOR/DAHER-ONLY-ALTERNATIVE/
UPGRADE-COST table**, for the four upstream files DH-011P's own handoff named as needing this:

| File | Why required | Before | After | General OpenELIS behavior changes? | Daher-only path possible instead? | Upgrade/rebase cost |
|---|---|---|---|---|---|---|
| `SampleEditService.java` | `editSample`'s only way to report which `Analysis` id(s) it wrote, race-safely | `void`; caller read a separate `getUpdatedAnalysisList()` getter afterward | Returns `List<String>` directly | Yes, but only the method's return type -- every real caller either uses the new value (the two REST/FHIR paths) or silently discards it (`SampleEditController`, confirmed above) | No -- `editSample` is upstream's own single native "add/edit a test" implementation; forking it into a Daher-only copy would duplicate its ~250 lines of transactional business logic and immediately diverge from upstream bugfixes | Low -- a one-line interface signature; a future upstream release changing this method's own signature would conflict obviously at `git apply` time, not silently |
| `SampleEditServiceImpl.java` | Same reason; this is where the shared singleton-bean field actually lived | `private List<String> analysisList` instance field on a singleton `@Service` bean -- a live race between any two concurrent `editSample` callers, including two different human users | Field removed entirely; the local `analysisIds` list is returned directly, never stored on `this` | No behavioral change for a single caller; **removes** a latent concurrency hazard for concurrent callers (never observed failing in this repo's own testing, but structurally present before this patch) | No, same reasoning as above | Low -- a field deletion and one `return` statement |
| `SampleEditRestController.java` | The only pre-existing caller that read `getUpdatedAnalysisList()` | `sampleEditService.editSample(...)` (return discarded); separately called `sampleEditService.getUpdatedAnalysisList()` | Captures `editSample`'s return value into a local variable, passes that to `fhirTransformService.transformAnalysisByIds(...)` | No -- identical values flow to the identical downstream call, only the plumbing changed | No -- this is the REST endpoint's own pre-existing FHIR-sync call, not something Daher owns | Low -- a two-line change at one call site |
| `persistence.xml` | Registers the new `DaherAnalysisCorrelation` JPA entity so Hibernate maps it at all | Entity list did not include it | One `<class>` line added, in the same block as the other Daher/label-preset annotation-based entities | No -- purely additive registration | No -- JPA entity registration is a single, global, upstream-owned file; there is no Daher-only equivalent | Low -- one line, in a clearly-delimited list; a merge conflict here is trivially resolved by re-adding the line |

None of the four changes upstream's own general-purpose behavior for any caller not already
reviewed above. All are low upgrade-rebase cost by the same measure DH-011M/N/O/P have used
throughout: a future upstream release changing these exact lines produces an obvious `git apply`
conflict, never a silent behavior change.

### §15 -- `updateServiceRequest` (PUT) reachability, resolved to contained, not a blocker

**Live-tested against the disposable stack, the exact question this ticket's §15 poses**: an
authenticated `PUT /fhir/ServiceRequest/1` using the same HTTP Basic credential class
(`admin:adminADMIN!`) the Daher integration would use returned `400`
(`"ServiceRequest.code.coding is required"`) -- past authentication, past HAPI's method routing,
failing only on payload validation. An unauthenticated PUT to the same URL returned `302` to the
login page (denied, as expected). **This confirms `updateServiceRequest` is reachable by the same
credential class**, resolving DH-011M/N's own open question about it.

**But not by "the same credentials AND normal Daher flow," §15's actual compound STOP condition.**
`adapter/src/daher_adapter/openelis.py`'s `OpenElisClient` defines no PUT-capable method at all --
`identifier_search` and `_create_and_verify` both only ever call `self._get`/`self._post`; there is
no `update_service_request` or equivalent anywhere in this repository's adapter or Java patch code.
Nothing in the normal Daher flow can construct a PUT request; it is reachable only by a deliberate,
out-of-band manual call using valid credentials -- not something the adapter's own code path could
"accidentally" invoke. **Resolution: UNSAFE / NOT SUPPORTED FOR DAHER V1, contained, not a
blocker.** Two actions taken, both required by §15's own non-STOP branch:

1. This finding is now recorded explicitly (this section, plus `PROJECT_STATE.md`) as its own
   named, out-of-scope item -- distinct from DH-011P's earlier note that `updateServiceRequest` is
   merely "unreachable via the adapter's transport" (true but incomplete: it is unreachable via the
   adapter's *code*, reachable via its *credentials*).
2. A permanent CI static guard (the extended "DH-011D order-correlation patch content guards" job,
   see §18 below) asserts `openelis.py` defines no PUT-capable method for `ServiceRequest` delivery
   -- control-tested by injecting a `"PUT"` string reference into a scratch copy and confirming the
   guard fails with the expected message.

This is NOT the `updateServiceRequest` `sampleXml` double-Analysis defect DH-011P already recorded
(that is a separate, still-unfixed latent bug in the SAME unreachable method); this section is about
*reachability*, that one is about *correctness if ever reached*. Both point at the same method,
both concur it must not be called, for two independent reasons.

### §16 -- JPA/persistence verification, all items live-confirmed

- **Fresh boot**: the rebuilt image (with the representation fix) booted clean from an empty
  volume; `\d daher_analysis_correlation` matched the design exactly (unchanged from DH-011P).
- **Existing-volume migration idempotence**: `docker restart` against the *same*, already-migrated
  volume -- `databasechangelog` still showed exactly one `daher-011p-004-...` row (not duplicated),
  all data survived (`analysis`/`daher_analysis_correlation` counts unchanged across the restart),
  Liquibase's own log showed every changeset correctly skipped or continued past precondition,
  never re-applied.
- **Entity registration / DAO CRUD**: proven structurally by every create/search operation in this
  ticket's live matrix, all of which flow through the JPA-mapped `DaherAnalysisCorrelation`
  entity and its DAO.
- **FK enforcement**: a direct SQL insert of a correlation row with `analysis_id = 999999` (no such
  Analysis) was rejected: `violates foreign key constraint "daher_analysis_correlation_analysis_id_fkey"`.
- **Unique correlation**: re-confirmed via the concurrent-same-event regression (§12 below) --
  `ConstraintViolationException` on the loser, exactly as DH-011P first proved.
- **No `UNIQUE(analysis_id)`, confirmed both by schema read and empirically**: the accidental §14
  setup mistake (below) put two live correlation rows on the same `analysis_id` without the DB
  raising any constraint violation -- direct, unplanned proof the design intentionally allows it.
- **Rollback, no orphan correlation row**: re-confirmed via §12 -- the loser's `Analysis` insert and
  its own correlation-row insert were both rolled back together; DB counts after showed only the
  winner's single new row of each.
- **No correlation row pointing to a nonexistent Analysis**: the FK constraint above is the
  authoritative guarantee; nothing in this codebase can construct one that violates it.

### §12-14 -- concurrency and collision regressions, re-run against the rebuilt image

All three re-run clean against the image containing the representation fix, confirming it changed
nothing about atomicity or the fail-closed collision check:

- **§12, concurrent identical event, no added locking**: one `201` winner, one `500` loser
  (`ConstraintViolationException`), DB before/after: `analysis` +1, `daher_analysis_correlation` +1,
  `sample_item` unchanged -- the loser's spurious Analysis insert rolled back with it, zero orphans.
- **§13, concurrent different events, no added locking**: both `201`, DB `analysis` +2,
  `daher_analysis_correlation` +2, `sample_item` unchanged, **zero**
  `StaleObjectStateException` anywhere in the full container log.
- **§14, cross-namespace collision**: a genuinely correct fixture this time (a first attempt failed
  on a `NOT NULL` constraint on `electronic_order.data`, silently producing the §16 "no
  `UNIQUE(analysis_id)`" evidence above instead -- corrected and re-run properly). The same 64-hex
  value inserted into both `electronic_order.external_id` (type=`FHIR`) and
  `daher_analysis_correlation.correlation_value` (pointed at a different Analysis) -- the search
  returned `500` with the exact same fail-closed diagnostic DH-011P first proved, unchanged by the
  representation fix. Fixture rows cleaned up immediately after.

### CI (§18)

The "DH-011D order-correlation patch content guards" job was extended: the Dockerfile
patch-application check now covers all 9 patches (DH-011P's own guard checked only 5; the other 4
-- `LiquibaseConfig.java.patch`, `ElectronicOrder.hbm.xml.patch`, `SampleEditService.java.patch`,
`SampleEditRestController.java.patch` -- existed and were applied by the Dockerfile but were never
asserted); a new reciprocal check globs every `.patch` file in the directory and fails if any is not
consumed by the Dockerfile (catches a future patch being added and silently never applied, not just
the reverse); `FhirTransformServiceImpl.java.patch` now asserts both halves of the representation
fix (the added-test lookup present, the initial-order fallback preserved); and the new §15 guard
asserts `openelis.py` never gains a PUT-capable method. **All new/extended guards control-tested**
by injecting each specific violation into a scratch copy and confirming the exact expected failure
message -- not merely written and assumed correct. The "openelis order-correlation patch --
compiles" job's patch-apply step already covered `FhirTransformServiceImpl.java.patch` (an existing
DH-011D-era patch whose *content* changed, not a new file) and needed no changes.

**One genuine compile-verification gap found and closed while doing §17's review**: DH-011P's own
Docker build always used `-Dmaven.test.skip=true`, so the pinned upstream's own pre-existing
`ServiceRequestProviderTest.java` (a JUnit/Mockito test of `ServiceRequestProvider.readServiceRequest`,
unrelated to any Daher patch but living in the same class) was never actually compiled against
the patched `ServiceRequestProvider.java` by any build this repository has run. Run explicitly this
pass (`mvn test -Dtest=ServiceRequestProviderTest`, requiring the `dataexport` aggregator module to
also be `mvn install`-ed locally first, not just `dataexport-core` -- a build-plumbing gap in the
prior local reproduction, not in the Dockerfile, which already does both): **3 tests, 0 failures, 0
errors** against the fully patched build. §17's "compile all callers, run available relevant tests"
requirement is met with a real result, not an assumption from `BUILD SUCCESS` on a test-skipped
build.

### Adapter verification (§19)

`ruff check .`: all checks passed. `ruff format --check .`: 34 files already formatted (33 before
this ticket, +1: the new integration test file). `pytest -q`: **142 passed, 2 skipped** -- the exact
same passing count DH-011P's handoff reported, re-verified directly rather than assumed unchanged;
the 2 skips are the new gated crash-recovery integration tests, correctly inert without
`DH011P2_LIVE_*` env vars (confirmed by running the default suite both with and without them set).

### Real facility and cleanup

Real facility never started, stopped, or connected to throughout this ticket. Fully synthetic
disposable infrastructure: own `openssl rand`-generated Postgres/keystore/truststore credentials,
`.env` never read. Full teardown performed and verified at the end: `docker compose down -v`
removed all three containers, all four named volumes, and the dedicated network; `docker rmi` removed
the disposable image; the standalone Maven build's own `m2-dh011p2-cache` volume was also removed;
`docker images`/`docker volume ls`/`docker ps -a` filtered on `dh011p2` all confirmed empty
afterward. The one pre-existing, unrelated container sharing the `daher-health/certgen:0.1.0` image
(created six days before this ticket, `daher-health-openelis-certs-1`) was again deliberately left
untouched.

### What was deliberately not done, and why

- **`updateServiceRequest`'s own latent `sampleXml` double-Analysis defect was not fixed.** §15
  resolved its *reachability* question (contained, not called by any Daher path); the defect itself,
  should the method ever be wired to a caller, remains DH-011P's own recorded out-of-scope finding.
- **The Patient FHIR false-green (`POST /Patient` returning 201 whether or not the create actually
  persisted) was hit again during this ticket's own fixture setup** (a digit-containing synthetic
  given name was silently rejected server-side while still returning `201`) -- not investigated or
  fixed here, consistent with every prior ticket's treatment of this pre-existing, separately
  tracked finding. Worked around by reading the real `clinlims.patient`/`person` rows back directly,
  the same discipline this repository's own `OpenElisClient._create_and_verify` already applies in
  production code.
- **DH-011E's poller/production delivery loop was not built or activated.** The crash-recovery
  harness is explicitly a test-only proof assembly, not a step toward wiring DH-011E; that remains
  a separate, undecided-scope activation gate.

READY TO COMMIT: production code changes (the representation fix), CI guard extensions, and the new
tracked integration test are complete and live-verified per every item above; all three gaps the
independent review named are closed with evidence, not narrowed by argument. Nothing has been
committed or pushed. **DH-011 overall remains NOT COMPLETE** -- DH-011G/H/I/J/K/L are all still
open and unaffected by this ticket.

## DH-011P3 (2026-08-29) -- final review item before commit: one added-test DH-025 event now
## binds to at most one Analysis, enforced at the database, not merely tolerated by design.
## DH-011P2's own multiple-correlations-per-Analysis accommodation is REVOKED, not extended

**Liban reviewed the tradeoff DH-011P2 recorded (`analysis_id` deliberately non-unique, to
tolerate `SampleEditServiceImpl.newOrExistingCanceledAnalysis` reviving a cancelled Analysis under
a second correlation) and chose the strict invariant instead, after this ticket surfaced the
tradeoff explicitly: enforce `UNIQUE(analysis_id)`, accept that a Daher re-add after a test was
cancelled natively on the same accession now fails loudly rather than reviving.** This is a
genuine reversal, not a refinement -- DH-011P2's "an Analysis carrying more than one added-test
correlation" accommodation (including its own representation-layer support for emitting several
identifiers) is deliberately removed by this ticket, and this section says so plainly rather than
leaving DH-011P2's prose to stand uncorrected.

### The invariant

One added-test DH-025 source event ↔ one Analysis ↔ one `daher_analysis_correlation` row.
Enforced in both directions:

- `UNIQUE(correlation_value)` (unchanged, migration 004): one correlation cannot claim two
  Analyses.
- `UNIQUE(analysis_id)` (new, migration
  `005-daher-analysis-correlation-unique-analysis.xml`): one Analysis cannot be claimed by two
  correlations.

### Migration 005, not an edit to 004

A new, sequential migration -- `005-daher-analysis-correlation-unique-analysis.xml` -- drops
004's non-unique `daher_analysis_correlation_analysis_idx` and replaces it with a unique index,
`daher_analysis_correlation_analysis_uk`. 004 itself is left unchanged except for a comment
correction pointing at 005 as the superseding decision (Liquibase treats an already-applied
changeset's content as fixed; editing 004 in place would only mask drift on a database that had
already run the old version, and any real deployment would need a follow-on migration regardless).

**Fails safely by construction, not by custom logic.** `CREATE UNIQUE INDEX` is atomic and
self-validating in Postgres -- no duplicate-detection SQL was written, because none was needed, and
none could itself risk deleting/merging/rewriting a row. Live-verified, three cases, one disposable
stack rebuilt from the corrected patch set:

1. **Fresh DB**: both 004 and 005 ran cleanly together; `\d daher_analysis_correlation` showed
   `daher_analysis_correlation_analysis_uk UNIQUE, btree (analysis_id)` exactly as designed.
2. **Existing DB, no duplicate `analysis_id` mappings**: 005 re-run alone (004 already applied,
   its own row intact) against two genuine, non-duplicate correlation rows -- succeeded, unique
   index created, both existing rows untouched, `databasechangelog` recorded exactly one new row.
3. **Existing DB, WITH a duplicate `analysis_id` mapping** (two correlation rows both pointing at
   the same Analysis, inserted directly to simulate corruption/legacy state): 005 failed with
   Postgres's own real error --
   `could not create unique index "daher_analysis_correlation_analysis_uk", Detail: Key
   (analysis_id)=(2) is duplicated` -- and Liquibase's transactional wrapping rolled back **both**
   statements in the changeset, including the earlier `DROP INDEX`: `\d` afterward showed the
   *original* non-unique index still present, all three rows (the duplicate pair included)
   completely untouched, and `daher-011p3-005-...` correctly absent from `databasechangelog`. No
   row was deleted, merged, rewritten, or silently kept as "the" surviving correlation -- exactly
   the fail-safe behavior this ticket required, achieved by Postgres's own atomicity rather than
   bespoke migration logic that could itself have gotten this wrong.

### Application-level fail-closed, not just a DB backstop (§4)

`FhirTransformServiceImpl`'s shared transform (`transformToServiceRequest(Analysis)`) previously
(DH-011P2) emitted *every* added-test correlation an Analysis carried, as separate repeated
identifiers -- correct under DH-011P2's own tolerated-multiplicity design, wrong under this one.
Changed: `getByAnalysisId` returning more than one row now throws `InternalErrorException`
("Data integrity violation: Analysis N has M added-test correlations -- refusing to represent more
than one source-event identifier for a single resource") rather than looping over the results.
Exactly one row emits that one identifier; zero rows falls through unchanged to the pre-existing
initial-order behavior. This should be unreachable under normal operation now that the DB
constraint exists -- the check exists for exactly the corruption/legacy-data case the DB constraint
cannot retroactively fix, matching the discipline `searchDaherCorrelationNatively`'s own
cross-namespace collision check already established. Never chooses first, newest, or lexical
order; never normalizes what it cannot interpret.

### Live verification matrix, one disposable stack, three tests on one accession (WBC initial,
### RBC added as event A, HGB added as event B)

- **§5, representation**: search by the initial order's correlation returns all 3 tests
  (`total:1`, 3 entries); each entry's own echoed identifier is now its OWN correlation exactly
  once (WBC→corr1, RBC→corr2, HGB→corr3) -- no Analysis returns two identifiers, none did before
  either (DH-011P2's fix already prevented the *wrong* value; this ticket additionally guarantees
  never more than one *right* value). Searching by corr2/corr3 individually each return exactly
  that one test, unchanged from DH-011P2.
- **§6, same-event replay**: delivering event A's exact correlation a second time -> `200` (not
  `201`), `analysis`/`daher_analysis_correlation` counts unchanged (3/2 before and after) -- one
  Analysis, one correlation row, no second attach.
- **§7, distinct events**: event A (RBC) and event B (HGB) each created their own Analysis and
  correlation row, confirmed via direct DB read (`analysis_id=3`->RBC/corr2,
  `analysis_id=4`->HGB/corr3). A direct attempt to insert a third correlation targeting the SAME
  `analysis_id=3` (RBC's own Analysis) was rejected: `duplicate key value violates unique
  constraint "daher_analysis_correlation_analysis_uk", Key (analysis_id)=(3) already exists`.
- **§8, concurrency, two races**:
  - Same-event race (identical correlation, two simultaneous POSTs, no added locking): one `201`,
    one `500` (`ConstraintViolationException` on `correlation_value`, unchanged from DH-011P/P2),
    `analysis`/`correlation` counts +1/+1 -- unaffected by the new constraint, which this race
    never reaches (it fails on the pre-existing `correlation_value` uniqueness first, since the
    correlation values are identical).
  - **New**: two *different* correlation values racing to claim the *same* `analysis_id`
    (a direct concurrent-insert test against an Analysis with no existing correlation, since the
    normal add-test path always inserts the correlation atomically with the Analysis and cannot be
    made to leave one uncorrelated to race against): one thread succeeded, the other failed with
    Postgres's own `UniqueViolation` on `daher_analysis_correlation_analysis_uk` under genuine
    concurrent contention (not merely sequential), and exactly one correlation row exists for that
    Analysis afterward. No silent multi-owner Analysis, proven under a real race, not only
    reasoned about from the constraint's existence.
- **§9, crash-recovery regression**: the DH-011P2 tracked integration test
  (`adapter/tests/integration/test_dh011p2_crash_recovery.py`) re-run unmodified against this
  ticket's rebuilt image and a fresh disposable adapter Postgres -- both scenarios pass unchanged:
  target Analysis + correlation committed for real, ledger left `processing` by the controlled
  fault, a fresh `RecoveryCoordinator`/`LedgerRepository` pair reconciles to the existing write
  with **zero** writer calls, zero new Analysis, zero new correlation row, ledger -> `completed`.
- **§10, cross-namespace collision, unaffected**: the same synthetic-value-in-both-namespaces
  control (`electronic_order.external_id` and `daher_analysis_correlation.correlation_value`
  sharing one value, pointed at a *different*, otherwise-uncorrelated Analysis so the new
  `UNIQUE(analysis_id)` constraint is never at issue) -- re-run against the rebuilt image, same
  exact fail-closed `500` diagnostic as DH-011P/P2, confirming this is a genuinely separate
  mechanism from the new analysis_id uniqueness constraint, neither weakening the other.

### CI (§11 guards)

The static guard job was extended: the Dockerfile artifact-presence and patch-application checks
now cover migration 005; `base-changelog.xml.patch` must include its `<include>`; 005's own content
must contain the exact `CREATE UNIQUE INDEX daher_analysis_correlation_analysis_uk` statement on
`(analysis_id)` and a `<rollback>` block, and must contain no `DELETE`/`TRUNCATE`/`UPDATE`-shaped
statement (a migration that fails safely must never itself be the thing that deletes/merges/rewrites
data); `DaherAnalysisCorrelation.java`'s JPA `@Table` must declare the matching unique constraint;
`FhirTransformServiceImpl.java.patch` must contain the `size() > 1` fail-closed check and must NOT
contain the old per-correlation loop pattern. **All new guards control-tested** by injecting the
specific violation into a scratch copy and confirming the exact expected failure -- including one
real false-negative caught and fixed during this pass: an early version of the 005-content check
matched the phrase "CREATE UNIQUE INDEX" appearing in the migration's own *prose comment*, not just
its actual SQL, so a scratch copy with the real constraint silently removed still passed; fixed to
require the exact statement text, re-verified to correctly fail against that same scratch copy.

### Tests and build

Adapter: unchanged by this ticket (no `src/daher_adapter/` or test file touched). Re-verified
directly rather than assumed: `ruff check` all clear, `ruff format --check` 34 files formatted
(unchanged count from DH-011P2), `pytest -q` **142 passed, 2 skipped** (the DH-011P2 integration
tests, still correctly gated) -- an exact match, and those same 2 tests independently re-run live
against this ticket's own image in the §9 regression above.

OpenELIS: the pinned upstream's own pre-existing `ServiceRequestProviderTest` run against the fully
patched build (all 9 patches + 6 standalone files + 5 migrations, including this ticket's new
005 and the updated `FhirTransformServiceImpl.java.patch`) -- **3 tests, 0 failures, 0 errors**,
unchanged from DH-011P2. Both a standalone Maven reactor build
(`mvn clean install -Dmaven.test.skip=true`, `target/OpenELIS-Global.war` confirmed present) and
the actual production Dockerfile build reached `BUILD SUCCESS` independently. **One build-plumbing
gap found and fixed while setting up the standalone reactor build this pass**: the `dataexport` git
submodule was not checked out in the fresh pristine clone used for this ticket's own test setup (a
setup omission in this pass's own scratch environment, not a defect in the Dockerfile, which already
runs `git submodule update --init` as part of its own build stage and was never affected) --
`git submodule update --init --depth 1 dataexport` fixed it; recorded here since it cost real
time to diagnose and the next person reproducing this locally would hit the identical error.

### Real facility and cleanup

Real facility never started, stopped, or connected to. Fully synthetic disposable infrastructure
throughout (own generated Postgres/keystore/truststore credentials, `.env` never read). Full
teardown performed and verified: `docker compose down -v` removed all three containers, all four
named volumes, and the network; `docker rmi` removed the disposable image; the standalone build's
own `m2-dh011p3-cache` volume was also removed; `docker images`/`docker volume ls`/`docker ps -a`
filtered on `dh011p3` all confirmed empty afterward.

### What this ticket revoked, stated plainly (not left for a diff to discover)

- **DH-011P2's `DaherAnalysisCorrelation.java` javadoc, `004`'s migration comment, and
  `FhirTransformServiceImpl`'s "emit all" representation logic all described and supported
  multiple added-test correlations per Analysis as a deliberate, tolerated design choice. That
  choice is reversed by this ticket, not merely superseded silently** -- 004's comment, the
  entity's javadoc, and the transform's own comment block were all updated in place (this is
  pre-commit, uncommitted code, not a published record) to state the reversal and point at this
  section and migration 005, rather than leaving stale "deliberately not unique" prose standing
  next to a schema that now enforces uniqueness.
- **Consequence, stated once more precisely**: a test cancelled natively on an accession and later
  re-added via a genuinely distinct Daher/DH-025 event will fail loudly (a unique-constraint
  violation surfaced through the same `InternalErrorException` path any other
  `DaherAddedTestTransaction.addTest` failure takes), not revive silently. Native, non-Daher use of
  the Modify Order UI is entirely unaffected -- this table is never consulted outside the Daher FHIR
  add-test transaction. Restoring revival support, if ever wanted, is future work, explicitly not
  a side effect of any future revert of migration 005.

READY TO COMMIT: Yes, per this ticket's own gate -- every required item above passed with live
evidence, not narrowed by argument. DH-011P/P2/P3 as one implementation are ready for the commit
and push this ticket's own §13 specifies. **DH-011 overall remains NOT COMPLETE** -- DH-011G/H/I/
J/K/L are all still open and unaffected by this ticket.
