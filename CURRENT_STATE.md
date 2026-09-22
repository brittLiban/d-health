# Current State — Daher Health

> **DHealth Connect, 2026-09-22 (DH-CONNECT-CONTRACT-001):** the integration **contract** and the
> **qualification evidence model** are implemented and qualified against SQLite and real PostgreSQL
> 18.6. The **transport is not built and has never run** — no FHIR client, HL7 parser, MLLP, SFTP
> runtime, file watcher, webhook receiver, polling loop, on-premise agent or delivery worker exists,
> and nothing in this repository has ever contacted an external EMR or LIS. **No connector exists
> for any product and none has been qualified against a real system**; the harness runs against a
> synthetic subject that contacts nothing. A CONNECT facility can now hold a registered, genuinely
> qualified connector and still be **unroutable**, because `connector_operational` is
> unconditionally false and blocking until DH-CONNECT-AGENT-001 supplies a real runtime. See
> `docs/CONNECT-CONTRACT.md`, `docs/CONNECT-QUALIFICATION.md`, ADR-056.
>
> **Corrected 2026-09-22, same ticket:** capability evidence now carries an explicit strength —
> `DECLARED` or `EXERCISED` — and a required capability satisfies production qualification only when
> it was exercised. Because `ORDER_RECEIPT` cannot be driven without a transport, **no laboratory
> connector in this build reaches `connector_qualified = true`**, and provider connectors cannot
> either (there is no provider harness). The synthetic suite still passes every scenario it can run;
> "the suite passed" and "production qualification is complete" are now separate statements. See
> ADR-056 decision 23.

> **Current Exchange status (DH-EX-014B, 2026-09-14):** post-release amendment episodes,
> immutable release-version history, provider exact-version reads, and version-bound review routes
> are implemented and qualified against SQLite and real PostgreSQL. Real Keycloak qualification,
> manual live/browser demonstration, and production deployment remain unrun; this does not establish
> pilot readiness.

> **Read this first.** DHealth's current product direction is `DECISIONS.md`'s **ADR-043** and
> **`docs/MVP-1.0.md`** (re-audited 2026-09-13, DH-FIELD-001) — an interoperable diagnostic network
> (Exchange/Clinic/Lab/Patient/Connect), not primarily the maternity/birth-centre system the
> "Assessed: 2026-08-17" section immediately below describes. That section, and most of this file,
> is an honest, unmodified implementation inventory for the **maternity/reference-connector
> track** — real, current, and still binding wherever that track is touched, just no longer the
> definition of DHealth's overall scope. **The DHealth Exchange backend's own current state**
> (DH-EX-001 through **DH-EX-014B** as of this note — organization onboarding, patient/order
> creation, provider- and patient-driven lab assignment, accept/reject/processing, an explicit
> sample-collection milestone, a multi-observation result entry/completion workflow, an explicit
> authorized release step distinct from entry, an immutable pre-release observation-correction
> workflow, an immutable release-version manifest + versioned review foundation, and post-release
> amendment preparation/release under ADR-048 Model 3 — what's
> WRITTEN/TESTED/CI-QUALIFIED/REAL-POSTGRESQL-QUALIFIED vs. NOT BUILT) is inventoried in
> `docs/MVP-1.0.md` §8, not duplicated here; this file's own Exchange-era entries (search "DH-EX-")
> remain as the detailed ticket-by-ticket record behind that summary. **The result data model that
> field evidence (`docs/FIELD-DIAGNOSTIC-WORKFLOW.md`, ADR-046) found inadequate — one ordered
> diagnostic service may need to return multiple observations — is implemented** (ADR-047,
> DH-EX-012C): `LabOrderItem → LabResult` (a container) `→ LabObservation[]` (0..N observations).
> **A pre-release, non-destructive correction workflow for an entered observation is implemented**
> (ADR-045 Questions 1–3, DH-EX-013): an immutable supersession chain
> (`supersedes_observation_id`/`superseded_at`), never a destructive overwrite. **An immutable
> release-version manifest binding every explicit release to its exact observation set, plus a
> version-capable `DoctorReview`, is implemented** (ADR-048 Model 3, DH-EX-014A) — provider reads
> of a released order derive content from that manifest, never live state. **Post-release
> amendment episodes and version releases are now implemented** (ADR-045 Questions 4–5, ADR-048
> Model 3, DH-EX-014B) — lab tech prepares corrections in an amendment episode, authorized releaser
> releases a new version manifest, and a fresh version-bound clinician review is required.
> ~~**DHealth Clinic, DHealth Lab, DHealth Patient, and DHealth Connect are NOT BUILT** as named
> applications anywhere in this repository, on either track.~~ **CORRECTED [DH-STATE-001,
> 2026-09-17]:** a native DHealth Clinic frontend now exists, built and component/UI-tested
> (`ui/src/features/clinic/`), but is not yet runtime-deployable (no wired authentication, no
> browser-safe OIDC client, no same-origin Exchange routing). **DHealth Lab, DHealth Patient, and
> DHealth Connect remain NOT BUILT** — no code of their own exists for any of the three. See root
> **`STATUS.md`** for the current, single source of truth on what is built/blocked/not built across
> the whole repository; this file remains the detailed historical ticket-by-ticket record behind it.

**Assessed:** 2026-08-17 · **Target:** pilot-ready by 2026-09-01 — **historical target date for the
maternity/reference-connector track; see the note above for the current product definition, and
`PROJECT_STATE.md`/the "DH-EX-" entries below for whether it was met**
**Method:** inspection of the actual repository and running environment, not the plan.

---

## 1. Honest summary

**Build steps 1 and 3 now run, and running them produced multiple findings the syntax check could not.**
Step 1 (MariaDB + OpenMRS O3) is verified through REST and FHIR **and by Liban himself in the SPA**.
Step 3 (PostgreSQL + OpenELIS) is verified: three co-tenant databases, 375 base tables in the
fully migrated OpenELIS `clinlims` schema, the webapp on HTTPS, and a FHIR server answering over
mutual TLS. Every image is digest-pinned.

The **DH-008 fail-closed SSO plumbing also runs and is runtime-verified**: the existing Keycloak
realm reconciles idempotently to OpenMRS OIDC and OpenELIS SAML; official OpenMRS OAuth2 Login
1.5.0 is installed in the persistent volume; and disabled mode preserves local authentication
without persisting its client secret. SSO is not active on real staff, and the real-staff one-login
acceptance demonstration is not complete — but a genuine synthetic-identity one-login round trip
**has** now been performed and proven end to end (2026-08-26, ADR-017F closure), on a disposable
environment, not the real facility. The native role/provider **types** are decided (ADR-017); the
`clinician` type was narrowed 2026-08-26 (ADR-017F: `Provider` + `Daher Clinician Admission`, not
`Organizational: Nurse`). What is still missing is which staff hold them, which is a fact from the
birth centre rather than a design choice (BLOCKER-5).

**DH-003 now runs and is empty-volume verified:** French backend properties, the `Maternité`
admission ward, both decided bed types, independent demo-patient/content controls, and a strictly
additive bed seed. Real bed counts remain zero placeholders pending facility input; the current
throwaway stack retains 3 delivery + 4 postpartum beds created only for lifecycle verification.

**DH-010 now runs as well:** the adapter's transport-neutral safety skeleton migrated its real
PostgreSQL database and is healthy with a durable unique ledger, dead-letter table, private audit
volume, transient-only retry policy, and dependency-aware readiness. It does not move FHIR data yet.
The unique claim protects concurrent delivery, but it does not by itself resolve a crash after an
outbound write and before `processing` becomes `completed`; DH-025 now makes that reconciliation
an explicit P0 gate before DH-011 transport activation.

**DH-025's recovery core now runs and is runtime-verified.** The adapter holds a PostgreSQL
advisory fence for its full lifetime; a second process cannot enter recovery or future transport.
Its stable opaque correlation and typed coordinator distinguish an existing target write, proven
absence, and ambiguity; only proven absence may retry. Twenty-seven tests and a rollback-only live
database probe cover concurrent claims and crashes on both sides of the write. The remaining gate
is concrete: DH-011 must attach/query this correlation on the selected OpenELIS resource. No target
resource or clinical message exists yet, so that binding is unverified rather than inferred.

**DH-011E's OpenMRS order poller now runs in tests and is deliberately not activated (2026-08-17).**
The accepted watermark design is implemented: a durable position bootstrapped once from the server's
own clock (ADR-015), a window bounded at both ends, deduplication by `(type, id, versionId)`, a
completeness check against the bundle's `total`, and **client-side ordering** — because probing found
that OpenMRS `fhir2` 4.2.0 accepts `_sort` and silently ignores it. 94 tests and five deliberate
controls. Its output is a transport-neutral `SourceOrderEvent` that names no target, and **no loop
runs it**: BLOCKER-6 has not decided how an order reaches OpenELIS, so there is nothing to deliver
to and no order has moved.

**DH-011B's full spike settled the ingestion question and found the correlation cannot bind at all.**
Authorized by Liban and run with synthetic data only. Confirmed: OpenELIS's business FHIR providers
write its **own domain tables** — synthetic Patient and Practitioner creates produced real
`clinlims.patient` and `clinlims.provider` rows — and `ServiceRequestProvider` calls the same
`SamplePatientEntryService` the human workflow uses. So Option F is not a façade, and it is the
strongest of the five candidates.

Blocked on two things an engineer must not decide: a **numeric facility site code** (accessions are
`%013d` under `SITEYEARNUM`, the prefix is alphabetic `DEV01`, so every order is refused), and the
**adapter correlation, which is discarded on ingestion** — OpenELIS replaces a client-supplied
identifier with its own. ✅ **Corrected 2026-08-17:** exact identifier *search* does work (a real
identifier returns 1; a wrong system with the right value returns 0), so DH-025's contract **is**
expressible and only persistence of the incoming identifier is missing. The first write-up said the
contract was inexpressible, which was wrong in the expensive direction — it would have justified a
larger OpenELIS patch than the evidence supports. Bug 19 adds a third constraint:
`201 Created` is returned even when the persist fails, so read-back is the only trustworthy check.
No `sample`, `analysis` or `electronic_order` was ever created, so laboratory workflow state and
cancellation remain unverified.

