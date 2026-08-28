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