**BLOCKER-6 resolved by decision 2026-08-28 (DH-011D/DH-011D2): Liban selected Option F, and the
`ServiceRequest`-owned external identifier as the correlation direction.** This closes the
architecture question; it does not close DH-011. What actually changed in code, honestly split:
**built, tested, and live-verified** — `adapter/src/daher_adapter/openelis.py` (the Option-F HTTP
Basic client, exact identifier search, read-back-verified Patient/Practitioner create defending
against bug 19, and the DH-025 `TargetProbe` binding for `ServiceRequest`), `Settings
.openelis_site_code`/`service_request_delivery_ready` (rejects `DEV01` and non-numeric values, never
invents a digit count), and **three** OpenELIS source patches now, not one:
`ServiceRequestProvider`'s missing `identifier` search parameter, its new inbound
correlation-extraction and `electronic_order` creation logic, and `FhirTransformServiceImpl`'s
outbound identifier echo — plus a real Liquibase migration adding a PostgreSQL partial unique index
(`electronic_order(external_id) WHERE type='FHIR'`). All built from the exact pinned upstream
commit via `openelis-config/Dockerfile`. Live-proven on a fully isolated, disposable three-container
stack (the real stack's database was never touched once this build gained its own schema migration):
the migration applies cleanly on a fresh database; search still goes 400→200 with its scoping and
auth controls intact; and a real two-process PostgreSQL concurrency race against the patched schema
produced exactly the required result — one commit, one constraint-violation rejection, exactly one
row persisted per correlation — with a parallel control confirming existing HL7/CSV duplicate
tolerance is untouched. 126 adapter tests pass (was 122), `ruff` clean. Two new CI static guards
added and control-tested. **What remains unprovable, and only because it needs a real
`ServiceRequest`**: end-to-end create→persist→search against an actual business-API create; the
full Java-layer concurrency race (as opposed to the raw-SQL proof above); live search cardinality
beyond zero-match. Exact accounting: `openelis-config/patches/order-correlation/PROVENANCE.md`.
**Still blocked, unchanged by this pass**: the numeric site code itself (NEEDS LIBAN #7), the
patient-identifier system decision (DH-011G), real requester approval (DH-011H, NEEDS LIBAN #6), and
test mapping (DH-011I, DH-006). The production polling loop remains unwired — this pass did not
touch that, deliberately.

**CORRECTION appended 2026-08-28 (DH-011D3, append-only — the two paragraphs above are superseded,
not retracted).** "What remains unprovable, and only because it needs a real `ServiceRequest`" was
wrong on the "only" — an independent review of the committed DH-011D implementation found three
pre-activation safety gaps (transaction atomicity across the native create and correlation write —
fixed, but only after `@Transactional` on the HAPI provider method itself broke provider
registration entirely, `HAPI-0289`; the correlation search reading the possibly-stale secondary
FHIR store instead of native OpenELIS state — fixed, along with a `Bundle.total` miscount on both
the OpenELIS and adapter sides that would have permanently blocked reconciliation for any order
with more than one test; the delivery writer not existing at all — now implemented, activation
still gated separately) and one further defect this pass fixed
(`electronic_order.external_id` was `varchar(60)`, too narrow for the 64-character correlation
value — every real write would have failed). It also found, but explicitly did not fix, that
**`ServiceRequestProvider.createServiceRequest` cannot create a new order at all** — it only
decorates a `ServiceRequest` against an *already-existing* `Sample`, reached via a `specimen`
reference; accession-number generation never runs otherwise. Proven not to be the site code: a
request with no site code configured and an identical request with a synthetic test-only numeric
site code both failed identically. This is `Human Decision Required: Yes` (Specimen-as-prerequisite
has clinical-surface implications), not an engineering task, and it — not the site code alone —
blocks every control requiring a live `createServiceRequest` success (formal transaction proof,
induced-failure rollback, service-layer concurrency, FHIR-sync-failure, crash-recovery). Full
account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011D3 section.

**CORRECTION appended 2026-08-29 (DH-011M, append-only — the paragraph above is superseded, not
retracted).** Liban approved Option P (2026-08-29): patch the ingestion path to generate a real
accession number via the same generator the human Sample Entry workflow already uses, and let
`SamplePatientUpdateData.createPopulatedSample`'s existing "create new sample" branch run. **Built
and live-proven**, fully synthetic disposable stack, `.env` never read, real facility never
touched: new-order happy path (`POST /fhir/ServiceRequest` with no specimen reference → `201`,
real `SITEYEARNUM` accession, one Sample/SampleItem/Analysis, correlation preserved, discoverable
by the real adapter writer/probe, no fabricated collection facts — `collector`/`quantity`/
`source_id` stay NULL, status reads "entered", never "received"); duplicate delivery (rejected,
zero extra rows); a genuine DB-level concurrent-delivery race (one winner, the loser leaves zero
orphan native rows — Finding A's atomicity holds under real concurrency for a brand-new order, not
just DH-011D2's raw-SQL proof); crash-ambiguity through the concrete `OpenElisClient` writer
against live infrastructure, not a mocked double; and the real `/rest/home-dashboard/
ORDERS_IN_PROGRESS` endpoint (unmodified, pre-existing) showing the new orders with no
collected/received claim. A duplicate-order safety gap was found and fixed during this ticket's
own testing: a *dangling* specimen reference (present but unresolvable) was indistinguishable from
*no* reference and would have silently created a second order for a caller with a typo'd
reference — now fails loud instead. **New finding, not fixed here**: adding a second test to an
*already-existing* order throws `StaleObjectStateException` live. Control-tested against a
completely pristine, unpatched build of the exact same pinned upstream tag/commit — the identical
error reproduced — **conclusively proving this is a pre-existing upstream defect, unrelated to any
Daher patch including DH-011M itself**, not a regression. Out of scope to fix in this ticket;
needs its own ticket and decision. The numeric site code (NEEDS LIBAN #7) is confirmed **not**
the reason new orders couldn't be created — that was proven live this pass — but the real
facility's accession prefix is still the upstream placeholder `DEV01` in every environment tested
and still needs the real value. Full account: `openelis-config/patches/order-correlation/
PROVENANCE.md`'s DH-011M section.

**CORRECTION appended 2026-08-29 (DH-011N, append-only — the paragraph above is superseded, not
retracted).** The "conclusively proving" language above rested on a directly-SQL-inserted fixture,
which DH-011M's own text already flagged as "NOT NATIVE ORDER PROOF" — a hand-written INSERT is
itself a common artificial cause of `StaleObjectStateException`, so the conclusion was correct but
under-proven. DH-011N reproduced the identical failure **live, three times, against a genuinely
native, Hibernate-managed `Sample`** (created through Option P's own path, not SQL) — the defect is
confirmed real. Root cause, proven by elimination and a live DB trace: **detached-entity reuse
across session boundaries**, not a genuine version/precision conflict (a hand-replay of Hibernate's
exact failing SQL via psql matched `UPDATE 1`) and not Hibernate JDBC batching (both
`batch_versioned_data=false` and `batch_size=0` tested live, both still failed identically).
`ServiceRequestProvider.createServiceRequest` resolves the existing `SampleItem`/`Sample` twice
*before* its only transactional boundary (`daherOrderTransaction.persist(...)`) opens — a live
trace showed **seven** separate `SELECT`s of the same `Sample` row in one request, which a single
Hibernate session's cache would never produce. The native "add a test" UI path
(`SampleEditService.editSample`) succeeds on the identical fixture because it is `@Transactional`
end-to-end and only re-saves `Sample` when something actually changed — live-proven, not inferred.
**Still not fixed**: routing the FHIR path through `SampleEditService` requires synthesizing its
replace-shaped form (`existingTests` with cancel/remove flags) from an add-shaped `ServiceRequest`,
an assumption never stated as a decision — `Human Decision Required: Yes`, alongside a second,
independent question of whether DH-025's ledger models "add a test" as its own source event. Both
recorded in `PROJECT_STATE.md`. No production code changed. Full account:
`openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011N section.

**CORRECTION appended 2026-08-29 (DH-011O, append-only — the paragraph above is superseded, not
retracted).** Liban approved both DH-011N decisions: additive semantics (add the requested test,
never implicitly touch existing analyses) and per-added-test correlation (each added test is its
own independent DH-025 source event). DH-011O source-proved the additive-semantics side needs no
synthesized state at all — `SampleEditServiceImpl`'s existing-test handling only ever acts on items
explicitly present and flagged in the submitted form; an item simply omitted is never touched by
any code path, so a pure add-a-test call can omit `existingTests` entirely. That decision is
implementation-ready. The correlation side hit a **structural blocker**: `Sample.clinical_order_id`
is a single scalar column and `searchDaherCorrelationNatively`'s native lookup both requires and
fail-closed-enforces exactly one Sample per correlation — a second `electronic_order` row for an
added test's own correlation cannot be linked without either leaving its own probe unresolvable or
breaking the *original* order's probe the same way. Satisfying the correlation decision needs a
genuine schema/model extension, and which shape it takes — a new column plus a first-ever patch to
upstream `Analysis.java` (design A), versus a new standalone correlation table needing no upstream
Java/`.hbm.xml` patches at all (design B, recommended) — is a decision with real, differently-costly
alternatives that DH-011O has no basis to choose alone. **Human Decision Required: Yes**, recorded
in `PROJECT_STATE.md`'s DH-011O entry. No code written for either design; no disposable
infrastructure was built (both findings came from source alone); real facility untouched. Full
account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011O section.

**CORRECTION appended 2026-08-29 (DH-011P, append-only — the paragraph above is superseded, not
retracted).** Liban selected design B (ADR-031): a standalone `daher_analysis_correlation` table,
zero patches to any existing upstream `Analysis.java`/`.hbm.xml` file. Built and live-verified this
pass: adding a second test to an existing accession now succeeds end to end — the original Analysis
is left unchanged, exactly one new Analysis and one new correlation row are created, no duplicate
specimen, zero `StaleObjectStateException`. This closes the core DH-011N regression this ticket
chain exists to fix. A dual-namespace search extension fails closed if a correlation value is ever
found in both the initial-order and added-test namespaces at once, live-confirmed (500, explicit
diagnostic). Concurrent identical events are resolved safely by the DB's own `UNIQUE
(correlation_value)` constraint alone, no added locking; concurrent different events on the same
accession do not contend at all, also without added locking — both live-confirmed, matching
DH-011O's own concurrency note. `SampleEditService.editSample`'s return type was widened from
`void` (a shared singleton-bean field, a live concurrency hazard) to `List<String>`, the one place
this ticket's changes reach outside the add-test path itself. One real bug was found and fixed by
live testing (a populated `sampleXml` caused a second, spurious Analysis per add-test event); one
gap is recorded but not fixed (the add-test response echoes the *original* order's correlation, not
the add-test event's own, in its `source-event` identifier — safe today because the adapter never
trusts this field, but a genuine representation gap on the response body). **True crash-recovery
(the adapter's own `RecoveryCoordinator` reconciling a target write it received no response for) is
NOT demonstrated** — the adapter was not started this pass; this remains open. Fully synthetic
disposable infrastructure throughout, torn down and verified clean; real facility never touched.
**Not committed or pushed — pending Liban's review.** DH-011 overall remains NOT COMPLETE:
DH-011G/H/I/J/K/L are all still open, unaffected by this ticket. Full account:
`openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011P section; decision record:
`DECISIONS.md`'s ADR-031.

**CORRECTION appended 2026-08-29 (DH-011P2, append-only — the paragraph above is superseded, not
retracted).** An independent review declined to approve DH-011P for commit, naming three gaps; all
three are now closed with live evidence. The representation gap the paragraph above recorded as "not
fixed" **is fixed**: `FhirTransformServiceImpl`'s shared transform now looks up an Analysis's own
added-test correlation before falling back to the initial-order one, live-verified on a 3-test
accession where each added test now correctly carries its own correlation value, not the original
order's. **True crash-recovery, previously NOT demonstrated, is now demonstrated**: since no
production entrypoint assembles `OpenElisClient` + `RecoveryCoordinator` + `LedgerRepository` into a
running delivery loop (DH-011E's poller remains unwired — confirmed by reading `app.py`/
`order_poller.py`), a test-only integration harness (`adapter/tests/integration/
test_dh011p2_crash_recovery.py`, gated behind live-infra env vars, never runs in CI) drives the real,
unmodified production classes through the exact crash sequence: a controlled fault fires after a
genuine OpenELIS commit and before ledger completion; a fresh coordinator/ledger pair (simulating
restart) reconciles to the existing write with zero second writes. A second scenario proves the same
for a lost-response/timeout shape. `updateServiceRequest`'s reachability (DH-011P said "unreachable
via the adapter's transport") is narrowed precisely: unreachable via the adapter's *code* (no
PUT-capable method exists), but reachable by the same credential class the adapter would use
(live-confirmed) — marked UNSAFE / NOT SUPPORTED FOR DAHER V1 and contained by a new CI guard, not a
blocker, since no normal Daher flow can construct the request. A full patch-surface caller review
found a third `editSample` caller (`SampleEditController`, the classic MVC UI) never previously named,
confirmed behaviorally unaffected. **Not committed or pushed — pending Liban's review.** DH-011
overall remains NOT COMPLETE: DH-011G/H/I/J/K/L are all still open, unaffected by this ticket. Full
account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011P2 section; decision
record: `DECISIONS.md`'s ADR-031 closure pass.

**CORRECTION appended 2026-08-29 (DH-011P3, append-only — the paragraph above is superseded, not
retracted; this is the final review item before commit).** The paragraph above records DH-011P2's
own deliberate tolerance of an Analysis carrying more than one added-test correlation (kept so a
natively-cancelled-then-revived test could legitimately retarget the same Analysis under a second
correlation). **That tolerance is revoked, not extended.** Liban reviewed the tradeoff directly:
`UNIQUE(analysis_id)` is now enforced at the database (a new, sequential migration, not an edit to
the already-applied 004), and `FhirTransformServiceImpl`'s transform fails closed
(`InternalErrorException`) rather than emit more than one identifier if it ever finds more than one
correlation for an Analysis — a state the new constraint should make unreachable in normal
operation. Consequence, stated plainly: a test cancelled natively and later re-added via a
genuinely distinct Daher event now fails loudly rather than reviving; native, non-Daher use of the
Modify Order UI is entirely unaffected. Live-verified, one disposable stack: fresh-DB migration
succeeds; migration re-run against an existing DB with non-duplicate data succeeds; migration
re-run against an existing DB with a genuine duplicate `analysis_id` fails via Postgres's own atomic
`CREATE UNIQUE INDEX` — Liquibase rolled back the whole changeset (including the earlier index
drop), zero data touched, zero data destroyed. A direct concurrent race between two different
correlation values claiming the same Analysis was also run: one wins, one gets a real
`UniqueViolation` under genuine contention, exactly one row survives — no silent multi-owner
Analysis. The DH-011P2 real-adapter crash-recovery integration test was re-run unmodified against
the rebuilt image with no regression; the cross-namespace collision control is confirmed a separate,
unaffected mechanism. **READY TO COMMIT: Yes** — this was the last review item; DH-011P/P2/P3
commit and push together as one implementation. DH-011 overall remains NOT COMPLETE:
DH-011G/H/I/J/K/L are all still open, unaffected by this ticket. Full account:
`openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011P3 section; decision record:
`DECISIONS.md`'s ADR-031 final closure.

**CORRECTION appended 2026-08-29 (DH-011J, append-only — the paragraph above is superseded, not
retracted).** DH-011P/P2/P3's "not committed" and "READY TO COMMIT: Yes, pending" status above is
now historical: committed as `05b55b292a3672dd75b7d67bbdb14158fee19066` and pushed to
`origin/master`; CI run `33257047337` — all 8 jobs green, verified directly via `gh run view`, not
assumed. DH-011P is DONE. DH-011 overall remains NOT COMPLETE.

**CORRECTION appended 2026-08-29 (DH-011J, append-only).** Added-test cancellation is now
implemented and live-proven: a new `$cancel` FHIR operation enforces ADR-018's status gate (proceed
only from `NotStarted`, idempotent from `Canceled`, refuse from every other status) — the one guard
the pristine native `deleteServiceRequest` primitive lacks entirely, live-confirmed it would
otherwise silently overwrite a `Finalized`/validated result. Full DH-025 crash-recovery,
idempotency, concurrency, and isolation proof against real disposable infrastructure and the real
production adapter components. Whole-initial-order cancellation was deliberately **not**
implemented — it raises a genuine, still-open Human Decision Required question (does it cascade to
independently-added tests?), recorded in PROJECT_STATE.md. Adapter tests: 161 passed, 4 skipped (up
from 142/2), ruff clean. **Not committed or pushed** — this ticket's own instruction, unconditional,
independent of the work's completeness. Full account:
`openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011J section; decision record:
`DECISIONS.md`'s ADR-018 closure pass.

**CORRECTION appended 2026-08-29 (DH-011J2, append-only).** DH-011J's own narrowed §15 claim
("verified by identical-pattern precedent, not demonstrated at this exact seam") is now superseded:
a live, deterministic Postgres-trigger fault injection (zero Java code changed, the real
`DaherCancellationTransaction` driven unmodified) proved a genuine rollback — post-fault DB state
byte-identical to pre-fault, including the `lastupdated` timestamp. **§15: EXACT FAULT-INJECTION
ROLLBACK: PROVEN.** Isolation/idempotency/status-gate/crash-recovery/concurrency all re-confirmed
unchanged on a freshly rebuilt stack. Separately, Liban's new specimen-collection/receipt
cancellation gate is **blocked**: no reliable native signal exists on the path added-test
cancellation targets — every candidate field is either unconditionally populated with a
non-discriminating creation-time default or permanently unreachable to set by any code path Daher
exercises, live-confirmed and source-cited twice independently. No gate code was written; no
invented proxy signal was shipped. Decision package (three options) recorded in PROJECT_STATE.md.
Added-test cancellation stands exactly as DH-011J left it, unmodified. **Not committed or pushed.**
Full account: PROVENANCE.md's DH-011J2 section; decision record: DECISIONS.md's ADR-018 DH-011J2
addendum.

**CORRECTION appended 2026-08-30 (DH-011J FINALIZE, append-only).** Liban resolved the decision
package above: **option (b)**. V1 added-test cancellation eligibility is **Analysis-status only**
— `NotStarted` → cancel, `Canceled` → idempotent, every other status → refuse, zero mutation.
Specimen-collection/receipt gating is **deferred, not implemented** — explicitly not a claim that
a specimen has or hasn't been physically collected, only that Daher cannot observe that fact
reliably through the path it uses today. No code changed by this decision: the implementation
DH-011J built and proved was already exactly this contract. Initial-order cancellation remains
unsupported; its cascade question remains a separate, open Human Decision. Decision record:
`DECISIONS.md`'s ADR-018 DH-011J FINALIZE resolution. **COMMITTED AND PUSHED 2026-08-30**:
`86f26dfb28ecffae83a3c96adb5a691aaee909a4` on `origin/master`; CI run `33298838019` — all 8 jobs
green, verified via `gh run view`. **DH-011J is DONE for V1 added-test cancellation.** DH-011
overall remains NOT COMPLETE. DH-011E remains NOT ACTIVATED.

**DH-027B closed an orphan in facility creation (2026-08-17):** the host was claimed before the facility record was inserted, so a failed insert left that host permanently marked occupied by a facility that never existed. Compensation is now conditional on the exact facility id — it cannot evict a live facility — and the audit log records `FAILURE` with whether the rollback actually happened. 17 failure-injection tests across both the in-memory and SQLite backends.

**DH-027D made fleet health observed rather than assumed (2026-08-20).** The control plane can now
truthfully answer "is this facility's stack healthy *right now*", separately from "what lifecycle
phase is it in". Previously `/status` fabricated its answer — zero counts with a **fresh
`checked_at`**, implying a check that had never happened. A real check now runs two read-only
compose commands through the existing agent, derives health from parsed service states rather than
the command's exit code, and is persisted durably. A STATUS report carrying no evidence, or evidence
that fails validation, is stored as **UNKNOWN, never HEALTHY** — health cannot be asserted by
omission. Run against the live stack it first reported `degraded, 10/11, caddy unhealthy` — a real fault,
surfaced rather than hidden. **Bug 20 was then diagnosed and fixed (2026-08-20)**, and the same parser
now reports `healthy, 11/11`. ⚠️ No scheduler exists: an observation is only as fresh
as the last request, and the portal shows its age for exactly that reason.

**Bug 18 fixed — the agent now polls while a job executes (2026-08-20).** `execute` used to run
inline inside the poll loop, so a `provision` blocked polling for up to 5400s and anything queued
in that window was refused as stale once the loop resumed (900s replay window, ADR-022). `execute`
now runs on a background thread; `status` (read-only) always runs, including alongside an in-flight
mutating job, while every mutating verb shares one slot so at most one runs at a time — a second
one claimed mid-flight is refused immediately rather than racing two `docker compose` invocations
against the same project. `JobValidator` still runs synchronously before dispatch; the staleness
window itself is unchanged. Fixing this exposed a real gap in DH-027C's lifecycle table: concurrent
polling makes it possible for a duplicate `PROVISION` to be claimed and refused while the real
attempt is still running, and the table previously treated any non-`succeeded` report as `DEGRADED`
— which would have mislabeled a healthy facility, permanently, since that row only fires from
`PROVISIONING`. `next_facility_state` now only degrades `PROVISION` on a genuine `failed` report;
`refused` is a no-op, matching every other row. 11 new tests, 464 provisioning tests total (453
before), Ruff clean. Control-tested, three deliberate breaks on a throwaway copy: disabling the
busy-refusal check fails 3; restoring the old binary lifecycle row fails 2; reverting `poll_once`
to call `execute` inline again — reproducing bug 18 exactly — fails 6 of 7 (the one unaffected
test doesn't depend on concurrency). Not exercised against a real ~50-minute boot — proven at the poller/queue
level, not against real `docker compose` timing.

**DH-027G wired backup configuration through to the real path (2026-08-21).**
`BackupSettings`/`render_env` (ADR-025) were fully built, but `ComposeExecutor._provision()` never
passed `backup=` to `render_env` — a real provisioning run wrote no backup section regardless of
host configuration. Closed: the operator's passphrase and off-site destination now reach the agent
through its own process environment at bootstrap (same manual step as `AGENT_CREDENTIAL`), and
`ComposeExecutor` forwards them. `BACKUP_NOW` now checks first and answers honestly — `not_configured`
without ever attempting the command on an unconfigured host, `success`/`failed` from a real attempt
otherwise — the same convention DH-027D's `STATUS` already uses. `/facilities/{id}/status`'s
`backup_state` was **hard-coded** to `never_run`; it now reflects the latest such report, persisted
durably (`SqliteBackupStore`). The control plane still never receives the passphrase or bucket name
— only a three-value outcome and a reason. 44 new tests, 508 provisioning tests total (464 before),
Ruff clean. Control-tested, four deliberate breaks: disabling the not-configured refusal fails 5;
malformed evidence reported as success instead of fail-closed failure fails 2; reverting the
`_provision()` wiring (the exact original defect) fails 1; reverting the portal's risk banner to not
recognise the new state fails 1. ⚠️ Still not covered: the nightly scheduler inside the backup
container runs independent of the job queue, so `backup_state` reflects the last *requested* check,
not necessarily last night's real backup, until something reads `last-run.status` into a report —
deliberately not built in this ticket. Not exercised against a real S3-compatible target or a real
`docker compose run --rm backup` — DH-014 already owns that verification boundary (local path
verified, off-site unexercised) and this ticket proves the wiring reaches it, not backup mechanics.

**DH-027H closed that exact gap: STATUS now observes the nightly scheduler's own evidence
(2026-08-21).** `backup/scheduler.sh` runs inside the backup container, independent of the job
queue — before this, the control plane only ever learned about backup from an explicitly-issued
`BACKUP_NOW`, so a facility could show a green manual test from last week while the real nightly
backup had silently failed every night since. `STATUS` now also reads
`${BACKUP_OUTPUT_DIR:-/backup}/last-run.status` — the exact command `backup/README.md` already
documented for a manual check — via one more fixed, read-only `docker compose exec` (no new job
verb; unconfigured hosts skip it entirely, same as `BACKUP_NOW`'s existing check). Parsing is
strict and fail-closed (`daher_agent/backup_observation.py`): anything malformed, absent, or
future-dated beyond a 60s tolerance becomes `NEVER_RUN`, never `SUCCESS`. The reported timestamp
is the backup's own run time parsed from the marker, not when the agent happened to check.
`backup.sh`'s status write is now atomic (temp file + rename) rather than truncate-in-place, since
the control plane reads it now, not just a human running `cat` by hand. Because two independent
paths (manual and scheduled) can now report evidence out of order, `SqliteBackupStore`/
`InMemoryBackupStore` reject a report older than what they already hold — real newer evidence,
good or bad, always wins; a stale replay cannot regress the fleet's displayed state. 41 new
tests, 549 provisioning tests total (508 before), Ruff clean. Control-tested, four deliberate
breaks: the parser always returning SUCCESS fails 2; removing the out-of-order guard from both
stores fails 5; trusting the exec command's exit code over the marker's actual content fails 5;
a successful observation wrongly setting the restore-drill date fails 2. ⚠️ Not exercised against
a real nightly run — proven against the documented command and file format with synthetic marker
text. DH-014's off-site boundary is unchanged.

**DH-027C gave facilities a real lifecycle (2026-08-17):** nothing ever set `RUNNING` or
`DEGRADED`, so every facility stayed `PROVISIONING` forever. One explicit transition table now
advances state, driven only by an agent report about work it actually ran — never by a job being
queued. `RUNNING` means "the agent reported `docker compose up` returned 0", which given this stack's
health-gated dependency graph is more than "containers created" but is still one claim at one moment,
not continuous health. That remains DH-027D.

**DH-027E made decommission real (2026-08-17):** the endpoint set `DECOMMISSIONED` on request and
queued nothing, so the fleet reported a facility as stopped while its stack was still serving. There
is now a `DECOMMISSIONING` state, a STOP job queued to the host that actually holds the claim, and the
terminal state is reached only on that agent's successful report. The host claim is deliberately
**not** released — the patient database is still on that host.

**DH-027F made facility env writes atomic (2026-08-17):** the writer used `O_TRUNC`, so a death
mid-write left a torn env file — a live facility's credentials, on the rotation path — and there was
no `fsync`, so a power cut could leave a partial file after a successful-looking write. Now
write-temp-then-rename with contents fsynced before the rename. ⚠️ Rotation *rollback* is still open:
the write is atomic, so the file is never torn, but a failed `up` afterwards leaves the new file in
place. Restoring the old one is a credential-custody question, not a code detail.

**CI now exists (DH-027M, 2026-08-17) and currently gates 678 tests.** Until this landed, every
verification claim in `PROJECT_STATE.md` depended on a human remembering to run something.
`.github/workflows/ci.yml` runs the adapter (94), provisioning (549), seed (16) and form-safety
(19) suites plus Ruff, compose
interpolation on both profiles, shell/JSON/XML/CSV syntax, image pinning, a tracked-secret scan, the
`.env`-untracked check, and relative-link resolution. Four guards were control-tested by breaking
them, and running the checks found **three** defects in the checks themselves — the third being that
the secret scan flagged the workflow's own dummy values, which is how the first GitHub run failed.
**All five jobs are now green on GitHub in 23 seconds** (run `32031078854`), so this is demonstrated
rather than written. The ~50-minute clinical cold boot is deliberately excluded.

**DH-024's fresh-deployment audit closed three false-green paths.** The OpenMRS demo-patient
property had two competing writers and injected a second 50-patient batch into this throwaway
volume despite the local switch returning to `off`; the derived image now removes only upstream's
competing trigger. Direct first-boot `on/on` also reset the trigger with zero patients, so a
read-only metadata preflight now enforces the demonstrated two-stage opt-in path. PostgreSQL
readiness no longer goes green against its temporary init server.
OpenELIS's documented `DEFAULT_PW` is ignored, so a verified post-bootstrap bcrypt rotation now
gates the frontend. Both current and empty-volume OpenELIS login tests accept the configured secret
and refuse `adminADMIN!`.

**DH-008's 2026-08-13 quality pass corrected two false-green paths before documentation was
updated.** Keycloak initially tried to import partial mapper JSON as complete realms; the fragments
now live outside the import directory. The OpenMRS OMOD initially existed only in the image
distribution layer and therefore did not reach an existing data volume; the runtime wrapper now
installs the exact release atomically. Existing-realm reconciliation is idempotent, fresh realm
import passed in an isolated database, all service endpoint/authentication checks passed, and the
final stack has zero restarts/OOM kills. SSO remains intentionally disabled pending BLOCKER-5.

**Post-commit quality audit 2026-08-13:** all local image paths rebuilt, both Python suites passed
again (adapter 14, maternity 5), all Compose/static/document checks passed, and the normal stack
returned to ten running services plus six successful setup jobs. Authentication, both FHIR
metadata paths, OIDC/SAML metadata, adapter service credentials, all nine PostgreSQL tenancy
cases, aggregate persistence, UTC+3, host-port confinement, Keycloak fresh import, repeated
reconciliation, and persisted-realm restart all passed. The log/code review expanded BLOCKER-4 to
both shipped Horiba CBC imports and exposed DH-025's in-flight crash ambiguity. No clinical
message is transported today, so the DH-025 defect has not affected a patient or test record.

**Post-decision quality audit 2026-08-13.** After ADR-015 and ADR-016 landed, the full check ran
again: both Compose profiles, all tracked shell/JSON/XML syntax, Git integrity/whitespace, and the
untracked-`.env` check passed; every image is digest-pinned once the locally built `daher-health/*`
images are resolved to their pinned bases. Adapter `pip check`/Ruff/**27 tests** and maternity
**5 tests** passed. Ten services run with six setup jobs at exit 0 and **zero restarts or OOM kills
across sixteen containers**. Configured OpenMRS and OpenELIS logins pass while `Admin123` and
`adminADMIN!` are refused; both FHIR metadata paths answer, OpenELIS FHIR only over mutual TLS and
correctly rejecting a missing client certificate; all nine PostgreSQL tenancy cases hold; every
container is `EAT+0300`; only `127.0.0.1:8080` is published; the adapter's ledger and dead-letter
tables remain empty; and OAuth 2.0 Login 1.5.0 is registered with `started=false`. SSO stays
fail-closed and no clinical message has moved. Probe-method notes and one corrected false alarm are
in `PROJECT_STATE.md`.

**What running it cost, and what it exposed** (details in §3a):

1. The gateway could not reach the backend at all — it hard-codes upstream hostnames.
2. The backend healthcheck reported **healthy 8 seconds in**, while the application was unusable.
3. `OMRS_CONFIG_ADD_DEMO_DATA` is inert in this image.
4. **The distro created 50 fictional demo patients** — the hazard `.env.example` warned about,
   reached through a mechanism nobody had identified. See BLOCKER-2 in `PROJECT_STATE.md`.
5. A cold boot takes **≈49 minutes**, nearly all of it importing 23 OCL concept packages.

Step 3 added four more (DH-005's ticket entry): `certs` cannot be dropped as the architecture
claimed; the webapp needs a Docker secret or its context dies while Tomcat still reports success;
the FHIR container ignores `JAVA_OPTS`; and its FHIR endpoint requires **mutual TLS**.

**The honest boundary:** substantial buildable work remains. OpenELIS local-admin authentication is
form-verified, but **no human has ever driven its browser workflow** — Caddy now gives it a host
route (DH-016), so that check is possible and simply has not been done. SSO activation and one-login
proof, maternity clinical forms, adapter transport, notifications, off-site backup and a re-run
restore drill, and publicly trusted TLS all remain incomplete. The maternity ward/bed foundation
itself is complete. Human decisions are collected under **NEEDS LIBAN** in `PROJECT_STATE.md`.

**BLOCKER-10 / ADR-030's "Terminer l'admission" (UI-015F, 2026-08-28): closed — code complete and
live-proven, not yet committed, reviewed, or deployed to the real facility.** A Daher-owned OpenMRS
module and its React frontend (all eight required UI states — ACTIVE/CONFIRMATION/SUBMITTING/ENDED/
ALREADY_ENDED/AMBIGUOUS/UNAVAILABLE/ERROR — individually tested) both run and are proven against the
disposable stack: live rollback proof (an external DB trigger, not a code change), live
true-concurrency proof (a real race behind a thread barrier, exactly one audit row written), three
live success cases with exact recorded DB values, exhaustive authorization proof (privileges
*enumerated*, not name-checked — exactly 9/3, zero roster change), a real CSRF-scoping control
(a sibling module's own servlet path still denied), and a payload-injection control (attacker-supplied
fields silently ignored). 236 UI tests and 18 module JUnit tests passing, full lint/typecheck/build/
secret-scan/`git diff --check`/`docker compose config` clean, all re-confirmed against the same
from-source Docker rebuild (module + the CSRFGuard WAR patch, both baked in at build time) as the
prior pass, since no backend Java changed this pass. **This is code-complete, not facility-live** —
nothing here has been committed, pushed, reviewed by Liban, or deployed to the real Daher birth
centre; no real patient or staff record was ever touched. One disclosed, deliberately-deferred item
remains: a genuinely-fresh-database boot was attempted with a real controlled pair (this image vs. a
pre-ADR-030 image, both against independently fresh databases) and found both hitting the identical
`stockmanagement` Liquibase changeset at the identical duration — confirming the slow phase is a
shared-platform characteristic, not this module's — so the specific "does the audit type provision
exactly once from true scratch" check is carried as `DH-017` evidence debt (ADR-029 precedent), not
treated as a blocker. Full evidence in `PROJECT_STATE.md`'s BLOCKER-10 (now ✅ CLOSED) and
`DECISIONS.md`'s ADR-030 (now `ACCEPTED / IMPLEMENTED`).

---

## 2. What is running

| Service | State |
|---|---|
| MariaDB | **running, healthy, verified** — 10.11.18, digest-pinned |
| OpenMRS backend | **running** — boots, migrates, serves REST + FHIR R4; configured admin password works; official OAuth2 Login 1.5.0 is registered and deliberately stopped by `SSO_ENABLED=false` |
| OpenMRS frontend | **running** — container healthy; Liban registered a test patient in the SPA |
| OpenMRS gateway | **running** — routes to backend and frontend after the alias fix |
| OpenMRS demo preflight | **one-shot job exited 0** — default patients-off path is inert; opt-in mode requires initialized `OpenMRS ID` metadata before backend startup |
| OpenMRS maternity config | **one-shot job exited 0** — French backend metadata, admission ward, both bed types, additive-only bed lifecycle verified; committed counts are `0/0` placeholders |
| PostgreSQL | **running, healthy, verified** — 14.4 (OpenELIS's image), 3 co-tenant databases; DH-020 denies all cross-tenant connections |
| OpenELIS webapp | **running, healthy, with configuration warnings** — serves HTTPS 8443; configured admin form login passes and `adminADMIN!` is refused; SAML properties are present but disabled; no browser workflow performed; upstream Horiba Micros60 and Pentra60 CBC imports abort on a duplicate target description (BLOCKER-4) |
| OpenELIS admin password | **one-shot job exited 0** — runs after genuine webapp readiness, updates exactly one admin bcrypt hash, verifies it, and gates the frontend |
| OpenELIS FHIR | **running** — `GET /fhir/metadata` → 200 over mutual TLS, refused without a client certificate. HAPI 6.6.0, FHIR 4.0.1. Now presents DH-021's locally generated certificate (valid to 2028-11-16), not upstream's public and already-expired one |
| OpenELIS frontend | **running; internal HTTP verified** — no browser login or workflow exercised; it has no host route until Caddy (DH-016) |
| Keycloak | **running, healthy, realm + DH-008 clients verified** — OpenMRS OIDC, OpenELIS SAML; existing-realm reconciliation passed twice; pilot session lifetimes approved (ADR-013) |
| Keycloak SSO config | **one-shot job exited 0** — non-deleting client/mapper reconciliation; idempotency exercised twice |
| Adapter | **running, healthy, DH-010 + DH-025 + DH-011E built and verified** — ledger/dead-letter/retry/audit, process-lifetime recovery fence, fail-closed crash coordinator, and now the OpenMRS order poller with its durable watermark (94 tests, migration `0002_watermarks` applied live). ⚠️ **The poller is not activated**: no compose service or `app.py` path starts it, and there is no delivery implementation because BLOCKER-6 is undecided. No order has moved |
| WhatsApp notifier | not written |
| Backup | **not running in the current stack** — deliberately behind Compose's `backup` profile until approved production settings exist. The local path is verified end-to-end, and **a restore has now actually been performed** (DH-015, `backup/restore-drill.md`): archive decrypted, all seven manifest hashes re-verified, restored into an isolated scratch stack, and patient `10001MJ` read back through the OpenMRS REST API. Off-site upload remains unverified; the drill archive and passphrase were destroyed afterwards. Two acceptance legs (a lab result, an SSO login) remain unexercised, so DH-015 is **performed, not done** |
| Caddy | **running, healthy — DH-016 built and locally verified** — subdomain routing for `emr`/`lab`/`id`, security headers, and a TLS-**verified** hop to the OpenELIS API using DH-021's exported CA (control-tested: an unrelated CA yields 502 `unknown authority`). Uses Caddy's internal CA under `BASE_DOMAIN=localhost`; public issuance still needs the real domain/DNS |

⚠️ **This local stack contains 100 fictional demo patients** plus two deliberate test patients (one
registered via the API, one by Liban in the SPA). It is a throwaway verification stack and must
never be confused with a clean instance. The second 50-row batch was the evidence that exposed
DH-024's competing-property bug; no patient rows were deleted during the repair.

**Build steps 1 and 3 are done; the DH-003 maternity foundation, Keycloak provider, and DH-008
protocol plumbing are built and verified.** The clinical forms in the larger build step 2 remain
DH-004 and require human sign-off. SSO activation/one-login proof and the remaining steps are not
complete.

---

## 3. What is configured but unverified

| Artefact | State | What "unverified" still means here |
|---|---|---|
| `docker-compose.yml` | **Booted and corrected** | Healthchecks, the `mariadb` CLI name, `${VAR:?}` guards, backend↔db handshake, DH-003 dependency gate, and normal one-command startup are exercised. The OpenMRS healthcheck now also gated a genuine empty-volume DH-003 boot. |
| `.env.example` | Used to boot | `OMRS_ADMIN_PASSWORD` is wired and **verified on an empty volume** (DH-019). OpenELIS's narrower password alphabet is documented; its configured secret is verified on current and empty volumes (DH-024). The old local credentials were rotated. Local `.env` is valid Compose syntax and mode `0600`. |
| Image digests | **Pinned from real pulls as each component landed** | Dates and exact values are recorded in `docs/ARCHITECTURE.md` §12. Reproducible now. |
| O3 SPA in a browser | **Exercised by Liban 2026-08-11** | He registered a patient through the SPA. French is configured and verified in frontend env + backend properties, but the visible browser language was **not** reported either way. |
| OpenELIS UI | **Authentication form exercised; browser workflow not exercised** | Configured admin login succeeds and the published default is refused on current and empty volumes. Its frontend still has no host route until Caddy (DH-016). |
| OpenELIS FHIR *delivery* | **Advertised, not proven** | Its CapabilityStatement offers `Subscription` with `create`, but no subscription has been created and no delivery observed — see ADR-005 Q4. |
| SSO | **Plumbing and synthetic one-login proof runtime-verified; real activation unverified** | OpenMRS OIDC module is registered/stopped in disabled mode; OpenELIS is configured for SAML; Keycloak client reconciliation is idempotent. Role mapping is decided, `clinician` narrowed 2026-08-26 (ADR-017F); a synthetic-identity one-login round trip (disposable environment) is proven. BLOCKER-5's staff-roster gap prevents real activation and a real-staff one-login proof. |
| **DH-011E order poller** | **Unit-verified against injected transport; never run against live OpenMRS** | 94 tests and five controls prove the cycle's safety properties — ordering, completeness, dedupe, halt-before-advance. What is *not* proven: a real poll against the running server, and any delivery at all. The bounded-window and `_sort` behaviours it relies on **were** probed live before the code was written. Deliberately unwired, so "not activated" is a property of the deployment, not a promise. |
| **Migration `0002_watermarks`** | **Applied live and inspected** | Table, columns, and the unique `(direction, resource_type)` index exist in the real adapter database; 0 rows, because nothing polls. |
| `docs/ARCHITECTURE.md` | Current | §3's stale "MariaDB 11.4" row corrected to 10.11 at DH-002; §6 and §16 brought current 2026-08-17. |

## 3a. What running it actually exposed

| # | Finding | Evidence | Status |
|---|---|---|---|
| 1 | Gateway 502 on every request — its nginx template hard-codes `http://backend:8080` and `http://frontend`, which do not resolve to our service names | `backend could not be resolved` in the gateway log; `/etc/nginx/templates/default.conf.template` in the image | **Fixed** — network aliases, chosen over renaming services or hand-editing upstream's config (ADR-010 reasoning) |
| 2 | Backend healthcheck reported `healthy` 8s into the first boot, while Liquibase ran and the gateway 502'd | `docker compose ps` vs. a 502 from the same instant | **Fixed** — explicit `= 200` test on the REST session endpoint. `curl -f` was the root cause: it passes on the 302 that URL returns during boot |
| 3 | `OMRS_CONFIG_ADD_DEMO_DATA` is not read by the image | Absent from the `OMRS_*` names in `startup-init.sh` / `startup.sh` | Documented as inert in compose and `.env.example`; **not** removed |
| 4 | **50 fictional demo patients created on first boot; later a second 50-row batch appeared despite `off`** | Initial DH-002 count 51; DH-024 aggregate count 102 = 100 rows by the demo creator + two deliberate patients. The deployed tree showed upstream `value=50` and our `value=0` competing for the same property | **Fixed at DH-024** — the pinned derived image removes only upstream's trigger block, leaving the mounted `on/off` file as sole writer. An `off` restart held 102→102; no patient rows were deleted |
| 5 | Cold boot ≈ 49 min, dominated by 23 OCL concept-package imports | Deployment start 06:36:49Z → first HTTP 200 at 07:26:08Z | Documented; healthcheck timings derived from it |
| 6 | No OpenMRS admin password variable; image defaults to the published `Admin123`, applied **first boot only** | `OMRS_ADMIN_USER_PASSWORD=${OMRS_ADMIN_USER_PASSWORD:-Admin123}` in `startup-init.sh` | **Fixed and verified on an empty volume — DH-019** |
| 7 | Existing throwaway OpenMRS volume still accepted `Admin123` because its container/DB predated DH-019 | Authenticated session probes against both configured and published passwords | **Fixed in live state 2026-08-13** — rotated through OpenMRS's password API; container recreated with current Compose env; configured secret passes and default is refused |
| 8 | OpenELIS startup repeatedly aborts both `horiba-micros60-cbc.csv` and `horiba-pentra60-cbc.csv` while attempting to give different rows the existing unique description `LYM#(Whole Blood)` | Repeated `test_desc_uk` violations followed by failed test-editor bridging. Live table has zero duplicate descriptions, so each transaction rollback protects uniqueness but both analyzer imports are incomplete | **BLOCKER-4 — Human Decision Required: Yes.** Do not alter laboratory tests until Liban confirms whether either analyzer is in facility scope |
| 9 | OpenELIS PostgreSQL health went green against the temporary `initdb.d` server; hardening then hit connection refused during its deliberate shutdown | Reproduced from empty volumes: `pg_isready` passed, hardening exited 2, final server started afterward | **Fixed and empty-volume verified — DH-024.** Health also requires PID 1 to be `postgres`; hardening exited 0 on the exact from-zero graph |
| 10 | OpenELIS ignores `DEFAULT_PW`; fresh volumes still accepted published `adminADMIN!` | Actual CSRF/form login on both old and empty volumes; configured secret refused before repair | **Fixed and empty-volume verified — DH-024.** Post-webapp one-shot rotates/verifies bcrypt; frontend waits for exit 0; configured login passes and default is refused |

Known upstream oddities observed and deliberately not chased, recorded so they are not later
mistaken for new breakage: `AddressConfigurationLoader` logs an ERROR that the distro's own
`addressConfiguration.xml` "appears invalid" and skips it; and the gateway config carries a
`/grafana → grafana:3000` block for a service this stack does not run (relevant at DH-016 — it
should not be exposed publicly). The pinned OpenMRS base itself runs as UID 1001 while its
root-owned Tomcat `conf` directory lacks `Catalina/localhost`, so Tomcat logs a non-blocking SEVERE
when it cannot create that deployment directory; the same permissions are present in the untouched
upstream image and REST/FHIR still pass. Keycloak startup/restart makes a failed PostgreSQL
`aurora_version()` compatibility probe before continuing healthy against ordinary PostgreSQL.
OpenELIS also logs that the optional Odoo test-product mapping CSV is absent; this stack has no
Odoo integration and v1 billing is out of scope, so no mapping was invented. Treat these as known
non-blocking startup behaviours only while the corresponding live endpoints continue to pass.

---

## 4. Blockers

**Blocker numbering lives in `PROJECT_STATE.md`.** This section summarises; that file is the
source. (Earlier revisions of this file numbered blockers independently, which produced two
different "BLOCKER-2"s. Fixed here — do not renumber in this file again.)

### BLOCKER-1 — Docker daemon unreachable · ✅ RESOLVED 2026-08-10

`libanbritt` was not in the `docker` group. Resolved by `usermod -aG docker $USER` plus a
logout/reboot. Docker has since pulled images and run the full stack, so this is confirmed
resolved by use, not just by inspection.

**Still live as a question for the pilot VM:** `docker` group membership is effectively root on
the host. Decide it deliberately there rather than by habit.

### BLOCKER-2 — The O3 distro creates 50 fictional demo patients · ✅ RESOLVED + HARDENED DH-024

`OMRS_DEMO_PATIENTS` now selects an explicit `off` (default) or `on` Initializer overlay at the
distribution source. The derived image removes upstream's competing trigger, so this overlay is
the sole writer. This is separate from the inert `OMRS_ADD_DEMO_DATA` variable and from
`OMRS_DEMO_CONTENT=on|off`. Patient generation requires one completed content-on/patients-off boot
first because upstream runs its generator before first-boot metadata initialization. A database
preflight rejects unready `patients=on` states. The isolated two-stage path produced exactly 50
patients, one creator, and one reset trigger. Neither control deletes stored data.

### (Not numbered) — ADR-005 · ✅ ACCEPTED 2026-08-12 · adapter skeleton unblocked

Both legs have primary evidence (DH-009a, DH-009b), and Liban approved the custom adapter with a
configurable 15-second OpenMRS order poll, inclusive watermark, and durable ledger. OpenELIS runs a
full HAPI server that advertises `Subscription`, but delivery and corrected-result behavior still
must be demonstrated before trusting the result path. DH-010 built and runtime-verified the safety
foundation, and DH-011E has since built and verified the watermark and poller themselves
(2026-08-17) — **unactivated**, because BLOCKER-6 leaves them with nothing to deliver to. One
implementation addition to the accepted design is recorded in `WATERMARK-DESIGN.md`: the poll window
is bounded at **both** ends, because an open-ended top makes the completeness check fire on healthy
traffic.

### BLOCKER-3 — DH-011 activation and cancellation contract · ✅ CLOSED BY DECISION 2026-08-14

All three legs are decided: **first sync** (ADR-015 — no historical backfill, watermark starts at
deployment time), **requester** (interim 2026-08-14 — a controlled "Pilot Test Clinician" replaces
upstream's sample until the meeting names the real one), and **cancellation** (ADR-018 — revoke if
the laboratory has not accepted or started the test, leave in-progress work for manual handling).

**DH-011 is therefore no longer blocked on decisions**, which makes it the critical path. What
remains is implementation plus two *verification* gates: DH-025's correlation must be attached to
the concrete OpenELIS resource, and ADR-018's "accepted or started" must map to a **verified**
OpenELIS status rather than an assumed one. No poller or clinical order has been activated.

### BLOCKER-4 — OpenELIS Horiba Micros60/Pentra60 analyzer imports · ✅ CLOSED BY SCOPE (ADR-016)

v1 is manual result entry only — no analyzer, HL7, or ASTM interface; DH-023 is deferred to v1.1.
The webapp remains reachable and both upstream analyzer CSVs still fail on the same unique
description, **left exactly as upstream ships them**: each transaction rolls back, the live table
has zero duplicate descriptions, and the endpoints answer. ADR-016 does not authorize disabling,
renaming, repairing, or log-suppressing those tests. No test or mapping has ever been changed.

### BLOCKER-6 — how an order actually enters OpenELIS · 🛑 OPEN 2026-08-16

Found by probing before writing DH-011's transport code. OpenELIS **pulls** orders from a remote
FHIR source it is configured to trust (`remote.source.uri`, currently empty, polled every 120s) — it
does not accept a push into its own FHIR store, which is where every document here implicitly
assumed the adapter would write. Its `electronic_order`, `sample` and `analysis` tables are all at
**0 rows**: the laboratory has never received an order.

There are now **five** candidates (A–D, plus F added 2026-08-16 as ADR-026). They change what the
adapter *is* — one removes the ledger/audit boundary ADR-005 made load-bearing for patient safety,
one turns the adapter into a FHIR server, one adds a second system storing patient data — so this is
a human decision, recorded in `PROJECT_STATE.md` with the evidence.

**Option F is fully spiked as of 2026-08-17 (DH-011B, authorized by Liban) and is the strongest
candidate — but it cannot be completed here.** The business providers write OpenELIS's **own domain
tables**: synthetic Patient and Practitioner creates through the supported FHIR surface produced real
`clinlims.patient` and `clinlims.provider` rows, and `ServiceRequestProvider` calls the same
`SamplePatientEntryService` the human sample-entry workflow uses. It is **not** a FHIR façade over the
generic store, which was the question worth answering.

**Two gates remain, neither an engineering choice.** (1) Every ServiceRequest is refused with
`Generated lab number is invalid`: `acessionFormat = SITEYEARNUM` formats accessions as `%013d` —
thirteen all-numeric digits — while the configured prefix is upstream's alphabetic `DEV01`, so a
**numeric facility site code** is needed (NEEDS LIBAN #7). Supplying an accession does not help; the
value is always server-generated. (2) The adapter correlation **does not survive**: our identifier is
discarded (`external_id` NULL) and search is delegated to `http://fhir.openelis.org:8080`, which this
stack does not serve — so a submitted identifier and a never-submitted control **both return
`total: 0`**. DH-025's 0/1/2+ contract is therefore inexpressible through this surface. → **DH-011D**.

Also note bug 19: `PatientProvider` returns **`201 Created` even when the persist fails**, so any
target implementation must confirm by read-back, never by status code.

⚠️ **Never reached, and not claimed:** no `sample`, `sample_item`, `analysis` or `electronic_order`
row was created, so native laboratory workflow state, the OpenELIS UI's view of an incoming order, and
cancellation behaviour all remain **unverified**.

⚠️ **This throwaway stack now also contains two synthetic OpenELIS records**, both created through
the supported FHIR surface by DH-011B and deliberately retained rather than deleted: patient `id=2`
`Dhspike Zzsynthetic` (`fhir_uuid b010ea84-…`) and provider `id=2` `Dhprac Zzsynthetic`
(`fhir_uuid bac7daf3-…`). Both also exist in the standalone HAPI store. No sample, analysis or
electronic_order exists; laboratory tests (177 active) and `site_information` (121 rows) were not
modified, and no database row was written or deleted directly.

### BLOCKER-5 — SSO native authorization mapping · ✅ ROLE TYPES DECIDED 2026-08-14 (ADR-017), clinician row narrowed 2026-08-26 (ADR-017F) · ⏳ NAMES PENDING

The protocol/client plumbing is verified, and the role *types* are now decided. **The `clinician`
mapping was narrowed by ADR-017F (2026-08-26, superseding ADR-017's original `clinician` row,
preserved unchanged as history): `clinician` → `Provider` + `Daher Clinician Admission` — not
`Organizational: Nurse`.** `Organizational: Nurse` was rejected because its inheritance chain
(`Organizational: Nurse` → four `Application:` roles → `Privilege Level: High`) closed over 302 of
the system's 345 privileges, versus the 9 ADR-017D deliberately approved for admission — including
`Delete Patients`, `Delete Users`, `Manage Roles`, and `Manage Global Properties`, none proven needed
by any Daher workflow. This narrow identity is live-proven end to end (fresh-identity first-login
OIDC, exactly 2 roles/9 privileges, `currentProvider` non-null, the canonical admission contract,
and a `Manage Roles` negative-privilege control) — full evidence in `DECISIONS.md`'s ADR-017F entry.
`lab-tech` → `Reception`, `Results`, `Validation`; `sys-admin` → `Organizational: System
Administrator` + `Global Administrator` are unchanged by this.

**Only the staff names remain**, and they need the birth-centre meeting. Until real people are
attached to those types, SSO stays fail-closed off and the one-login acceptance test against real
staff is deliberately unperformed — turning it on would grant clinical and laboratory access to an
unreviewed set of accounts. (The one-login proof *has* now been performed against synthetic
identities on a disposable environment, closing the design/mechanism risk; what remains is the real
roster, a fact from the birth centre, not a design or mechanism gap.) **Bug #23 is now fixed,
automated-tested, and disposable-live-demonstrated**: the `openmrs` client's `oidc-usermodel-client-
role-mapper` (claim `roles`) shipped with no `usermodel.clientRoleMapping.clientId`, Keycloak's
all-clients form — it relayed every client role a user held on *any* client, not just `openmrs`. This
was proven exploitable, not inert: a synthetic user holding only a foreign client's role named
exactly `Provider` (no `openmrs` `Provider` grant) produced a `roles` claim containing `Provider`,
indistinguishable from a genuine grant by ADR-017E2's exact-string check, and — critically — OpenMRS
itself would have created a `currentProvider` from that. The fix adds
`usermodel.clientRoleMapping.clientId: "openmrs"` to the mapper, in both the fresh-realm import
(`daher-realm.json`) and the existing-realm reconciliation fragment
(`keycloak/mappers/openmrs-client-roles-mapper.json`) — Keycloak-native, no oauth2login/Java change,
ADR-017E2's exact-match logic untouched. Live-proven end-to-end on a disposable Keycloak 26.7.1 +
`openmrs-adr017f`: post-fix, a user with only the foreign `Provider` role plus `openmrs` → `Daher
Reception` authenticated with `roles: [Daher Reception]`, `currentProvider: null`, and **zero**
Provider database rows — the escalation path is closed. Full evidence, both provisioning paths, all
negative/mixed/composite/realm-role controls, and the CI static guard are in `DECISIONS.md`'s Bug #23
entry. **Bug #25 is fixed, automated-tested, and disposable-live-demonstrated**:
oauth2login now uses the authoritative committed User returned by OpenMRS's daemon creation call
instead of rereading through its old `REPEATABLE-READ` transaction snapshot; two fresh clinicians
and one fresh Reception identity authenticated on their first callbacks with no duplicates or
partial state. Exact evidence is in `PROJECT_STATE.md`'s Known Bugs table and `DECISIONS.md`'s Bug
#25 implementation record. A third issue found during this same pass, **Bug #24, is closed**: what
first looked like a first-login provisioning defect was this
session's own synthetic test data (an email using the non-existent `.invalid` TLD, correctly
rejected by OpenMRS's own email validator) — no platform defect, no partial database writes on the
failure, and first-login provisioning is confirmed clean (fresh identity, idempotent repeat logins)
once the test data was corrected.

---

## 5. Known placeholders — deliberate, not gaps to fill

**These are decided placeholders. An agent that "helpfully" fills them in is violating §5 of the
sprint spec, not being useful.**

| Placeholder | Status | Who resolves |
|---|---|---|
| Maternity bed counts | `.env` config, additive-only seed (ADR-007). Committed defaults are placeholders, marked as such. | Liban, from the birth centre |
| WhatsApp credentials | None held. Mock build; ticket stays `BLOCKED` not `DONE` (ADR-003). | Liban |
| Prenatal lab order set | **Mechanism built and verified; panel is deliberately EMPTY.** Neither system ships one to copy, so its contents must be composed — that is the clinical lead's decision. `docs/CLINICAL-SIGNOFF.md` lists the 37 installed lab sets for them to tick; nothing is pre-selected. | Clinical lead |
| Partograph form fields | Not yet written. Data-entry only, no decision support (ADR-006). | Clinical lead |

---

## 5a. What changed 2026-08-13/14

DH-015's restore drill was **performed** (partial coverage). DH-021 replaced the OpenELIS keystore,
whose private key was public *and whose certificate had already expired unnoticed*. DH-016's Caddy
proxy serves all three subdomains locally over HTTPS with a **verified** hop to the OpenELIS API.
DH-017's README now covers the whole stack. DH-006 and DH-004 gained their mechanisms — an
additive-only order-set seed shipping an **empty** panel, and an **executable** ADR-006 guard that
refuses any form performing clinical interpretation. Two false greens were closed: the OpenELIS
healthcheck that reported healthy through a total outage, and one inside the new form guard itself.

**Nothing above chose any clinical content.** The panel is empty, no form schema is committed, and
both sign-off requests are in `docs/CLINICAL-SIGNOFF.md`.

## 5c. What changed 2026-08-16/17 — the order path, half built and now waiting on a decision

Four commits, and the shape of the ticket changed twice because probing came before coding.

**Known bug 17 fixed** — the provisioning executor had one 900-second timeout for every verb, which
was shorter than a single healthcheck's grace period in the compose file it runs (`openmrs-backend`
declares `start_period: 1800s`). The first genuine facility provisioning would have been reported
`failed` while the stack came up correctly. Now per-verb, asserted against the real compose file.
It left **bug 18** open by decision rather than weakening ADR-022's replay window.

**BLOCKER-6 raised** — every document here said "the adapter routes the order to OpenELIS" without
naming a mechanism, and the mechanism they implied turns out not to be the one OpenELIS documents. It
**pulls** from a remote FHIR source. Five candidates are recorded; none is selected, because the
choice changes what the adapter *is* and one option adds a second system storing patient data.

**`_sort` correction** — the accepted watermark design's pseudocode relied on server-side ordering.
`fhir2` 4.2.0 accepts `_sort` and ignores it: ascending, descending and a nonsense key returned
byte-identical order over 50 distinct resources. Corrected in `WATERMARK-DESIGN.md` §3 by appending,
not by rewriting history.

**Option F spiked (DH-011B)** — OpenELIS's own webapp serves business FHIR providers that create
genuine native laboratory rows. Two constraints came with it: `ServiceRequest` is not
`identifier`-searchable there, and `PatientProvider` returns `201` on a failed persist.

**DH-011E built** — the OpenMRS half of the order path, verified under five controls, deliberately
unactivated.

**What this session did not do, on purpose:** select a BLOCKER-6 option, write a probe order into the
laboratory to force an answer, grant itself OpenELIS permissions to make a spike pass, change the
accession configuration, delete the synthetic patient it created, restart Caddy to clear bug 20, or
wire up the poll loop to look finished.

## 5b. Multi-facility provisioning — works end to end, on a stand-in

A provisioning API and portal (`provisioning/`) began 2026-08-15 as a **deliberate schedule trade**:
built before DH-011's transport chain, so the golden end-to-end flow will very likely not be
demonstrated by 1 September. That is a recorded decision, not a slip.

**What works, demonstrated across four containers:** signing into the portal and submitting a form
records a facility, queues a job, and the host's agent claims it over TLS, validates it locally, and
**actually runs `docker compose`** — containers come up, namespaced by `FACILITY_ID`, with secrets
the agent generated on its own host and never transported. 360 tests. State is durable (SQLite), so
a control-plane restart no longer loses the fleet.

**⚠️ Proven against a minimal stand-in compose file.** No OpenMRS, OpenELIS, Keycloak or adapter has
ever booted from a provisioning job, and the ~49-minute first boot is unexercised on that path. The
executor's single 900-second timeout — shorter than one healthcheck's grace period in the compose
file it runs — was fixed 2026-08-16 with per-verb bounds asserted against that file (known bug 17,
371 tests). That removes a certain false failure; it does not turn the stand-in into a real boot.

**Two design flaws this work found in itself:**

- **Rotation would have broken every facility** (ADR-024). Rewriting the env file would present new
  passwords to databases provisioned once by `initdb`, claim an admin-password change that never
  happened, and make every prior backup undecryptable. Rotation is now scoped, and refuses the rest
  with what is actually required.
- **The backup key would have died with the host** (ADR-025). Generating it on the machine being
  backed up means losing that machine — the disaster backups exist for — makes every off-site
  archive permanently undecryptable. The operator now supplies it at bootstrap.

**Still open:** public DNS/TLS (needs the domain), and a real clinical boot through this path.

## 6. What is genuinely missing

Derived from `PROJECT_STATE.md`'s ticket table rather than maintained by hand — an earlier revision
of this section still listed the restore drill as missing after it had been performed. If the two
disagree, the ticket table is right and this section is stale.

**Nothing built at all:**

| Ticket | Missing |
|---|---|
| DH-011 / DH-012 | The adapter's order and result transport. 🛑 **Blocked on BLOCKER-6** — the mechanism by which an order reaches OpenELIS is not what the design assumed. **The OpenMRS-side half is now built** (DH-011E: poller, durable watermark, cohort completeness, client-side ordering — 94 tests, five controls) but **deliberately not activated**, because there is nothing to deliver to. What is missing is the target leg, the correlation binding (DH-011C), and the mappings (DH-011G/H/I) |
| DH-013 | The WhatsApp notifier. Not written, and gated on credentials nobody holds (ADR-003) |
| DH-018 | The golden end-to-end demonstration — 1 of 8 steps |

**Mechanism built, contents deliberately empty — these need a person, not an engineer:**

| Ticket | State |
|---|---|
| DH-004 | Partograph/ANC forms. The ADR-006 guard is executable and refuses any form that interprets; **no form schema is committed** |
| DH-006 | Prenatal panel. Seeded additively and ships with **0 members**, by design. `docs/CLINICAL-SIGNOFF.md` is the handover sheet |
| DH-008 | SSO activation and the one-login proof. Plumbing verified; role *types* decided (ADR-017); staff **names** pending |

**Built but not finished — the boundary is the point:**

| Ticket | Done | Not done |
|---|---|---|
| DH-014 | Local encrypted path verified end to end | **Off-site upload never exercised** |
| DH-015 | A restore was **actually performed** 2026-08-13 | Two acceptance legs (a lab result, an SSO login) could not run because that data does not exist yet. Must be re-run |
| DH-016 | Routing, local HTTPS, verified TLS hop to OpenELIS | Publicly trusted certificates — needs the real domain and DNS |
| DH-017 | README covers the whole stack | Never walked on a genuinely fresh VM |
| DH-025 | Recovery core runtime-verified | Correlation not yet bound to a concrete OpenELIS resource (a DH-011 gate) |
| DH-027 | Provisioning works end to end, and a stack starts from a job | **Against a stand-in compose file.** No clinical stack has ever booted this way |
| DH-011G | Patient-identity contract fully investigated 2026-08-30: source-proven identifier ingestion behavior, live 4-way exact-search matrix (1/0/0/0), and a live-proven duplicate-Patient-creation gap that applies to every candidate option | ~~**No policy chosen or implemented — Human Decision Required.** Decision matrix, technical recommendation (Option B), and the exact question for Liban are in PROJECT_STATE.md's DH-011G section~~ (superseded — Liban rejected Option B and selected Option D; see DH-011G2 below, kept for history) |
| DH-011G2 | Native OpenMRS-UUID ↔ OpenELIS Patient linkage implemented and live-verified 2026-08-30 (**LIBAN DECISION: SELECT OPTION D**): atomic create+link, native `@Search` routing closing the split-store hazard for this identifier system, full cardinality/corruption/concurrency/crash-recovery/twin matrix against fully synthetic disposable infrastructure. Adapter tests 161 → 176 + 9 new gated live-integration tests, all passing | Committed `de70db2` (2026-08-30), pushed to `origin/master`, CI run [33323310292](https://github.com/brittLiban/d-health/actions/runs/33323310292) all 8 jobs green on this exact SHA. Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011G2 section, `DECISIONS.md`'s ADR-032 |
| DH-011K | Crash-point qualification live-verified 2026-08-30. No canonical five-point enumeration existed anywhere in the repository before this ticket (`WATERMARK-DESIGN.md` §5 named three) — the five points qualified here are this ticket's own clarification, derived from source and cited in full in `test_dh011k_crash_matrix.py`'s module docstring. All five points, ambiguous-target/write-failure/dead-letter controls, claim concurrency, and the real `PostgresRecoveryFence` (genuine advisory locking) live-qualified against a real disposable OpenMRS + patched OpenELIS + Postgres stack, composing the real production `OrderPoller`/`RecoveryCoordinator`/`OpenElisClient` end to end. Zero production code changed. A real DH-011E production-wiring gap was found and documented (not fixed): `OrderPoller`'s fixed `write_target` cannot regenerate a per-event payload from the opaque correlation hash alone — closed by DH-011E2 below. Adapter tests 176 → 177 | Independent review approved commit. Committed `0a6dc1a` (2026-08-30), pushed to `origin/master`, CI run [33329195493](https://github.com/brittLiban/d-health/actions/runs/33329195493) all 8 jobs green. Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011K section |
| DH-011E2 | Closes the production recovery-writer gap DH-011K found: `OrderPoller._handle` now builds a per-event closure (`_retry_with_validated_correlation`) over the *current* event and `self._deliver`, instead of a poller-lifetime-fixed `write_target` (removed entirely from `OrderPoller.__init__`). Validates the recovery correlation against `source_correlation(event.identity)` before ever calling `deliver`, fails closed (`RecoveryCorrelationMismatch`) on a mismatch. `RecoveryCoordinator`'s own transport-neutral contract is completely unmodified. Proven live through the real composed `OrderPoller.poll_once()` (not `RecoveryCoordinator.reconcile` called directly, which is what DH-011K's own proof used) for both stale-ABSENT recovery and stale-FOUND/crash-after-commit reconciliation; DH-011K's own five-point matrix re-run live and unaffected. One pre-existing DH-011K test-flakiness bug (dead-letter test's cohort-order assumption) was found along the way, unrelated to this composition fix, and split into its own separate commit `eebaa8e` (CI [33335104297](https://github.com/brittLiban/d-health/actions/runs/33335104297), all 8 jobs green) per independent review. Adapter tests 177 → 181 | Independent review approved this design. Committed `22e8ec9` (2026-08-30), pushed to `origin/master`, CI run [33335414196](https://github.com/brittLiban/d-health/actions/runs/33335414196) all 8 jobs green. `DEAD-LETTER OPERATOR RESOLUTION` remains open, untouched — see DH-011E3 below. DH-011E remains BUILT + VERIFIED, NOT ACTIVATED — no scheduler, no production wiring added. BLOCKER-6 itself is resolved (Option F selected); what remains unassembled is the production `DeliverOrder` composition and real facility mapping inputs (DH-011H/DH-011I). Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011E2 section |
| DH-011E3 | **Evidence + design only, nothing implemented.** The only dead-letter creation path (`OrderPoller._walk`'s catch-all) does not distinguish pre-target-mutation failures from possibly-post-mutation ones — untested assumption, not a proven property. `dead_letters.processed_message_id` is UNIQUE (one dead-letter per message today); `resolved_at`/`resolution_note` columns exist since migration `0001` but are dead code — no resolver identity, no resolution-history table. Recommended design: flip `dead_letter` → `processing` (row-locked like `mark_completed` already is) plus a new append-only `dead_letter_resolutions` audit table — once flipped, the event is indistinguishable from an ordinary stale `processing` claim and DH-025/DH-011K's already-proven probe-before-write machinery handles the rest with zero new recovery algorithm. `adapter/src/daher_adapter/app.py` has no authentication subsystem at all; recommended surface is a local host CLI, with the identity-verification gap stated as a blocker, not glossed over. Source-version drift and cancellation-while-blocked (a dead-lettered original can block a later cancellation event indefinitely, per `_walk`'s own unconditional halt) are both real, evidence-grounded risks flagged for Liban's decision, not resolved here | Investigation only, superseded by DH-011E4's implementation below (chronology preserved, not deleted). Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011E3 section |
| DH-011E4 | Implements DH-011E3's approved A+B design (**LIBAN DECISIONS**: retry uses the exact stored payload for the exact `MessageIdentity`, immutable; mandatory source preflight; a superseded/canceled source fails the retry closed; SKIP/DISCARD/FORCE-COMPLETE not supported in V1 — `DECISIONS.md`'s ADR-033). Migration `0003_dead_letter_resolutions`: relaxes `dead_letters`' one-row-per-message uniqueness to `(processed_message_id, sequence)`, adds the append-only `dead_letter_resolutions` audit table. `LedgerRepository.authorize_dead_letter_retry` — row-locked, refuses a stale attempt id, writes the audit row for both authorized and refused outcomes. `openmrs.check_source_supersession` — a real FHIR `read` (never `search`), comparing identity and `status` against what was stored; **live-verified against a real disposable OpenMRS stack**: a genuine `DISCONTINUE` flips `status` `active`→`completed` while `meta.versionId`/`meta.lastUpdated` stay unchanged (confirms WATERMARK-DESIGN.md §6 still holds), and `?replaces=` search is confirmed HTTP 400 (unsupported) — the real, unmodified preflight function correctly classified both a real untouched order and a really-discontinued one. New `dead_letter.py` orchestrator never contacts the target (asserted by its own signature). New `cli.py`: `dead-letter list`/`retry`, metadata-only output, no `skip`/`discard`/`force-complete` (permanent registry-inspecting guard, control-tested). Adapter tests 181 → 218, plus 3 live-gated Postgres concurrency/lifecycle tests (25 concurrent-race trials, real row locking, all correct) | **Not committed — pending review**, per this ticket's own commit gate. `DEAD-LETTER OPERATOR RESOLUTION` is now CLOSED. DH-011E remains BUILT + VERIFIED, NOT ACTIVATED — the retry CLI changes ledger state only, never runs a poll cycle. Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011E4 section |
| DH-011E4B | Independent-review closure pass on DH-011E4, three gaps: (1) **supersession completeness** — audited the exact pinned `openmrs-core` 2.8.7/`fhir2` 4.2.0 source (fetched from the tagged GitHub commits): `OrderServiceImpl.saveOrderInternal` routes both `REVISE` and `DISCONTINUE` (the only two actions that can supersede an order) through the identical `stopOrder` method, unconditionally setting `dateStopped`; fhir2's status translator derives status purely from `dateStopped`, never `Action` — action-agnostic by construction; `Action.RENEW` confirmed (full-repo code search) never referenced in `OrderServiceImpl`'s validation logic, cannot supersede anything. Live-proven on two fresh disposable OpenMRS-only stacks: DISCONTINUE (re-confirmed) and REVISE (new) both flip `status: active→completed`, `meta.versionId`/`meta.lastUpdated` unchanged, real `check_source_supersession` classified both `SUPERSEDED`. (2) **operator attribution** — new `scripts/daher_adapter_admin.py` host-side wrapper derives real OS account name+UID via `pwd.getpwuid(os.getuid())` on the host before `docker compose exec` destroys it, forwards via list-argv `subprocess.run` (`shell` never `True`, proven against 9 hostile reason strings); `cli.py`'s `retry` no longer accepts `--actor` at all (confirmed via argparse's own registered option strings). (3) **critical Postgres evidence** — `test_dh011e4_dead_letter_retry.py` confirmed already permanent/gated (not one-shot); added a 4th test closing "no duplicate active retry," re-ran live (4/4 passing). Adapter tests 218 → 230 | **Not committed — pending review**. DEAD-LETTER RETRY MECHANISM: DONE. SUPERSESSION PROTECTION: PROVEN FOR ALL V1 OPENMRS ORDER ACTIONS. OPERATOR ATTRIBUTION: HOST-OS-DERIVED V1 TRUST BOUNDARY. Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011E4B section; decision record: `DECISIONS.md`'s ADR-033 (updated in place) |

---

## 7. Risks to 1 September, specifically

| Risk | Severity | Why | Mitigation |
|---|---|---|---|
| **Restore drill gets squeezed last** | **Reduced 2026-08-13** | Was the top risk. A real restore has now been performed and documented, and it found two defects — `pg_restore --clean` silently losing rows over the pre-populated OpenELIS image, and a half-initialised Postgres at exit 127. | Largely mitigated. Residual: the drill must be **re-run** once a lab result exists and SSO is active, and off-site is still unexercised. Budget that re-run rather than assuming DH-015 is closed. |
| SSO across two apps | **Reduced 2026-08-14** | Protocol/client plumbing is verified and the role *types* are decided (ADR-017), so the design question is answered. What is left is naming the staff who hold each type — a fact from the meeting, not a design problem. Caddy now provides the HTTPS origins the browser session needs. | Keep fail-closed off until the names exist, then activate and demonstrate the full two-app session. Budget the demonstration itself: it has never been performed. |
| OpenMRS order polling safety | **High** | DH-025's recovery core, crash tests, and now DH-011E's poller/watermark all pass under five deliberate controls — but safety still depends on the concrete OpenELIS write carrying its correlation and a demonstrated exact target lookup, and **DH-011B found that the business FHIR server does not support `identifier` search on `ServiceRequest` at all**. So the correlation cannot bind where the design assumed. | Resolve BLOCKER-6, then bind and demonstrate correlation attach/query in DH-011C before activation; `Specimen` is identifier-searchable and worth testing before DH-011D's patch. Multiple matches or an unavailable lookup remain ambiguous and fail closed. |
| **Trusting an upstream success code** | **High** | New 2026-08-17. OpenELIS's `PatientProvider` returns `201 Created` when its native persist fails (bug 19), with no `Location` header. An adapter that believed it would write `completed` into the ledger for an order that never existed — silent loss, at scale, looking entirely healthy. | Any target implementation **verifies by read-back**, never by status code. The rule is recorded in the poller's own module docstring and in the known-bugs table so it survives this session. |
| O3 first-boot slowness | Medium | Liquibase on an empty database takes minutes; easy to misread as a hang and start "fixing" a working system. | Generous `start_period` already set. Watch logs, don't restart. |
| WhatsApp credentials never arrive | Medium | Real risk given none exist today. | Already handled by design: mock now, `.env`-only swap, ticket stays `BLOCKED`. Does not block anything else. |
| Clinical sign-off not obtained | **High** | Gates *clinically live*, not pilot-ready — but a pilot with no sign-off path is a pilot that cannot proceed to use. | Get the order set and partograph fields in front of the clinical lead as soon as they are drafted, in week 1, not week 3. |
| Version drift on moving tags | Medium | `3.7.x` is a branch tag; a rebuild could silently change the application. | Pin digests at DH-002, immediately after first pull. |

**Schedule reality:** 22 days for eight build steps, with roughly three of them (SSO, adapter,
restore drill) historically prone to consuming multiple days each. There is no slack for
rework caused by skipping verification. Verify each step before moving on, as the build prompt
requires.

---

## 2026-08-31 correction — DH-011H1/I1 reached a mandatory privilege stop

Liban's approved `Add Orders`-only increment is present in the uncommitted review worktree, but it is not
sufficient to place an order through the real OpenMRS REST resource. A fresh disposable proof
stopped at the separately protected concept lookup (`Get Concepts`) with zero native orders
written. Per Liban's explicit rule, that privilege was not added. ADR-034 and the appended
DH-011H1/I1 `PROJECT_STATE.md` entry are authoritative; do not describe clinician ordering as
working until Liban decides this second privilege and the positive proof passes.

The pass started at `c592b898711e8a01aa4aff892f885621b611a0bb`, so DH-011E4/E4B is committed
DONE. Real staff names, the real numeric OpenELIS site number, and clinical/lab test selection are
still pending; DH-006 remains open.

The same pass closed two stale evidence assumptions without activating transport:

- OpenELIS practitioner create is native but not replay-safe: submitted source id/identifier are
  discarded, exact replay creates a duplicate provider/person, missing or unresolved requester can
  still create an unattributed order, and secondary-store search can diverge from native read.
  DH-011H's automatic requester mechanism is not done.
- The pinned native test inventory is 189 total / 177 active / 33 LOINC-coded, while the OpenMRS
  candidate source is 37 / 21 coded. Exact LOINC intersection is **zero**; seven active OpenELIS
  codes are internally ambiguous. The final human mapping table is empty, not preselected.

No prior DH-011M/P evidence contains the requested stable OpenMRS concept UUID → OpenELIS test
mapping: DH-011M proved a direct OpenELIS `736-9` write, while DH-011K used a run-local synthetic
OpenMRS concept and independently hard-coded target `777-3`, explicitly not a DH-011I mapping.
Therefore DH-011L cannot run under the approved inputs, no `poll_once()` was invoked, and no
scheduler or production `DeliverOrder` was assembled. **READY TO COMMIT: NO.**

---

## 2026-08-31 correction — `Get Concepts` approved; order path now stops at `Get Care Settings`

Liban subsequently approved ADR-034's exact `Get Concepts` question. The uncommitted clinician
role now carries that read plus `Add Orders`, and no other Daher role gained either privilege.
Fresh disposable runtime proof advanced beyond concept resolution but stopped at another distinct
read gate, `Get Care Settings`, before any order write. That new privilege was not granted. Native
order count remained zero across clinician, clinician-minus-`Add Orders`, reception, and zero-role
lab-tech attempts. ADR-035 and the latest append-only `PROJECT_STATE.md` section supersede only the
older privilege-stop wording; all Practitioner, LOINC, DH-011L, and real-input blockers remain
unchanged. **READY TO COMMIT: NO.**

---

## 2026-08-31 correction — `Get Care Settings` approved; order path stops at `Get Providers`

Liban approved `Get Care Settings` as the third chronological order-path addition. Fresh
disposable proof with the exact resulting clinician role advanced to `orderer` conversion, where
OpenMRS required the fourth privilege `Get Providers`. It was not granted. All six synthetic
callers left native `orders` at zero; the `Get Concepts` and `Get Care Settings` removal controls
failed at their exact gates, while the `Add Orders` removal control remains masked by the earlier
`Get Providers` gate.

Liban also approved the Daher-owned native practitioner identity architecture (ADR-037), but it is
recorded only: implementation did not start because DH-011H2 required an immediate stop on any
fourth privilege. DH-011I remains OPEN and unchanged; DH-011L did not run; the poller remains
inactive. **READY TO COMMIT: NO.**

---

## 2026-08-31 correction — `Get Providers` approved, but source audit stops before role change

Liban approved `Get Providers` as the fourth chronological addition. Before applying it, the
required exact-source audit found that the same supported Test Order CREATE also resolves its
required `orderType` through the unapproved read privilege `Get Order Types`. It further found that
a cold/cache-miss native save with no client-assigned order number reads the generator global
property through the unapproved `Get Global Properties` privilege. Neither was granted.

Because the instruction required stopping before any role modification on such a finding,
`Get Providers` is approved but not yet present in the role. The clinician role remains the
original nine plus `Add Orders`, `Get Concepts`, and `Get Care Settings`. No fresh runtime or
practitioner implementation followed the source finding. ADR-038 and
`docs/DH-011H2-OPENMRS-ORDER-PRIVILEGE-AUDIT.md` are authoritative. **READY TO COMMIT: NO.**

---

## 2026-08-31 correction — approved Provider/Order Type reads expose an upstream baseline boundary

The uncommitted clinician role now includes Liban's approved `Get Providers` and `Get Order Types`,
making its order additions exactly `Add Orders`, `Get Concepts`, `Get Care Settings`,
`Get Providers`, and `Get Order Types`. It still does not list `Get Global Properties`.

However, OpenMRS automatically applies its built-in `Authenticated` role to every logged-in user,
and this stack's upstream baseline grants that role `Get Global Properties`. A clinician therefore
already has broad configuration-read access effectively; direct disposable REST proof returned a
system-setting value. A true disposable no-privilege control required removing the shared baseline
row and restarting. It then failed cold order creation exactly at `Get Global Properties` with zero
new rows. Administrator cache warming allowed the clinician save to commit, but the generic REST
response failed afterward because `Get Orders` is also required and unapproved. Restart restored
the cold failure.

Neither shared-role removal nor `Get Orders` was added to repository configuration. Caller-supplied
numbers and cache prewarming are unsafe. DH-011H is **BLOCKED** on a human architecture decision
for the shared baseline and a narrow server-side create seam; practitioner implementation remains
deferred. DH-011I is unchanged, DH-011L did not run, transport remains inactive, and no real
facility was touched. See ADR-039 and `docs/DH-011H2A-ORDER-NUMBER-BOUNDARY.md`.
**READY TO COMMIT: NO.**

---

## 2026-08-31 correction — DH-011H2C closes the OpenMRS source-order boundary

Liban selected ADR-041 after DH-011H2B: retain built-in Authenticated `Get Global Properties`
because pinned OpenMRS patient search requires it internally, and contain configuration at the
external HTTP boundary instead of adding a replacement search endpoint. The uncommitted module now
requires `Daher View System Configuration` on every pinned configuration/settings resource. Only
`Organizational: System Administrator` directly holds that privilege; clinician, reception, and
lab-tech receive fixed `403` responses while patient search remains `200` for clinician/reception.

The narrow Daher TestOrder endpoint is live-proven cold, warm, after generator change/reset, after
database recovery, and across corrected-image restarts. It accepts only encounter, concept, and
care-setting UUIDs; derives patient, sole authenticated-user Provider, Test Order type, number, and
creator; requires caller-owned `Add Orders`; and uses only four exact temporary internal reads.
The clinician role is again exactly the original nine admission privileges plus `Add Orders`.
Generic `/order` create and every generic-field injection wrote zero rows. Registration, maternity
admission, concurrent caller isolation, native row identity, and the existing end-admission
endpoint also passed on synthetic disposable data.

The first restart attempt exposed an Initializer wildcard-role copy of the new privilege and was
rejected. The corrected post-roles loader removes every non-sys-admin direct copy; two consecutive
corrected boots each converged to one direct holder. Full evidence is
`docs/DH-011H2C-EXTERNAL-CONFIGURATION-BOUNDARY.md`.

This does **not** close DH-011H end to end. ADR-037's exact OpenMRS Provider UUID → native OpenELIS
provider mapping is still unimplemented, and DH-011I still has zero exact one-to-one test mappings.
No production composition, poller, scheduler, DH-011L run, real staff, or real facility data was
used. Worktree remains uncommitted pending independent review.

**CORRECTION appended 2026-09-08 (DH-PIVOT-001).** The above section's own "worktree remains
uncommitted" language describes DH-011H2C's state *at the time that pass was written*. It is now
stale: `4dfaeef26f624e09a1869fddd1565b4106d4612e` ("uncomplete but pushing", 2026-09-01) committed
and pushed exactly this DH-011H2C source-order boundary work. See PROJECT_STATE.md's own
append-only correction under the DH-011H2C entry, and `DECISIONS.md`'s ADR-042, for the same note.
Nothing about the work's verification claims above changes.

---

## §0 Pivot note (2026-09-08, DH-PIVOT-001 / ADR-042)

**Product direction changed.** DHealth is no longer primarily a maternity/birth-centre EMR — it is
a digital lab order + result exchange connecting doctors/clinics, patients, and independent
laboratories, with OpenMRS and OpenELIS as the first reference connectors rather than the product's
definition. This is a *pivot*, not a correction: every maternity-first assessment above (DH-002
through DH-011H2C, all blockers, all NEEDS LIBAN items) remains an accurate historical record of
that scope and is unaffected. See `DECISIONS.md`'s ADR-042 for the full recorded decision
(architecture: Option C hybrid; central data boundary; patient result-release policy for MVP;
what leaves the new MVP's critical path and why nothing is deleted).

**DH-EX-001 (Exchange core domain + persistence foundation) is the first ticket under the new
direction.** New `adapter/src/daher_adapter/exchange/` subpackage: 12 ORM tables, 6 repository
modules, 1 additive Alembic migration (`0004_exchange_core`), 8 new test files. **Written and
manually re-reviewed, not run** — no Python interpreter was available in the authoring environment
(confirmed: no working `python`/`python3`/`py`), so `ruff check`, `ruff format --check`, `pytest`,
and a real `alembic upgrade head` have not actually been executed against this code. Every file was
checked by script for this repo's 100-character line-length convention, and
`.github/workflows/ci.yml`'s `EXPECTED_HEAD` was updated to `0004_exchange_core` so the existing
migration-history CI guard does not silently fail on the next run. Full status: PROJECT_STATE.md's
DH-EX-001 entry. **Not committed, not pushed, no OpenMRS/OpenELIS connector touched, no poller
activated, no real facility or patient data used**, per that ticket's own boundary.

**CORRECTION appended 2026-09-08 (DH-EX-001R, append-only — the paragraph above is superseded on
the "not run" point only, not retracted).** A real runtime was found and used: WSL2's already-
installed Ubuntu 26.04 provided a genuine Python 3.14 environment and, via `apt` (Docker Hub pulls
were unreliable in this sandbox), a real PostgreSQL 18 server. `ruff`, `pytest`, and
`alembic upgrade head` were all actually executed. Doing so found **four real defects no amount of
re-reading had caught**: a dataclass silently dropping the price-snapshot fields; patient/
practitioner source-identity uniqueness scoped too broadly (two different organizations reusing
the same identity value/namespace would have silently merged into one reference — fixed by scoping
to `(source_organization_id, source_system, source_identity_system, source_identity_value)`);
`LabOrder.idempotency_key` uniqueness scoped globally instead of per-provider-organization (a
real cross-tenant data-leak shape — fixed by scoping to `(provider_organization_id,
idempotency_key)`, plus a new `IdempotencyKeyConflict` refusal when a resubmitted key's canonical
content doesn't match); and `CatalogRepository.set_price` raising a raw, unhandled `IntegrityError`
under genuine concurrent writes, reproduced live against real PostgreSQL and fixed. All four fixed
and re-verified, including 10 repeated real-PostgreSQL concurrency runs post-fix with zero
failures. Transaction atomicity (rollback leaves no partial aggregate; retry produces exactly one
complete aggregate) was proven by real fault injection, not just reasoned about. Full account:
PROJECT_STATE.md's DH-EX-001R entry. **READY FOR COMMIT: YES** per that ticket's own gate — still
**not committed, not pushed**, stopped for Liban's review first. No OpenMRS/OpenELIS connector
touched, no poller activated, no real facility or patient data used.

**CORRECTION appended 2026-09-09 (DH-EX-001D, append-only — the "not committed" point above is
superseded, not retracted).** DH-EX-001 qualified at SHA
`4f9c1d10dc4543aa785c0bee939108a339b45e61`. The combined DH-EX-001 + DH-EX-001R work was committed
(`b1f8d85c64992feff41e8acc078d0904c5ad0a00`) and pushed. Its first exact-SHA CI run found one
pre-existing, unrelated red job ("OpenMRS narrow TestOrder module" — confirmed identical on the
parent commit `4dfaeef`'s own CI run, not a regression from this work); DH-CI-001 fixed that in a
single build-configuration-only commit (`4f9c1d10dc4543aa785c0bee939108a339b45e61`, one file,
`openmrs-config/modules/daherendadmission/omod/pom.xml`, +46/-0 — no Java, no CI workflow, no
Exchange, no OpenELIS file touched). **Exact-SHA CI run `34317829117` on that commit: all 9
required jobs green**, including the OpenMRS job (37/37 module tests, both its build-and-test and
test-loss-guard steps green). Full account: PROJECT_STATE.md's DH-CI-001 entry and DH-EX-001's
FINAL CLOSURE entry.

**What is qualified and what is not, stated precisely.** The DHealth Exchange **core domain and
persistence foundation** (`adapter/src/daher_adapter/exchange/`: Organization, PatientReference,
PractitionerReference, TestCatalogEntry, LabOffering, LabPrice, LabOrder, LabOrderItem,
LabSelection, LabResult, ResultDelivery, DoctorReview, plus their repository layer and migration
`0004_exchange_core`) is committed and exact-SHA CI qualified — real `pytest`/`ruff`/
`alembic upgrade head`, real PostgreSQL concurrency/atomicity qualification (PostgreSQL 18 in the
qualification sandbox; **this repo's actual CI adapter job runs `postgres:16-alpine`** — the two
have not been proven byte-identical, only not expected to diverge for the plain SQL used here).
This is **not** the Exchange MVP. Still future work, all unstarted: organization onboarding,
multi-org authentication, tenant user management, the Provider Portal's order workflow, the Lab
Portal, the patient secure link/lab-selection UI, WhatsApp/SMS integration, lab-directory
administration, production clinical test mappings, a results-inbox UI, a doctor follow-up UI, the
OpenMRS Exchange connector, the OpenELIS Exchange connector, production deployment, and any real
pilot. **NEXT IMPLEMENTATION TICKET: DH-EX-002** (organization onboarding + auth) — not begun.

**Connector-track work, unaffected and unerased.** DH-011H (including the still-open DH-011H2D
implementation thread), DH-011I (test-mapping, still zero exact matches), and ADR-037 (OpenMRS
Provider UUID → OpenELIS practitioner mapping, accepted, still unimplemented) remain exactly as
CURRENT_STATE.md's earlier sections above describe them — not completed by this pivot, not
reclassified, not touched by DH-EX-001/DH-EX-001R/DH-CI-001. No file under `openmrs-config/` or
`openelis-config/` was modified by any of that work except DH-CI-001's single build-configuration
line, which changed no clinical/application behavior. OpenMRS/OpenELIS pollers remain inactive; no
real facility was touched by any ticket in this Exchange-foundation sequence.

**CORRECTION appended 2026-09-09 (DH-EX-002, append-only — "NEXT IMPLEMENTATION TICKET: DH-EX-002
— not begun" above is superseded, not retracted).** DH-EX-002 (organization onboarding, OIDC
authentication, tenant isolation, a minimal `/exchange/v1` API) is **implemented and qualified,
but explicitly not committed and not pushed** — this ticket's boundary requires returning the
implementation for independent review first, the same position DH-EX-001 was in before DH-EX-001R.
Full detail is in PROJECT_STATE.md's DH-EX-002 entry; the load-bearing facts:

- **Auth architecture:** Keycloak (a new, separate `dhealth-exchange` realm — the existing
  connector `daher` realm is untouched) authenticates identities by `(issuer, subject)` only, never
  email. The Exchange Postgres database is the sole authority for organization membership and
  role — no per-organization Keycloak realm role exists or was created. Platform-admin authority is
  a single Keycloak realm role (`dhealth-platform-admin`), never inferred from an organization role.
- **82 new tests, all passing**, against SQLite (the committed suite's existing convention) plus a
  real-PostgreSQL migration and 25-thread concurrency qualification (not committed, matching the
  DH-EX-001R precedent). Full adapter suite: 357 passed / 39 skipped, `ruff` clean.
- **REAL KEYCLOAK QUALIFICATION = NOT RUN** — `docker pull` failed with a TLS handshake timeout in
  this sandbox, the same failure DH-EX-001R already recorded. All token-validation claims are
  proven against a locally generated RSA keypair, never a real IdP; this is stated plainly, not
  papered over with a mock-equals-real claim.
- **One real bug was caught only by running the code**, not by review: a `from __future__ import
  annotations` import silently broke FastAPI's dependency injection for every protected endpoint
  (all returned 422 instead of running any auth check). Fixed once the HTTP test suite actually ran
  it. Recorded in full in PROJECT_STATE.md's DH-EX-002 entry as a concrete instance of why this
  repository insists on runtime qualification over code-review-only sign-off.
- **Known, deliberate MVP limitation:** attaching a member to an organization is a lookup by
  `identity_id` only — the target must have authenticated at least once already; there is no
  invitation/email flow yet.
- Real facility untouched, pollers inactive, no lab-order API/portal UI/WhatsApp/patient surface
  built — none of that was in this ticket's scope.
- ~~Audit is fail-open on the durability boundary~~ — **fixed by DH-EX-002R, see below.**
- **A repo-wide LF→CRLF line-ending flip was discovered during this ticket, affecting ~344 tracked
  files this ticket never touched.** Confirmed whitespace-only (`git diff -w` shows zero real
  change outside this ticket's own 8 files) and not caused by this ticket's edits. This session's
  own changes were normalized back to LF; the tree-wide flip itself is unresolved and will make
  `git add` unsafe without careful path scoping on whatever ticket commits next. Full evidence and
  detail: PROJECT_STATE.md's DH-EX-002 entry.

**CORRECTION appended 2026-09-09 (DH-EX-002R, append-only).** DH-EX-002R (the DH-EX-001R-equivalent
independent review of DH-EX-002) has now run. Still **not committed, not pushed**. Load-bearing
changes, full detail in PROJECT_STATE.md's DH-EX-002R entry:

- **The audit fail-open BLOCKER above is fixed.** Organization creation, membership creation, and
  role assignment now write their mutation *and* an authoritative `ExchangeSecurityAuditEvent` row
  in one database transaction (`session_factory.begin()` in `exchange/api.py`) — proven not just by
  code review but by three failure-injection tests that break the audit insert, the commit, and a
  late audit insert mid-multi-statement-request, each confirming zero mutation and zero audit row
  survive. Refusals (403/404/409) are explicitly excluded from this table (a refusal is not a
  mutation) and stay in the existing best-effort file, now wrapped so a telemetry failure can never
  turn an intended refusal into an accidental 500.
- **A second, unrelated real bug was found while proving the above: SQLite/pysqlite silently broke
  SAVEPOINT rollback** under a specific sequence (a nested `begin_nested()` that completes, followed
  by a later unrelated failure in the same outer transaction). Confirmed absent against real
  PostgreSQL (this repo's actual driver) — a test-environment gap, not a production bug. Fixed in
  the shared `adapter/tests/conftest.py` engine fixture via SQLAlchemy's own documented workaround,
  with a full regression re-run (366 passed / 39 skipped, zero regressions) proving the fix didn't
  disturb any of the 350+ pre-existing tests that also use that fixture.
- 9 more tests added (concurrent role assignment; platform-admin/issuer-confusion negatives;
  org-admin-delegates-to-a-peer; role-omission-does-not-remove; a permanently named regression test
  for the FastAPI nested-dependency 422 bug). Full suite: **366 passed / 39 skipped.**
- Real PostgreSQL re-qualified (fresh migration, downgrade drops exactly the 4 new tables including
  the new audit table, re-upgrade, all 3 concurrency proofs including the newly-added role one).
  REAL KEYCLOAK QUALIFICATION remains NOT RUN (same Docker Hub TLS-timeout evidence as before).
- ~~NEXT: Liban's review, then a combined DH-EX-002+DH-EX-002R commit/push/exact-SHA-CI ticket.~~ —
  **done, see the DH-EX-002D correction below.**

**CORRECTION appended 2026-09-09 (DH-EX-002D, append-only — the "not committed"/"not begun" framing
above is superseded, not retracted).** DH-EX-002 + DH-EX-002R were committed together
(`dc3d13b7a8d6a53422091b56138718a73e096715`, parent `e93e7cc17c01ba79e24b34524cb76d42cc4d928a`),
pushed, and exact-SHA CI qualified (run `34390897519`, all 9 required jobs green, adapter
`366 passed / 39 skipped` confirmed independently in CI's own logs, migration head confirmed
`0005_exchange_org_auth` against CI's real `postgres:16-alpine` service). Full account:
PROJECT_STATE.md's **DH-EX-002 · FINAL CLOSURE** entry.

**DH-EX-001 and DH-EX-002 are both closed.** The DHealth Exchange currently has: the core
domain/persistence layer (DH-EX-001); organization identity, OIDC validation, memberships, tenant
roles, the platform-admin boundary, a minimal onboarding/membership `/exchange/v1` API, and a
transactionally-authoritative security-mutation audit (DH-EX-002). It does **not** yet have: a lab
directory API, marketplace/pricing, a Provider Portal order workflow, a Lab Portal result workflow,
a patient secure link, or WhatsApp/SMS integration. **REAL KEYCLOAK QUALIFICATION remains NOT RUN**
— stated plainly here again because it is exactly the kind of fact that is easy to let go stale.

**NEXT: DH-EX-003 — lab directory, test offerings, and pricing.**

**CORRECTION appended 2026-09-09 (DH-EX-003, append-only — the "NEXT" line above is superseded, not
retracted).** DH-EX-003 (lab directory, DHealth test catalog, lab offerings, current pricing,
whole-order eligibility) is implemented and real-PostgreSQL qualified, returned for independent
review first — the same position DH-EX-001 and DH-EX-002 were each in before their own R-review.
Full detail is in PROJECT_STATE.md's DH-EX-003 entry; the load-bearing facts:

- **Not committed, not pushed.**
- Migration `0006_exchange_lab_directory`, purely additive on `0005_exchange_org_auth`. Fresh
  upgrade, downgrade (drops exactly the new table + reverses the audit-table widening, leaves
  DH-EX-001/DH-EX-002 untouched), and re-upgrade all verified against real PostgreSQL 18 — but only
  on a database with **no DH-EX-003 audit rows yet**. Also verified, deliberately, on a database
  seeded with a DH-EX-003-shaped row (`organization_id IS NULL`, from a platform-level catalog-entry
  mutation): the downgrade **fails** there (`NotNullViolation` re-narrowing `organization_id`), by
  necessity, not by bug — and fails cleanly, leaving the DB at `0006` with zero partial schema
  change (transactional DDL, confirmed via `alembic current`). Downgrading a live `0006` database
  requires confirming no DH-EX-003 mutation has run first.
- A real concurrency bug was found and fixed: `set_price`'s original discover-then-supersede as two
  separate statements raced under PostgreSQL READ COMMITTED (4 threads wrongly believed they created
  the first price in a 25-way same-price test) — invisible under SQLite, whose coarser locking
  masked it. Fixed by folding both steps into one atomic `UPDATE ... RETURNING`. Re-verified clean
  across 3 independent real-Postgres qualification re-runs after the fix.
- Whole-order eligibility is all-tests-only by construction (a 3-pass query with a defensive line-
  count re-check), never a partial-lab result — verified against the ticket's exact 4-lab scenario
  on both SQLite and real PostgreSQL.
- DHealth test catalog HTTP creation is synthetic-only at three independent layers (no
  `is_synthetic` field on the request schema, handler hardcodes `is_synthetic=True`, marketplace
  query filters and pre-validates `is_synthetic=true`).
- DH-EX-002's transactional security-audit pattern was generalized (nullable `organization_id`,
  polymorphic `resource_type`/`resource_id`), not replaced; audit atomicity re-proven with a
  failure-injection test against real PostgreSQL. All 6 new audit `event_type` values have a live
  HTTP call site (DH-EX-002 shipped 2 with none), but only 3 are asserted by exact `event_type`
  string in a test; `lab_directory_profile_updated` and `lab_offering_status_changed` are never
  actually exercised as a successful mutation via HTTP in any current test — full detail in
  PROJECT_STATE.md's DH-EX-003 entry.
- Full adapter suite: **429 passed / 39 skipped** (was 366 at the DH-EX-002 handoff), `ruff`/
  `ruff format`/`pip check` all clean.
- **REAL KEYCLOAK QUALIFICATION remains NOT RUN** — unaffected by this ticket, no new auth surface
  added.
- Not built (explicitly out of scope): order creation, patient links, any UI, WhatsApp, payments,
  connector code. The marketplace endpoint is read-only — no `LabSelection` write, no
  `LabOrderItem` price freeze.

**CORRECTION appended 2026-09-09 (DH-EX-003R, append-only — the "NEXT: independent review" line
above is superseded, not retracted).** DH-EX-003R (the DH-EX-001R/DH-EX-002R-equivalent independent
review of DH-EX-003) has now run. Still **not committed, not pushed**. Full detail is in
PROJECT_STATE.md's DH-EX-003R entry; the load-bearing facts:

- **One BLOCKER found and fixed:** `require_any_active_membership` (the gate for
  catalog/directory/marketplace reads) checked membership status only, never the membership's own
  organization status — an identity whose only membership was in a **deactivated** organization
  retained standing network-browsing access indefinitely. Fixed: now requires an ACTIVE membership
  in an organization that is also currently ACTIVE. Verified at the HTTP layer (403 on all three
  network-read endpoints immediately after deactivation; `/me` itself unaffected).
- **Four HIGH findings fixed:** (1) a real mixed-currency-total gap — `find_whole_order_eligible_labs`
  pass (3) never re-checked `LabPrice.currency == "DJF"` even though pass (1) required it, so a
  non-DJF price reaching the DB by any path other than the HTTP API could have been silently summed
  into a response still labeled `"DJF"`; (2) `GET /catalog/tests` returned every catalog entry
  unfiltered to any network member, inactive/non-synthetic included, with no platform-admin
  distinction — now `active_synthetic_only` for ordinary members, unfiltered for platform admin;
  (3) the three-pass eligibility query's snapshot coherence under PostgreSQL READ COMMITTED was
  only structurally self-healing, not proven — now runs under `REPEATABLE READ` on PostgreSQL
  specifically, verified both by confirming the isolation level actually takes effect and by a
  stress test (616 reads under continuous concurrent mutation, 0 inconsistent results); (4) the
  previously-disclosed raw `NotNullViolation` on an incompatible `0006→0005` downgrade is now an
  intentional, explicit fail-fast guard (`DowngradeBlockedByAuditHistory`) that runs before any DDL
  and never deletes audit rows or fabricates ownership to force compatibility — verified in both
  states against real PostgreSQL (compatible downgrade succeeds; incompatible downgrade refuses
  immediately, `alembic current` confirms the DB stays exactly at `0006`, byte-for-byte unchanged).
- **Three MEDIUM/LOW findings fixed:** a whitespace-only `public_address` could pass validation;
  `LabPriceSetRequest.amount` had no `max_digits`/`decimal_places` matching the DB's
  `Numeric(12, 2)` column; the two disclosed audit-coverage gaps
  (`lab_directory_profile_updated`, `lab_offering_status_changed` never exercised on their
  successful-mutation path via HTTP) are now closed with positive tests proving both the real
  mutation and the no-op-replay outcome.
- Also added: a LAB_TECH lab-management-mutation-refusal regression test (previously untested for
  any DH-EX-003 endpoint); audit-atomicity failure injection for the three mutation classes the
  DH-EX-003 handoff had only proven for offering-creation; the ticket's price-snapshot-non-
  regression scenario (directly testable — DH-EX-001's `LabOrderItem`/`LabSelection` tables already
  exist); real-Postgres 25-way concurrency proofs for catalog-entry and lab-offering creation.
- Full adapter suite: **441 passed / 39 skipped** (was 429 at the DH-EX-003 handoff), `ruff`/
  `ruff format`/`pip check` all clean. Real PostgreSQL 18 fully re-qualified after every fix.
- **REAL KEYCLOAK QUALIFICATION remains NOT RUN** — unaffected by this review.
- **READY FOR COMMIT: YES** per this review's own gate — but still explicitly not committed, not
  pushed; Liban's review comes first, independent of that verdict.

**NEXT: Liban's review of DH-EX-003+DH-EX-003R, then a combined commit/push/exact-SHA-CI ticket,
then DH-EX-004 — lab order creation.**

**CORRECTION appended 2026-09-10 (DH-EX-003 FINAL CLOSURE, append-only — the "NEXT: Liban's review"
line above is superseded, not retracted).** DH-EX-003 + DH-EX-003R were committed together
(`33c5edb9ed76ac6bc0f22b1663de3b868dce2364`, parent `a76455db8677815c4a32deee4fce592c7761dafb`),
pushed, and exact-SHA CI qualified (run `34432960783`, all 9 required jobs green, adapter
`441 passed / 39 skipped` confirmed independently in CI's own logs, migration head confirmed
`0006_exchange_lab_directory` against CI's real `postgres:16-alpine` service). Full account:
PROJECT_STATE.md's **DH-EX-003 · FINAL CLOSURE** entry.

**DH-EX-001, DH-EX-002, and DH-EX-003 are all closed.** The DHealth Exchange currently has: the core
domain/persistence layer (DH-EX-001); organization identity, OIDC validation, memberships, tenant
roles, the platform-admin boundary, and a transactionally-authoritative security-mutation audit
(DH-EX-002); a lab directory, a synthetic-only pilot test catalog, lab offering administration, DJF
current pricing with CAS and immutable history, and whole-order lab eligibility — all evaluated
under PostgreSQL `REPEATABLE READ` for snapshot coherence (DH-EX-003). It does **not** yet have: lab
order HTTP creation, patient reference/selection linkage to an order, a Provider Portal order
workflow, a Lab Portal result workflow, a patient secure link, or WhatsApp/SMS integration. The
marketplace query is read-only and does not yet produce a permanently frozen patient quote.
**REAL KEYCLOAK QUALIFICATION remains NOT RUN** — stated plainly here again because it is exactly
the kind of fact that is easy to let go stale; DH-EX-003 added no new authentication surface.

**NEXT: DH-EX-004 — Provider lab-order creation** (exact patient reference, exact practitioner
reference, exact DHealth catalog tests, provider-scoped idempotency key, resulting in
`AWAITING_LAB_SELECTION`) — **not started.**

**CORRECTION appended 2026-09-10 (DH-EX-004, append-only — the "NEXT: DH-EX-004 ... not started"
line above is superseded, not retracted).** DH-EX-004 (provider lab-order creation) is implemented
and real-PostgreSQL qualified, returned for independent review first — the same position DH-EX-001,
DH-EX-002, and DH-EX-003 were each in before their own R-review. Full detail is in
PROJECT_STATE.md's DH-EX-004 entry; the load-bearing facts:

- **Not committed, not pushed.**
- Migration `0007_exchange_order_api`, purely additive on `0006_exchange_lab_directory` — widens
  the audit table's CHECK constraints for `lab_order_created`/`lab_order` only; no new table (the
  order/item/review/delivery tables already existed from `0004_exchange_core`).
- Three new routes: `POST/GET /organizations/{provider_id}/orders`,
  `GET /organizations/{provider_id}/orders/{order_id}` — gated by a new `require_clinician`
  authorization check that deliberately does **not** let platform admin bypass (network
  administration ≠ clinical access) and refuses an `ORG_ADMIN` who lacks `CLINICIAN` too.
- The ordering practitioner is always server-derived from the authenticated identity — never
  client-supplied (`practitioner_reference_id` is not even a field on the request schema).
- `LabOrderRepository.create` and `PractitionerReferenceRepository.get_or_create` were refactored
  (not rebuilt) to accept a caller-owned session with SAVEPOINT-scoped race handling, so
  practitioner get/create + order creation + the authoritative `lab_order_created` audit row all
  commit or roll back together in one transaction — no orphaned practitioner reference on any
  failure path.
- No lab-availability requirement at order creation, no price snapshot
  (`selected_price_amount`/`currency` stay `NULL`), no `LabSelection` row, status stays
  `AWAITING_LAB_SELECTION` — all by explicit design.
- Real-Postgres 25-way concurrency proofs: same-idempotency-key (exactly one order/review/
  delivery/practitioner/audit row), conflicting-same-key (exactly one winner, controlled conflicts
  for the rest), and practitioner-reference-under-concurrent-first-orders (exactly one
  `PractitionerReference` despite 25 simultaneous first orders for one clinician). Migration `0007`'s
  downgrade follows the exact `0006` precedent: refuses cleanly, before any DDL, when a real
  `lab_order_created` audit row exists.
- Full adapter suite: **474 passed / 39 skipped** (was 441 at the DH-EX-003C handoff), `ruff`/
  `ruff format`/`pip check` all clean.
- **REAL KEYCLOAK QUALIFICATION remains NOT RUN** — unaffected, no new authentication surface (only
  a new authorization check).
- Not built (explicitly out of scope): patient lab selection, any Portal UI, patient secure link,
  WhatsApp/SMS, a status-transition endpoint, lab routing, price freezing.

**NEXT: independent review of DH-EX-004 (the R-step), then a combined commit/push/exact-SHA-CI
ticket, then DH-EX-005 — patient secure link + lab selection.**

**CORRECTION appended 2026-09-10 (DH-EX-004R, append-only — the "NEXT: independent review" line
above is superseded, not retracted).** DH-EX-004R (the DH-EX-001R/DH-EX-002R/DH-EX-003R-equivalent
independent review of DH-EX-004) has now run. Still **not committed, not pushed**. Full detail is
in PROJECT_STATE.md's DH-EX-004R entry; the load-bearing facts:

- **No BLOCKER.** Four HIGH findings fixed: (1) the practitioner `source_system` constant named a
  specific UI (`"dhealth-provider-portal"`) rather than the durable fact it asserts — renamed to
  `"dhealth-exchange"`; (2) `Idempotency-Key`'s "canonical UUIDv4" contract was not actually
  enforced — `uuid.UUID()` silently accepted braced/URN/uppercase forms — now a strict RFC 4122 §3
  regex rejects anything non-canonical; (3) the order-list `limit` had no HTTP query parameter or
  bounds validation — now `Query(ge=1, le=100)`, default 50; (4) duplicate `catalog_entry_id`s were
  only rejected at the HTTP schema layer, not the repository boundary — now an explicit
  pre-transaction `DuplicateOrderItems` check (a DB-level `UNIQUE` constraint was considered and
  deliberately rejected — it would have made a duplicate-item race collide with the existing
  idempotency-key `IntegrityError` handler and escape as a raw, uncaught error).
- Five additional controls proven that the DH-EX-004 handoff had asserted by reasoning but not yet
  tested: platform admin gets the byte-identical 403 for a real vs. fake order UUID (no
  order-existence oracle); the same clinician identity gets two separate `PractitionerReference`
  rows across two different providers; **a losing conflicting idempotency-key request leaves no
  orphaned practitioner reference — proven under real PostgreSQL concurrency, not just SQLite**; an
  adversarial `PatientReference` seeded at a LAB organization's id still 404s for a real provider's
  clinician; same-key replay is proven set-identity (`[A,B,C]` = `[C,B,A]`), never list order.
- CI workflow diff independently re-verified: exactly the one expected `EXPECTED_HEAD` line change,
  no guard weakened anywhere.
- No logging of patient/practitioner/test/JWT/Authorization-header data found anywhere in this
  ticket's changes; the audit row itself carries only organization id, actor id, resource type,
  and resource id.
- Full adapter suite: **495 passed / 39 skipped** (was 474 at the DH-EX-004 handoff), `ruff`/
  `ruff format`/`pip check` all clean. Real PostgreSQL 18.6 fully re-qualified after every fix,
  including the new losing-conflict-practitioner proof at real-database concurrency scale.
- **REAL KEYCLOAK QUALIFICATION remains NOT RUN** — unaffected by this review.
- **READY FOR COMMIT: YES** per this review's own gate — but still explicitly not committed, not
  pushed; Liban's review comes first, independent of that verdict.

**NEXT: Liban's review of DH-EX-004+DH-EX-004R, then a combined commit/push/exact-SHA-CI ticket,
then DH-EX-005 — patient secure link + lab selection.**

**CORRECTION appended 2026-09-10 (DH-EX-004 FINAL CLOSURE, append-only — the "NEXT: Liban's
review" line above is superseded, not retracted).** DH-EX-004 + DH-EX-004R were committed together
(`6cc6bdac45faf07a843a08699f8c1b8f269b313f`, parent `8bbf2de80b196e49c53b91943faa435419029736`),
pushed, and exact-SHA CI qualified (run `34441434606`, all 9 required jobs green, adapter
`495 passed / 39 skipped` confirmed independently in CI's own logs, migration head confirmed
`0007_exchange_order_api` against CI's real `postgres:16-alpine` service, runtime-reported
PostgreSQL 16.15). Full account: PROJECT_STATE.md's **DH-EX-004 · FINAL CLOSURE** entry.

**DH-EX-001, DH-EX-002, DH-EX-003, and DH-EX-004 are all closed.** The DHealth Exchange currently
has: the core domain/persistence layer (DH-EX-001); organization identity, OIDC validation,
memberships, tenant roles, the platform-admin boundary, and a transactionally-authoritative
security-mutation audit (DH-EX-002); a lab directory, a synthetic-only pilot test catalog, lab
offering administration, DJF current pricing with CAS and immutable history, and whole-order lab
eligibility evaluated under PostgreSQL `REPEATABLE READ` (DH-EX-003); and DHealth-native Provider
lab-order creation/read — clinician-only authorization (platform admin has no implicit clinical
access), server-derived durable practitioner provenance (distinct per provider organization),
provider-scoped canonical-UUIDv4 idempotency, and a transactionally-atomic order+pending-rows+audit
aggregate landing at `AWAITING_LAB_SELECTION` (DH-EX-004). It does **not** yet have: a patient
secure link, patient lab selection, a frozen price snapshot, any `LabSelection` row, a Provider
Portal UI, a Lab Portal, a result workflow, or WhatsApp/SMS integration. **REAL KEYCLOAK
QUALIFICATION remains NOT RUN** — stated plainly here again because it is exactly the kind of fact
that is easy to let go stale; DH-EX-004 added no new authentication surface.

**NEXT: DH-EX-005 — patient secure link + lab selection.** Golden journey after DH-EX-005:
clinician creates order → `AWAITING_LAB_SELECTION` → patient receives/uses scoped secure access →
patient sees eligible labs/current prices → patient chooses one lab → selected lab + price
snapshot become durable → order transitions to `SELECTED` — **not started.**

**CORRECTION appended 2026-09-10 (DH-EX-005, append-only — the "NEXT: DH-EX-005 ... not started"
line above is superseded, not retracted).** DH-EX-005 (patient secure link + lab selection) is now
**implemented, self-reviewed, and qualified against both SQLite and real PostgreSQL 18.6**. Still
**not committed, not pushed**, and — unlike DH-EX-004 at the equivalent point — **no DH-EX-005R
independent review has run yet either**; this ticket's own self-review is documented but is not a
substitute for that separate step. Full detail is in PROJECT_STATE.md's DH-EX-005 entry; the
load-bearing facts:

- A clinician (same CLINICIAN-in-ACTIVE-PROVIDER-org boundary as DH-EX-004, still no platform-admin
  bypass) issues/revokes an opaque, order-scoped 256-bit bearer secret for a `LabOrder`
  `AWAITING_LAB_SELECTION`. Only its SHA-256 digest is ever persisted. Three entirely separate patient
  endpoints (`GET .../patient/order`, `GET .../patient/order/lab-options`,
  `POST .../patient/order/selection`) are authenticated by that secret alone — never OIDC, never a
  client-supplied order/provider/patient id (an entire IDOR class removed structurally).
  `LabSelectionRepository.select()` now re-runs the *full* DH-EX-003 marketplace-eligibility contract
  plus an exact price-id-set consent check inside the same transaction that freezes prices, creates
  the `LabSelection`, and transitions the order to `SELECTED` — no TOCTOU window. Patient selections
  carry their own provenance via `LabSelection.selected_via_patient_access_grant_id`, never a fake
  `ExchangeIdentity`.
- Self-review found and fixed two real bugs (a stray `outcome` column left on the new model by a
  copy/paste slip; a SQLite-only naive/aware `datetime` comparison `TypeError`) and two structural
  issues within approved architecture (`revoke()` now locks the order row first, exactly like
  `issue()`, closing a race where a revoke could falsely report success while a fresh grant silently
  survived; the grant-issuance retry loop now re-queries for a genuine `token_hash` collision instead
  of blindly retrying and misreporting any `IntegrityError`).
- 33 new tests in `test_exchange_patient_access.py`, including the ticket's exact golden/stale-price/
  lab-unavailable scenarios and same-lab-replay-after-a-price-change (proven to return the original
  frozen amount, never re-check). Full adapter suite: **532 passed / 39 skipped** (was 499 before this
  ticket), `ruff` clean, CR-byte/whitespace/secret scans clean.
- Real PostgreSQL 18.6 (scratch DB `dhex005_qual`): fresh `0001→0008` clean; schema-parity verified
  column-by-column including the partial unique index and that the recreated audit CHECK constraints
  still contain every prior DH-EX-002/003/004 value; `0007→0008` upgrade over genuine pre-existing
  data left every row intact; compatible downgrade + clean re-upgrade; incompatible downgrade
  (proven twice — real grant row, and separately a real audit-row-only trigger) refuses before any
  DDL in both cases, schema/data confirmed byte-identical afterward. 25-way grant-issuance race,
  25-way same-lab and two-lab selection races, and price-update/offering-deactivation two-outcome-
  only races all proven directly against real PostgreSQL.

**NEXT: Liban's independent review of DH-EX-005 (the DH-EX-005R step), then a combined
commit/push/exact-SHA-CI ticket, then DH-EX-005D documentation closure, then DH-EX-006 — lab
acceptance and result workflow — not started.**

**CORRECTION appended 2026-09-10 (DH-EX-005R, append-only — the "NEXT: Liban's independent review"
line above is superseded, not retracted).** DH-EX-005R (the independent review) has now run. Still
**not committed, not pushed**. Full detail is in PROJECT_STATE.md's DH-EX-005R entry; the
load-bearing facts:

- **One BLOCKER found and fixed**: `LabSelectionRepository.select()` never re-validated the patient
  access grant inside its own mutation transaction — a grant revoked moments after the FastAPI auth
  dependency's own (already-closed) check could still authorize a real `LabSelection`. Proven
  exploitable deterministically (no race needed) before the fix; fixed by re-reading the exact grant
  row inside the transaction, right after locking the order, and verifying it's still bound to this
  order, unrevoked, and unexpired. Proven closed via SQLite (deterministic + a genuine concurrent-
  thread race) and **20 real-PostgreSQL races** (staggered to hit both orderings, 10/10 each way,
  zero corrupted outcomes), plus a real-Postgres issue-vs-revoke race and expiry-boundary control.
- Four smaller gaps closed: missing `Cache-Control: no-store` on all four patient-sensitive
  responses; no explicit length bound on the bearer token before hashing; no permanent negative
  control proving a real `LabResult` row never leaks through the patient order response; no
  audit-atomicity test for revocation (issuance had one, revoke didn't — the code was already
  correct, just untested for that path).
- Test-count delta from DH-EX-005's own 495→532 claim independently reconciled exactly: 33 new test
  functions in the new file + a net 4 new in `test_exchange_selection.py` (one legitimate rename,
  zero weakening) = 37, zero parametrization involved. Pre-existing baseline (495/39) independently
  re-run from a clean `git worktree` at the exact base commit to confirm it, not just trusted.
- Full regression after every fix: **541 passed / 39 skipped** (532 + 9 new tests from this review
  pass), `ruff`/`pip check`/`git diff --check`/secret-scan all clean. CI workflow diff confirmed to
  be exactly the one `EXPECTED_HEAD` line, nothing else.
- **READY FOR COMMIT: YES** per the ticket's own checklist, but the ticket explicitly says stop for
  Liban's sign-off regardless — not committed, not pushed.

**NEXT: Liban's own sign-off on this review, then a combined DH-EX-005+DH-EX-005R
commit/push/exact-SHA-CI ticket, then DH-EX-005D documentation closure, then DH-EX-006 — lab
acceptance and result workflow — not started.**

**CORRECTION appended 2026-09-10 (DH-EX-005 FINAL CLOSURE, append-only — the "NEXT: Liban's own
sign-off" line above is superseded, not retracted).** DH-EX-005 + DH-EX-005R were committed together
(`1a382f86df82c35e20a6bf79bccb22961d65cf47`, parent `f3d8715b83c29297f1671174453a549d3be45a69`),
pushed (after 14 attempts due to sustained network transport instability, independently confirmed
via `git ls-remote` before every retry that nothing was silently pushed), and exact-SHA CI qualified
(run `34514393091`, all 9 required jobs green, adapter `541 passed / 39 skipped` confirmed
independently in CI's own logs, migration head confirmed `0008_exchange_patient_access` against
CI's real `postgres:16-alpine` service, runtime-reported PostgreSQL 16.15). Full account:
PROJECT_STATE.md's **DH-EX-005 · FINAL CLOSURE** entry.

**DH-EX-001 through DH-EX-005 are all closed.** The DHealth Exchange currently has: the core
domain/persistence layer (DH-EX-001); organization identity, OIDC validation, memberships, tenant
roles, the platform-admin boundary, and a transactionally-authoritative security-mutation audit
(DH-EX-002); a lab directory, a synthetic-only pilot test catalog, lab offering administration, DJF
current pricing with CAS and immutable history, and whole-order lab eligibility (DH-EX-003);
DHealth-native Provider lab-order creation/read (DH-EX-004); and now opaque order-scoped patient
bearer access, patient auth structurally separate from OIDC, patient-facing order/lab-options views,
exact-price-id patient consent, in-transaction grant and marketplace revalidation, immutable frozen
price snapshots, and the transition to `SELECTED` (DH-EX-005). It does **not** yet have: a Provider
Portal or Lab Portal UI, a selected lab's own view of or action on its order, a result workflow, or
WhatsApp/SMS integration. **REAL KEYCLOAK QUALIFICATION remains NOT RUN** — stated plainly here
again because it is exactly the kind of fact that is easy to let go stale; DH-EX-005's patient path
is intentionally not Keycloak-based, and the provider-side surface it does touch reuses DH-EX-002's
already-qualified OIDC machinery unchanged.

**NEXT: DH-EX-006 — selected-lab workflow.** Golden backend journey after DH-EX-006: a selected LAB
can see its assigned order → accept or reject it → an accepted order follows an explicit processing
lifecycle — **not started.**

**CORRECTION appended 2026-09-10 (DH-EX-006, append-only — the "NEXT: DH-EX-006 ... not started"
line above is superseded, not retracted).** DH-EX-006 (selected-lab assigned-order workflow) is now
**implemented, self-reviewed, and qualified against both SQLite and real PostgreSQL 18.6**. Still
**not committed, not pushed**, and no DH-EX-006R independent review has run yet. Full detail is in
PROJECT_STATE.md's DH-EX-006 entry; the load-bearing facts:

- A `LAB_TECH` in an ACTIVE lab organization (new `require_lab_tech` authorization check, mirroring
  `require_clinician` — platform admin and `ORG_ADMIN` alone are both refused) can list/read its
  assigned orders and move one through `SELECTED`→`ACCEPTED`, `SELECTED`→`REJECTED`, or
  `ACCEPTED`→`IN_PROGRESS`. **Assignment ownership is `LabSelection` alone** — never re-derived from
  DH-EX-003 marketplace eligibility, so a lab that becomes unlisted, stops accepting orders,
  deactivates an offering, or changes its price after a patient already chose it still owns and can
  work that specific assigned order (proven by a permanent test, SQLite and real Postgres both).
  Frozen selection-time prices remain immutable throughout. Patient-grant revocation/expiry never
  erases the assignment (the two are independent facts once a selection exists).
- No `/complete` route, no path to `COMPLETED` anywhere in the new code, zero `LabResult` rows ever
  created by a workflow command, `DoctorReview`/`ResultDelivery` untouched — DH-EX-007's scope,
  deliberately not preempted.
- 29 new tests (golden accept+start-processing journey, golden rejection flow, cross-lab isolation,
  authorization matrix, replay idempotency, audit atomicity, frozen-price/patient-grant
  independence). Full adapter suite: **570 passed / 39 skipped** (was 541 before this ticket), ruff
  clean.
- Real PostgreSQL 18.6: fresh `0001→0009`, compatible downgrade, incompatible downgrade refusal
  (`DowngradeBlockedByLabWorkflowHistory`, before any DDL), 25-way accept/reject/start-processing,
  accept-vs-reject and accept-vs-start races (10 iterations each, always coherent, never a direct
  `SELECTED`→`IN_PROGRESS` shortcut), cross-lab refusal, and audit-rollback-on-failure for all three
  mutations, all proven directly.

**NEXT: Liban's independent review of DH-EX-006 (the DH-EX-006R step), then a combined
commit/push/exact-SHA-CI ticket, then DH-EX-006D documentation closure, then DH-EX-007 — lab result
submission and delivery — not started.**

**CORRECTION appended 2026-09-10 (DH-EX-006R, append-only — the "NEXT: Liban's independent review"
line above is superseded, not retracted).** DH-EX-006R (the independent review) has now run. Still
**not committed, not pushed**. Full detail is in PROJECT_STATE.md's DH-EX-006R entry; the
load-bearing facts:

- **No BLOCKER found.** The two BLOCKER-level targets this review most needed to check — that
  mutation + assignment-ownership check + audit all share one transaction, and that the
  wrong-lab-assignment check happens *before* idempotent-replay handling (so a wrong-lab caller can
  never get a false "replay success" on an order it doesn't own) — were both confirmed correct by
  fresh code reading, no fix needed.
- Four test-coverage gaps closed, all confirming already-correct behavior: a wrong lab probing an
  order Lab A already transitioned now has a permanent test (previously only the still-`SELECTED`
  case was tested); the `updated_at DESC` list-ordering claim now has a permanent test proving an
  older-but-just-mutated order sorts before an untouched newer one; rejection-terminality
  (accept/start refused after reject) now has its own test; and audit-actor-correctness under
  multi-user same-lab access is now proven deterministically (a genuine two-thread HTTP race was
  tried and abandoned — it reproduced the exact SQLite/TestClient fragility DH-EX-005R already
  worked around; the property itself doesn't need a race to prove).
- **Zero production code changed this review pass** — only 4 new tests. 574 passed / 39 skipped
  (was 570), `ruff` clean.
- Real PostgreSQL 18.6 independently re-verified (not just re-trusted): fresh `0001→0009`;
  incompatible downgrade tested against a *complete* real 0008-era clinical chain (order, frozen
  price, patient grant, selection, and a real `lab_order_accepted` audit row) — refused before any
  DDL, every row confirmed byte-identical afterward; 25-way accept/reject/start-processing;
  accept-vs-reject and accept-vs-start races (10x each); cross-lab refusal; audit rollback for all
  three mutations; post-selection marketplace independence; patient-grant independence — 11/11.
- **READY FOR COMMIT: YES** per the ticket's own checklist, but the ticket explicitly says stop for
  Liban's sign-off regardless — not committed, not pushed.

**NEXT: Liban's own sign-off on this review, then a combined DH-EX-006+DH-EX-006R
commit/push/exact-SHA-CI ticket, then DH-EX-006D documentation closure, then DH-EX-007 — lab result
submission and delivery — not started.**

**CORRECTION appended 2026-09-10 (DH-EX-006 FINAL CLOSURE, append-only — the "NEXT: Liban's own
sign-off" line above is superseded, not retracted).** DH-EX-006 + DH-EX-006R were committed together
(`1bfb08a19fc3f135e7e552f77b73acad4967c88d`, parent
`fcb6c6fd7dcbfda363da1ce84706ca19fdb3310f`) and pushed to `origin/master`, following Liban's
sign-off — first read from `.git` object files directly (no `git` binary reachable at that point),
then re-confirmed with real `git` once `C:\Program Files\Git\cmd\git.exe` was located:
`git rev-parse HEAD`/`HEAD^`/`origin/master` all match. **Exact-SHA CI qualification is confirmed by
Liban directly** (run `34537938219`, `head_sha` = `1bfb08a19fc3f135e7e552f77b73acad4967c88d`,
`event`=`push`, `status`=`completed`, `conclusion`=`success`, all 9 required jobs green, adapter
`574 passed / 39 skipped`, migration current/heads/expected all `0009_exchange_lab_workflow`, CI
PostgreSQL `16.15` via `postgres:16-alpine`) — checked by him against the real GitHub Actions run
and recorded here on his word, not independently reproduced by this session (`gh` remains
unavailable here). Full detail: PROJECT_STATE.md's **DH-EX-006 · FINAL CLOSURE** entry.

**DH-EX-001 through DH-EX-006 are implementation-closed: committed, pushed to `origin/master`, and
CI-qualified.** (This DH-EX-006D documentation-closure pass itself is not yet committed, pushed, or
CI-qualified — see PROJECT_STATE.md's DH-EX-006 · FINAL CLOSURE entry.)
The DHealth Exchange backend can now carry an authenticated clinician-created lab order from
`AWAITING_LAB_SELECTION` through secure patient lab selection (`SELECTED`) and into the selected
laboratory's own assigned work queue, where a `LAB_TECH` may accept (`ACCEPTED`), reject
(`REJECTED`), or start processing an accepted order (`IN_PROGRESS`). Assignment is `LabSelection`
alone, independent of later marketplace changes (listing/accepting/offering/price); frozen
selection-time prices are never rewritten. It does **not** yet have: result submission,
`IN_PROGRESS`→`COMPLETED` semantics, provider result delivery/review, doctor follow-up disposition
(`FOLLOW_UP_REQUIRED`/`NO_FOLLOW_UP_REQUIRED`), WhatsApp/SMS transport, or any Provider/Lab/Patient
portal UI. **REAL KEYCLOAK QUALIFICATION remains NOT RUN.**

**CORRECTION appended 2026-09-11 (DH-EX-006D2, append-only — the parenthetical above, "This
DH-EX-006D documentation-closure pass itself is not yet committed, pushed, or CI-qualified," is
superseded, not retracted; it was true when written).** DH-EX-006D's own documentation-closure
commit `f5309db8310a13295ee32b197e07d0bc24ab470c` (parent
`1bfb08a19fc3f135e7e552f77b73acad4967c88d`) is committed and pushed to `origin/master` — confirmed
directly by this session via `git`. Its CI — run `34618617566`, all 9 required jobs green, adapter
`574 passed / 39 skipped, 1 warning`, migration head `0009_exchange_lab_workflow`, CI PostgreSQL
`16.15` via `postgres:16-alpine` — is recorded **per Liban's report in the DH-EX-006D2 ticket, not
independently reproduced by this session** (`gh` remains unavailable here). This DH-EX-006D2
correction commit's own CI has likewise not been checked by this session. Full detail:
PROJECT_STATE.md's **DH-EX-006 · FINAL CLOSURE** entry.

**NEXT: DH-EX-007 — lab result submission and delivery — not started.**

**CORRECTION appended 2026-09-12 (DH-EX-007D, append-only — the "NEXT ... not started" line above
is superseded, not retracted; it was true when written).** DH-EX-007 (lab result submission, order
completion, provider result retrieval, and doctor review) has since been implemented, committed,
and pushed to `origin/master`: commit `30c5212` (feature implementation) followed by `053004a`
(CI-only, advancing `EXPECTED_HEAD` to `0010_exchange_results_workflow`), both merged into
`origin/master` via PR #1 at merge commit `d3a72d5c`. This documentation-closure pass (DH-EX-007D)
audited `d3a72d5c` directly.

**What DH-EX-007 established, per commit `30c5212`'s own message and direct code inspection of
`adapter/src/daher_adapter/exchange/{api.py,results.py,models.py}`:** the selected lab's
`LAB_TECH` (`require_lab_tech`, same boundary as DH-EX-006) submits exactly one immutable result
per ordered item once the order is `IN_PROGRESS`; result mutations serialize at the `LabOrder`
aggregate; the order transitions to `COMPLETED` only once PostgreSQL proves every item now has a
result. The ordering provider (`require_clinician`, same boundary as DH-EX-004) retrieves completed
results only after the order is `COMPLETED` (409 otherwise, never a bare 404 that would misrepresent
a real, owned, not-yet-ready order) and records a terminal review disposition
(`no-follow-up-required` / `follow-up-required`) through two new routes. `ResultDelivery` stays
`PENDING` — no delivery, message, or notification code was added. The patient-facing path is
unchanged: `PatientOrderResponse` (api.py, `extra="forbid"`) defines no result field, and the only
`/patient/*` routes remain the three from DH-EX-005 (`GET /patient/order`,
`GET /patient/order/lab-options`, `POST /patient/order/selection`) — no patient route returns a
result value. Migration `0010_exchange_results_workflow` is the tenth adapter migration and the
ninth Exchange one; `EXPECTED_HEAD` in `.github/workflows/ci.yml` was independently confirmed to
read `0010_exchange_results_workflow`, matching the migration chain on disk.

**Evidence boundary, stated precisely — this documentation-closure session's own verification, not
a re-statement of the commit's claims as fact.** Confirmed by this session via direct code/file
inspection: the routes, models, migration, and patient-response schema described above all exist as
described on `origin/master`'s HEAD. **Not confirmed by this session:** the commit message's own
"605 passed / 42 skipped / 1 pre-existing warning" and "real PostgreSQL result-concurrency
qualification passed" — no Python interpreter was reachable in this environment, so the suite was
not re-run; these are recorded as the implementing session's claim only. **CI for both `30c5212`
and `053004a` (and therefore for merge commit `d3a72d5c`) is NOT PROVEN in this session** — `gh` is
not reachable on `PATH` (searched `C:\Program Files`, `C:\Program Files (x86)`,
`%LOCALAPPDATA%\Programs`, and a full filesystem search), and the repository is private to
unauthenticated web access (`api.github.com` and `github.com` both returned 404 for this repo to an
unauthenticated `WebFetch`). Unlike DH-EX-006's closure entries, no first-person "Liban checked the
real run" confirmation was given for `30c5212`/`053004a` at the time of this pass either — this is
an open item, not a claim resolved one way or the other. **REAL KEYCLOAK QUALIFICATION remains NOT
RUN** — DH-EX-007 reuses DH-EX-004/006's existing `require_clinician`/`require_lab_tech` machinery
and adds no new authentication surface. **Manual live/browser demonstration NOT RUN.** The
implementing commit also names one gap it did not close: audit row counts were not directly
observed through an API-over-live-PostgreSQL concurrency harness during the final-two-results race
(SQLite/direct-repository concurrency proofs were run instead, per this ticket's own precedent from
DH-EX-005R/DH-EX-006R).

**What this does not mean:** no Provider Portal, Lab Portal, or patient UI exists; no WhatsApp/SMS
or any other result-delivery transport exists (`ResultDelivery` rows stay `PENDING` indefinitely
under current code); no OpenMRS or OpenELIS Exchange connector exists (no file under
`openmrs-config/` or `openelis-config/` changed); no production deployment exists; no pilot has
begun. Real facility: untouched. Pollers: inactive, unrelated to this ticket.

**Classification for this ticket's surfaces, per the evidence above:** result submission, automatic
completion, provider result retrieval, and doctor review are **WRITTEN** (confirmed present in code
on `origin/master`) and **TESTED per the implementing commit's own message** (not independently
re-run this session) — they are **not CI-QUALIFIED**, because no GitHub Actions run for either
commit has been independently observed by any session, unlike DH-EX-001 through DH-EX-006 which
each have an independently- or Liban-confirmed exact-SHA CI run on record.

**CORRECTION appended 2026-09-12 (DH-EX-007D2, append-only — "CI for both `30c5212` and `053004a`
... is NOT PROVEN in this session" and "not CI-QUALIFIED" above are superseded, not retracted; both
were true of the DH-EX-007D session that wrote them).** A later session (this one, DH-EX-007D2)
retrieved GitHub's stored git credential already authorized for this repository (`git credential
fill`, not `gh`) and used it to call the GitHub Actions API directly, closing the evidence gap the
paragraph above named as open:

- Run `34649375844`, merge commit `d3a72d5c42d3151d39637831a87e541ee4dfc562`: `head_branch=master`,
  `event=push`, `status=completed`, `conclusion=success`; `total_count=9` jobs, every job's own
  `status`/`conclusion` pair `completed`/`success` (verified by field position, not by trusting a
  substring match against step-level fields of the same name). The 9 job names match the repo's
  established set: OpenMRS narrow TestOrder module, provisioning, oauth2login patched identity
  tests, static checks, ui, openelis order-correlation patch, seed + form-safety suites, compose
  interpolates and parses, adapter — ruff + pytest.
- Run `34656008030`, the DH-EX-007D documentation-closure commit `a17ebad456498a2ebfff148af511618f814a54bf`:
  identical result — `head_branch=master`, `event=push`, `status=completed`, `conclusion=success`,
  9/9 jobs `success`.

**DH-EX-007 is now exact-SHA CI-qualified**, on the same footing as DH-EX-001 through DH-EX-006.
This closes the CI evidence gap only — it does not change anything else in this entry: **REAL
KEYCLOAK QUALIFICATION remains NOT RUN, manual live/browser demonstration remains NOT RUN,
production deployment does not exist, and no pilot has begun.** A green CI run proves the test
suite and static checks passed on GitHub's runners; it does not independently confirm the
605/42/1 test-count itself (this session read the run's job/step metadata, not its log output line
by line), and it proves nothing about real-world/clinical readiness.

**DH-EX-014B update (2026-09-14):** Post-release amendments now preserve immutable observation
history, release a new manifest/version, and create a fresh version-bound clinician review
obligation. SQLite unit/API tests (136 passed) and real-PostgreSQL qualification (33 passed across
6 test suites, migration 0018->0019 upgrade/downgrade clean, and both downgrade guards verified)
pass. Real Keycloak qualification, manual live/browser demonstration, and production deployment
remain not run; this is not a pilot-readiness claim.

**DH-PATIENT-001 update (2026-09-20):** The first DHealth Patient frontend now exists
(`ui/src/features/patient/`) — a single-screen, mobile-first, French-first status view authorized
solely by DH-EX-005's pre-existing order-scoped opaque grant, entered by QR code or link with the
credential in the URL fragment and scrubbed from the address bar on ingestion, never persisted in
browser storage, and never routed through Keycloak. A Clinic-side handoff issues/revokes/rotates
that access and produces a locally-generated QR plus a printable sheet for patients without a
smartphone. Backend: a provider-authorized patient-access metadata read and a server-owned
`result_status` projection that reverts to awaiting-review when an amendment version is released.
Patients see status only — never a result value, range, price, internal identifier, or clinician
identity. 18 new adapter tests (1104 → 1122 passed) and 92 new frontend tests (684 passed across
all 46 UI test files, measured in batches), with 18 negative
controls actually executed; three real bugs found and fixed (naive `expires_at` on SQLite read-back,
a 204 parsed as an ambiguous failure so every successful revoke looked lost, and a false "Lien
copié." when the Clipboard API is absent). No migration — Alembic head unchanged at
`0025_native_result_binding`. **Not run, and not claimed:** no real browser rendered this screen, no
real phone scanned one of these QR codes, no printer produced one of these sheets, responsive/print
behaviour is reasoned rather than measured, and real Keycloak qualification, manual live/browser
demonstration, and production deployment all remain not run. No SMS/WhatsApp/email/voice transport
was added and `ResultDelivery` is untouched. This is not a pilot-readiness claim.

**DH-MVP-ONBOARD-001 update (2026-09-21):** DHealth can now represent how a clinic or laboratory
*becomes* a participant, which it previously could not: an `Organization` row said a name and a type
existed and nothing else. A platform operator enrols a facility, records whether it will use DHealth
directly (`NATIVE`) or keep its existing software (`CONNECT`), captures what that software is, and
reads a server-derived answer to "can clinics send orders here, and if not exactly why". Migration
`0026_facility_enrollment` adds three tables and backfills every existing organization as
`NATIVE`/`ORGANIZATION_READY`.

**What actually works:** enrollment and organization creation in one transaction; explicit
participation-mode choice, refused once real work depends on the current mode; Connect technical
discovery with three distinct truth levels (reported / documented / verified by DHealth); a
server-derived readiness verdict computed from live rows on every read; a production-routing gate on
all three paths that can send work (the activation endpoint, `assign_by_provider` inside its row
lock, and the eligible-destinations read); and a platform screen that switches routing on, with a
two-gesture confirmation, no control at all for a Connect or unready laboratory, and a server
refusal rendered reason by reason.

**The distinction this ticket exists to protect:** enrolled ≠ configured ≠ operationally ready ≠
production-routable. A Native laboratory that loses its last `LAB_RESULT_RELEASER` keeps its
configured route untouched, stops being an eligible destination, and reads on screen as *Relation
configurée · Routage indisponible* — both halves at once. Restoring the staffing makes the same
route usable again with nothing recreated.

**NOT BUILT, and not implied anywhere in the product:** DHealth Connect transport. No FHIR, HL7,
SFTP, webhook, agent, VPN, OpenELIS or OpenMRS binding; no partner credential storage; no mapping,
connector health, or qualification. `READY_FOR_ROUTING` is unreachable for a CONNECT facility by
construction, and no interface state or operator action can move it. That is
DH-CONNECT-CONTRACT-001's subject. Also not built: Keycloak user provisioning and any invitation
system — the intended administrator must sign in once before they can be attached, and the wizard
currently finds that person without yet creating their membership.

**Not run, and not claimed:** no real browser has been used by a person (seven platform scenes were
captured in headless Chromium at 1280/768/390 and two were reviewed by eye); no E2E; no live stack;
no real Keycloak. **GitHub Actions executed nothing for any commit in this ticket** — every job was
refused before starting, and GitHub's own annotation on run `35621071589` gives the cause:
*"The job was not started because recent account payments have failed or your spending limit needs
to be increased."* Repository code was never evaluated, so there is no CI conclusion to report in
either direction. Local qualification is the authoritative evidence: adapter 1168 passed / 118
skipped, 811 UI tests across all 54 files, migration re-qualified against real PostgreSQL 18, and
five negative controls executed and reverted. This is not a pilot-readiness claim.

**DH-MVP-ONBOARD-001 correction (2026-09-21):** two gaps the ticket itself had left open are closed.
**The enrollment wizard now performs the first-administrator attachment it was offering** — the
membership and role assignments are created in the same transaction as the organization and its
enrollment, so a failure creates nothing at all rather than a facility nobody can administer; the
completion screen reports the administrator read back from what was persisted, never from what was
selected. **And a Native laboratory can only be sent work DHealth can finish**: an active offering
with no active result template is refused per service (not an eligible destination, and direct
assignment refused naming the test), while the laboratory's other services route normally; a
laboratory with no templated offering at all is blocked outright. Partial coverage deliberately does
not block the facility, and nothing deactivates an offering or fabricates a template. **CONNECT is
unaffected by that rule by construction** — a partner's results are produced in their own system and
the Exchange observation model stays source-flexible for the connector work to come. Adapter tests
1168 → **1189 passed, 118 skipped**; four further negative controls executed and reverted; ten
platform scenes captured at 1280/768/390 with `PROBLEMS: none`. No migration. Still not run, still
not claimed: no real browser used by a person, no E2E, no live stack, no real Keycloak, and no CI
conclusion — GitHub Actions remains blocked before step execution by the account billing condition.

**DH-MVP-ONBOARD-001 closed (2026-09-22):** the last onboarding hole is shut. `first_administrator`
is optional at enrollment on purpose — a partner is routinely signed up before the person meant to
run the facility has ever logged in — but that left a facility that **nobody could administer and no
screen could fix**, reachable only through the API with somebody's identity UUID. Every facility
detail now has an **Équipe et accès** section: who belongs here, what each of them may do, whether
that access is live, and *Ajouter une personne* → search → select → roles → membership, after which
the facility's readiness is re-read so the `administrator_present` blocker clears from the server's
answer rather than from React's guess. **This is frontend work only** — it calls the
`GET`/`POST /organizations/{id}/memberships` pair that already existed, which a platform
administrator reaches through `require_membership`'s `via_platform_admin` bypass; no new endpoint,
no new model, no new audit vocabulary, no migration. The search/select/roles panel was extracted
from the enrollment wizard rather than copied, so the two routes to a membership cannot drift.
**Nothing on the screen removes access:** `POST .../memberships` is ensure/add and this API revokes
nothing, so a role already held renders ticked and disabled, an inactive membership is listed but
not editable, and no control carries a label implying a removal the server would refuse. Adapter
1189 → **1194 passed, 118 skipped** (TM1–TM5 pin the platform-admin reachability, the
attach-then-readiness loop, the 404 for a stranger, the add-never-replace semantics and the
server-side role refusal); UI 56 of 56 test files accounted for, **831 passed, 0 failed**; two
negative controls executed and reverted; thirteen platform scenes at 1280/768/390 with
`PROBLEMS: none`. Deliberately still absent, by this ticket's scope: invitations,
Keycloak account provisioning, role removal, membership reactivation. Still not run, still not
claimed: no real browser used by a person, no E2E, no live stack, no real Keycloak, and no CI
conclusion.
