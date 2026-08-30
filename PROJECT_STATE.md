# Project State — Daher Health

**Updated:** 2026-08-21 · **Phase:** provisioning control plane works end to end on a stand-in;
DH-011 transport is **blocked on BLOCKER-6** (target ingestion path — a human decision), not the
unblocked critical path this line claimed until now (stale since BLOCKER-6 was raised 2026-08-16,
the day after this line was written)

**⚠️ Recorded schedule trade, made deliberately by Liban 2026-08-15.** The provisioning API and
portal are being built *before* DH-011→012→013. The consequence, stated once and accepted: the
order/result transport chain and therefore the **golden end-to-end flow will very likely not be
demonstrated by 1 September**. This is a decision, not an oversight — it is recorded here so nobody
later reads the missed date as a delivery failure.
**Target:** pilot-ready 2026-09-01 · **Not** clinically live (ADR-012)

Single agent, one ticket at a time. Update this file before ending a session.

---

## Current phase

**Step 1** (MariaDB + OpenMRS O3) boots and works — verified through REST and FHIR, and by Liban
himself in the SPA. **Step 3** (PostgreSQL + OpenELIS) has its runtime foundation verified: three
co-tenant databases, 375 base tables in the fully migrated OpenELIS `clinlims` schema, webapp on
HTTPS, and a FHIR server answering `metadata` over mutual TLS. **DH-019** closed the published
default admin password. BLOCKER-4 records an upstream analyzer configuration import that is not
verified.

**DH-008's fail-closed protocol plumbing is built and runtime-verified.** OpenMRS has official
OAuth2 Login 1.5.0 built from its exact release commit and installed into both fresh and existing
data volumes; OpenELIS uses its working upstream SAML path. Keycloak fresh definitions and the
existing-realm reconciliation agree on OpenMRS OIDC and OpenELIS SAML. The reconciliation passed
twice and the normal stack is healthy with `SSO_ENABLED=false`, preserving local logins. Activation
and the one-login demonstration are blocked by BLOCKER-5: native application-role mapping and the
OpenMRS-provider rule require Liban's approval.

The running stack surfaced multiple findings that `docker compose config` could never have caught
(`CURRENT_STATE.md` §3a and DH-005's ticket entry). Buildable work remains alongside the decisions
gathered under **NEEDS LIBAN** below.

**DH-003 is complete and empty-volume verified.** French backend locales, the `Maternité`
admission ward, both decided bed types, the explicit demo-content switch, and an additive-only
bed seed are configuration as code. The real bed counts remain a facility placeholder rather
than something an agent may invent. The SPA's visible language still needs a human browser look;
the backend properties and frontend environment are verified, but that observation is not inferred.

**Quality verification 2026-08-13:** the current Compose stack passed config validation; all ten
long-running project services had zero restarts and zero OOM kills; OpenMRS REST/FHIR, OpenELIS
webapp, OpenELIS FHIR mutual TLS, the OpenELIS frontend's internal HTTP endpoint, Keycloak, and the
adapter readiness path answered. The adapter test image again passed dependency consistency, Ruff,
and all 14 tests. Shell syntax, realm JSON, OpenMRS XML, Git integrity/whitespace, tracked-secret
patterns, image-tag policy, and every relative Markdown link passed static checks. A completed
two-container `daher-demotest` password-verification stack was found and stopped without deleting
its containers or volumes. The backup scheduler remains intentionally absent from the running
stack until production encryption/off-site settings are supplied; DH-015 was unperformed at that
time — it has since been **performed 2026-08-13**, see the DH-015 ticket.

**Deep quality verification 2026-08-13:** both local images rebuilt; adapter dependency consistency,
Ruff lint/format, and all 14 tests passed. OpenMRS REST/FHIR and configured admin authentication,
OpenELIS HTTPS/FHIR mutual TLS/frontend, Keycloak discovery/realm, adapter readiness, all nine
PostgreSQL role/database cases, all container timezones, and aggregate database state passed.
Every running/setup container had zero restarts and zero OOM kills. A fresh local-only encrypted
backup captured all seven required components and passed decrypt/manifest/schema/data read-back;
its archive and status marker were removed because the ephemeral key was not retained. This was
not an off-site test or restore. The audit also corrected the local `.env` from mode `0664` to
`0600`, rotated the old live OpenMRS volume away from `Admin123`, completed DH-022, and raised
BLOCKER-4 for the reproducible OpenELIS analyzer import conflict.

**DH-003 verification 2026-08-13:** exact Initializer 2.12.0 and bedmanagement 7.2.0 source was
checked before selecting the native REST seam. Five seed safety tests plus Ruff lint/format passed.
The live throwaway stack demonstrated same/increase/lower/zero count behavior, and an isolated
empty-volume stack demonstrated no-click first boot with demo patients/content off. The normal
`docker compose up -d` then completed with all ten long-running services healthy/running as
applicable, all three setup jobs exited 0, and every container reported zero restarts and zero OOM
kills. Exact evidence and the remaining visible-browser-language check are in DH-003 below.

**DH-024 fresh-deployment audit 2026-08-13:** found and fixed three paths that looked healthy but
were unsafe on real or empty volumes. OpenMRS had two writers for the demo-patient trigger and this
throwaway database received a second 50-row batch; upstream's trigger is now removed from the local
derived base so `patients.on/off.xml` is authoritative. A direct first-boot `on/on` also proved the
upstream generator runs before its metadata is ready; the new read-only preflight refuses that
state, while the enforced two-stage path produced exactly 50 patients. OpenELIS PostgreSQL
readiness now rejects the temporary init server, and a post-webapp password job replaces/verifies
the ignored published admin hash before the frontend starts. The exact empty-volume OpenELIS graph
passed: database healthy, hardening 0, webapp healthy, password job 0, frontend running, configured
form login accepted, `adminADMIN!` refused. No existing patient row, laboratory test, or analyzer
mapping was changed.

A fresh local-only encrypted backup also passed decrypt, seven-entry manifest/hash validation,
three `pg_restore --list` reads, MariaDB patient DDL/data checks, Keycloak JSON parsing, and both
volume archive reads; the ephemeral-key archive/status were removed. This is not off-site or a
restore, so DH-014/DH-015 remained open at that time. DH-015 has since been **performed
2026-08-13** with partial coverage; DH-014's off-site leg is still unexercised.

**DH-008 deep verification 2026-08-13:** both OpenMRS backend targets built with official OAuth2
Login 1.5.0 from the exact release commit. The live persistent volume received the byte-identical
OMOD; OpenMRS REST reported it registered and stopped in fail-closed mode, configured local admin
authentication and FHIR passed, and `Admin123` remained refused. OpenELIS configured CSRF/form
login passed, `adminADMIN!` remained refused, and mutual-TLS FHIR metadata passed. OIDC discovery,
SAML metadata, both OpenELIS HTTP paths, and adapter readiness answered. Keycloak existing-realm
reconciliation passed repeatedly, an isolated networkless H2 import proved the fresh realm JSON,
and a final clean Keycloak recreation imported/skipped normally with zero restarts. The complete
project container set finished at zero restarts/OOM kills and every setup job at exit 0. Adapter
dependency/Ruff/14 tests and maternity Ruff/5 safety tests passed; Compose variants, shell, JSON,
XML, Git, image-tag policy, whitespace, and relative Markdown links passed. SSO activation itself
was deliberately not attempted because BLOCKER-5 is unresolved.

**Post-commit deep quality audit 2026-08-13:** rebuilt OpenMRS `on`/`off`, adapter, and backup
images; adapter dependency/Ruff/14 tests and maternity Ruff/5 tests passed. All Compose variants,
shell/JSON/XML/CSV syntax, Git integrity/whitespace, tracked-secret scan, image policy, and relative
Markdown links passed. The normal graph has ten running services, six setup jobs at exit 0, only
the OpenMRS gateway published on `127.0.0.1:8080`, and no backup scheduler by design. Configured
OpenMRS/OpenELIS logins pass while `Admin123`/`adminADMIN!` fail; both FHIR metadata paths,
OpenELIS frontend, OIDC/SAML metadata, adapter readiness/service token, all nine PostgreSQL tenancy
cases, aggregate data state, and every timezone passed. Keycloak fresh import, repeated existing-
realm reconciliation, and persisted-realm restart passed. Final project containers report zero
OOM kills and no crash restarts.

The same audit found two honest gaps. Both shipped Horiba CBC imports—not only Pentra60—fail on
the existing `LYM#(Whole Blood)` description, so BLOCKER-4 now asks about Micros60 and Pentra60.
More importantly, a durable unique ledger claim cannot resolve a crash after the target write but
before `processing` becomes `completed`. No transport exists, so no message has been affected;
DH-025 is now a P0 technical gate requiring target reconciliation and crash-point tests before
DH-011 may activate.

**DH-025 implementation 2026-08-13:** the adapter now derives a stable opaque correlation from the
full source-event identity, retains a PostgreSQL advisory fence for its complete process lifetime,
and exposes a typed recovery coordinator. Exactly one target match completes the existing claim;
proven zero matches authorises one retry; an unreachable target, multiple matches, `dead_letter`,
or any other ambiguity fails closed. The suite now passes 27 tests, including actual concurrent
claims and crashes immediately before/after a target write. A rollback-only live-PostgreSQL probe
exercised recovery without retaining a row; a second process was refused while the healthy adapter
held its fence; restart released/reacquired it; operational rows remain zero.

**Boundary not claimed:** no OpenELIS resource currently carries that correlation and no concrete
OpenELIS lookup has been demonstrated because DH-011's order shape/requester/mapping is not yet
selected. The transport-neutral safety core is verified; the writer/probe binding remains an
activation criterion inside DH-011, alongside BLOCKER-3. No clinical message moved.

**Deep quality verification 2026-08-13 (post-ADR-015/016).** Both Compose profiles parse; every
tracked shell/JSON/XML file passes syntax; Git integrity and whitespace pass; `.env` is untracked.
The three non-digest image references are the locally built `daher-health/*` images, and all four of
their `FROM` bases are digest-pinned. Adapter: `pip check`, Ruff lint, Ruff format, and **27 tests**
passed; maternity seed safety **5 tests** passed. Runtime: ten services running, six setup jobs at
exit 0, **zero restarts and zero OOM kills across all sixteen containers**. OpenMRS REST session and
FHIR R4 metadata answered 200 for the configured admin while `Admin123` returned
`authenticated:false`; OpenELIS webapp HTTPS 200; OpenELIS FHIR returned 200 with FHIR 4.0.1 from
HAPI **over mutual TLS**, and correctly failed TLS with `bad certificate` when no client
certificate was presented; OpenELIS frontend 200; Keycloak realm, OIDC discovery, and SAML
descriptor 200. All nine PostgreSQL tenancy cases behaved exactly as specified. Every container is
`EAT+0300`. Only `127.0.0.1:8080` is published. The adapter ledger and dead-letter tables remain at
zero rows on migration `0001_safety_ledger`; OpenMRS holds 102 patients, none voided. OAuth 2.0
Login 1.5.0 is registered and `started=false` (fail-closed); no backup container exists in the
default profile, by DH-022's design.

**Two probe-method corrections, recorded so the next audit does not repeat them.** The OpenELIS
frontend serves nginx on **port 80**, not 3000 — a probe against 3000 returns connection refused and
looks like an outage. Keycloak and the frontend images contain **no `curl`**; probe them with Python
from the adapter container over `daher-health_internal`. Extracting the client certificate for the
mutual-TLS FHIR probe needs `openssl pkcs12 -legacy`, because the shipped keystore uses RC2-40-CBC
which OpenSSL 3 refuses by default.

**A false alarm worth recording.** An initial OpenELIS login check judged success by grepping the
Dashboard body for "login", and reported that `adminADMIN!` still authenticated. It does not. The
POST returns 302 in every case; the discriminator is the `Location` header — configured admin
redirects to `/OpenELIS-Global/`, while `adminADMIN!` redirects to `/LoginPage`, byte-identical to
the behaviour of a deliberately wrong control password. DH-024's result stands. Test the redirect
target, not the page text, and always run the wrong-password control.

**End-of-session verification 2026-08-14.** Both Compose profiles parse; all tracked
shell/JSON/XML/CSV syntax, Git whitespace, and the untracked-`.env` check pass; the only non-digest
images are the four locally built `daher-health/*`, whose bases are all pinned. **Three test suites
pass: adapter 27, seeds 16, forms 18 (+1 skipped)**, with Ruff lint/format clean in all three under
the project config. Runtime: **11 services running, 7 setup jobs at exit 0, zero restarts, zero OOM
kills**. Configured OpenMRS login passes while `Admin123` is refused; OpenELIS serves DH-021's
generated certificate (`CN=openelis.org`, not expired); all three Caddy vhosts answer and the
OpenELIS API shares the `lab` origin. Data unchanged: 102 patients, ledger 0 rows, `Bilan prénatal`
present with **0 members** exactly as intended.

## Ticket status

| Status | Tickets |
|---|---|
| ✅ Done | DH-001, **DH-002** (Liban confirmed 2026-08-11), **DH-003**, **DH-007**, **DH-009a**, **DH-009b**, **DH-010**, **DH-019** (empty-volume verified; old live volume rotated), **DH-020**, **DH-022**, **DH-024**, **DH-011A** (ADR-026), **DH-011B** (spike performed) |
| 🟢 Built + verified, awaiting human input | **DH-005** (Liban's own look) |
| 🔨 In progress | DH-014 (local encrypted path verified; off-site unverified), DH-017 (README brought current; needs a fresh-VM walk-through), **DH-025** (recovery core verified + committed; concrete OpenELIS binding remains with DH-011C) |
| 🟢 Built + verified, activation blocked | **DH-008** (protocol/client plumbing; BLOCKER-5 — 🗓 meeting) |
| 🔨 Performed, partial coverage | **DH-015** (restore drill performed 2026-08-13; re-run needed for the lab-result and SSO legs) |
| 🔨 Built + locally verified, external input to finish | **DH-016** (routing/TLS verified locally; public certificates wait on #15 DNS) |
| 🗓 Blocked on the birth-centre meeting | **DH-004** (ADR-006 guard built; fields await sign-off), **DH-006** (mechanism built; contents await sign-off), **DH-008** activation (staff **names** only — types decided, ADR-017) |
| 🟢 Built + verified, **not activated** | **DH-011E** (OpenMRS poller + durable watermark, 94 tests, 5 controls — no loop runs it and no delivery target exists) |
| ✅ **BLOCKER-6 RESOLVED BY DECISION** 2026-08-28 | Option F selected (ADR-026); adapter Option-F client + DH-025 binding built and tested; OpenELIS search + persistence + uniqueness all built, compiled, and live-proven on isolated infrastructure (real DB concurrency race proven on the unique index). DH-011 itself is **not** complete — see below. |
| ✅ **BLOCKER-11 RESOLVED BY DECISION** 2026-08-29 (DH-011M) | Liban approved **Option P**: patch `ServiceRequestProvider`/`FhirTransformServiceImpl` to generate a real accession number (the same native generator the human Sample Entry workflow uses) and let `SamplePatientUpdateData.createPopulatedSample`'s existing "create new sample" branch run, instead of an ElectronicOrder-first inbox (Option O) or a Specimen-first bootstrap (Option S, already ruled structurally incomplete). **Built and live-proven** on fully synthetic disposable infrastructure (own generated keystore/DB credentials, `.env` never read): new-order happy path (real accession, one Sample/SampleItem/Analysis, correlation preserved, no fabricated collection facts, discoverable by the real DH-025 probe/adapter writer), duplicate delivery (rejected, zero extra rows), concurrent delivery (DB-level race: one winner, loser leaves zero orphan rows, atomicity holds), crash-ambiguity through the concrete adapter writer (not mocked), and the actual dashboard endpoint a lab worker sees (`ORDERS_IN_PROGRESS`) showing no collected/received claim. A safety gap found and fixed during this ticket's own invalid-input testing: a *dangling* specimen reference (present but unresolvable) was indistinguishable from *no* specimen reference and would have silently created a duplicate order — now fails loud (400) instead. **New finding, NOT fixed here**: adding a second test to an already-existing order (`ServiceRequest.specimen` referencing a real resolvable SampleItem) throws `StaleObjectStateException` live — control-tested against a completely pristine, unpatched upstream build and reproduced identically, conclusively proving this is a **pre-existing upstream defect unrelated to any Daher patch**, not a DH-011M regression. Out of this ticket's scope to fix ("smallest patch", "one ticket only"); needs its own ticket. Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011M section. |
| 🟡 **DH-011** remaining leg (2026-08-29, updated by DH-011P) | DH-011C's `ServiceRequest` half is now **built, live-proven, committed, and pushed** for both new-order creation (DH-011M) and adding a test to an already-existing order (DH-011P/P2/P3, design B) — commit `05b55b292a3672dd75b7d67bbdb14158fee19066`, pushed to `origin/master`, CI run `33257047337` all-green (verified via `gh run view`, not assumed). See below for details. Still open, unaffected by DH-011P: DH-011G (patient identity — human decision on identifier system, not resolved), DH-011H (real requester, NEEDS LIBAN #6), DH-011I (test mapping, DH-006), DH-011J (cancellation), DH-011K (crash points beyond what DH-025's generic tests already cover — including DH-011P's own item 9 crash-recovery gap, see below), DH-011L (demonstration). The numeric site code (NEEDS LIBAN #7) is **no longer the reason new orders can't be created** — DH-011M proved that live — but is still needed for the real facility's own accession prefix (currently the upstream placeholder `DEV01` in every environment tested). **DH-011 overall remains NOT COMPLETE.** |
| 🛑 **DH-011N — Human Decision Required: Yes (add-vs-replace semantics)** 2026-08-29 | DH-011M's "pre-existing upstream defect" claim for the existing-order `StaleObjectStateException` was reproduced against a directly-SQL-inserted fixture (its own text flagged this: "FHIR SEARCH FIXTURE ONLY / NOT NATIVE ORDER PROOF"), which is itself a common artificial cause of this exact exception — the conclusion was correct but under-proven. DH-011N closed that gap: reproduced the identical failure **live, three times, against a genuinely native, Hibernate-managed `Sample`** created through Option P's own path, then root-caused it by elimination (DB triggers, Hibernate JDBC batching — both `batch_versioned_data` and `batch_size` tested live, hand-replaying Hibernate's exact failing SQL via psql proved `UPDATE 1`, ruling out a real version mismatch) down to **detached-entity reuse across session boundaries** (outcome B): `ServiceRequestProvider.createServiceRequest` loads/resolves the existing `SampleItem`/`Sample` twice *before* `daherOrderTransaction.persist(...)` opens the only transactional boundary in the method (matches DH-011D3's own finding A javadoc), each in its own short-lived, already-closed session — confirmed by a live DB trace showing **seven** separate `SELECT ... FROM clinlims.SAMPLE WHERE ID=?` round-trips for one request, which a single Hibernate session's first-level cache would never produce. Live-proven the fix's correct *shape* (outcome C) too: the native "add a test" UI path, `SampleEditServiceImpl.editSample` (`POST /rest/SampleEdit`), succeeds cleanly on the identical fixture because it is `@Transactional` end-to-end and only re-saves `Sample` `if (sampleChanged)` — a deliberate guard the FHIR path lacks. **Not fixed, by design**: routing the existing-order branch to `SampleEditService` requires synthesizing a `SampleEditForm`'s `existingTests` (with per-item `canceled`/`removeSample` flags) from current DB state, since a `ServiceRequest` create carries only the test being added — an assumption ("add this test, touch nothing else") never previously stated as a decision, and exactly AGENTS.md's flagged "obviously should be filled a particular way" trap. **Human Decision Required: Yes.** Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011N section. No production code changed; real facility never touched; disposable stack fully torn down (containers, volumes, network, test image). |
| 🛑 **DH-011N — Human Decision Required: Yes (DH-025 ledger contract for "add a test")** 2026-08-29 | Separate from the above: DH-011N's live reproduction of "add a test to an existing order" required a *fresh* Daher correlation per attempt, purely because `ServiceRequestProvider`'s existing uniqueness check demands a not-yet-used correlation on every create. Whether DH-025's adapter ledger is meant to model "add a test to an existing order" as its own distinct source event (and, if so, what reconciliation looks like for it), or something else entirely, is an adapter-contract question DH-011N has no basis to decide — it is upstream of and independent from the add-vs-replace question above. **Human Decision Required: Yes.** |
| 🛑 **DH-011O — both DH-011N blockers resolved by decision; ONE new, narrower blocker: which correlation-storage design — Human Decision Required: Yes** 2026-08-29 | Liban approved both DH-011N decisions: (1) existing-order updates are **additive** (add the requested test, never implicitly touch/cancel/replace existing analyses — explicit cancellation stays governed by the existing cancellation contract); (2) each added test is its **own independent DH-025 source event** with its own stable correlation, independently deduplicated/recoverable, while the accession itself stays stable. DH-011O verified decision (1) needs **no synthesized state at all**: source-proven (`SampleEditServiceImpl.createRemoveList`/`createSampleItemUpdateList` iterate only the *submitted* `existingTests` array and act only on items explicitly flagged `canceled`/`sampleItemChanged` — an item simply absent from the array is never visited by any loop, so omitting `existingTests` entirely for a pure add-a-test call is safe by construction, not an inference). Decision (1) is therefore **implementation-ready as designed** once the item below is resolved. Decision (2) hit a **structural blocker**, characterized here rather than resolved by inference (source-proven, no live test needed): `Sample.clinical_order_id` is a single scalar column (`Sample.hbm.xml`), and `ServiceRequestProvider.searchDaherCorrelationNatively` (DH-011D3) pivots every correlation lookup through it — `ElectronicOrder → sampleService.getAllMatching("clinicalOrderId", order.getId()) → exactly one Sample`, with both the "no linked sample" and "multiple linked samples" branches **failing closed** (`InternalErrorException`, deliberately, per that method's own javadoc). A second `electronic_order` row for an added test's correlation is not merely unlinked: it makes that event's own probe throw a data-integrity error, and repointing `clinical_order_id` at it breaks the *original* order's probe the same way. Decision (2) cannot be satisfied by reusing the existing `electronic_order`/`Sample.clinical_order_id` mechanism at all — it needs a genuine model extension, and which shape that extension takes is exactly AGENTS.md's flagged "a gap that obviously should be filled a particular way" — there is more than one reasonable design and DH-011O has no basis to choose between them alone. Two candidates, both evaluated, neither implemented: **(A) a new nullable column directly on `clinlims.analysis`** (e.g. `daher_correlation_id varchar(64)`, partial unique index `WHERE daher_correlation_id IS NOT NULL`) — requires a new Liquibase changeset, a new `.hbm.xml` patch, **and a new patch to upstream `Analysis.java` itself** — a core clinical valueholder no Daher patch has ever touched, meaningfully growing the patch surface and upgrade/rebase burden this ticket elsewhere asks to keep small. **(B, recommended) a new standalone correlation table** (e.g. `daher_analysis_correlation(id, analysis_id → analysis.id, correlation_value varchar(64) unique, created_at)`), mapped by a brand-new Hibernate/JPA entity class analogous to the existing `DaherOrderCorrelation.java`/`DaherOrderCorrelationTransaction.java` pattern — requires a new Liquibase changeset and a new mapping/entity file, but **zero patches to any existing upstream Java or `.hbm.xml` file**, matching this codebase's own stated preference for JPA-annotated additions over further `.hbm.xml` patching (`hibernate.cfg.xml`'s own comments: "Moved to annotation-based mappings (JPA)... per Constitution IV"). Checked against the adapter before proposing either: `adapter/src/daher_adapter/openelis.py`'s `identifier_search` accepts a bundle with exactly one `entry` as `MatchCount.ONE` **regardless of `Bundle.total`** — a new Analysis-scoped native search branch returning one entry for an added-test correlation needs **no adapter-side code change** for the base case under either design. Two things still need an explicit answer whichever design is chosen, not left to lookup order: what happens if the same correlation value is ever found in *both* namespaces (`electronic_order.external_id` and the new added-test store) — almost certainly the same fail-closed ambiguity discipline `searchDaherCorrelationNatively` already applies elsewhere, but must be stated, not assumed; and confirmation that `Bundle.total` for the new branch should read 1 (matching the existing "one logical order" convention) for consistency even though the adapter's own `len(entries)==1` shortcut doesn't strictly require it. Two cheap alternatives considered and rejected in writing, not just dismissed: deduplicating by "does this accession already have an Analysis for this test" is **clinical-state deduplication**, which the ticket's own Same-test-semantics section forbids inventing; keeping the added-test↔correlation mapping only in the adapter's own database fails the ticket's requirement that the *target* (OpenELIS) be authoritative and searchable across the crash-after-target-commit-but-before-ledger-completion window DH-025's recovery contract must close. **Human Decision Required: Yes — which design (A, B, or another), before any implementation.** Concurrency note (not yet a blocker, flagged for the eventual implementation): a pure add never sets `SampleEditServiceImpl.editSample`'s `sampleChanged`, so it never issues a `Sample` `UPDATE` — two concurrent additions to the same accession may not contend on any shared versioned row at all. Do not design a pessimistic lock in ahead of evidence; run the concurrent-different-additions test unmodified first and add `LockMode.PESSIMISTIC_WRITE` only if it demonstrably fails. No production code changed by DH-011O; real facility never touched; no disposable infrastructure was built this pass (the blocker was resolved from source alone, at zero Docker cost). |
| ✅ **DH-011O's blocker RESOLVED BY DECISION (Design B) + BUILT + LIVE-VERIFIED** 2026-08-29 (DH-011P) | Liban selected **Design B** (ADR-031). Built: standalone `daher_analysis_correlation` table (unique on `correlation_value` only, deliberately not `analysis_id`), new JPA entity/DAO/service, a `@Transactional` `DaherAddedTestTransaction` covering the native `editSample` write and the correlation insert in one transaction, and a dual-namespace extension to `searchDaherCorrelationNatively` that fails closed if a correlation value is ever found in both the initial-order and added-test namespaces at once. `SampleEditService.editSample`'s return type was widened from `void` (a shared singleton-bean instance field — a live concurrency hazard) to `List<String>`, eliminating the hazard rather than working around it; this reaches `SampleEditServiceImpl` and the pre-existing `SampleEditRestController` REST caller too — the one place this ticket's changes are not purely additive, named here rather than left to a file-diff to discover. **Live-verified, 10 items, all against one disposable stack**: schema exactly as designed; new-order non-regression; the core DH-011N regression — add a second test to an existing accession — now **succeeds** (old Analysis unchanged, exactly one new Analysis + one new correlation row, no duplicate specimen, zero `StaleObjectStateException`); dangling-specimen fail-loud non-regression; replay/idempotency (200 not 201, zero new rows); concurrent identical event without added locking (DB `UNIQUE` constraint alone resolves it, loser's Analysis insert fully rolled back — zero orphans); concurrent different events without added locking (both succeed, zero contention, per DH-011O's own concurrency note); cross-namespace collision fails closed (500, explicit diagnostic, neither namespace silently preferred); rollback-between-Analysis-and-correlation proven as a natural byproduct of the concurrent-identical-event case; and a happy-path probe on the added-test namespace (200, `Bundle.total=1`, correct resource). **One real implementation bug found and fixed by live testing** (not by review): a populated `sampleXml` drove a separate, unconditional `SampleAddService` code path that created a *second*, spurious `Analysis` per add-test event — root-caused and fixed by submitting an empty `<samples/>` document instead, routing the add exclusively through the path that correctly reuses the existing `SampleItem`. **`updateServiceRequest` (pristine, unpatched upstream) has this identical latent defect but is unreachable via the Daher adapter** (POST-only transport, confirmed by reading `adapter/src/daher_adapter/openelis.py`) — not fixed, out of scope, its own future ticket if that method is ever wired to a caller. **A representation gap, not fixed, recorded rather than silently accepted**: the add-test response's `source-event` identifier echoes the *original* order's correlation, not the add-test event's own — the search itself resolves correctly by the right value through the correlation table; only the echoed field on the returned resource names the wrong one. Safe for the one real consumer (the adapter never trusts this field, always re-probes by the value it itself sent) but Liban's call whether a future consumer could be misled by it. **One item NOT demonstrated**: true crash-recovery (the adapter's `RecoveryCoordinator` marking `RECONCILED_EXISTING`) — the adapter was not started this pass; reported as a gap, not inferred from the rollback proof. A pre-existing, unrelated CI bug (the compile job's "Install git" step never installed `apache2-utils`, causing an unbounded retry loop in `createDefaultPassword.sh`) was found and fixed while reproducing the job locally. Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011P section; decision record: `DECISIONS.md`'s ADR-031. Fully synthetic disposable infrastructure throughout (own generated credentials, `.env` never read); real facility never started, stopped, or connected to; full teardown performed and verified (`docker ps -a`/`docker volume ls` confirmed clean). **Not committed or pushed — an independent review declined this handoff; see the DH-011P2 row below for the closure pass.** |
| ✅ **DH-011P2 — independent review's three closure gaps CLOSED, all with live evidence** 2026-08-29 | An independent review of DH-011P's handoff declined to approve commit, citing: (1) unverified worktree/`origin master` relationship, (2) true DH-025 crash recovery NOT DEMONSTRATED, (3) the representation gap recorded but not fixed. **(1)**: `git rev-parse` directly confirmed local HEAD already equaled `origin/master` (`797eb7c...`) with zero divergence — the prior handoff's stated HEAD was simply stale, not actually wrong in effect; no rebase was needed or performed. **(2)**: no production entrypoint assembles `OpenElisClient` + `RecoveryCoordinator` + `LedgerRepository` into a running delivery loop (confirmed by reading `app.py`/`order_poller.py` — DH-011E's poller remains unwired), so a test-only integration harness was built and committed (`adapter/tests/integration/test_dh011p2_crash_recovery.py`, gated behind live-infra env vars, never runs in CI) driving the real, unmodified production classes through a controlled fault exactly after a proven real OpenELIS commit and before ledger completion, then a fresh coordinator/ledger pair (simulating restart) correctly reconciles to the existing write with **zero** second writes — plus a second scenario proving the same for a lost-response/timeout shape. Six independent runs all left the adapter's ledger `completed`, zero stuck `processing`, zero dead-lettered. **(3)**: see the CLOSED row above — the representation gap is fixed, not merely documented, live-verified on a 3-test accession where each added test now correctly carries its own correlation. **Also performed**: a full §4 patch-surface caller review found a third `editSample` caller (`SampleEditController`, the classic MVC UI) DH-011P never named — confirmed behaviorally unaffected (Java discards its unused return value; the full reactor compiles it unconditionally); re-ran §12/13/14's concurrency/collision regressions against the rebuilt image (all still pass, unchanged by the representation fix); ran the pinned upstream's own pre-existing `ServiceRequestProviderTest` against the patched build for the first time in this repo's history (DH-011P's Docker build always skips test compilation) — 3/3 pass; extended the CI static guard to cover all 9 patches (was checking only 5) and added a reciprocal "every shipped patch must be applied" check; re-confirmed adapter `ruff`/`pytest` — 142 passed (exact match, re-verified not assumed), 2 skipped (the new gated tests). Fully synthetic disposable infrastructure, own generated credentials, real facility never touched, full teardown verified. Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011P2 section; decision record: `DECISIONS.md`'s ADR-031 closure pass. **Not committed or pushed — see the DH-011P3 row below for the final review item and the commit.** |
| ✅ **DH-011P3 — final review item: one Analysis ↔ one added-test correlation, enforced at the DB — READY TO COMMIT** 2026-08-29 | Reviewed DH-011P2's own deliberate tolerance of multiple added-test correlations per Analysis (kept so a natively-cancelled-then-revived test could carry two) and chose to revoke it: Liban selected the strict invariant instead, accepting that a Daher re-add after native cancellation now fails loudly rather than reviving. **Enforced via a new migration** (`005-daher-analysis-correlation-unique-analysis.xml`, sequential, not an edit to the already-applied 004) adding `UNIQUE(analysis_id)`, alongside the pre-existing `UNIQUE(correlation_value)`. **Backstopped at the application layer**: `FhirTransformServiceImpl`'s transform now throws `InternalErrorException` if it ever finds more than one added-test correlation for an Analysis (should be unreachable under the new constraint; never silently picks first/newest/lexical if it somehow isn't). **Live-verified, one disposable stack, all three required migration-safety cases**: fresh DB succeeds; existing DB with non-duplicate data succeeds; existing DB with a genuine duplicate `analysis_id` fails via Postgres's own atomic `CREATE UNIQUE INDEX` — Liquibase rolled back the whole changeset (including the earlier `DROP INDEX`) on failure, zero rows deleted/merged/rewritten, changelog correctly shows it never applied. Re-ran the representation matrix (§5), same-event replay (§6), distinct-events (§7, plus a direct proof that a second correlation on an already-claimed Analysis is rejected), and two concurrency races (§8: the pre-existing same-correlation race, unaffected; and a **new** race — two different correlation values simultaneously claiming the same Analysis — one wins, one gets a real `UniqueViolation` under genuine contention, exactly one row survives). Re-ran the DH-011P2 real-adapter crash-recovery integration test unmodified against the rebuilt image — unchanged, both scenarios pass. Re-confirmed the cross-namespace collision control is a separate, unaffected mechanism. Extended the CI static guard for migration 005's content, the entity's matching JPA constraint, and the transform's fail-closed check — **one real false-negative caught and fixed during control-testing** (an early guard matched the phrase "CREATE UNIQUE INDEX" in the migration's own prose comment, not its SQL). Adapter unchanged: `ruff`/`pytest` re-confirmed identical (142 passed, 2 skipped). OpenELIS: pristine `ServiceRequestProviderTest` still 3/3 pass; both standalone Maven reactor build and the production Dockerfile build reached `BUILD SUCCESS`. One build-plumbing gap fixed in this pass's own test setup (a missing `git submodule update --init` for `dataexport` in a fresh clone — not a Dockerfile defect). Fully synthetic disposable infrastructure, real facility never touched, full teardown verified. Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011P3 section; decision record: `DECISIONS.md`'s ADR-031 final closure. **READY TO COMMIT: Yes** — this was the last review item; DH-011P/P2/P3 commit and push together as one implementation. **COMMITTED AND PUSHED 2026-08-29**: `05b55b292a3672dd75b7d67bbdb14158fee19066` on `origin/master`; CI run `33257047337` — all 8 jobs green, verified directly via `gh run view`, not assumed. |
| ✅ **CLOSED 2026-08-29 (DH-011P2, representation corrected again by DH-011P3)** — was: New finding, NOT fixed (DH-011P) | The add-test `ServiceRequest` response (and the added-test-namespace search result) echoed the *original order's* correlation value in its `urn:daher-health:adapter:source-event` identifier, not the correlation of the add-test event that actually produced it. **Fixed (DH-011P2)**: `FhirTransformServiceImpl`'s shared transform looks up `daher_analysis_correlation.getByAnalysisId` first before falling back to the pre-existing initial-order identifier. DH-011P2's own initial fix emitted every correlation an Analysis carried as separate identifiers, tolerating the canceled-then-revived multi-correlation case — **DH-011P3 revoked that tolerance**: `analysis_id` is now unique at the DB, and the transform fails closed (`InternalErrorException`) rather than emit more than one identifier for a single Analysis, which should be unreachable under the new constraint. Live-verified on a 3-test accession: searching by the initial order's correlation returns each test with its OWN correlation, exactly one each, not all three sharing the original's. Full account: PROVENANCE.md's DH-011P2 and DH-011P3 sections. |
| 🔵 **Finding resolved to CONTAINED, not a blocker** 2026-08-29 (DH-011P2) — was: NOT fixed, needs its own ticket (DH-011P) | `updateServiceRequest` (pristine, unpatched upstream `ServiceRequestProvider`) has the "populated sampleXml creates a spurious second Analysis" defect DH-011P found and fixed in the new added-test path. DH-011P recorded it as unreachable via "the Daher adapter's transport" (POST-only) — narrowed here: it is unreachable via the adapter's *code* (`OpenElisClient` defines no PUT-capable method at all) but **is reachable by the same HTTP Basic credential class the adapter would use** — live-confirmed, `PUT /fhir/ServiceRequest/1` with `admin:adminADMIN!` returns 400 (payload validation), not 401/404/302 — meaning it is authenticated and routed successfully. **Marked UNSAFE / NOT SUPPORTED FOR DAHER V1**, contained by a new CI static guard asserting `openelis.py` never gains a PUT-capable method (control-tested: fails correctly when a `"PUT"` reference is injected). Not a blocker because no normal Daher flow can construct a PUT request; would become one the moment any code adds one without revisiting this finding. Full account: PROVENANCE.md's DH-011P2 section. |
| ✅ **DH-011J — added-test cancellation implemented and live-proven; not committed pending Liban's review** 2026-08-29 | Answered DH-011J's 8 cancellation-scope questions separately (§2, full text in PROVENANCE.md). Implemented and live-proven the one its own §8 calls "the cleanest target": **cancelling one added-test event**, via a new `$cancel` FHIR operation (`ServiceRequestProviderCancel.java.patch`) delegating to a new `@Transactional` `DaherCancellationTransaction` bean — resolves the target through the existing `daher_analysis_correlation` table (no new table needed, per §10), enforces ADR-018's status gate (proceed only from `NotStarted`; idempotent no-op if already `Canceled`; refuse — no mutation — from every other status), reuses the pristine `deleteServiceRequest`'s own safe mutation (`analysis.setStatusId(Canceled)` + `analysisService.update()`) rather than reinventing it. **Live-proven, one disposable stack**: isolation (cancelling one added test leaves sibling tests and the initial order's own test untouched — DB-confirmed); the cancelled correlation stays searchable (`ServiceRequest.status` reads `"revoked"`, a pre-existing transform behavior this ticket discovered rather than added); idempotency at both the native-endpoint and adapter/ledger level; a genuine 5-way concurrency race (final DB state correct — exactly one cancellation, no corruption — three of five HTTP calls surfaced `StaleObjectStateException`, the identical race shape DH-011M already proved and accepted on the create path); the ADR-018 status gate refusing from both `TechnicalAcceptance` and, specifically, `Finalized` — **the one guard the pristine native primitive has zero protection against at all**; and true DH-025 crash-after-commit recovery with the real production adapter components (`OpenElisClient`, a new `DaherCancellationCoordinator` — deliberately not a change to `RecoveryCoordinator`'s existing FOUND/ABSENT model, which has no vocabulary for "found but not cancelled" — and `LedgerRepository`) against a real disposable Postgres. New permanent adapter tests: 19 (`test_cancellation.py`) + 9 (`test_openelis.py`) + 2 live-gated integration tests; `pytest -q`: **161 passed, 4 skipped** (up from 142/2), ruff clean. CI static/compile guards extended for the two new files and control-tested (14 control tests; two real gaps found and fixed in the guards themselves while control-testing — a bare-`PUT`-substring false-positive matching this patch's own javadoc prose, and a missing `.delete(` pattern in the hard-delete check — both the exact "test the guard, not just the feature" discipline DH-011P3 established). **Whole-initial-order cancellation deliberately NOT implemented** — see the Human Decision Required row immediately below. **One item narrowed, not overclaimed**: §15's atomicity is verified by the identical `@Transactional(rollbackFor = Exception.class)` pattern DH-011D3 already proved with explicit failure injection, plus the real (not manufactured) fault observed in the concurrency race above — not by a separately staged single-transaction failure injection at the exact seam requested. §2 question 4 (cancel after specimen collection) was not exercised live; its answer follows mechanically from the status gate, stated as such rather than inferred. Fully synthetic disposable infrastructure (own generated credentials, `.env` never read), real facility never touched, full teardown verified. Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011J section; decision record: `DECISIONS.md`'s ADR-018 closure pass. **Not committed or pushed — this ticket's own instruction, unconditional.** |
| 🛑 **DH-011J — Human Decision Required: Yes (does cancelling an initial order cascade to independently-added tests?)** 2026-08-29 | An initial order's own first test has no `daher_analysis_correlation` row of its own — it is identified only by the order-level `electronic_order`/`Sample.clinical_order_id` correlation, structurally distinct from every added test under the same accession (live-confirmed: attempting to build a "multi-test initial order" in one shot showed every test after the first always takes the added-test branch and gets its own correlation — see the DH-011J row above, §2 question 3). This means "cancel the initial order" and "cancel every test under this accession" are not the same operation today, and whether a human/product decision wants them to become the same thing (cascading cancellation) or to stay independent (cancel only the order-level record, leave already-added tests alone) is a clinical-workflow question this ticket has no basis to decide. DH-011J's own `$cancel` operation was deliberately scoped to resolve targets only through the added-test correlation namespace — live-confirmed it returns `404` and touches nothing when handed an initial-order correlation, so the current implementation cannot accidentally cascade, but this does not answer what a human asking to cancel an initial order should expect to happen. **Human Decision Required: Yes — before whole-initial-order cancellation is implemented.** |
| ✅ **DH-011J2 — §15/§7 exact rollback fault-injection CLOSED: PROVEN; §8-§11 regressions re-confirmed on a rebuilt stack** 2026-08-29 | A Postgres `AFTER UPDATE` trigger on `clinlims.analysis`, scoped to the one target Analysis transitioning to `Canceled`, was used to inject a genuine, deterministic fault *after* the real `analysisService.update()` mutation but *before* the outer `DaherCancellationTransaction` commits — zero Java code changed, the real production transaction driven unmodified. Live result: `HTTP 500` (`GenericJDBCException: could not execute batch`), and the post-fault DB read shows the Analysis's `status_id` **and `lastupdated` timestamp byte-identical** to the pre-fault snapshot — proving a genuine rollback of an attempted write (not merely "the write never happened"). Sample/SampleItem/result/correlation rows all unchanged. Trigger dropped; the same target then cancelled normally on the very next call, confirming the trigger — not some other defect — was the sole cause. **DH-011J's own §15 classification is upgraded from "verified by identical-pattern precedent" to `EXACT FAULT-INJECTION ROLLBACK: PROVEN`.** Isolation, idempotency, the ADR-018 status gate (`TechnicalAcceptance`/`Finalized` refusal), crash-recovery (real adapter components against real Postgres), and the 5-way concurrency race were all re-run against a **freshly rebuilt** disposable stack (new stack, new credentials, not a reused environment) — every conclusion unchanged from DH-011J's own proof. Adapter suite unchanged: 161 passed, 4 skipped, ruff clean. Full account: PROVENANCE.md's DH-011J2 section. |
| 🛑 **DH-011J2 — Human Decision Required: Yes (specimen-collection/receipt cancellation gate cannot be implemented — no reliable native signal exists)** 2026-08-29 | Liban decided that, in addition to the existing `AnalysisStatus.NotStarted` gate, an added-test cancellation must also require the specimen to not yet be collected or received, failing closed if that state cannot be determined authoritatively. **Investigated exhaustively — source-cited and live-confirmed, independently twice (a dedicated research pass, then a live t=0 database read on a fresh fixture) — and no such signal exists on the path Daher's cancellation targets**: `SampleItem.collection_date`/`received_date` are unconditionally set to the creation timestamp by `SampleUtil.buildSampleXml`/`SampleAddService` for every added test (live-confirmed: both fields already carry today's date immediately after creation, before any real-world collection/receipt could have happened); the SAMPLE-namespace `status_of_sample` vocabulary has no "Received"/"Collected" value at all (only `SampleEntered`/`SampleCanceled`/`SampleDisposed`); a genuinely separate REQUESTED→COLLECTED state machine (`sample_type_request`) exists but is structurally unreachable — `DaherAddedTestTransaction`/`SampleEditService`/`ServiceRequestProvider` never reference it; and `SampleItem.collector`/`quantity`/`source_id` (never auto-populated, confirmed NULL live, DH-011P's own earlier finding for the new-order path now confirmed for the added-test path too) are also never writable afterward — the only reachable edit surface, `SampleEditItem`/`SampleEditServiceImpl`, never references `collector` in any form — so these fields stay NULL forever regardless of real-world specimen handling, not because nothing was collected. Two shortcuts were considered and explicitly rejected, not silently taken: synthesizing "not collected" by nulling fields directly via SQL in a test fixture (tests a state the real create path can never produce); treating "collection time exactly 00:00:00" as a not-collected proxy (arithmetically identical to a constant, since nothing on this path ever writes any other time — not a signal). Applied honestly, the "cannot determine authoritatively → refuse" instruction Liban already gave means the gate, as specified, would refuse every added-test cancellation unconditionally — a real product consequence, not decided here. **Three options, no code written for any of them:** (a) ship the gate exactly as specified, accepting V1 cancellation always refuses; (b) ship added-test cancellation on the Analysis-status gate alone (already built and proved by DH-011J), specimen-state explicitly deferred, requires relaxing the two-gate decision to one for V1; (c) build a new Daher-owned specimen-state marker (new column/table, new design decision, new scope). **Human Decision Required: Yes — which option, before the specimen-state gate can be implemented.** Full account: PROVENANCE.md's DH-011J2 section; decision record: DECISIONS.md's ADR-018 DH-011J2 addendum. Not committed or pushed — no production code changed by this ticket; added-test cancellation stands exactly as DH-011J left it. |
| ✅ **DH-011J FINALIZE — DECIDED: Option (b) selected. V1 cancellation-eligibility is Analysis-status only; specimen-collection/receipt gating deferred, not implemented** 2026-08-30 | Liban resolved the row above: **option (b)**. The specimen-collection/receipt investigation stands as correct and is not re-litigated — it is *withdrawn as a V1 requirement*, not disproven. **Canonical V1 contract, permanent, superseding the two-gate framing**: `AnalysisStatus.NotStarted` → cancel permitted; `Canceled` → idempotent success; every other recognized status (`TechnicalAcceptance`, `Finalized`, `TechnicalRejected`, `BiologistRejected`, `NonConforming_depricated`, `SampleRejected`) → refuse, zero mutation; any unmapped status → refuse by the same allowlist construction (only `NotStarted`/`Canceled` are matched, everything else falls through to refusal). **Explicitly not a claim that a specimen has or hasn't been physically collected** — it means Daher cannot observe that fact reliably through the path it uses today; a later ticket may reopen this if a real signal becomes available (option (c) from the row above remains live, not foreclosed). Initial-order cancellation remains unsupported; the cascade question remains a separate, still-open Human Decision, unaffected by this resolution. **No code changed by this decision** — the implementation DH-011J built and proved was already exactly this Analysis-status-only contract; DH-011J2's specimen-gate work never reached code. Decision record: `DECISIONS.md`'s ADR-018 DH-011J FINALIZE resolution. **COMMITTED AND PUSHED 2026-08-30**: `86f26dfb28ecffae83a3c96adb5a691aaee909a4` on `origin/master`; CI run `33298838019` — all 8 jobs green, verified via `gh run view`. **DH-011J: DONE for V1 added-test cancellation.** DH-011 overall NOT COMPLETE; DH-011E NOT ACTIVATED. Full account: PROVENANCE.md's DH-011J FINALIZE section. |
| 🔨 Provisioning control plane | **DH-026** (facility parameterisation), **DH-027** (API, portal, agent, durable state — works end to end **on a stand-in**; no clinical stack has booted this way) |
| ✅ Done (infrastructure) | **DH-027M** (CI — 700 tests now gated, every check control-tested), **DH-027B** (compensated facility creation — not a single ACID transaction; see its entry), **DH-027F** (atomic env writes; rotation rollback still open), **DH-027E** (real soft decommission), **DH-027C** (lifecycle state machine), **DH-027D** (observed infrastructure status), **bug 18** (agent polls through a long job), **DH-027G** (backup bootstrap/off-site wiring), **DH-027H** (nightly-scheduler backup evidence reaches the control plane) |
| ⏭ Deferred to v1.1 by decision | **DH-023** (analyzer interface — ADR-016) |
| ⏸ Waiting on dependencies | DH-012, DH-013, DH-018 |
| ✅ Done (architecture/product) | **UI-001** (unified clinical UX + data-contract map — no frontend built; see `docs/UI-001-CLINICAL-UX.md`) |
| ✅ Done (verification spike) | **UI-001A** (turned UI-001's UNVERIFIED backend assumptions into runtime-measured facts against the synthetic stack — patient search/registration, bed assignment, admission shape, and audit identity now RUNTIME VERIFIED; order-write PARTIALLY VERIFIED, stopped at the clinical-concept boundary by instruction; BFF/SSO identity remains UNRESOLVED pending SSO activation) |
| ✅ Done (first frontend) | **UI-002** (`ui/` — Vite + React + TypeScript app shell, small design system, functional read-only patient search against the pinned stack via a dev-only proxy that never exposes the OpenMRS credential to the browser; no clinical write; no SSO; 21 tests passing, real container build verified) |
| ✅ Done (architecture decided) | **UI-003** (production identity — ADR-027: no BFF in V1, browser holds real OpenMRS/OpenELIS sessions directly, same-origin under a future `app.$BASE_DOMAIN` proxying each backend's API prefix; a read-only service-account BFF was considered and explicitly rejected; coordinated logout across Keycloak/OpenMRS/OpenELIS recorded as an open gap; live end-to-end proof stays UNRESOLVED pending BLOCKER-5) |
| ✅ Done (foundation built) | **UI-004** (`app.$BASE_DOMAIN` real and verified live — `/openmrs/*` same-origin, OpenMRS session cookie hardened `Secure`+`SameSite=Lax` on that route only, a real Caddy-level CSRF guard control-tested with 5 deliberate breaks, `openmrs` Keycloak client reconciliation extended idempotently; OpenELIS's SAML ACS derivation stays `UNRESOLVED` so it is not mounted; no clinical write; `SSO_ENABLED` untouched) |
| ✅ Done (evidence gathered) | **UI-005** (disposable Keycloak+OpenMRS+OpenELIS stack, no real facility SSO touched — OpenELIS ACS resolved: **APP-ORIGIN SUPPORTED**, full synthetic SAML round trip proven, conditional on a redirect-URI addition not made to the real client; OpenMRS OIDC found **STILL UNRESOLVED** — `/oauth2login` 404s (initial attribution to an `IllegalStateException` corrected by UI-006, see below); coordinated logout found **PARTIAL as implemented, but a working fix proven** — Keycloak's SSO session silently survives app-level logout alone (confirmed live), yet redirecting the browser to Keycloak's own end-session endpoint and completing its confirmation click genuinely closes it (also confirmed live) — not yet wired into the real logout action; no clinical write; `SSO_ENABLED` untouched) |
| ✅ Done (precisely blocked) | **UI-006 / DH-008A** (root-caused OpenMRS OIDC's `/oauth2login` 404 to the actual mechanism — no servlet mapping covers the bare path, `OAuth2LoginRequestFilter`'s exclusion list creates a dead end, and `UserRedirectRequiredException` is never caught because `oauth2ClientContextFilter` is never wired into the request pipeline; corrected UI-005's `IllegalStateException` misattribution — that exception belongs to `legacyui`, is non-deterministic, and is unrelated; proved fresh-DB and initialized-DB configurations fail identically, ruling out a first-boot race; found a separate real gotcha where a module stopped once stays `started=false` in `global_property` across a later SSO-enable reboot; no upstream fix exists; a precise microscopic-patch design recorded for a follow-on ticket; no patch attempted, no clinical write, `SSO_ENABLED` untouched) |
| ✅ Done (bridge proven; activation blocked) | **UI-007 / DH-008B** (built and proved UI-006's bridge filter: patched `oauth2login`, unmodified baseline vs. patched control, full synthetic OIDC round trip — `authenticated:true`, real `user`/`currentProvider`, no Basic Auth/admin/service-account fallback — restart and Keycloak-outage-fail-closed both proven; patch source + provenance committed under `openmrs-config/patches/oauth2login-bridge/`, **not** wired into the Dockerfile; found three new, separate blockers on real activation, each **Human Decision Required: Yes**: an `oauth2login`/`authentication`-module `AuthenticationScheme` bean collision with no config-level fix, break-glass local login lost entirely while `oauth2login` is active (the module's own designed behavior), and `oauth2login.started=false` does not self-heal when SSO is later re-enabled; one §14 negative control did not reproduce the expected failure and is recorded as unexplained rather than forced; no clinical write, `SSO_ENABLED` untouched, no production file modified) |
| ✅ Done (SSO-off/on proven; §7/§8 deferred; §6 script's guards untested live) | **UI-008 / DH-008C** (wired the proven bridge patch into `openmrs-config/Dockerfile` reproducibly; resolved the `authentication`/`oauth2login` collision per this ticket's own decision — `authentication-2.3.0.omod` excluded when SSO is on, config-only, re-evaluated every boot; both SSO-off and SSO-on proven live on a real booted database — single `AuthenticationScheme` bean, no collision, break-glass correctly unavailable while `oauth2login` is active; solved `oauth2login.started=false` without SQL after a disproven first design — REST module-start updates the database but never restores live HTTP dispatch in place (measured 3×) — via `oauth2login.mandatory=true` (self-cleaning global property) plus the existing `restart: unless-stopped` policy, **mechanism proven live twice**, but verifying it against a fresh database surfaced two real defects in the script's checks (a status-code-only predicate that can't tell a genuine redirect from OpenMRS's own wizard-incomplete one, and activation attempted before first-time setup finishes — produced a real crash loop, `RestartCount` past 20) — both fixed with source-level reasoning and a clean build, **not re-verified live**; found and exonerated a genuine base-image/host first-boot install-duration issue (~50min+, reproduced identically on the pristine pre-ticket image); §7 break-glass mechanism and §8 full OIDC re-proof **not done this ticket** — scope cut, recorded as not-started, not as a finding; 700 tests unchanged) |
| ✅ Done (crash-loop closed; mandatory-property trap found + fixed; §6/§7/§9 re-proven with the fixed mechanism) | **UI-009 / DH-008D** (closed UI-008's three verification gaps against the current committed image, `0e6f982`. Fresh database + `SSO_ENABLED=true` from first boot: zero restarts across the full ~50min install, no premature activation, genuine redirect-vs-`/initialsetup` distinction proven with both literal `Location` values captured. Full integrated-image synthetic OIDC round trip against a disposable Keycloak realm: `authenticated:true`, correct synthetic user/`systemId`/`Provider` mapping, no Basic Auth/admin/service-account/client-secret exposure. **Found a second, more serious mandatory-module trap while re-proving §6/§7 live**, independent of the one UI-008 already closed: `ModuleFactory.stopModule()`'s guard (config.xml `Module.isMandatory()`, UI-008's finding) is not the only consumer of "mandatory" — `ModuleUtil.checkMandatoryModulesStarted()` (openmrs-core 2.8.7, `ModuleUtil.java:965`, called from every `Context.startup()`) rereads the `<id>.mandatory` *global property* fresh every boot and fatally aborts the **entire OpenMRS webapp**, not just the one module, if a module named there isn't in the started-modules map — regardless of *why* it isn't started. UI-008's `oauth2login.mandatory=true` activation mechanism sets exactly that property; if it is still `true` going into a boot where SSO is off (so `OAuth2LoginActivator.willStart()` deliberately self-unloads, exactly as designed), the whole webapp fatally aborts. Reproduced live against the real, uncorrupted module on an otherwise-healthy database — worse than a restart loop, since Tomcat's own process stays up (so `restart: unless-stopped` never fires) while `openmrs.war` is permanently dead and the activation script never gets a turn. **Fixed** by switching the activation mechanism to set `oauth2login.started=true` instead of `oauth2login.mandatory=true` — drives the identical `getModulesThatShouldStart()` next-boot-eligibility OR-condition, but is read by neither fatal check (both key strictly on `.mandatory`/`Module.isMandatory()`, never `.started`); same supported `SystemSettingResource1_9` REST write, same restart-triggered recovery shape, `mandatory` untouched (confirmed `false` throughout). Live production DB confirmed clean (`oauth2login.mandatory=false`, `.started=false`) before and unaffected by this investigation — the trap was never armed on master since `SSO_ENABLED` has stayed off there. Re-proved with the fixed mechanism on the same warm database: SSO-off boot no longer aborts (zero `MandatoryModuleException`, admin Basic Auth `authenticated:true`, `/oauth2login` correctly 404 while off); SSO-off→on recovery completes in exactly one restart to a genuine Keycloak redirect; a further clean restart preserves it. AuthenticationScheme collision re-confirmed both directions, including a throwaway negative control that restored the excluded module and reproduced the exact `Multiple authentication schemes overrides` error. §11 readiness gap confirmed real and fixed: `/ws/rest/v1/session` returns 200 whether or not anyone can log in, so the healthcheck now additionally requires, when `SSO_ENABLED=true`, that `/oauth2login`'s redirect target not be `/initialsetup` — local configured-auth-path readiness, not a dependency on Keycloak staying online. Keycloak outage/recovery reconfirmed fail-closed with no fallback. Two disposable-environment cleanup items could not be completed this session — `docker kill`/`stop`/`rm` began returning daemon-level "permission denied" partway through (universal, not container-specific; unrelated to anything in this ticket's own changes) — leaving `openmrs-ui009`, `openmrs-ui009c`, `kc-ui009`, `mariadb-ui009` and their volumes/network running and needing a human `docker rm -f`; noted rather than routed around. Also observed, unrelated to this ticket and not investigated: the live `daher-health-adapter-1` container is restart-looping on `Can't locate revision identified by '0002_watermarks'` (Alembic), and `daher-health-daher-ui-1` has been failing its healthcheck for its entire 28h uptime — both pre-existing, on a separate network from this ticket's disposable resources. No clinical write, no break-glass, no OpenELIS-at-origin, no real facility SSO activation. 700 tests unchanged; `openmrs-config/seed` (16) and `openmrs-config/forms` (19) floors reconfirmed locally) |
| ✅ Done (mechanism built, tested, and proven live end-to-end; one credential-custody gap stated, not solved) | **UI-010 / DH-008E** (explicit, operator-only break-glass authentication. **Architecture: mode transition, not dual-auth** — reuses UI-007/008/009's already-proven `SSO_ENABLED` toggle exactly as designed (NORMAL: oauth2login active, `authentication` module excluded; BREAK_GLASS: oauth2login disabled, `authentication` module restored), never two competing `AuthenticationScheme` beans at once. **Operator interface**: two new closed-vocabulary job verbs, `enable_break_glass`/`disable_break_glass` (`provisioning/src/daher_provisioning/jobs.py`), a new `Scope.BREAK_GLASS` **deliberately withheld from `PORTAL_SCOPES`** (same posture as decommission — a privileged emergency action, not a routine portal operation), two audited endpoints (`POST /facilities/{id}/break-glass/enable|disable`), and a narrow executor transition (`ComposeExecutor._set_break_glass`) that flips `SSO_ENABLED` (and a second, independent `DAHER_BREAK_GLASS` marker — see below) in the facility's own env file and recreates **only** `openmrs-backend` via `up --detach --no-deps openmrs-backend`, the exact narrow-recreate pattern bug-21 and bug-22 already proved live (no cascade to Caddy/OpenELIS/Keycloak/adapter). **A real bug was found and fixed before this shipped**, caught by the advisor rather than a test: the first version inferred `authentication_mode` from `SSO_ENABLED` alone, which would have reported **every never-activated facility — including the real one — as already in break-glass**, since `STACK_DEFAULTS` sets `SSO_ENABLED=false` fail-closed for every provisioned facility pending BLOCKER-5. Fixed with a second, independent env flag (`DAHER_BREAK_GLASS`, provisioning-layer bookkeeping only — OpenMRS itself never reads it) so `AuthenticationMode` has three real states — `NORMAL` (SSO on), `LOCAL_ONLY` (SSO never activated — where the real facility sits today), `BREAK_GLASS` (an explicit emergency deviation from a normally-SSO facility) — plus `UNKNOWN`; SSO-on always wins if the two flags ever disagree. Also measured, not assumed: whether `up -d --no-deps` actually recreates on an env-only change (yes — a throwaway one-service compose project showed compose issue a genuine `Recreate` the moment the interpolated value changed, confirmed independently of this sandbox's own docker-kill permission quirk which only blocked completing that same throwaway test's teardown) and that a failed recreate leaves the env file genuinely flipped while the container still runs the old mode — a documented, tested, visible partial-failure property (§16), not a silent one; a retry converges. **Full live round trip proven on UI-009's still-running disposable environment** (`openmrs-ui009c`, `kc-ui009`, `mariadb-ui009` — reused rather than rebuilt): a real synthetic OIDC login in NORMAL mode (`authenticated:true`, correct `Provider`/`currentProvider`) → recreated with `SSO_ENABLED=false` → old NORMAL-era session cookie replayed and correctly `401 Session timed out` (Tomcat's own restart invalidates it, no special handling needed) → `oauth2login` genuinely `404`, zero `AuthenticationScheme` collisions, local admin Basic Auth now `authenticated:true` as routine access → readiness confirmed correct in this mode (UI-009's `SSO_ENABLED`-gated healthcheck clause correctly skips the oauth2login check, plain session-endpoint check alone determines health) → **restart-while-BREAK_GLASS proven stable** (killed and manually restarted the same container; mode, `oauth2login` absence, and local-login-works were all unchanged after) → recreated back to `SSO_ENABLED=true` → local admin login correctly stops working as routine (`authenticated:false`) → a fresh synthetic OIDC login succeeds again, no rebuild, no DB repair. **Keycloak outage re-proven fresh** on this same container in NORMAL mode: killed Keycloak, confirmed OpenMRS still only redirects toward it (never falls back), local admin login stays `authenticated:false` throughout, `SSO_ENABLED` never changes on its own — restored Keycloak, a fresh OIDC login succeeded immediately. One incidental finding while wiring the live test: the disposable Keycloak realm's `openmrs` client had never had `openmrs-ui009c`'s redirect URI registered (a leftover from an earlier container rename mid-UI-009); added it to the synthetic client only, not any real one. AuthenticationScheme-collision negative control **not re-run this ticket** — cited from UI-008/UI-009's exhaustive, repeated proof of the identical `exclude-authentication-when-sso.sh` mechanism rather than repeated a fourth time. **§7 emergency-credential gap, stated rather than solved, per the ticket's own explicit instruction**: the credential is facility-specific, not a shipped default (DH-019), not in git, and inaccessible during NORMAL (Basic Auth gets redirected while `oauth2login` is active, per UI-007/008) — but `OMRS_ADMIN_PASSWORD` is listed in `UNROTATABLE` (`facility_env.py`: "applied on first boot only; change it through OpenMRS"), so **no committed mechanism rotates it**, and **no custody path is documented for how an operator actually obtains it during a real emergency** (it lives in the facility host's `.env`). Both are Human-Decision-shaped gaps, not implementation debt — recorded, not invented around. Also measured: local admin auth via REST Basic Auth issues **no session cookie at all** — each request re-authenticates independently, so there is no lingering "emergency session" artifact to worry about surviving a return to NORMAL through that specific path. 580 provisioning tests (from 552 pre-ticket baseline — the pre-fix run showed 576 before the semantics bug above was caught, corrected to 580 with the fix's own regression tests), 28 of them new and specific to this mechanism, `ruff` clean; 700-test repo baseline otherwise unchanged. No clinical write, no real facility SSO activation, no OpenELIS-origin change, no coordinated logout, no maternity-form change. New disposable resource from this ticket (`curl-ui010`) could not be torn down — same intermittent daemon-level `docker kill`/`stop`/`rm` denial recorded at the end of UI-009/bug-22; needs a human `docker rm -f` alongside that session's other leftovers) |
| ✅ Done (registration works end-to-end, synthetic-proven; real activation blocked on a new staff-role decision, #17) | **UI-012** (the first clinical write from the Daher UI: `POST /openmrs/ws/rest/v1/patient`. Re-audited first: no numeric OpenELIS site code, no fresh VM, no approved off-site backup credential had cleared. **Contract re-verified live, not copied from UI-001A**: the "OpenMRS ID" identifier type's `locationBehavior` is `NOT_USED` (read from the discovered identifier source, not assumed) — live-confirmed a `201` with no `location` key on the identifier at all, which **dissolves the identifier-location ambiguity question this ticket's own brief anticipated as a likely STOP condition**. `GET /patient?identifier=<exact>` confirmed genuinely exact (zero results for a nonexistent identifier, exactly one after creation, correctly `400`s a true duplicate) — the reconciliation primitive the ambiguous-write protocol depends on. **A blocker found, recorded as #17 above rather than routed around**: OpenMRS's real, unmodified privilege set never grants `Add Patients`/`Edit Patient Identifiers` to `Provider` or `Organizational: Nurse` — the only roles ADR-017 maps real staff to — so no mapped staff member can use this screen once SSO activates for real; the synthetic §9 proof below used a disposable-database-only throwaway role, explicitly not a change to any committed config. **A second finding, precise rather than repeating UI-001A's stronger claim**: a fabricated identifier is not *categorically* rejected by the `LuhnMod30IdentifierValidator` — it is a checksum check, not a provenance check, so a coincidentally-valid fabricated string (`9999999`, live-confirmed) is accepted as a fresh patient while a checksum-invalid one (`1234567`) is correctly refused. This means the frontend never exposing manual identifier entry is the real safety mechanism, not sole reliance on server-side rejection — exactly why `createPatient()` takes no identifier parameter from any form field. **Ambiguous-write handling implemented as specified**: `createPatient()` treats a dropped connection or an unparsable 2xx body as `AMBIGUOUS_WRITE` (never a silent success, never a definite failure); `useRegisterPatient()` reconciles by the pre-generated identifier via the exact-match endpoint — exactly one match is success, zero is an explicit unresolved state an operator may retry deliberately (never automatic), more than one stops rather than picks a winner. A definite server rejection (validation, authorization, conflict) never triggers reconciliation at all — confirmed by a test asserting `findPatientByIdentifier` is never called on that path. **§9 audit-identity proof — proven via direct protocol calls matching the built code's exact contract** (same endpoints, headers, and no-location payload shape the TypeScript client sends, unit-test-verified) **against UI-009's disposable Keycloak/OpenMRS, not literally clicked through a rendered browser** (no disposable Caddy was built this ticket, matching UI-011's judgment that it would add TLS/routing risk without new information once the backend contract is proven identical): `auditInfo.creator.uuid` on the created patient read-back matched the synthetic OIDC user's own OpenMRS UUID exactly — not admin, not a service account. **Error normalization implemented for the first time in code**: UI-001A's finding that an unauthorized write can come back as a structurally valid `400` with "Privileges required" buried in nested text is now handled by scanning the raw response text for that substring and mapping to `NOT_AUTHORIZED` regardless of status — verified against the real production stack too: with `SSO_ENABLED=false` (today's real facility state), an unauthenticated write instead gets a clean `401 Basic authentication required`, confirming the 400-shaped quirk is SSO-mode-specific, not universal; both paths are handled. The raw OpenMRS response (which can carry a full Java stack trace in its `detail` field) is scanned and discarded — never rendered, logged, or returned to a caller; a test asserts the thrown error's own message never contains the backend class names. **PHI is memory-only by construction** — the registration form, generated identifier, and created-patient response live entirely in React `useState`/hook state with no explicit persistence path; tests assert `localStorage.setItem`/`IndexedDB.open` are never called during a full successful registration. **A real, generally-applicable bug found and fixed, not worked around in a test**: `design-system/ErrorState.tsx`'s retry button had no explicit `type`, so nested inside `RegisterPatientForm`'s own `<form>` it defaulted to `type="submit"` — a click on "Réessayer" would fire the retry handler AND resubmit the form, double-invoking the registration attempt. Fixed with an explicit `type="button"`, benefiting every existing call site, not just this one. **Mutation guard**: no Caddy change was needed — the existing `/openmrs/*` CSRF guard already covers the new idgen/patient paths; live-verified directly against the running production Caddy (`app.localhost`, zero write risk: anonymous requests, no session): a mutating POST with no `X-Daher-App-Request` header got `403` at Caddy before reaching OpenMRS at all; the identical request WITH the header reached OpenMRS and was correctly rejected there (`401`) for lacking authentication — proving the header alone grants no access. **Frontend, kept small**: `RegisterPatientForm.tsx` (prénom/nom/genre/date de naissance only — every field verified against this pinned stack, none added because OpenMRS merely supports it) replaces the old disabled "Nouveau patient" button in `PatientSearchPage`'s zero-results state; pre-fill only triggers when the search query unambiguously splits into exactly two whitespace-separated tokens, never a name-parsing guess on a single token or an identifier-shaped query. No UUID, identifier-source, or FHIR terminology ever reaches the component's own text — all user-facing copy is the existing `ERROR_MESSAGES` French vocabulary, keyed by code alone (never the backend's own message string). **Tests**: `ui/` 29 → **65** (36 new: `client.test.ts`'s idgen/create/reconciliation-primitive contract tests including the stack-trace-leak and location-omission assertions, `useRegisterPatient.test.ts`'s full state-machine coverage including the "never a second POST" and "reconciling is a real transient state" assertions, `RegisterPatientForm.test.tsx`'s form/validation/error/PHI-memory-only coverage, plus two `PatientSearchPage.test.tsx` integration touches for the new entry point). Re-measured, not copied: adapter 94, provisioning 580 (unchanged — `git diff` on `provisioning/` is empty), seed 16, form-safety 19, ui 65 = **774 total**. **Cleanup**: three synthetic test patients created during contract verification on `openmrs-ui009c` remain un-voided (a `Get Visits`-privilege gap on the throwaway test role blocked `DELETE`, not chased further — synthetic, harmless, will go with that environment's eventual teardown); the disposable-database-only `UI-012 Synthetic Registrar` role and its `user_role` grant remain in `mariadb-ui009` for the same reason, clearly named and commented as test-only in the database itself. No clinical write, no real facility patient created, no OpenELIS/adapter/provisioning behavior changed, no admission/bed/lab-order/maternity-form work — exactly as scoped) |
| ✅ Done (evidence only — exact registration/admission privilege sets experimentally proven; no committed authorization change; #17 now decision-ready) | **ADR-017A / UI-012A** (closes UI-012's #17 blocker with evidence, not a fix — this ticket **never touched** `keycloak/reconcile-sso.sh`, any realm definition, `DECISIONS.md`'s ADR-017, or any committed OpenMRS role; every grant lived only in the disposable `mariadb-ui009`/`kc-ui009` this session has reused since UI-009, and was deleted at the end. Re-audited first: neither the numeric OpenELIS site code nor a fresh VM had cleared. **Methodology finding, load-bearing for everything downstream**: `openmrs-ui009c`'s long-lived JVM (reused across five prior tickets) caches a Role entity's privilege collection at first load — adding `role_privilege` rows to an *already-loaded* role via direct SQL silently does **not** take effect, producing false-negative 403s that look like a missing privilege but are a stale cache. A **brand-new role name plus a brand-new, never-before-authenticated Keycloak user** for every distinct privilege combination sidestepped this reliably and is the only methodology this ticket's results should be trusted under; two early, contaminated readings (a "View Patients alone suffices" result and a false "Get Patients insufficient" result) were caught by re-testing with genuinely fresh users and are explicitly retracted in favor of the clean results below. Also found: the OIDC bridge module re-syncs `user_role` from the JWT `roles` claim on **every** login, so a role assigned outside that claim survives only until the next login — each test therefore did its work within one login. **§3/§4 REGISTRATION_MINIMUM — the true minimum, proven positive and reproduced clean**:

  | Operation | Before privilege | Privilege added | After privilege |
  |---|---|---|---|
  | Discover identifier source (`GET /idgen/identifiersource`) | `200` — no privilege required at all | — | `200` |
  | Generate identifier (`POST /idgen/identifiersource/{uuid}/identifier`) | `403` "Edit Patient Identifiers" | `Edit Patient Identifiers` | `201` |
  | Create patient (`POST /patient`, identifier already generated) | `403` "Add Patients,Edit Patients" → then `400`-wrapped "Get Patients" once `Add Patients` alone was granted (the create response itself requires read access to build) | `Add Patients` + `Get Patients` | `201` |
  | Exact-match read-back (`GET /patient?identifier=`) | `403` "Get Patients" | `Get Patients` | `200`, `auditInfo.creator` correctly attributed to the minimal-role user |
  | Fuzzy search (`GET /patient?q=`) | `403` "Get Patients" | `Get Patients` | `200` |

  **Exactly three privileges** — `Edit Patient Identifiers`, `Add Patients`, `Get Patients` — smaller than UI-012's own improvised 9-privilege test grant, none of which UI-012 had actually shown to be necessary. `Edit Patients` (needed to modify an *existing* patient) is confirmed **distinct** from `Add Patients` (needed only to create one) — the minimal role can register new patients but cannot edit anyone's existing demographic data. **§5 negative capability testing, direct REST calls against the disposable account, not hidden navigation**: creating a clinical Encounter — **DENIED** (`403`, "Add Encounters,Edit Encounters", live-tested). Entering an Observation and creating a lab Order could not be driven to a live privilege denial — OpenMRS's REST layer validates field types *before* the AOP privilege check runs, and reaching it would have meant fabricating clinical concept UUIDs, which AGENTS.md forbids; structural evidence stands in instead — `Add Observations`/`Edit Observations` and `Add Orders`/`Edit Orders` are confirmed distinct, ungranted rows in the real `privilege` table, checked by the identical AOP mechanism live-proven on Encounters. Editing an existing (unrelated) patient's clinical data — **DENIED** (`Edit Patients` required, not granted). Assigning a bed — **DENIED** (`Get Encounters` required as a prerequisite, `Assign Beds` for the write itself; neither granted). Administering roles — **DENIED** (`Manage Roles` required). Administering users could not be driven to a live denial for the same field-binding reason as Observations/Orders; `Add Users`/`Edit Users`/`Delete Users` confirmed as distinct, ungranted rows. **One capability found unintentionally open, flagged rather than fixed around**: `GET /systemsetting` returned `200` under the minimal role with **zero** privilege — re-tested on a *genuinely zero-privilege* account (no role at all) and it was equally accessible, confirming this is a general "any authenticated session may read system settings" property of this OpenMRS/REST build, **not** something the registration privileges specifically grant; it exposes configuration metadata (visit-type UUIDs, scheduler timestamps), not credentials, and is out of this ticket's scope to fix. **§7 ADMISSION_INCREMENT, kept separate as instructed, not built**: characterizing UI-001A's already-proven Visit+Encounter contract found a real surprise — creating a bare Visit unexpectedly cascades into the bedmanagement module (`Get Beds`, `Get Admission Locations`) before a bed is ever touched, on top of the visit/patient privileges themselves:

  `ADMISSION_INCREMENT` = `Get Visit Types`, `Add Visits`, `Get Visits`, `Get Visit Attribute Types`, `Get Beds`, `Get Admission Locations`, `Add Encounters`, `Get Encounters` — **8 privileges**, `Get Patients` already shared with registration. Live-proven end-to-end (`201` Visit → `201` Encounter, correctly linked, `visit` field populated) once the full set was granted; the same bedmanagement coupling was found by iterating one privilege at a time and hitting three more `403`s than the visit/encounter privileges alone would suggest. This is materially larger than registration's 3-privilege footprint — direct evidence that a receptionist granted registration alone does **not** get admission "for free," and that bundling the two now would mean redesigning the role next ticket rather than composing it. **§8 bed assignment — proven separable, not assumed**: the full `ADMISSION_INCREMENT` role, fully admission-capable, was denied bed assignment (`403`, "Assign Beds") — a distinct write privilege neither `Get Beds` (read) nor anything else in the admission set implies. Bed assignment can remain out of both registration and admission without an engineering obstacle; whether it *should* stay a separate clinical/midwife function is explicitly left as the product decision this ticket was told not to make. **§9 authorization is server-side, demonstrated not asserted**: every result above is a direct `curl` against the disposable OpenMRS backend — no Daher UI code was touched or run this ticket — proving the permitted registration call succeeds and the unrelated clinical mutation fails at the same REST layer regardless of what any frontend navigation shows or hides. **§10 Keycloak mapping, characterized only**: every test role here was an additional client role on the *existing* `openmrs` client (the same one `clinician`'s `Provider` role already lives on) — confirmed additive by construction and by direct inspection afterward: `Provider`'s own OpenMRS privilege set (empty, as shipped) and its being the only non-`adr017a` client role on `openmrs` were both unchanged at the end. A fourth realm role (candidate name `reception`, **not committed**) would integrate identically: `Keycloak reception → OpenMRS role → {Edit Patient Identifiers, Add Patients, Get Patients}`, next to `clinician`/`lab-tech`/`sys-admin`, none of which this ticket touched. **Decision package** — OPTION A (expand `clinician`): every `Provider`/`Organizational: Nurse` account gains registration; smallest change, no new realm role, but reopens the exact "receptionist mapped to clinician inherits broad clinical authority" problem UI-012 flagged, since there is no narrower existing role to map reception staff to instead. Exact additional privileges: the same 3 (`Edit Patient Identifiers`, `Add Patients`, `Get Patients`) added to `Provider`. OPTION B (dedicated `reception`): proven cleanly separable from every clinical capability tested (§5); needs one new Keycloak client role, one new OpenMRS role, and `reconcile-sso.sh`'s existing `reconcile_mapper`-style pattern extended additively (not attempted this ticket); admission stays a deliberate second decision (`ADMISSION_INCREMENT`) rather than inherited. Exact proposed privileges: `Edit Patient Identifiers`, `Add Patients`, `Get Patients`. **RECOMMENDATION (technical, not authorizing)**: Option B — the evidence shows registration's true footprint is small and precisely separable from every clinical capability checked, which is exactly the condition under which a narrow dedicated role costs little and buys real isolation; Option A's only advantage is avoiding a fourth realm role, which is not a large cost. **HUMAN DECISION REQUIRED — not made or implemented by this ticket.** No production system, real staff record, real role assignment, or committed authorization file was touched. Tests unchanged this ticket (no code was written): adapter 94, provisioning 580, seed 16, form-safety 19, ui 65 = **774**, re-confirmed. **Cleanup**: all disposable Keycloak test users and client roles (14 users, ~15 client roles created during characterization) deleted at the end — confirmed only `Provider` remains on the `openmrs` client. The corresponding OpenMRS-side `role`/`role_privilege` rows were deleted too; the disposable test *users* in `mariadb-ui009` could not be deleted (a real, correct FK constraint — `idgen_log_entry.generated_by` — protects the identifier-generation audit trail they're referenced from), left in place, clearly named `adr017a-*`, harmless and synthetic. Several synthetic test patients created during characterization remain un-voided in the same disposable database for the same reason prior tickets have left similar leftovers — noted, not chased, since this ticket's own definition of done never required cleanup completeness, only that no committed or production state changed) |
| ✅ Done (role deployable, not activated; #17 fully closed pending only roster/SSO-domain) | **ADR-017B** (implements the human decision recorded in `DECISIONS.md`: Option B, a dedicated `Daher Reception` role, registration only. Re-audited first: no numeric OpenELIS site code, fresh VM, or off-site backup credential had cleared. **A mechanism finding that changed the implementation plan, measured live before writing any config**: `daher-realm.json`'s `clinician`/`lab-tech`/`sys-admin` are REALM-level roles, but the `openmrs` client's only role-bearing protocol mapper (`oidc-usermodel-client-role-mapper`, scoped to that one client) reads only CLIENT roles — realm roles never reach OpenMRS via OIDC. Proven empirically: a fresh user carrying only the realm role `clinician` authenticates but lands with `"privileges":[],"roles":[]`. Realm roles remain load-bearing only for `lab-tech`, via OpenELIS's separate `saml-role-list-mapper` (which, unlike the OIDC mapper, does include them). The unmodified `oauth2login` module then assigns any OpenMRS role whose name matches a JWT `roles` claim value **exactly** — no translation table; also proven both directions (a matching name round-trips privileges; a non-matching name silently grants zero, no auto-created role, no error). **Consequence**: the Keycloak-side artifact is a CLIENT role on `openmrs` named identically to the OpenMRS role, `Daher Reception` — not a fourth realm-level entry, which would be inert for OpenMRS and misleadingly imply OpenELIS/SAML participation reception never has. A second gap found the same way: `reconcile-sso.sh` has never automated CLIENT ROLE creation for any of the three existing roles (only client *settings* and protocol *mappers*) — `Provider`'s presence on the disposable `openmrs` client has always been a manual/test artifact, matching ADR-017's own framing that only the roster ever needed the meeting. Extending it was still additive, not a parallel system: a new `reconcile_client_role` function, same idempotent list-and-filter idiom as the existing `reconcile_mapper`, called once for `Daher Reception` only. **A real bug caught by control-testing idempotency, not assumed**: the first implementation checked existence via `kcadm get clients/{id}/roles/{name}` — kcadm does not URL-encode spaces in path segments, so a name containing a space (`Daher Reception`) made the lookup fail outright (`Illegal character in path`) rather than 404, silently falling through to `create` on every run and hitting a real `409` on the second. Fixed by listing-and-filtering (matching `reconcile_mapper`'s own pattern) and updating via `roles-by-id` — a realm-root endpoint, not nested under the client (a second wrong-path error caught immediately by re-running, not by inspection). **Committed**: `openmrs-config/initializer/roles/roles-daher.csv` (Initializer CSV format, `Daher Reception` with exactly `Edit Patient Identifiers; Add Patients; Get Patients`, no inherited roles, a Daher-owned UUID); `docker-compose.yml` gained the matching `roles/zz-daher-health` mount alongside the existing `globalproperties`/`locations` ones; `keycloak/reconcile-sso.sh` gained `reconcile_client_role` plus one additive call for `openmrs_id`. **§6 OpenMRS-side provisioning, proven live via the real Initializer module, not SQL**: cloned `openmrs-ui009c`'s live database (schema+data, not a fresh empty one — a truly-fresh-DB boot was attempted first and abandoned after ~15 minutes of unrelated first-install liquibase/module-loading time with no signal specific to this change; the clone boots in under a minute and exercises the identical Initializer code path) into disposable databases and booted fresh containers (`openmrs-adr017b`, then `openmrs-adr017b3` from a snapshot of the first) against the `roles/zz-daher-health` mount: first boot's log showed the real `initializer-api-2.12.0.jar` (`BaseCsvLoader`/`CsvParser`) processing the file, and the database confirmed exactly one `Daher Reception` row with the committed UUID and exactly the three committed privileges — created, not assumed. Second boot (a fresh container against a database that already had the role) reconfirmed the identical single row and three privileges — **idempotent**, proven by two independent real boots, not a restart of the same one (this sandbox's intermittent `docker stop/rm/restart` permission denial blocked restarting any existing container, including ones created this ticket — worked around by cloning forward into new containers each time rather than reusing one, which if anything is a stronger proof since each pass is a genuinely separate Initializer invocation). **§7 Keycloak-side idempotency, proven live, 3 consecutive runs**: the real `quay.io/keycloak/keycloak:26.7.1` reconciliation image (same image, same script, same mapper mount `docker-compose.yml` uses for the real `keycloak-sso-config` job) run three times against the disposable realm — run 1 created `Daher Reception` (after the URL-encoding bug above was caught and fixed on run 2's failure and re-verified), runs 2 and 3 both reconciled cleanly with no duplication; `Provider` unchanged throughout. **§5/§8/§9 effective-privilege and full registration proof — real OIDC login, not SQL substitution for authorization evidence** (the SQL role creation above is a separate, narrower, disclosed exception: since this sandbox's boot friction prevented completing a *fresh* SSO-enabled clone boot within reasonable time — one was left running and did eventually reach a working state, log-confirmed past the point of freezing, but only after the alternate path below was already complete — the runtime-authorization-boundary proof used the same fresh-role/fresh-user methodology ADR-017A already established and validated, applied to the real `Daher Reception` name, against the already-reliable `openmrs-ui009c`; this is evidence-gathering on behavior, not a substitute for §6's already-separately-proven Initializer mechanism): a genuinely fresh, never-before-authenticated Keycloak user carrying only the `Daher Reception` client role, real `/oauth2login` round trip (no Basic Auth, no admin, no service account, no manual token) — `session` confirmed `roles:[{"name":"Daher Reception"}]` exactly, `privileges` exactly the three approved ones, **`currentProvider: null`** (no Provider inheritance). Full UI-012 contract proven end-to-end: discover (`200`, one source) → generate (`201`, `100008E`) → create (`201`) → exact read-back with `v=full` (`auditInfo.creator.uuid` matched the synthetic user's own OpenMRS UUID exactly) → fuzzy search (`200`, found). **§9 negative controls, all live, all denied**: editing the just-created patient's demographics — denied (`500`, "Privileges required: Edit Patients" — a non-403 status for a privilege denial, an OpenMRS quirk already characterized in UI-001A, not new); creating a clinical Encounter — denied (`403`, "Add Encounters,Edit Encounters"); assigning a bed — denied (`403`, "Get Encounters" prerequisite ungranted); creating a user — denied (`403`, "Add Users", a live denial ADR-017A itself could not force past REST field validation — obtained here by supplying the one missing required field); creating a role — denied (`403`, "Manage Roles"). **§10 bidirectional clinician regression, proven live, not inferred**: the `Daher Reception` session above never inherited `Provider`/`Organizational: Nurse` (`currentProvider: null`, confirmed above); a second fresh user carrying only the `Provider` client role was confirmed to hold `roles:[Provider]`, `privileges:[]` — no `Daher Reception` leakage in either direction. `git diff` on `keycloak/reconcile-sso.sh` and `daher-realm.json` shows zero deletions across every changed file this ticket — the `openmrs`/`openelis` client-update blocks and every existing mapper call are byte-for-byte unchanged, so `lab-tech`/`sys-admin` mappings are unchanged by construction, not just by inspection. **§11 frontend, confirmed not assumed**: grepped `PatientSearchPage.tsx`/`App.tsx`/the design system for any privilege/role gate on navigation or the registration entry point — none exists; UI-012's "Nouveau patient" button is already gated only on search-zero-results state. **No frontend change made.** **§12 admission, untouched**: no `ADMISSION_INCREMENT` privilege granted, `Add Encounters` denied live above, UI-013 recorded still blocked. **§13 production untouched**: no real SSO activation, no real staff assignment, no real facility database write, no manual production OpenMRS role mutation, OpenELIS untouched. **§14 CI guard added and control-tested**: a new `static` job step asserts exactly the three realm-level roles remain, `reconcile_client_role`/`roles-by-id` are present, the CSV's `Daher Reception` privileges are exactly the approved three and never include `Add Encounters`/`Assign Beds`/`Edit Patients`/`Get Encounters`, no inherited roles, and the compose mount exists; control-tested by adding `Add Encounters` to a throwaway copy of the CSV and confirming the check fails on both the privilege-set and forbidden-privilege assertions, then restoring the real source. **`DECISIONS.md`** gained a new `ADR-017B` entry recording the decision and the mechanism finding; **`PROJECT_STATE.md`**'s NEEDS LIBAN #17 updated to DECIDED/IMPLEMENTED. Tests: adapter 94, provisioning 580, seed 16, form-safety 19, ui 65 = **774**, re-confirmed unchanged (no application code touched — this ticket is entirely config/infra/CI). **Cleanup**: probe Keycloak users deleted; the direct-SQL evidence-gathering role on `openmrs-ui009c` removed from `role_privilege` but the `role` row itself could not be deleted (`user_role` FK from the now-deleted probe users' OpenMRS-side records — the same class of harmless leftover FK this session has hit repeatedly, e.g. `idgen_log_entry.generated_by`); the Keycloak-side `Daher Reception`/`Provider` client roles and this ticket's `reconcile-sso.sh` changes to `kc-ui009` are left in place deliberately, matching the same hygiene pattern as `Provider`'s own persistence across tickets. A self-inflicted regression was found and fixed within the ticket: running the real reconciliation script against the disposable realm overwrote `openmrs-ui009c`'s ad-hoc testing redirect URI (a side effect of `BASE_DOMAIN` differing from what earlier tickets had set) — caught immediately when the next login failed with "Invalid parameter: redirect_uri", restored additively. Four throwaway boot-proof containers (`openmrs-adr017b`, `openmrs-adr017b3`, `openmrs-adr017b4`, and the abandoned fresh-empty-DB `openmrs-ui017b`) and one throwaway adapter-test Postgres (`pg-adr017b-final`) could not be torn down — the same intermittent daemon-level `docker kill`/`stop`/`rm` denial recorded at the end of nearly every prior ticket this session; needs a human `docker rm -f openmrs-adr017b openmrs-adr017b3 openmrs-adr017b4 openmrs-ui017b pg-adr017b-final` plus their now-orphaned volumes/databases (`openmrs-adr017b-data`, `openmrs-adr017b3-data`, `openmrs-adr017b4-data`, `openmrs-ui017b-data`, and `openmrs_adr017b2`/`openmrs_adr017b3` in `mariadb-ui009`), alongside every prior ticket's own still-pending leftovers) |
| ✅ Done (evidence only — Add Encounters/Add Visits proven unscoped, no committed authorization change; #18 raised, decision-ready) | **ADR-017C / UI-013A** (answers whether reception can safely perform admission without gaining broader clinical authority — evidence only, no committed config touched, UI-013 not built. Re-audited first: no numeric OpenELIS site code, fresh VM, or off-site backup credential had cleared. Re-measured baseline unchanged: adapter 94, provisioning 580, seed 16, form-safety 19, ui 65 = **774**. **§3 admission contract re-proven, no drift**: the disposable stack's own metadata (never assumed) supplied a real `Admission` encounter type, `Facility Visit` visit type, and `Inpatient Ward`/`Ward 1-3` admission-tagged locations (this container has no Daher `Maternité` overlay, unchanged since ADR-017B) — Visit → linked Encounter reconfirmed live end-to-end. **§4/§5 the central finding**: `Add Encounters` is a genuinely blanket, non-type-scoped privilege — a disposable `Daher Reception + ADMISSION_INCREMENT` account created both the intended `Admission` encounter type and an unrelated `Vitals` type on the same visit with identical `201`s, and a fully standalone `Lab Results` encounter with no visit at all. Confirmed not just behaviorally but structurally: `encounter_type`'s schema carries `view_privilege`/`edit_privilege` columns (OpenMRS's own supported per-type scoping mechanism) but **no create/add-privilege column exists at all** — direct proof the platform has no native way to scope who may *create* a given encounter type, only who may view/edit an existing one. The full `privilege` table was enumerated for every `Encounter`/`Location`/`Bed`-related row: none is type- or location-scoped. `Add Visits` shows the identical pattern — `visit_type` has zero privilege-scoping columns of any kind, and the same account created a `Home Visit` at `Ward 2` (unrelated to the maternity workflow) as easily as the intended `Facility Visit` at `Inpatient Ward`. **§5 answer: NOT SUPPORTED**, stated plainly, not blurred. **§7 Visit-only vs Encounter-only vs both, all three measured**: Visit-only succeeds and *is* discoverable (`GET /visit?patient=...&includeInactive=false` returns it) but carries no clinical documentation of the admission event. Encounter-only (the proven `Admission` type, explicitly no `visit` field, tested against a patient with zero prior visits to rule out OpenMRS's own active-visit auto-attach) succeeds as a write but is **invisible** to the same active-visit query (`results:[]`) — a real, structural finding: an encounter alone is not discoverable as an admission by any means a future UI would use. Visit+Encounter reconfirmed as the only complete representation, exactly UI-001A's original contract — no shortcut found, none proposed. **§8 the eight `ADMISSION_INCREMENT` privileges, precisely re-classified by iterative one-at-a-time removal on fresh roles/users (JVM-cache-safe methodology, per ADR-017A)**: `Add Patients`/`Edit Patient Identifiers` unrelated (registration, untouched); `Add Visits` and `Add Encounters` are the base write privileges by definition; `Get Visit Attribute Types` and `Get Visits` were freshly isolated this ticket on fresh roles/users and confirmed **REQUIRED FOR WRITE** — each produces a clean pre-save `403` if missing (`Get Visits`' stack trace traced the exact call site: `VisitValidator.validate()` → `getVisitByUuid()`, a genuine validation-time dependency, not decorative). `Get Visit Types`' requirement rests on ADR-017A's original characterization, not re-isolated this ticket — consistent with the same validation-time-lookup pattern but not independently re-confirmed. `Get Beds`/`Get Admission Locations` are **REQUIRED — BEDMANAGEMENT CASCADE**, and this ticket found precisely why they are *not* location-conditional as first suspected: the identical `403`s fired for `POST /visit` at a location carrying **no** `Admission Location` tag at all (`Outpatient Clinic`), proving the gate is a global module/REST-registration side effect of the bedmanagement module on all visit creation, not a targeted check tied to admission-tagged wards — the exact registering interceptor was not traced to source (time-boxed; the behavior itself is fully, reproducibly characterized). **The one dangerous outlier — `Get Encounters` is REQUIRED ONLY FOR RESPONSE, not for the write, and that is the actual hazard**: a role holding `Add Encounters` without `Get Encounters` gets a `500` (`ClassCastException`, response-serialization failure) on encounter creation — but a direct-SQL read-back proved **the encounter is created anyway**, silently. A future maintainer trimming `Get Encounters` off an admission role believing it only gates reading would introduce exactly this write-succeeds/response-lies trap, on top of which the affected caller cannot even self-diagnose it, since the same missing privilege blocks the read-back that would reveal the truth. **§9 bed assignment reconfirmed out of scope**: the full admission-capable account, having just completed a real admission, was still denied `POST /beds/{id}` (`403`, "Assign Beds") — admission does not implicitly touch bed state. **§12/§13 provider requirement — the decisive finding for the whole decision package**: the proven `Admission` encounter accepted `encounterProviders:[]` and completed successfully while the account remained `currentProvider:null` throughout — **no Provider identity is required for this admission flow**, so ADR-017B's boundary (`Daher Reception` never becomes a clinical Provider) is not in tension with admission working at all; had a provider been required this would have been an automatic STOP, and it was not needed. **Audit identity, proven not assumed**: every successful synthetic write's `creator.uuid` (Visit, both Encounters) matched the synthetic account's own OpenMRS UUID exactly — no admin, no service account, no substitution. **§16 systemsetting**: structurally unrelated — none of the eight `ADMISSION_INCREMENT` privileges touch it, and ADR-017A's already-recorded finding (any authenticated session, zero privileges, `200`) is unchanged by anything this ticket did; not re-tracked as a new item, not fixed, exactly as instructed. **Decision package — evidence only, no choice made**: OPTION A (reception + admission) is technically achievable (all eight privileges proven sufficient, no Provider needed) but grants a **genuinely unconstrained** ability to create any of the 19 configured encounter types and any visit type at any location — not a narrow "can admit" capability — plus inherits the `Get Encounters` response-reliability trap into whatever code relies on this role. OPTION B (clinician-only admission) adds zero new authority to reception and needs no new engineering; its cost is a workflow handoff, a fact question this ticket does not judge. OPTION C (a narrower native mechanism) is **NOT AVAILABLE WITH CURRENT PLATFORM AUTHORIZATION** — stated as a platform limitation, not designed around: confirmed by schema (no create-privilege field on either type table) and by the exhaustive privilege-table enumeration, not inferred from behavior alone. **§11 characterized, not built**: a future narrow Daher-side request-shape allowlist (fixed Visit type, fixed Encounter type, fixed location set, reject anything else) could technically enforce what OpenMRS cannot — architecture characterization only, explicitly flagged as its own separate decision matching ADR-027's existing no-BFF stance, nothing prototyped. **TECHNICAL RECOMMENDATION (not authorizing)**: Option B, given Option A's unconstrained encounter-type/visit-type authority and the undiagnosable `Get Encounters` failure mode neither of which this ticket found any native way to narrow. **HUMAN DECISION REQUIRED — not made here.** **§14 production untouched**: `roles-daher.csv` and every committed reception privilege byte-for-byte unchanged (`git status` confirms); every extra privilege used this ticket lived only in disposable roles named `adr017c-*`, all deleted at the end except for the same class of harmless `user_role` FK leftover this session has hit repeatedly. **§15 no real clinical writes**: every Visit/Encounter/patient this ticket touched was synthetic, on the disposable stack only; no real SSO, no real staff, no OpenELIS, no lab order, no maternity form, no bed touched. **§17 no UI work**: no button, no navigation change, no frontend file touched. **§18's required "base Daher Reception → admission denied" control, run properly after a self-inflicted complication**: attempting it against the literal `Daher Reception` name found that role's privileges in `openmrs-ui009c`'s database were **empty** — traced to ADR-017B's own end-of-ticket cleanup, which deleted `role_privilege` rows for a same-named disposable-evidence role and, not realizing the name collided with the committed one, left the real `Daher Reception` role a privilege-less husk on this specific long-lived container (the `role` row itself survives only because of the usual `user_role` FK). Restoring the three rows in the database did **not** fix live behavior — ADR-017A's JVM Role-privilege-caching finding applies here too: this container has now had `Daher Reception` loaded privilege-less at least once, so it stays privilege-less in memory until next boot, regardless of what the database holds. Purely a disposable-test-container artifact (`roles-daher.csv`, the committed source, was never touched and is unaffected); flagged here so a future ticket reusing `openmrs-ui009c` isn't confused by a `Daher Reception` login that mysteriously can't register a patient. The control itself was then completed cleanly under a fresh role name carrying the identical three privileges (matching ADR-017A's own established methodology for exactly this kind of cache collision): registration succeeded (`201`), Visit creation was denied (`400` wrapping "Privileges required: Get Visit Types" — a `ConversionException` from resolving the referenced type during request deserialization, not a validation failure), Encounter creation was denied cleanly (`403`, "Add Encounters,Edit Encounters") — confirming the base registration-only boundary is fully preserved and admission genuinely requires the increment. Tests unchanged (774) — this ticket is entirely evidence, no application/config/CI file modified, matching §21's explicit allowance. **Cleanup**: nine disposable Keycloak client roles and users created this ticket in total (`adr017c-admission`, `adr017c-visitonly`, `adr017c-visitonly2`, `adr017c-getbeds`, `adr017c-addenc-only`, `adr017c-addvisit-only`, `adr017c-baseline-registration`, plus two throwaway probes against the (temporarily broken) real role name) deleted; confirmed only `Daher Reception`/`Provider` remain on the `openmrs` client. The matching OpenMRS-side `role_privilege` rows were deleted too; the `role` rows themselves could not be deleted (`user_role` FK from the now-Keycloak-deleted synthetic users' OpenMRS-side records — the same harmless leftover class this session has hit repeatedly). Every prior ticket's own still-pending `docker rm -f` leftovers remain, unchanged by this ticket) |
| ✅ Done (Daher Clinician Admission committed, deployable not active; #18 fully closed pending only roster/SSO-domain) | **ADR-017D** (implements the human decision recorded in `DECISIONS.md`: Option B from ADR-017C — admission stays clinician-only, reception never gains it. Re-audited first: no numeric OpenELIS site code, fresh VM, or off-site backup credential had cleared. Re-measured baseline unchanged: adapter 94, provisioning 580, seed 16, form-safety 19, ui 65 = **774** (this ticket touches no application code — `roles-daher.csv`, `reconcile-sso.sh`, `ci.yml`, `DECISIONS.md`, `PROJECT_STATE.md` only). **§3 disposable-environment hygiene, followed exactly as instructed**: confirmed live that `openmrs-ui009c`'s `Daher Reception` role was still JVM-cache-stuck at zero privileges (ADR-017C's own disclosed leftover) and did **not** use that container as evidence — cloned `openmrs-ui009c`'s database (schema+data) into a fresh disposable container instead, matching the established clone-forward pattern (a truly-fresh-empty-DB boot was not re-attempted, for the same time-cost reason ADR-017B recorded). **A second, entirely self-inflicted timing bug found and fixed within this same hygiene step**: the first clone booted against the CSV file as it stood at boot time — a single-row `Daher Reception`-only version, since the `Daher Clinician Admission` row was added to the working tree *after* that container had already started and its Initializer pass had already run. Caught immediately by checking the database afterward (`Daher Clinician Admission` absent) rather than assumed present; fixed by cloning forward a second time, this time confirming the CSV was final and stable *before* starting the container — the second clone's database showed exactly the intended role with exactly nine privileges, Initializer-created, not SQL-inserted. **§5 the committed role, provisioned via the proven Initializer mechanism**: `openmrs-config/initializer/roles/roles-daher.csv` gained one new row, `Daher Clinician Admission` (a fresh Daher-owned UUID, no inherited roles), carrying exactly `Get Patients; Get Visit Types; Add Visits; Get Visits; Get Visit Attribute Types; Get Beds; Get Admission Locations; Add Encounters; Get Encounters` — nine privileges, matching the ticket's own specified self-contained set precisely; re-proven live rather than assumed sufficient (§11 below). No upstream role modified. **§8 Keycloak side, proven idempotent, 3 consecutive runs**: `keycloak/reconcile-sso.sh` gained one additive `reconcile_client_role` call for the new role, immediately after the existing `Daher Reception` one — the real `quay.io/keycloak/keycloak:26.7.1` reconciliation image run three times against the disposable realm (run 1 created the role, runs 2–3 reconciled cleanly with zero duplication); `Daher Reception`/`Provider` unchanged throughout, confirmed by listing the client's roles after each run. Each run resets the client's `redirectUris`/`webOrigins` to its own `BASE_DOMAIN`-derived list (a pre-existing, unrelated property of the script, not something this ticket introduced or needed to fix) — this session's own ad-hoc testing entries (`openmrs-ui009c`, the new clone's hostname) were restored additively after each run, exactly as ADR-017B's own log already documented needing to do. **§10 synthetic clinician proof, real OIDC, not Basic Auth/admin/service-account/manual-token**: a genuinely fresh Keycloak user was assigned three client roles on `openmrs` — `Provider`, `Organizational: Nurse` (the two ADR-017 maps `clinician` to; `Organizational: Nurse` did not yet exist as a client role on this disposable realm and was created for this proof, matching how `Provider`'s own presence has always been a manual/roster artifact per ADR-017's own framing), and `Daher Clinician Admission` — then completed a real `/oauth2login` browser-redirect round trip. Session confirmed: `roles: [Organizational: Nurse, Daher Clinician Admission, Provider]`; `privileges` included the full nine-privilege admission set plus (unexpectedly, see below) hundreds more. **A significant, unplanned finding, reported honestly rather than smoothed over**: `Organizational: Nurse`'s own upstream demo-content role definition inherits `Application: Enters ADT Events` (among three others), and *that* role itself inherits `Privilege Level: High` — an unmodified upstream chain that grants this synthetic clinician session essentially the platform's entire privilege set, independent of anything this ticket added. This is pre-existing (ADR-017 already committed `clinician → Provider + Organizational: Nurse` using these exact unmodified upstream roles) and out of this ticket's scope to fix, but it materially means the real clinician identity, as currently mapped, is already far broader than the nine admission privileges alone would suggest — recorded here since it bears directly on how much marginal risk `Daher Clinician Admission` actually adds for a *real* clinician account versus a hypothetical narrowly-scoped one. **A second pre-existing, load-bearing gap found the same way**: `currentProvider` was `None` for this session despite holding the `Provider` role — traced to `keycloak/mappers/openmrs-provider-disabled-mapper.json`, a hardcoded `oidc-hardcoded-claim-mapper` that unconditionally sets the JWT `provider` claim to `"false"` for every user, regardless of role. ADR-017's own text states this hardcoded claim "is superseded by this matrix: `clinician` now maps to a provider" — but the committed mapper file was never actually updated to reflect that; it still hardcodes `false` and is still named "provider disabled until approved". **Not fixed here** (out of this ticket's stated scope, and changing it is exactly the kind of clinical/scope-surface decision AGENTS.md reserves for a human) — recorded as a real, currently-live discrepancy between `DECISIONS.md`'s stated intent and the actual deployed config, for a future ticket. Per §12's own instruction, no provider was fabricated and no other clinician substituted; the actual result (`currentProvider: null`) is reported as-is. **§11 the exact admission contract, proven live on a real patient**: registered a synthetic patient via a fresh `Daher Reception` session on the *same* clean container (Basic Auth does not work here — `SSO_ENABLED=true` — so registration had to go through a real authenticated session, not admin credentials), then as the synthetic clinician: `POST /visit` (`201`, `Facility Visit` @ `Inpatient Ward`) → `POST /encounter` (`201`, `Admission` type, `visit` field correctly populated) — no misleading `500`, confirming the committed role's `Get Encounters` closes ADR-017C's exact trap. Read-back (`v=full`) on both Visit and Encounter: `creator.uuid` matched the synthetic clinician's own OpenMRS UUID exactly on both — not admin, not the registering reception account, not substituted. Active-visit query returned the new visit — discoverable, matching UI-001A's contract. **§14 bed assignment reconfirmed separate**: the same account, having just completed a real admission, was denied `POST /beds/{id}` (`403`, "Assign Beds") — admission does not implicitly touch bed state; the role does not carry it. **§15 the platform limitation reconfirmed honestly, not hidden**: the same clinician-admission account created an unrelated `Vitals` encounter on the same visit exactly as easily as the intended `Admission` one — `PLATFORM LIMITATION: OpenMRS cannot server-side restrict Add Visits/Add Encounters to only Daher maternity admission with the currently installed supported authorization model. Accepted for V1 only because the role is restricted to clinicians rather than reception.` No frontend language anywhere claims otherwise. **§13 reception negative controls, proven live on the committed `Daher Reception` role itself** (correct per this ticket's own hygiene rule — testing the canonical role by name is appropriate here, not a violation of it): patient search succeeded (`200`, found); registration privileges confirmed present via the session's own `privileges` array (`Add Patients`, `Edit Patient Identifiers`, `Get Patients` — exactly three, nothing else); Visit creation denied (`400` wrapping "Privileges required: Get Visit Types", the same `ConversionException` pattern ADR-017C already characterized); Encounter creation denied (`403`, "Add Encounters,Edit Encounters"); bed assignment denied (`403`, "Get Encounters" — reception lacks that too). **§16 existing-role regressions**: `git diff --stat` on both changed config files shows **zero deletions** across the whole ticket — the `Daher Reception` CSV row, every existing `reconcile-sso.sh` client/mapper block, and `daher-realm.json` are all byte-for-byte untouched by construction, not merely by inspection; the fresh clone's own `Daher Reception` session (above) independently reconfirms its three-privilege boundary at runtime on a container that never saw ADR-017B's cache-collision artifact. `lab-tech`/`sys-admin`/OpenELIS mappings were not runtime-exercised this ticket (no lab-tech/sys-admin client roles exist on this disposable realm to test against — same as every prior ADR-017 ticket) but are structurally unchanged by the same zero-deletion evidence. **§19 CI guards added and control-tested twice**: a new `static` job step asserts `Daher Clinician Admission` exists with exactly the nine approved privileges, never carries `Assign Beds`/registration-write privileges beyond `Get Patients`/administrative privileges, has no inherited roles, and that `Daher Reception` never gains any admission-shaped privilege either; control-tested by adding `Assign Beds` to a throwaway copy of the admission role (guard failed on both the exact-set and forbidden-privilege assertions) and separately by adding `Add Encounters` to a throwaway copy of `Daher Reception` (both the pre-existing ADR-017B guard and this ticket's new reception-side check failed), then restoring the real committed source both times. **`DECISIONS.md`** gained a new `ADR-017D` entry recording the decision and both pre-existing gaps found along the way; **`PROJECT_STATE.md`**'s NEEDS LIBAN #18 updated to DECIDED/IMPLEMENTED. **§17 no real staff, no real facility write, no UI-013**: no button, no navigation change, no frontend file touched; no real SSO activation; no real clinician assigned — the role is deployable, not active. **Cleanup**: the two disposable Keycloak users created this ticket (`adr017d-clinician-user`, `adr017d-reception-user`) deleted; confirmed all four intended client roles remain on the `openmrs` client (`Daher Reception`, `Daher Clinician Admission`, `Provider`, `Organizational: Nurse` — the last newly added to this disposable realm this ticket, left in place as a legitimate, reusable part of the clinician-identity test fixture, not committed config). Two new throwaway containers/databases this ticket (`openmrs-adr017d`, abandoned mid-ticket once its stale single-row CSV boot was diagnosed, and `openmrs-adr017d2`, the one actually used) could not be torn down — the same intermittent daemon-level `docker kill`/`stop`/`rm` denial recorded at the end of nearly every prior ticket this session; needs a human `docker rm -f openmrs-adr017d openmrs-adr017d2` plus their volumes (`openmrs-adr017d-data`, `openmrs-adr017d2-data`) and databases (`openmrs_adr017d`, `openmrs_adr017d2` in `mariadb-ui009`), alongside every prior ticket's own still-pending leftovers) |
| ✅ Done (app-origin OpenELIS mounted; coordinated logout proven live on BOTH the OpenMRS/OIDC and OpenELIS/SAML legs) | **UI-011 / DH-008F** (closed UI-005's remaining browser-identity gap: OpenELIS reachable under `app.$BASE_DOMAIN` and coordinated logout proven end-to-end, not just designed. **§4/§5 config, additive only**: `caddy/Caddyfile`'s `app.{$BASE_DOMAIN}` route gained a `handle /OpenELIS-Global/*` block mirroring `lab.`'s existing reverse-proxy exactly (same upstream, same TLS trust anchor `openelis-ca.pem`, same `tls_server_name`) — deliberately with **no** `header_down Set-Cookie` rewrite, since OpenELIS's `JSESSIONID` was already `Secure; HttpOnly; SameSite=Strict`, stricter than OpenMRS's, and weakening it would regress a working control for no gain. `keycloak/reconcile-sso.sh`'s `openelis` client update gained `$daher_app_url` in `redirectUris`/`webOrigins` alongside its existing `lab.` entries — every other SAML attribute, and the `openmrs` client block, untouched. A pre-existing CI guard (`app.$BASE_DOMAIN origin...` job) had asserted the **opposite** — that `daher_app_url` must never appear in the `openelis` block, citing "ACS derivation unresolved, ADR-027" — correctly identified as UI-005's now-resolved open risk rather than patched around blindly; rewritten to assert presence instead, plus new assertions for the Caddy route (proxy target, path, trust anchor, and that Set-Cookie is NOT rewritten). Validated `caddy validate` clean; the exact CI Python check extracted verbatim from the file and run standalone against current disk state passes, and against a simulated revert of just the `reconcile-sso.sh` change correctly fails. **§6 dual-identity — proven live, with the exact linkage stated rather than assumed from username similarity**: built a disposable OpenELIS (`itechuw/openelis-global-2:3.2.1.11`, fresh Postgres, DH-021's real `certgen` image — no shortcuts) on a new network, and reused UI-009's still-live disposable Keycloak (`kc-ui009`) and OpenMRS (`openmrs-ui009c`) rather than standing up a third stack — connecting `kc-ui009` to the new network under the alias `keycloak.openelis.org` OpenELIS's SAML config hard-codes, and creating an `openelis` SAML client in that same realm with the app-origin addition this ticket's own real config now makes. A synthetic OIDC login against `openmrs-ui009c` and a synthetic SAML login against the disposable OpenELIS, run **under a simulated `Host: app.ui011.invalid` header** (no disposable Caddy — UI-005 already proved Caddy's Host-preserving proxy is the only thing between this and the real thing, and re-validating Caddy itself would have re-spent budget on an already-closed question), both authenticated as the same Keycloak user record `ui009-synthetic-user` (id `cad15efb-efda-48f9-a626-0bf8cb9e30cb`, confirmed via `kcadm`). The AuthnRequest's `AssertionConsumerServiceURL` was measured as `https://app.ui011.invalid/OpenELIS-Global/login/saml2/sso/keycloak` — tracking the Host header exactly, as UI-005 found, now proven accepted end-to-end because the client trusts that origin. **The exact linkage, stated precisely**: OpenMRS's OIDC `id_token` carries `sub: cad15efb-...` and OpenMRS additionally surfaces it as its own `systemId` — a real UUID match. OpenELIS's SAML assertion, because `saml_name_id_format=username` (unchanged, real config), carries only the **username** `ui009-synthetic-user` as `NameID` — OpenELIS itself never receives or displays the Keycloak UUID. Identity equivalence for the OpenELIS leg is therefore established at the **Keycloak** level (one user record, one realm, authenticating both protocols) and confirmed independently via `kcadm`, not by comparing an application-exposed identifier OpenELIS never actually has. Both sides' Keycloak `roles` claim/SAML `Role` attribute agreed (`Provider`). **§7 cookies, measured under the real app-origin Host header on both systems**: OpenELIS `JSESSIONID` — `Path=/OpenELIS-Global; Secure; HttpOnly; SameSite=Strict`, identical to its `lab.`-origin behavior, confirming `SameSite=Strict` does not break the app-origin SAML flow being shipped (session-fixation-safe new JSESSIONID issued post-auth, also measured). OpenMRS's `app.`-hardened cookie (`Secure; SameSite=Lax`, from UI-004) reconfirmed unchanged. **§9–§13 coordinated logout — proven live on both legs independently, including a finding that changes how §8 must be sequenced**: OpenMRS `DELETE /ws/rest/v1/session` → old cookie replay correctly unauthenticated. OpenELIS `/Logout` without `_csrf` → `302` denied, session survives (control reconfirmed); with the page's real scraped `_csrf` → succeeds, old `JSESSIONID` rejected after. Left at local-logout-only, **both legs independently reproduced UI-005's silent-reauth finding**: a fresh SP-initiated OIDC or SAML flow against the surviving `KEYCLOAK_SESSION` issued a working session with **zero credential prompt**, confirmed by completing that silent flow end-to-end into a real second authenticated session each time — this is the exact shared-workstation risk, reproduced fresh on this ticket's own build, not merely cited. Keycloak's end-session then genuinely closed each SSO session: the OIDC leg via `id_token_hint` (a real ID token captured from OpenMRS's own `oauth2IdToken` user property) skipped the confirmation page entirely — `200` with `KEYCLOAK_SESSION`/`KEYCLOAK_IDENTITY` cleared in the same response, the no-click path UI-011 §11 permitted characterizing; the SAML leg (no ID token available on that side) required and got the proven confirmation-page submission, **including its `session_code` hidden field** — omitting it was tried first and produced a Keycloak-side `NullPointerException` (`SessionCodeChecks.initialVerify`, `flowPath` is null), a `500` that left the SSO session alive — a genuine fragility finding (a malformed confirm POST fails **open**, not closed) recorded for §14, not routed around. **A real gap in the first test pass, caught by the advisor before the report shipped**: the OIDC-leg silent reauth had re-established a live `authenticated:true` OpenMRS session, and closing Keycloak's SSO session afterward did **not** retroactively kill it — proving OpenMRS's own local session and the Keycloak SSO session are fully independent artifacts, so §8's three-leg sequencing (both local logouts **and** the Keycloak logout) is not belt-and-suspenders, it is strictly necessary; the surviving session was then explicitly closed via a second `DELETE /session` and reconfirmed dead. With that done, §12's critical proof holds on both systems: a fresh OpenMRS `/oauth2login` now genuinely redirects to a Keycloak **login form** (`name="username"`), and a fresh OpenELIS SAML flow now genuinely lands on `data-page-id="login-login"` with a real username field — zero silent SSO reconstruction on either app, matching §13's shared-workstation requirement. **§14, characterized rather than re-tested for the Keycloak-unavailable case**: both local logouts run first and independently of Keycloak (measured — OpenMRS's `DELETE` and OpenELIS's CSRF `/Logout` never call out to Keycloak), so if Keycloak is unreachable at the redirect step, both local sessions are already genuinely dead while the SSO session survives — a real "locally logged out, silently re-authenticatable" partial state that must not be reported as fully logged out; killing `kc-ui009` to re-derive this was judged unnecessary evidence-spend given the mechanism is already directly observed. **§15 frontend, kept to one button**: `ui/src/features/auth/logout.ts` (`logoutLocalSessions` — OpenMRS `DELETE` with the required `X-Daher-App-Request` CSRF-boundary header, then a same-origin `GET /OpenELIS-Global/Home` to scrape the real `_csrf` token before POSTing `/OpenELIS-Global/Logout`, reporting `'skipped'` rather than inventing a token when none is found; `navigateToKeycloakEndSession` — a real `window.location.assign`, never a `fetch`, to `id.$BASE_DOMAIN`'s end-session endpoint derived from the current hostname) wired into a new "Se déconnecter" button in `AppShell`'s top bar (`onLogout` prop) — no new route, no new screen, French-first. **§16/§17/§18 reconfirmed, not assumed**: the secrets-in-bundle guard re-run against the new bundle found nothing (the new code holds no credential, only a hostname-derivation string); `git diff` on `provisioning/` is empty this ticket — break-glass's `AuthenticationMode` machinery genuinely untouched; the live production Caddy container (`daher-health-caddy-1`) confirmed still `Up 36 hours (healthy)`, undisturbed, and `git diff caddy/Caddyfile` shows the change is purely additive — `emr.`/`lab.`/`id.` blocks byte-for-byte unchanged. **§20 negative controls, all run and recorded above**: app-origin OpenELIS SAML acceptance (positive, since the redirect-URI addition is what this ticket makes real); OpenELIS logout without CSRF (denied, session survives) vs. with CSRF (succeeds); local-logout-only silent SSO reauth (reproduced on both legs) vs. full coordinated logout (silent reauth no longer occurs, both legs); stale OpenMRS and OpenELIS cookies after logout (both refused); browser-bundle secret guard (passes). **Tests**: `ui/` 22 → **29** (7 new, `logout.test.ts` — CSRF-header assertion, all three OpenELIS-leg outcomes including the "never invent a token" control, and the Keycloak-URL derivation), typecheck/build/secrets-guard clean via `vite build` directly (a pre-existing, unrelated root-owned `node_modules/.tmp/*.tsbuildinfo` in this sandbox blocks `tsc -b`'s incremental cache specifically — not caused by this ticket, confirmed absent from a fresh CI checkout, worked around locally by invoking `tsc --noEmit` and `vite build` directly, both clean). Re-measured, not copied: adapter 94, provisioning 580 (unchanged, confirmed via `git diff` showing zero provisioning changes), seed 16, form-safety 19, ui 29 = **738 total**. **Cleanup**: this ticket's disposable OpenELIS (`openelis-webapp-ui011`, `openelis-db-ui011`, network `daher-ui011_internal`, volumes `ui011-openelis-{keystore,properties,db-data}`) could not be torn down — the same intermittent daemon-level `docker kill`/`stop`/`rm` denial recorded at the end of every prior UI-00X ticket this session; `ui011-openelis-ca` (a `--rm` one-shot certgen container) was already removed cleanly. `kc-ui009` and `curl-ui010` were successfully disconnected from the disposable network before that denial was hit; `curl-ui010` has since exited on its own (`Exited (0)`, harmless). Needs a human: `docker rm -f openelis-webapp-ui011 openelis-db-ui011 && docker network rm daher-ui011_internal && docker volume rm ui011-openelis-keystore ui011-openelis-properties ui011-openelis-db-data`, alongside every prior ticket's own still-pending leftovers. No clinical write, no real staff, no real facility SSO activation, no expansion of break-glass to OpenELIS — `SSO_ENABLED` untouched on the real facility throughout) |
| ⛓️ SUPERSEDED by ADR-017E2 below — finding stands, resolved by a patch instead of config | **ADR-017E / DH-008G** (scoped to close ADR-017D's second leftover gap: make the OIDC `provider` claim conditional on clinician identity instead of the committed mapper's unconditional hardcoded `false`. Re-audited first: no numeric OpenELIS site code, fresh VM, or off-site backup credential had cleared. Read the pinned `oauth2login` 1.5.0 source directly before touching config: provider creation is safely idempotent per-Person (`getProvidersByPerson` checked before create, correctly linked), but a genuinely **absent** claim defaults to `provider=true` (fail-open) — the fact that shapes everything below. Three candidate Keycloak mechanisms tried live in a disposable realm, all disproven or rejected: (1) `oidc-usermodel-client-role-mapper`, `multivalued=false` — emits one arbitrary role name from whatever client roles the user holds, non-deterministically, when a real clinician holds `Provider` + `Organizational: Nurse` + `Daher Clinician Admission` simultaneously (ADR-017D's own committed shape) — proven with a synthetic multi-role user getting `"Daher Clinician Admission"` instead of `"Provider"`, and never produces the literal string `"true"` even for a single-role user. (2) `oidc-usermodel-attribute-mapper` reading a per-user attribute — correct when the attribute is set, but **omits the claim entirely when unset**, and a synthetic `Daher Reception`-only user with the attribute unset was live-confirmed to receive a real `currentProvider` — fail-open, proven by login, not inferred. A "reconcile writes the attribute on every managed user" mitigation was considered and rejected: any user created outside a reconcile run stays fail-open until the next run (not a safety property), and it would be a second materialized truth source alongside the client role that already signals clinician identity, which this ticket's own brief warned against inventing. (3) Two same-claim mappers together (fail-closed hardcoded `false` baseline plus the attribute mapper) — the baseline consistently won in every trial; two independent attempts to invert the winner (varying mapper creation order, then mapper name/alphabetical order) both failed, and Keycloak's protocol-mapper REST schema exposes no priority field for same-claim collisions — undefined behavior, not a foundation for a clinical safety property. Keycloak 26.7.1's full mapper catalog was enumerated live (`kcadm get serverinfo`): no role-conditional claim mapper exists, and a scripted mapper is unavailable in this version regardless (and would be borderline "application code," which this ticket's brief excluded). **Conclusion, reported as a platform limitation rather than compensated for in frontend code**: no supported Keycloak mechanism can emit a deterministic, fail-closed provider claim from client-role membership given oauth2login's fail-open default and the clinician identity's multi-role shape. **Nothing changed in the committed config** — `openmrs-provider-disabled-mapper.json` stays as-is, which is functionally wrong (no clinician gets attributed as a Provider yet) but is the correct side to be wrong on (fail-closed). Two candidate paths recorded for the meeting, neither implemented: patch `oauth2login`'s single default-value argument (`"true"` → `"false"`) — a one-line change to logic this project already patches, which would make the whole same-claim-collision problem moot; or accept a manually-set, audited per-user attribute with a compensating "unset attribute" alarm script. **Also recorded, not built**: a dedicated follow-up ticket, **ADR-017F — Clinician Effective-Privilege Audit / Organizational: Nurse Breadth**, for ADR-017D's other leftover gap (`Organizational: Nurse` inheriting `Privilege Level: High`) — deliberately not touched or mixed into this ticket. Full finding recorded in `DECISIONS.md`'s new `ADR-017E` entry. HEAD unchanged from before this ticket (`15df4fbb`) — no repo file besides `DECISIONS.md`/`PROJECT_STATE.md` touched, so the 774-test baseline (adapter 94, provisioning 580, seed 16, form-safety 19, ui 65) needed no re-run; CI already confirmed green at this exact commit. **Cleanup**: two stray leftover Keycloak mappers found live on `kc-ui009` mid-investigation and not attributable to this session's own visible work (a hardcoded global `provider=true`, origin unverified, and a throwaway `provider_probe` client-role probe from this investigation's own earlier ruled-out approach) were deleted; the disposable realm was restored to the single committed `provider disabled until approved` mapper before the session ended. One disposable artifact was **not** cleaned up: a synthetic `Daher Reception`-only probe user (`probe-without-provider`) on `openmrs-adr017d2` was left holding a real, incorrectly-created Provider record from the fail-open test — deleting it requires `Get Providers`, which reception correctly does not hold, and no admin credential was pursued to force it; disposable-environment-only, no production impact. No real SSO activation, no real staff assigned, no real facility write, no OpenELIS change) |
| ✅ Done (role-derived, fail-closed provider identity committed and live-proven; #19 fully closed pending only real SSO activation) | **ADR-017E2 / DH-008G** (resolves ADR-017E's own OPEN finding — this ticket's explicit technical direction supersedes ADR-017E's "not application code" constraint: patch `oauth2login` itself to derive Provider activation from the same `roles` claim OpenMRS role assignment already consumes, exact `Provider` client-role membership only, reusing the project's existing patch/build mechanism for this module (`openmrs-config/patches/oauth2login-bridge/`, first established by UI-007/008 for the `/oauth2login` bridge). **The rule, one path, no second truth source**: OpenMRS client role `Provider` → Keycloak `roles` claim (existing, unchanged mapper) → a new package-private `isProviderRoleGranted(UserInfo)` → the existing, unmodified `activateProviderAccount`/`deactivateProviderAccount`. **The patch** (`OAuth2LoginController.java.patch`, unified diff against the pinned `1.5.0` commit, applied via one more `git apply` step in `openmrs-config/Dockerfile`): replaces the single line that read `userInfo.getString(UserInfo.PROP_PROVIDER, "true")` with a call to the new method, which checks exact `List.contains("Provider")` membership on `userInfo.getRoleNames()` — never substring/case-fold — and catches any `RuntimeException` from a missing/null/malformed claim, returning `false` (fail-closed) rather than propagating. The existing per-Person duplicate-prevention logic inside `activateProviderAccount` is untouched — only *whether* it's called changed. **Reproducibility, proven three independent ways**: the real `openmrs-config/Dockerfile` `oauth2login-build` stage built clean with both patches applied (bridge + this one); the exact committed patch file also applies via plain `patch -p1` against a fresh pristine archive of the pinned commit, independent of `git apply`; a full image build (`daher-health/openmrs-backend:adr017e2-test`) booted successfully. **Unit coverage, real `UserInfo` JSON parsing, no mocking of that layer**: a new `ProviderRoleDerivationTest` (10 cases, all passing) covering every case this ticket specified — `["Provider"]`→true, `["Provider","Daher Clinician Admission"]`→true, `["Daher Reception"]`→false, `["Organizational: Nurse"]`→false, `[]`→false, missing claim→false, null claim→false, malformed (non-array) claim→false with no exception escaping, `["SuperProvider"]`→false, `["Provider Assistant"]`→false; the pre-existing `ProviderAttributeUserInfoTest` (whose old fixture asserted the *opposite* of the new correct behavior) was updated rather than left contradicting the shipped logic. Three pre-existing upstream integration tests fail under this sandbox's JDK17 Maven image on old CGLIB/PowerMock incompatibilities — **confirmed pre-existing and unrelated to this patch** by a control run against pristine, unmodified upstream source under the identical toolchain (identical three failures, zero patch applied). **Live proof, disposable environment only, a fresh clone (`openmrs-adr017e2`) never sharing JVM state with any prior ticket's container**: a genuinely fresh, never-before-authenticated synthetic clinician carrying exactly `Provider` + `Organizational: Nurse` + `Daher Clinician Admission` completed a real OIDC round trip — `authenticated: true`, OpenMRS user UUID `04133caa-8b7f-4d89-8b01-20a349dca2aa`, Person UUID `36116ed9-13ce-425f-b438-42ce27d98331`, Provider UUID `f870d211-e03a-4e12-9e5c-72a021939f62` (identifier = the user's own `systemId`, creator = `daemon`, both matching `oauth2login`'s documented behavior), Provider's linked Person UUID exactly equal to the authenticated user's own Person UUID, `currentProvider` UUID exactly equal to the Provider UUID — **even though the token's stale legacy `provider` claim still read `"false"`**, proving the patch genuinely ignores that claim rather than merely overriding its default. **Repeated-login idempotence, three logins total**: `users`/`provider`/distinct-`person_id` counts held at `1/1/1` throughout, same Provider UUID every time; restart-survival was **not** re-confirmed — blocked by this sandbox's own recurring, pre-existing `docker restart` permission denial (documented at the end of nearly every prior ticket this session) — a disclosed gap, not a fabricated pass. **Reception negative control**: fresh `Daher Reception`-only user — `authenticated: true`, `currentProvider: null`, zero Provider rows, exactly the three approved registration privileges present — the exact fail-open failure mode ADR-017E's attribute-mapper attempt produced, closed here. **Zero-role negative control**: a fresh user with no client role at all failed to authenticate outright (`oauth2login`'s own pre-existing, unmodified logic, not this patch's decision method, which never even runs) — zero Provider rows, consistent with this ticket's own anticipated fallback. **Sys-admin boundary, documented rather than fabricated**: no OpenMRS client role has ever existed for sys-admin (ADR-017's mapping is realm-level, inert for OIDC per ADR-017B) and creating one here would itself be an out-of-scope roster decision this ticket's own §12 explicitly excludes; the nearest safe existing proxy — a fresh user holding only the already-broad `Organizational: Nurse` role (`Privilege Level: High` per ADR-017D) but not `Provider` — correctly got `currentProvider: null`, zero Provider rows: broad administrative privilege alone does not imply clinical Provider identity. **Lab-tech boundary**: zero OpenELIS/SAML files touched (`git diff` confirmed), no OpenMRS client role exists for lab-tech, lab-tech does not authenticate to OpenMRS by design. **Admission re-proved with the now-provider-enabled clinician**: patient → `POST /visit` (`201`) → `POST /encounter` (`201`, `Admission`, linked), using the existing unmodified UI-001A/ADR-017D payload — `encounterProviders: []`, **not silently populated** despite `currentProvider` now existing, recorded precisely rather than compensated for; both Visit and Encounter `auditInfo.creator.uuid` matched the clinician's own OpenMRS user UUID exactly. **Three identities kept explicitly separate**: authenticated user = audit creator (same UUID, confirmed) ≠ clinical Provider (a distinct entity, correctly Person-linked, not itself an audit creator or an Encounter field). **Provider ambiguity control, isolated throwaway DB clone only (`openmrs_adr017e2_ambig`), never the canonical database**: a second, independent, non-retired Provider row was inserted by direct SQL for the same Person, then the clinician logged in against a fresh container on that clone — no third Provider was created (the existing per-Person check held), and OpenMRS's own pre-existing `currentProvider` computation returned **`null`** rather than arbitrarily picking either one — the safe side of ambiguity, no STOP needed. **Fresh-realm/existing-realm parity, proven**: `keycloak/realm-config/daher-realm.json`'s `openmrs` client no longer carries the stale `provider disabled until approved` mapper; `keycloak/reconcile-sso.sh` gained `remove_mapper_if_present` (mirrors `reconcile_mapper`'s own list-and-filter idiom, applied to deletion) in place of the old `reconcile_mapper` call for that mapper name — run twice live against the disposable realm (run 1 removed it, confirmed by listing only `client roles` remained afterward with all four existing client roles untouched; run 2 reconciled cleanly with zero change); importing `daher-realm.json` fresh into an independent test realm produced the identical single-mapper set. Zero Keycloak JSON anywhere under `keycloak/` references a `provider` claim after this ticket (grepped). **CI guard added and control-tested six ways**: a new `static` job step asserts the decision method and call site exist, the legacy fail-open call is not reintroduced on an added diff line, the role constant is exactly `"Provider"`, membership is exact `List.contains`, the method fails closed, the Dockerfile applies the patch, no Keycloak JSON mentions a provider claim, and reconciliation removes rather than recreates the stale mapper; six deliberate in-memory breaks (revert to fail-open, make Reception the role constant, substring match, reintroduce a global provider mapper, leave the stale mapper reconciled, unwire the patch) each correctly failed, baseline restored and re-passed after each. A pre-existing ADR-017D guard's blunt `"delete" in reconcile.lower()` check — which would have false-failed on this ticket's own legitimate mapper deletion — was narrowed to the specific dangerous shape (a *role* deletion call), since roles may carry real staff-assignment history and mappers do not. **Regressions**: zero changes to `roles-daher.csv` or any existing client role; all four client roles survived two live reconciliation runs unchanged; zero OpenELIS files touched; zero application code touched (`adapter/`, `provisioning/`, `ui/`, `openmrs-config/seed`, `openmrs-config/forms` all untouched by `git diff`) — the 774-test baseline unaffected by construction, matching this repo's own established reasoning for config/infra-only tickets. **`DECISIONS.md`** gained the new `ADR-017E2` entry and marked ADR-017E's own status SUPERSEDED (finding stands, resolved differently than ADR-017E's own scope allowed); **`PROJECT_STATE.md`**'s NEEDS LIBAN #19 updated to DECIDED/IMPLEMENTED. **Explicitly not done**: no real SSO activation, no real staff assigned, no real facility write, no patient/Visit/Encounter written outside disposable/throwaway environments, `Organizational: Nurse`'s own breadth untouched (ADR-017F, separate). **Cleanup**: the disposable realm's superseded mapper and ADR-017E's own two stray leftover mappers were removed from `kc-ui009` (disposable-only, not committed config); five synthetic Keycloak users from this ticket left in place deliberately (established hygiene pattern); the throwaway ambiguity database and user were dropped successfully, its one-off container (`openmrs-adr017e2-ambig`) could not be torn down — the same intermittent daemon-level `docker kill`/`rm` permission denial recorded at the end of nearly every prior ticket this session — needs a human `docker rm -f openmrs-adr017e2-ambig` alongside this ticket's own new `openmrs-adr017e2` and its database `openmrs_adr017e2` in `mariadb-ui009`, plus every prior ticket's own still-pending leftovers; the throwaway realm-import test (`daher-fresh-test`) was already deleted cleanly. **Final closure pass (same day, before commit)**: the 774 baseline was re-run live under CI's exact pinned toolchain (`python:3.13.14-slim-bookworm`, Node 22) rather than cited unchanged — adapter 94 (ruff+format+pytest, all clean), provisioning 580 (ruff+format+pytest, all clean — an initial `ruff check .` run surfaced pre-existing, out-of-CI-scope issues in `provisioning/demo/`, resolved by re-running with CI's actual `ruff check src tests` scope, which is clean), seed 16, form-safety 19, ui 65 (lint+typecheck+vitest+build+secrets-check, all clean) — confirmed **774 total, live, not assumed**. The entire `static` job (env/secrets/image-pinning/compose-invariants/shell-syntax/JSON-parse/doc-links/app.$BASE_DOMAIN/ADR-017B/ADR-017D/ADR-017E2 guards) and all three `compose` profile parses were also re-run locally and pass. **Clarified**: the 10 `ProviderRoleDerivationTest` cases are not part of the 774 and never were — no CI job builds or tests the `openmrs-config` Docker image at all, and the Dockerfile itself always builds with `-Dmaven.test.skip=true`; they are a manual, out-of-band verification artifact for the third-party `oauth2login` patch. **Restart-survival, honestly scoped**: `docker restart` remains blocked by this sandbox's daemon permission issue, so a genuinely separate process (`openmrs-adr017e2-freshproc`, `docker run`, not `restart`) was booted from the same final image against the same already-populated database — identical OpenMRS user/Person/Provider UUIDs, `currentProvider` still linked, Provider row count still 1 — reported as fresh-process evidence, not claimed as a literal restart. Reception and non-Provider (`Organizational: Nurse`-only) negative controls, the admission re-proof, and the provider-ambiguity control (a second, freshly-built throwaway clone this pass) were all re-run against that same fresh process/image with identical results to the prior pass. Keycloak reconciliation converged a third time with zero drift. The image was rebuilt clean from this pass's final tree (Docker's cache correctly reused the byte-identical patch layer) — OMOD hash `ae148634ec06817efcc99fdf9c0a1a4e05565ef76292c8eb7899b3b4aec8966b`, confirmed identical to the hash of the OMOD actually loaded inside the running fresh-process container. Provider-creation-failure remains source-inspected only (`activateProviderAccount`'s own `catch(Exception)`+`finally` logs and returns normally, untouched by this patch) — reported as residual evidence, not a completed live control. **Cleanup this pass**: `curl-ui010` had exited after ~27h and was restarted via `docker start` (works; `restart`/`kill` do not); a second ambiguity database was dropped cleanly, its container could not be removed (same daemon permission issue) — `openmrs-adr017e2-ambig2` added to the list of containers needing a human `docker rm -f`. **DH-008G-CI (closure)**: the 10 `ProviderRoleDerivationTest` cases are now genuinely gated by CI, not manual-only — a new `oauth2login-provider-tests` job (`.github/workflows/ci.yml`, the only file this ticket touches) runs inside the exact pinned Maven/JDK image, clones and commit-verifies the pinned `oauth2login` source, applies the same committed patch files the Docker build consumes, installs the test file, and asserts the literal `Tests run: 10, Failures: 0, Errors: 0` line tied to the class name. `ProviderAttributeUserInfoTest` stays excluded (confirmed unstable on this JDK17/CGLIB toolchain, same pre-existing failure ADR-017E2 already found) — documented, not gated. **A real bug in the gate's own first draft, caught by running it, not assumed**: the initial `^Tests run:` regex anchor never matches because every Maven line is prefixed `[INFO] ` — fixed to an unanchored, class-name-scoped match. **Control-tested four ways** on a throwaway copy, never the committed file: substring matching (2 failures), fail-open on missing/malformed roles (2 failures), wrong role constant (3 failures), corrupted patch context making `git apply` itself fail (pipeline halts before any test runs) — all four correctly failed the gate; the throwaway copy was restored and diffed byte-identical to the real file afterward. Static guard retained unchanged. No provider architecture, Keycloak, role-privilege, or application code touched — `git diff --stat` shows exactly one file. Application total stays 774; oauth2login provider tests (10) now reported as a separate, CI-mandatory figure) |
| 🛑 Blocked on **BLOCKER-7** (human) — pre-write gate, zero application code written | **UI-013** (clinician admission workflow — the first Daher-UI admission write. Re-audited first per §1: NEEDS LIBAN #7's numeric OpenELIS site code, DH-017's fresh VM, and DH-014's off-site backup credential all remain externally blocked, unchanged since the last ticket to check them; no unblocked ticket displaced this one. §3's pre-write gate — is there an approved OpenMRS Visit type for a Daher maternity admission — failed closed: `GET /ws/rest/v1/visittype` against the local stack's full committed config returns the same five generic types UI-001A already characterized (`Facility Visit`, `Home Visit`, `OPD Visit`, `Offline Visit`, `Group Session`); `Facility Visit` has been reused since UI-001A purely as an inherited authorization-test fixture (ADR-017C/D/E2), never approved as the clinical designation; the full `DECISIONS.md` ADR inventory (ADR-001 through ADR-027, headers enumerated) contains no visit-type approval. Recorded as **BLOCKER-7 / NEEDS LIBAN #20**, not routed around. `Maternité` (the location half of the payload) is separately confirmed committed and unambiguous (`locations-daher.csv`, `Admission Location` tag, sole result by name) — but flagged as its own residual: every live admission proof to date (ADR-017C/D/E2) ran at generic `Inpatient Ward` on containers explicitly lacking the `Maternité` overlay, so admission has never actually been proven at the real target location either, independent of the visit-type gate. No code, config, or CI file touched; `ui/src/features/patients/PatientWorkspace.tsx`'s existing "Admission et contexte de grossesse non disponibles" placeholder and `ui/src/api/client.ts` (no Visit/Encounter functions) are both unchanged. Test baseline re-measured, not copied: adapter 94 (pytest, local venv), ui 65 (vitest), seed 16, form-safety 19 (`unittest`) all directly re-run and matched; provisioning 580 not re-run this session (local Python 3.14 cannot build `pydantic-core`/PyO3 against this repo's pin — a local tooling gap, not a code claim) and carried forward on `git diff` evidence that no application file changed since ADR-017E2's own live 580-pass confirmation. Total unchanged at **774**; oauth2login provider tests remain the separate 10/10 CI-mandatory figure, untouched. Per AGENTS.md's blocked-work rule, this ticket does not sit idle — no other unblocked ticket exists per §1's own re-audit, so UI-013 stops here rather than being silently reassigned) |
| ✅ Done (BLOCKER-7 / NEEDS LIBAN #20 resolved; UI-013 unblocked, not yet built) | **ADR-028** (implements Liban's decision recorded in `DECISIONS.md`: Option C from BLOCKER-7 — a dedicated Daher-owned OpenMRS Visit type, `Daher Maternity Admission` (UUID `194bf546-68d7-42c0-8371-5b0f2b1cf5f3`), rather than promoting any upstream generic. **Mechanism confirmed by disassembly before writing config, per the ticket's own instruction not to assume a CSV path/schema**: `docker exec` into the running backend, `jar xf` the pinned `initializer-2.12.0.omod`'s `initializer-api-2.12.0.jar`, `javap -c -v` the extracted classes. `Domain.VISIT_TYPES.getName()` — bytecode-confirmed — computes the on-disk domain folder as `"VISIT_TYPES".replace("_","").toLowerCase()` = `visittypes`, the identical mechanism already backing `locations`/`roles`/`globalproperties`. `VisitTypeLineProcessor extends BaseLineProcessor<VisitType>` — bytecode-confirmed — reads exactly `name`/`description` plus the shared generic `uuid`/`void-retire` columns every domain uses; the identical CSV shape `locations-daher.csv`/`roles-daher.csv` already use, so no new schema was invented. **Committed:** `openmrs-config/initializer/visittypes/visittypes-daher.csv` (one row, additive, `Void/Retire` empty) and one new additive bind mount in `docker-compose.yml`, alongside the existing three `zz-daher-health` mounts, none of which were touched. **Live proof, three throwaway environments, never the real `openmrs-data` volume**: a genuinely fresh empty database (`mariadb:10.11.18` + the pinned `daher-health/openmrs-backend:3.7-off` image) produced exactly one visit type, `Daher Maternity Admission` — and, honestly reported rather than smoothed over, **zero upstream generic types**, since this exact pinned image/config ships no default `visittypes` CSV of its own on an empty database, a pre-existing characteristic unrelated to and unchanged by this ticket. A `mysqldump`/restore clone of the long-lived `openmrs-ui009c` disposable stack's real, already-populated schema (into a fresh schema on the same `mariadb-ui009`) proved the retrofit case that actually matters: before, 5 visit types; after mounting the new domain, **6 — the five originals at their original UUIDs, unchanged, plus exactly one new row**. A second, independent container process booted against that same already-provisioned clone schema (`docker restart`/`stop` remain blocked by this sandbox's recurring daemon permission denial, so a second fresh process was used instead, matching ADR-017E2's own established workaround) reproduced **identical six rows** via both the REST API and a direct `SELECT name, uuid, retired FROM visit_type` — no duplicate, `retired=0` throughout. `Maternité` reconfirmed exactly once, both on the fresh DB and the real main stack (port 8080) — `6538f097-382d-49b6-a839-601d03c888c0`, untouched. **CI**: a new static guard (`.github/workflows/ci.yml`, "ADR-028 — Daher Maternity Admission is a single, additive, Daher-owned Visit type") asserts exactly one committed row with the pinned name and UUID, not voided, not reusing an upstream name, no stray extra file in the domain, and the compose mount present; control-tested against five deliberate breaks (rename to `Facility Visit`, wrong UUID, duplicate row, voided row, missing compose mount) — all five correctly failed, the real committed config passes. **`DECISIONS.md`** gained the new `ADR-028` entry; **`PROJECT_STATE.md`**'s BLOCKER-7 marked RESOLVED and NEEDS LIBAN #20 marked DECIDED/IMPLEMENTED. **Test baseline re-measured, not copied**: adapter 94, ui 65, seed 16, form-safety 19 all directly re-run and matched; provisioning 580 not re-run this session (same local Python 3.14/`pydantic-core` tooling gap as the prior ticket) and carried forward — `git diff` confirms zero changes to `provisioning/`. Total unchanged at **774**. The 10 `oauth2login` provider-patch tests were not re-run — `git diff` confirms zero changes to `openmrs-config/patches/oauth2login-bridge/` or the `oauth2login-provider-tests` CI job — remain the separate, CI-mandatory 10/10 figure from DH-008G-CI. **No patient/Visit/Encounter clinical write, no real staff assigned, no real SSO activation, no production container or volume touched.** **Cleanup**: the throwaway clone schema `openmrs_ui013_existing` was dropped successfully; four throwaway containers (`ui013-fresh-db`, `ui013-fresh-omrs`, `ui013-existing-omrs`, `ui013-existing-omrs-pass2`), the `daher-ui013-net` network, and two volumes (`ui013-fresh-db-data`, `ui013-fresh-omrs-data`) could not be removed — the same recurring daemon-level `docker kill`/`stop`/`rm` permission denial this session has hit repeatedly — added to the list of leftovers needing a human `docker rm -f`/`docker network rm`/`docker volume rm` alongside every prior ticket's own still-pending ones) |
| ✅ Done — clinician maternity admission workflow built and live protocol-proven | **UI-013** (resumed after BLOCKER-7/ADR-028 and BLOCKER-8/ADR-029 cleared the canonical contract. Implementation: `src/features/admission/` (pure `deriveAdmissionState`/`deriveReadiness`, the `useAdmission` orchestration hook, `AdmissionPanel`), `src/api/admissionConstants.ts` (pinned canonical UUIDs), and additions to `src/api/client.ts`/`types.ts` — `getSession`, `getActiveVisits`, `createMaternityVisit`, `createAdmissionEncounter`, `verifyAdmissionMetadata` (a drift check only, never the payload source). Replaces the Résumé tab's former admission placeholder. **Canonical contract used exactly**: Visit type `Daher Maternity Admission` (`194bf546-…`) + Encounter type `Admission` (`e22e39fd-…`) + location `Maternité` (`6538f097-…`) — never `Facility Visit`/`Inpatient Ward`. **Clinician OIDC: live-proven**, genuine `/oauth2login` round trip against a real Keycloak (synthetic identity `Provider`+`Organizational: Nurse`+`Daher Clinician Admission`), no Basic Auth, no admin substitute. **currentProvider: live-proven non-null**, role-derived per ADR-017E2, reconfirmed working end-to-end. **Visit + linked Admission Encounter: live-proven** — `POST /visit` 201, unconditional reconciliation, `POST /encounter` 201 with the confirmed Visit uuid, unconditional reconciliation, linkage confirmed, final state `COMPLETE`. **Audit creator**: both Visit and Encounter `auditInfo.creator.uuid` equal the authenticated clinician's own OpenMRS user UUID, not admin/daemon. **encounterProviders: `[]`**, unmodified, exactly the ADR-017E2 contract. **Partial Visit recovery: live-proven** — a second synthetic patient established at Visit-only, fresh state derivation resolved `PARTIAL_VISIT`, `resume()` produced zero additional Visit POSTs and exactly one Encounter POST, final state `COMPLETE`. **Existing COMPLETE: live-proven zero-write** — re-deriving state on an already-COMPLETE patient and re-invoking `admit()` produced zero POSTs. **Reception**: genuine OIDC login (`currentProvider: null`, registration privileges intact, used to register the synthetic patients), then direct server denial of both Visit create and Encounter create (`400`, "Privileges required" — matching ADR-017C/D's own characterized shape); the read-denial path (`403` → the neutral `unavailable` UI state, not broadened) confirmed at the protocol level. **Provider-missing: live-proven fail-closed** — a synthetic identity holding exactly `Daher Clinician Admission` without `Provider` authenticated with `currentProvider: null`, no provider fabricated. **Ambiguous-write evidence: deterministic unit fault-injection only, not live-induced** (network drop, timeout, unparsable 2xx, Encounter 5xx all route to reconciliation, never a blind retry — unit-tested, not packet-loss-induced live). **Browser-rendered live UI: not demonstrated** — component behavior is test-demonstrated (148 tests) and the protocol is live-demonstrated end-to-end, but no headless-browser/jsdom pass against a live backend was attempted, disclosed rather than implied. **Tests**: ui 148 (was 65, +83), adapter 94/provisioning 580/seed 16/form-safety 19 unchanged and confirmed untouched by `git diff`; application total **857**; oauth2login provider tests remain the separate 10/10 figure, not touched by this ticket (though its underlying mechanism was independently reconfirmed working via the live clinician login above). Local verification (`tsc -b --noEmit`, `oxlint`, `vitest`, `vite build`, secrets-in-bundle) all green. **No clinical decision changed.** **No real facility mutation**: every write was to a `mysqldump`-cloned throwaway schema, a throwaway Keycloak realm, and disposable synthetic patients/users; the real `openmrs` database was only ever dumped from, read-only. All disposable containers cleanly torn down (`docker exec <name> kill -9 <pid>`, since `docker stop`/`kill`/`rm` remain daemon-permission-denied) — no leftovers this ticket. See DH-028 for a real, previously-undiscovered operational gap found along the way: the main stack's own running OpenMRS image predates the oauth2login patches) |
| 🟡 Built + tested, not yet committed — awaiting Liban's review | **UI-014** (Maternité board — a read-only screen replacing the empty Maternité nav placeholder, so a clinician can see who is currently admitted and open their existing workspace. **§1 authoritative query, proven live, not assumed**: `GET /ws/rest/v1/visit?location=<Maternité>&visitType=<Daher Maternity Admission>&includeInactive=false&limit=100&v=custom:(...)`, run verbatim (the exact `MATERNITY_BOARD_REPRESENTATION` string, not an investigation variant) against the disposable `openmrs-adr017f` clone: both `location` and `visitType` are genuinely enforced server filters (a garbage location UUID 404s; combining both returns their true intersection, not either alone), `includeInactive=false` is the real "active" signal (a stopped visit is excluded at `false`, included at `true`), and the shipped `limit=100`/`v=` pair returned the 5 real matching rows intact. Re-run at `limit=2` against the same data to confirm the `next` pagination link genuinely appears (not assumed) — this is the only real-server evidence for the `truncated` UI flag. **A correction to UI-001A's own documentation, found live, not invented**: the `patient` sub-representation embedded in this (and, by the same mechanism, any) visit-list query never carries a nested `person` field at any representation depth tried, on this pinned build — contradicting §13a.1's claim that result cards carry age/DOB. `GET /person/{uuid}` does carry `gender`/`age`/`birthdate`, confirming the data exists as a genuinely separate resource, not embedded here; the board deliberately does not add an N+1 fetch for it, and so omits the birth-info line entirely rather than printing a misleading "non renseignée." `docs/UI-001-CLINICAL-UX.md` §13a.1 needs a future correction pass; not edited by this ticket (outside §14's list), and `OpenMrsPerson` was left in `types.ts` since `formatBirthInfo`/patient-search still read it on a different call path. **§2 membership rule**: a Visit qualifies alone — `visitType.uuid` and `location.uuid` both matching the two pinned UUIDs exactly, `stopDatetime` absent — re-checked client-side in `deriveMaternityBoard.ts` even though the server query already filters on them, the same defense-in-depth `deriveAdmissionState` already applies elsewhere. 13 permanent tests cover every exclusion (wrong type, wrong location, wrong-both, inactive, an unrelated active visit for an otherwise-genuine patient). **§3 authorization — no new privilege needed, no STOP triggered**: live OIDC-proven that the existing nine-privilege `Provider`+`Daher Clinician Admission` identity already reads this query cleanly, while `Daher Reception` gets a clean `403` (`"Privileges required: Get Visit Types"`) — mapped to the same neutral `unavailable` UI state `useAdmission` already established, never a broadened privilege; `git diff` confirms zero changes outside `ui/` (`roles-daher.csv`/`reconcile-sso.sh` untouched). **§7 sorting**: client-side ascending by earliest `startDatetime` (oldest-admission-first — a new admission appends to the bottom rather than jumping to the top), computed once in `deriveMaternityBoard` as `entry.admittedAt` and rendered directly by the card (no second recomputation to drift out of sync) — documented as UX-only, never clinical priority; server order is never assumed sorted. **§8 malformed-duplicate handling**: the normal write path structurally cannot produce two concurrent active Daher-maternity visits for one patient (`Visit.visitCannotOverlapAnotherVisitOfTheSamePatient`, confirmed live — the attempt itself returned `400`), so the condition can only arise from historical/direct-database data; reproduced anyway via direct SQL against a dedicated fresh synthetic patient (never reusing another ticket's fixture — an initial reuse of ADR-017F's own patient was caught and reverted), and the board collapses it to one `AMBIGUOUS` card with an inline warning, never two silent cards, never a mutation. **§9 refresh**: refetch on screen entry (mount effect, matching `useAdmission`'s established pattern) plus an explicit "Actualiser" button; no polling, no websocket, matching the ticket's own V1 scope. **UI**: `ui/src/features/maternity/` (new) — `deriveMaternityBoard`, `useMaternityBoard`, `MaternityBoardPage`, `MaternityPatientCard`, `formatAdmissionDateTime`; `App.tsx` wires the nav item (was `available:false`) to the new page or, when a patient is selected, the same `PatientWorkspace` used from search (`backLabel` prop added, no duplicated workspace logic); required states (loading/populated/empty/unauthorized/network/server-error/ambiguous) all implemented and tested; empty-state copy is exactly "Aucune patiente actuellement admise en maternité." — never implying no patients exist in the facility. **§6 data safety**: no UUID/backend-internal term ever rendered (DOM-content regex-checked, not source-text-scanned); no localStorage/sessionStorage/IndexedDB write, tested. **Tests**: ui 195 (was 148, +47 — includes one added after an advisor pass caught two independent "earliest admission" computations that could silently disagree; consolidated to one `admittedAt` field, with a new assertion that the admission date actually renders); adapter/provisioning/seed/form-safety **not independently re-run this ticket** — local `pytest` remains unavailable (`ModuleNotFoundError`, the same tooling gap prior tickets recorded) — carried forward at 94/580/16/19 on `git diff` evidence that nothing outside `ui/` changed; application total unchanged at 857 elsewhere, **903 including the new ui count**. `tsc -b --noEmit`, `oxlint`, `vite build`, and the build's own secrets-scan all clean. **Browser-rendered live UI: not attempted**, disclosed rather than implied, matching UI-013's own precedent — what was proven live is the real derivation against a genuinely live disposable backend (real OIDC sessions, real Visit rows, real 403s) plus 195 component/unit tests, not a headless-browser pass. **No real facility mutation**: every write was SQL/API against the disposable `openmrs_adr017f` clone; real SSO stayed off. **Do NOT commit** — per Liban's explicit instruction this ticket stops for review before staging anything; disposable containers (`kc-ui009`, `mariadb-ui009`, `openmrs-adr017f`) and their synthetic fixtures (Patients B/C/D) were left running/in place for that same reason, not yet torn down) |
| ✅ Done — redeploy completed and independently re-verified 2026-08-26 | **DH-028** (rebuild and verify the main OpenMRS runtime image. **Re-proof, before touching anything**: running container `124cb0d94fa4…` (image `456ae25160a4…`, tag `3.7-off`, started `2026-08-21T20:20:37Z`, `SSO_ENABLED=false`, DB volume `daher-health_openmrs-db-data` unchanged), its baked `oauth2login-1.5.0.omod` SHA-256 `85164506ea51…`, containing only upstream `OAuth2LoginRequestFilter`/`OAuth2ServiceAccountFilter` — **no** `OAuth2LoginBridgeFilter`. With `SSO_ENABLED=false` the module is correctly excluded/not loaded at all (absent from `GET /module`, `oauth2.enabled=false`), so the current `/oauth2login` 404 is the intended fail-closed inactive state, not itself evidence of anything broken — the omod-file staleness is a separate, static fact. Local-login control reconfirmed: real admin credential `authenticated:true`, published default `Admin123` `authenticated:false` (DH-019 intact). Read-only sentinels before touching anything: patient=103, users=6, encounter=725, visit=143 (synthetic/demo data accumulated this session, not real facility data). **Build, verified before any deploy attempt**: `docker compose build openmrs-backend` from HEAD `600343d…` (all layers cache-hit — this exact content was already produced earlier this session for other verification purposes) → image `f1376269c286`, extracted `oauth2login-1.5.0.omod` SHA-256 `ae148634ec06817efcc99fdf9c0a1a4e05565ef76292c8eb7899b3b4aec8966b` — **identical to the hash ADR-017E2's own log already recorded**, and containing `OAuth2LoginBridgeFilter.class`; `javap` on the extracted `OAuth2LoginController.class` confirms `isProviderRoleGranted`/`DAHER_PROVIDER_ROLE_NAME` present — both patches verified in the artifact itself, not assumed from a successful build. The Dockerfile's own comment notes the OMOD build is not guaranteed byte-reproducible (jar timestamps); this hash matching a prior independent build anyway is corroborating, not required, evidence. **Redeploy: BLOCKED.** `docker compose up -d --no-deps --force-recreate openmrs-backend` reached the "Stopping" step, silently failed to actually stop the running container (the same daemon-level `docker stop/kill/rm` permission denial recorded at the end of nearly every prior ticket this session), then failed on a container-name conflict trying to create the replacement. Per this ticket's own explicit instruction, **no alternative process-manipulation workaround was attempted** on the main stack container — that workaround (`docker exec <name> kill -9 <pid>`) is documented elsewhere in this file as safe for *disposable* throwaway containers only, never demonstrated safe for the main stack, and this ticket's own safety boundary asked to stop rather than invent one here. The orphaned never-started `Created` container from the failed attempt was removed cleanly (no permission issue — it never ran). **Verified after the attempt, nothing changed**: same container ID, same image ID, same `StartedAt`, DB sentinels identical (103/6/725/143), `SSO_ENABLED` still `false`. **Steps 7–13 (runtime identity proof, data-preservation re-check, local-login regression, module-state inspection, disposable SSO proof, OMOD equivalence, service regression) were not reached** — they all depend on a successful redeploy that did not happen. **Not done. DH-028 stays open**, blocked on the same environment-level Docker daemon permission limitation as every prior ticket this session, not on any new decision or code defect. **No source, config, role, Keycloak, or clinical data changed** — `git status` confirms a clean tree at unchanged HEAD `600343d…` throughout. **Recommended path**: either grant this environment's Docker daemon stop/kill/recreate permission for the main stack specifically, or perform the redeploy from a host/session that already has it — the built, verified image (`f1376269c286`, tag `daher-health/openmrs-backend:3.7-off`) is ready and waiting locally either way. **RESOLUTION 2026-08-26 (Bug #24/#25 ticket, reported by Liban, independently re-verified in this session before being recorded here — not taken on trust):** the redeploy was performed manually, outside the environment's own blocked daemon path. Re-verified directly against the live main stack: `daher-health-openmrs-backend-1`'s running image is `sha256:f1376269c286fd94c5559a18d429acaf70cf9b7f0e801b89d38d2e23a048e0ed` (exact match to the image built and hash-verified above); its `oauth2login-1.5.0.omod`, `sha256sum`'d directly inside the container, is `ae148634ec06817efcc99fdf9c0a1a4e05565ef76292c8eb7899b3b4aec8966b` (exact match); `OAuth2LoginBridgeFilter.class` confirmed present via a direct `zipfile` listing of that exact artifact, not assumed from the hash. `SSO_ENABLED=false` unchanged (deliberate — BLOCKER-5 still gates real activation). `GET /ws/rest/v1/session` and `GET /ws/fhir2/R4/metadata` both `200`. Data-preservation sentinels re-measured by direct `SELECT COUNT(*)`: patient `103`, users `6`, encounter `725`, visit `143` — identical to the pre-redeploy figures recorded above, confirming no data loss across the redeploy. **DH-028 is done — the main stack now runs the same patched image ADR-017F's live proof used on its disposable clone.** No source, config, or committed file changed by this verification pass (read-only checks only)) |

## ▶ START HERE (next session)

**Read `DECISIONS.md` first — 27 ADRs, several load-bearing.** Then this section.

### Where things actually are

Two systems now live in this repo:

1. **The clinical stack** (`docker-compose.yml`) — 11 services running locally, verified. Golden
   flow is **1 of 8 steps**. The order/result transport (DH-011/012) is **not built**.
2. **The provisioning control plane** (`provisioning/`) — API, portal, host agent, durable state.
   580 tests (UI-011 re-measured). Works end to end **against a stand-in compose file**.

⚠️ **The 1 Sept pilot target is at risk by a recorded decision, not an oversight.** Liban chose on
2026-08-15 to build provisioning before DH-011's transport chain, so the golden end-to-end flow will
very likely not be demonstrated by then.

### Unblocked and buildable right now

**The DH-011 order path is half built and now waits on you.** Read BLOCKER-6 before anything else in
this section — it is the decision that unblocks five of the eight golden-flow steps.

1. 🛑 **DH-011's target leg is blocked on BLOCKER-6 / NEEDS LIBAN #16.** Probing before writing code
   found that OpenELIS **pulls** orders from a remote FHIR source rather than accepting a push into
   its FHIR store — the mechanism every document here implicitly assumed. **Five** candidates are
   recorded with their costs; DH-011B proved Option F reaches real native lab state but is refused
   by accession configuration (`DEV01`, NEEDS LIBAN #7). Which path to use changes what the adapter
   *is*, and one option adds a second system storing patient data, so it is yours.
   ✅ **Already done meanwhile:** DH-011A (ADR-026), DH-011B (spike), **DH-011E** (poller + watermark,
   built and verified, deliberately not activated).
2. ~~**DH-027M — CI**~~ — ✅ **DONE 2026-08-17.** `.github/workflows/ci.yml` gated **582 tests** at
   landing; it currently gates **700 tests across five suites** (adapter 94, provisioning 549, seed
   16, form-safety 19, `ui/` frontend 22 — measured against the real CI run, not copied) plus Ruff,
   `ui/`'s lint/typecheck/build (including its own secrets-in-bundle check), the app.$BASE_DOMAIN/
   OpenMRS-reconciliation structural check (UI-004), compose interpolation on both profiles,
   shell/JSON/XML/CSV syntax, image pinning, a tracked-secret scan, the
   `.env`-untracked check, and relative-link resolution. The `ui-frontend` job was added in UI-002 —
   before that, the new frontend's own tests ran locally but nothing gated them on push. Details and
   the honest limits are in the ticket entry below.
3. **DH-017** — the README needs a walk-through on a genuinely fresh VM. Documentation verified only
   against an already-working machine is the weakest kind. ⚠️ Now partly entangled with the
   cloud-primary direction, which is itself an open decision (data residency). **This is the next
   agent-buildable ticket**, and it needs a VM someone can give me access to.
4. ~~**Known bug 17**~~ — ✅ **fixed 2026-08-16** with per-verb timeouts. It left **bug 18** open by
   decision: the agent does not poll while a long job runs, so jobs issued in that window are refused
   as stale. **Bugs 19 and 20 were also opened** — see the known-bugs table; 19 is an upstream
   OpenELIS `201`-on-failure that any target implementation must defend against. ~~**Bug 18**~~ — ✅
   **fixed 2026-08-20**: `execute` runs on a background thread, so the loop keeps polling while a
   long job runs; a companion fix to DH-027C's lifecycle table (`app.py`) was required alongside it —
   see the known-bugs table.
5. ~~**UI-013**~~ — ✅ **DONE 2026-08-26.** Clinician maternity admission workflow, built and
   live protocol-proven end to end against the canonical contract (`Daher Maternity Admission` +
   `Admission` + `Maternité`, ADR-028/ADR-029). See its own ticket-status entry above for the full
   evidence. `Maternité` itself was live-proven, closing the residual note this item used to carry.
   **DH-028** (a separate, non-clinical operational follow-up found along the way — the main stack's
   own OpenMRS image predated the oauth2login patches) was not a UI-013 blocker and is now
   ✅ **done** — redeployed and independently re-verified 2026-08-26, see its own entry.
6. ~~**ADR-017F**~~ — ✅ **DECIDED AND LIVE-PROVEN 2026-08-26.** Liban chose **Option A**: the Daher
   clinician identity is `Provider` + `Daher Clinician Admission`, `Organizational: Nurse` excluded.
   `Organizational: Nurse` had measured at 302-of-345 effective privileges (87.5%); all of ADR-017D's
   approved admission privileges were a strict subset; zero committed code depended on it. No
   committed config changed (the old mapping was never automated). Live-proven the same day (Bug
   #24/#25 ticket): fresh-identity first-login OIDC, exactly 2 roles/9 privileges, `currentProvider`
   non-null, canonical admission contract, `Manage Roles` negative control denied. See **BLOCKER-9 /
   NEEDS LIBAN #22** and `DECISIONS.md`'s **ADR-017F**. Along the way, **Known Bug #24** (the
   `createUser` first-login failure blocking this proof) was root-caused to this session's own
   invalid-TLD test email — corrected, not a platform defect — and a genuinely new, separate **Known
   Bug #25** (a first-login read-after-write reliability gap) was found. Bug #25 was subsequently
   root-caused and fixed in its own 2026-08-26 follow-up, recorded immediately below.
7. ✅ **Known Bug #25 — fixed and disposable-live-proven 2026-08-26.** Ten fresh valid-email
   clinicians reproduced the old behavior on their first callback (**10 failures / 10 attempts**).
   Instrumentation then confirmed the exact mechanism: `OAuth2UserInfoAuthenticationScheme`'s
   outer `REPEATABLE-READ` transaction opened its snapshot with the initial null username lookup;
   OpenMRS 2.8.7's synchronously-awaited `Daemon.createUser` committed and returned the authoritative
   User from a separate thread/session; oauth2login discarded it and reread through the old outer
   snapshot, still receiving null. A separate SQL connection and the next HTTP transaction already
   saw the row. The narrow patch now uses `ContextDAO.createUser`'s returned User directly. Focused
   Java tests pass 3/3 plus ADR-017E2's 10/10; the pristine-upstream negative control fails the two
   newly guarded properties. Two fresh clinicians each authenticated on the first callback, retained
   one Person/User/Provider and stable UUIDs over three total logins, and retained exactly the
   ADR-017F two roles/nine privileges. Fresh Reception, canonical admission, validation rollback,
   full 857 application tests, compose validation, static guards, and the real Docker build all
   passed. Facility SSO remained off; all writes were disposable. See the Known Bugs row and the
   2026-08-26 Bug #25 implementation record in `DECISIONS.md`.
8. 🟡 **UI-014 — Maternité board built and tested, awaiting Liban's review; NOT committed.** A
   read-only screen showing who is currently admitted (canonical `Daher Maternity Admission` @
   `Maternité` only), replacing the empty nav placeholder; clicking a patient opens the same
   `PatientWorkspace` used from search. No new privilege was needed — the existing clinician role
   already reads the query live; Reception's live `403` maps to the existing neutral `unavailable`
   state, not a broadened grant. ui tests 195 (was 148, +47). See its own ticket-status entry above
   for the full live-proof evidence, including a documentation-drift finding (UI-001A §13a.1
   overstates what the patient-search/visit-list representation actually carries — no `person`
   data at any depth tried) and Known Bug #26 below. **Per explicit instruction this ticket stops
   here — nothing was staged or committed; review the diff before deciding whether to land it.**
9. 🛑 **UI-015 — ending a maternity admission is blocked on BLOCKER-10 / NEEDS LIBAN #23, a new
   platform-limitation finding, not a decision this session could make.** Ending any OpenMRS Visit
   (the only real mechanism — `POST /visit/{uuid} {"stopDatetime":…}`; there is no dedicated
   discharge endpoint) unconditionally triggers every installed module's Visit-save cascade;
   live-reproduced that the bed-management and patient-queue modules each gate their own
   unconditional side effect behind privileges (`Assign Beds`+`Edit Admission Locations`,
   `Get Queue Entries`) the Daher clinician role does not have and was deliberately never given
   (ADR-017D). No UI/API/config code was written — see BLOCKER-10 for the four options and full
   live evidence. **This does not affect UI-013 or UI-014**, both unchanged and still working.
   **UI-015A (2026-08-27) added a full server-side stack-trace mechanism trace, proved `Edit
   Visits` is NOT the missing privilege, confirmed no narrower native mechanism exists, and found
   that a failed (403) end-Visit call can still leave `stopDatetime` durably persisted** — see
   BLOCKER-10's "UI-015A follow-up" subsection. **UI-015B (2026-08-27) enumerated the complete
   privilege cascade to termination: true minimum is 4 new privileges for the realistic case**
   (`Assign Beds`, `Edit Admission Locations`, `Get Queue Entries`, `Edit Visits` — live-confirmed
   with a clean success and correct post-state), characterized a non-authorization patch to the two
   upstream module hooks as a real alternative to broadening the clinician role. Still 🛑 Human
   Decision Required — see BLOCKER-10's "UI-015B follow-up" subsection. **CORRECTION — UI-015C
   (2026-08-27): the "patch two modules, keep 9 privileges" idea above is wrong.** `Edit Visits`
   (a third, independent gate) is hardcoded inside OpenMRS **core**
   (`VisitServiceImpl.saveVisit()`), not a module, and is unconditional/fully unscoped — patching
   bedmanagement+queue alone still leaves the clinician denied. UI-015C's own contribution is a new
   Option D: a narrow Daher-owned OpenMRS module endpoint using `Daemon` system-execution context
   so the browser session never needs any of the four/six privileges — recorded as `ADR-030`
   (OPEN, not selected, evaluated against ADR-027). See BLOCKER-10's "UI-015C follow-up".
   **Liban selected Option D (2026-08-27) and approved the narrow "Terminer l'admission" semantics.
   A build attempt followed, gated per requirement #29 ("stop rather than weaken the design") on
   live-proving atomicity and audit attribution before writing the full module — both failed.** A
   disposable spike module, built and deployed into the real disposable OpenMRS stack, proved: (a)
   `changedBy` does not survive a Daemon-context `saveVisit()` — it is overwritten with OpenMRS's
   own daemon-user UUID, confirming ADR-030's proposed `VisitAttributeType` audit fallback is
   required, not optional; (b) a genuine (non-privilege) mid-cascade failure still leaves partial
   writes committed even under Daemon context — live-reproduced a fourth time, this time worse: Visit
   ended, bed released, but the queue entry left corrupted (`sort_weight=NULL`) rather than closed or
   restored. Daemon removes the *privilege-throw* trigger UI-015A/B saw, but not the underlying cause
   (the module-hook cascade does not commit as one transaction, regardless of caller/thread). Full
   before/after evidence table, spike architecture, and analysis in BLOCKER-10's "UI-015/ADR-030
   implementation attempt" subsection. **BLOCKER-10 stays open; UI-015 has still not been started.**
   🛑 Human Decision Required: whether to pursue a module-owned-transaction alternative (a scope
   change from what was approved — the module would orchestrate Visit/bed/queue writes itself
   instead of delegating to `saveVisit()`'s hook chain), a different option, or reconsider the
   approach entirely.
   **UI-015D (2026-08-27/28) answers that exact question: yes, live-proven.** Root-caused why the
   first spike's state partially committed (source-level: core services' own
   `TransactionProxyFactoryBean` wiring runs `RequiredDataAdvice` — and so bedmanagement's/queue's
   own nested `@Transactional` calls — *before* the target service's own transaction opens; one
   Hibernate session shared across the whole "unit of work" lets an in-memory mutation get
   auto-flushed as a side effect of an unrelated module's commit). Built a second disposable spike: a
   module-owned service, wired with the *same* `TransactionProxyFactoryBean` pattern core/bedmanagement
   already use, called as the outermost entry point instead of calling `saveVisit()` directly — no
   DAO-bypass orchestration needed. Live rollback negative control on the exact bed+queue worst case:
   **zero partial state**, every table (including the induced-failure SQL itself) rolled back
   together. Live success control (with and without bed/queue) and idempotence both correct. Audit gap
   also solved live: a dedicated `VisitAttributeType` record, written in the same transaction, holds
   the true clinician's UUID on success and does not exist after rollback (native `changedBy` still
   fails, as anticipated — it was never meant to be the primary mechanism). **OPTION D: VIABLE** — see
   BLOCKER-10's "UI-015D follow-up" subsection for the full transaction-map proof and evidence tables.
   Not yet built to production standard (security boundary, true concurrency, the 25 specified tests,
   and the UI itself all remain unbuilt). 🛑 Human Decision Required: not whether Option D works — it
   does, with live evidence — but whether to proceed to the full UI-015 implementation on this
   revised, module-owned-transaction mechanism.

### Blocked, and on what

| Blocked on | Items |
|---|---|
| 🗓 **Birth-centre meeting** | Staff names for the SSO role map (types already decided, ADR-017) · clinical sign-off for DH-004/DH-006 · confirm ADR-018's cancellation default · the facility's **city** |
| ⏳ **Real domain + DNS** | DH-016 public certificates · the provisioning API's last `incomplete_step` |
| 🔒 **External** | Off-site bucket + key escrow (DH-014) · WhatsApp credentials (DH-013, ADR-003) |

### Do not

- **Do not activate SSO** until BLOCKER-5's staff names exist. Types are decided; names are not.
  Separately, BLOCKER-9 (ADR-017F) is now resolved and live-proven — the clinician identity is
  `Provider` + `Daher Clinician Admission`, `Organizational: Nurse` excluded. Known Bug #25's
  first-login reliability gap is now fixed and disposable-live-proven. Known Bug #23 (the
  OpenMRS client-role mapper leaking other clients' roles) is now fixed, automated-tested, and
  disposable-live-proven.
- **Do not fill the prenatal panel or partograph fields.** Both mechanisms ship **empty on purpose**
  and tests enforce it. `docs/CLINICAL-SIGNOFF.md` is the handover sheet.
- **Do not touch the Horiba imports.** ADR-016 deferred analyzers to v1.1 and did not authorize
  disabling or repairing shipped laboratory configuration.
- **Never `docker compose down -v`.** It destroys the volumes and every piece of evidence in this
  repo. Cold boot is ~50 minutes; warm is ~38 seconds.

### The pattern worth inheriting

This session found **eight** checks that passed for reasons unrelated to what they claimed — a
healthcheck green through a total outage, a guard that validated zero files, a control a dependency
silently overwrote, a TLS control that never reached TLS, string replacements that matched nothing
and reported success. Several were in tooling written the same hour.

So: **control-test every guard** — break it deliberately and watch it fail — and treat an empty or
suspiciously clean result as a finding, not a pass.

---

## ▶▶ NEEDS LIBAN — the decision list

Everything an agent could not decide alone, in one place, roughly in the order it starts costing
time. Nothing below is urgent for the local stack; several are hard gates before the pilot VM
exists. Each links to the detail rather than repeating it.

| # | Decision | Why it isn't mine | Blocks |
|---|---|---|---|
| ~~1~~ | ~~**The 50 fictional demo patients**~~ — ✅ **RESOLVED 2026-08-12.** Answered with "make it an option": `OMRS_DEMO_PATIENTS`, default `off`. The demo *concept* content becomes a second knob in DH-003, default as-shipped — no decision needed from you unless you want a different default | — | Nothing |
| ~~2~~ | ~~**Poll interval for the adapter**~~ — ✅ **RESOLVED 2026-08-12 by Liban:** 15 seconds, configurable by environment variable | — | DH-010 is unblocked |
| ~~3~~ | ~~**Does voiding an order need to reach the laboratory?**~~ — ✅ **RESOLVED TECHNICALLY 2026-08-13.** A proper `DISCONTINUE` creates a new visible ServiceRequest carrying `replaces`; the adapter must forward it and must not filter on `status` | — | Nothing; implementation constraint recorded in `WATERMARK-DESIGN.md` §6 |
| ~~4~~ | ~~**Revisit `labonfhir` (ADR-004)?**~~ — ✅ **RESOLVED 2026-08-12 by Liban:** continue the custom adapter for v1; revisit after the golden flow | — | DH-010 is unblocked |
| ~~5~~ | ~~**Keycloak session lifetimes**~~ — ✅ **RESOLVED 2026-08-12 by Liban:** 1h idle, 10h maximum, 5m access token (ADR-013) | — | DH-007 complete |
| ~~6~~ | ~~**OpenELIS default requester identity**~~ — ⏳ **INTERIM DECIDED 2026-08-14:** create one controlled test provider ("Pilot Test Clinician") replacing upstream's sample ("Felix Dr. Mauritius"). The real ordering clinician swaps in after the meeting — a one-line change, not a redesign. **Not yet created; tracked in DH-011.** | — | DH-011 |
| 7 | **Facility identity** — ✅ **PARTIALLY SET 2026-08-14:** country `Djibouti`, facility id `DJ-BC-001` (deliberate internal placeholder). ⏳ **Still needed: city**, plus state/district/postal code. ⚠️ **Now also blocks Option F and therefore the whole order path (DH-011B, 2026-08-17).** The exact question, narrowed so it can be asked of the laboratory directly: **what numeric site code should lab accession numbers carry?** OpenELIS is configured `acessionFormat = SITEYEARNUM`, whose validator formats the accession as **`%013d` — 13 digits, all numeric** — while the configured prefix is still upstream's alphabetic `DEV01`. So every order is refused with `Generated lab number is invalid`. A supplied accession does not work around it: the value is always server-generated. This is a laboratory identifier convention and is deliberately not being invented | The birth centre's real details are yours | `common.properties.template`, **and the DH-011 order path** |
| 8 | **Maternity bed counts** — ✅ **PLACEHOLDERS SET 2026-08-14:** 2 delivery / 4 postpartum, clearly marked. ⚠️ Additive-only means these CANNOT be removed later; set real numbers before the pilot deployment | Facility numbers | DH-003 unblocked |
| 9 | **Prenatal panel + partograph fields** — untouched, both need the clinical lead | Hard gate for *clinically live* (ADR-012) | DH-004, DH-006 |
| ~~10~~ | ~~**DH-011 first-sync boundary**~~ — ✅ **RESOLVED 2026-08-13 by Liban (ADR-015):** no historical backfill; the watermark starts at deployment time. The pilot is forward-going patients, not a paper-record digitisation | — | BLOCKER-3 leg 1 closed |
| ~~11~~ | ⏳ **OpenELIS requester identity** — **INTERIM DECIDED 2026-08-14** (controlled test provider; real name after the meeting). Was: 🗓  — provide the facility's real requester identity; the upstream sample clinician must not silently own pilot orders | Order attribution is clinical/audit data. The current upstream value is deliberately only a placeholder | DH-006, DH-011 |
| ~~12~~ | ~~**Cancellation contract**~~ — ✅ **DECIDED 2026-08-14 (ADR-018):** revoke if the lab has not accepted/started; leave in-progress work for manual handling. Interim policy, to be **validated against real workflow** at the meeting | — | BLOCKER-3 closed |
| ~~13~~ | ~~**Horiba Micros60/Pentra60 analyzer scope**~~ — ✅ **RESOLVED 2026-08-13 by Liban (ADR-016):** v1 is manual result entry only; no analyzer/HL7/ASTM interface. DH-023 deferred to v1.1. The shipped imports are left untouched — the decision does not authorize editing lab test rows | — | BLOCKER-4 closed |
| ~~14~~ | ~~**SSO authorization matrix**~~ — ✅ **ROLE TYPES DECIDED 2026-08-14 (ADR-017).** `clinician`→`Provider`+`Organizational: Nurse` (provider=yes); `lab-tech`→`Reception`,`Results`,`Validation`; `sys-admin`→`Organizational: System Administrator`+`Global Administrator`. ⏳ **Only the NAMES still need the meeting.** Was:  — map `clinician`, `lab-tech`, and `sys-admin` to exact OpenMRS and OpenELIS native roles, and decide which staff classes are OpenMRS providers | These mappings grant access to clinical records and laboratory functions and change clinical attribution. An agent may not infer them from role labels | DH-008 / BLOCKER-5 |
| 15 | ⏳ **Pilot domain and DNS** — *in progress 2026-08-13; Liban is registering it.* Provide/control the real `BASE_DOMAIN` and point `emr`, `lab`, and `id` records at the pilot VM | Public certificate issuance cannot be demonstrated against `localhost` or invented facility DNS | DH-016 |
| 16 | 🛑 **How an order enters OpenELIS** — BLOCKER-6, raised 2026-08-16. OpenELIS **pulls** orders from a remote FHIR source it is configured to trust; it does not accept a push into its own FHIR store, which is where the adapter was implicitly assumed to write. **Four** options with costs are in BLOCKER-6 (D added by Liban, none selected) | A removes the adapter's audit/ledger boundary that ADR-005 made load-bearing for patient safety; B turns the adapter into a FHIR server; **D adds a second system storing patient/order data**, changing backup, retention and security scope. Architecture and data-flow decisions, not implementation details | **DH-011** target leg, and therefore DH-012, DH-013, DH-018. **DH-011A** (poller + watermark) is deliberately carved out and proceeds |
| 17 | ✅ **Which staff role may register a patient?** — raised 2026-08-24 (UI-012), evidence completed 2026-08-24 (ADR-017A / UI-012A), **DECIDED AND IMPLEMENTED 2026-08-24 (ADR-017B): Option B, a dedicated `Daher Reception` role.** Committed: an OpenMRS role `Daher Reception` (`openmrs-config/initializer/roles/roles-daher.csv`, Initializer-provisioned) carrying exactly the three proven privileges, plus a Keycloak client role of the same name on the `openmrs` client (`keycloak/reconcile-sso.sh`, additive, idempotent). **Not yet ACTIVE**: no real staff assigned, `SSO_ENABLED` still `false` on the real facility — this ticket makes the role deployable, not live. Full runtime proof (fresh synthetic user, real OIDC login, exact registration contract, negative controls, bidirectional clinician-regression) in this ticket's own log entry below | Which real staff role registers patients was a facility workflow/staffing decision — now made and recorded (ADR-017B) | Real activation still needs SSO's public domain (#15) and the roster meeting (#14) for the actual name |
| 18 | ✅ **Should reception be allowed to perform admission (Visit + linked Encounter), or does admission stay clinician-only?** — raised 2026-08-24 (ADR-017C / UI-013A), evidence complete, **DECIDED AND IMPLEMENTED 2026-08-25 (ADR-017D): Option B, clinician-only.** Committed: a second, self-contained, Daher-owned OpenMRS role `Daher Clinician Admission` (nine privileges — `Get Patients` plus ADR-017A's eight-privilege `ADMISSION_INCREMENT`), Initializer-provisioned, no inheritance to or from `Daher Reception`; a matching Keycloak client role on `openmrs`, reconciled via the same idempotent mechanism ADR-017B proved. `Daher Reception` is untouched and remains exactly its three privileges. The accepted platform limitation (`Add Visits`/`Add Encounters` are blanket, unscopable) is recorded explicitly as the reason admission had to move to a separate, clinician-only role rather than being narrowed in place. Full runtime proof (real OIDC clinician login, live Visit+Encounter admission, reception negative controls, bed-assignment control, broad-scope control reconfirmed honestly) in this ticket's own log entry below | Whether a receptionist may complete admission unassisted was a facility risk-tolerance decision — now made and recorded (ADR-017D) | Real activation still needs the roster meeting (#14/#17, now also #18's clinician-admission assignment) and SSO's public domain (#15); UI-013 can now be built against a committed, proven authorization boundary |
| 20 | ✅ **Which OpenMRS Visit type represents a Daher maternity admission?** — raised 2026-08-25 (UI-013, BLOCKER-7), **DECIDED AND IMPLEMENTED 2026-08-25 (ADR-028): Option C, a dedicated Daher-owned Visit type, `Daher Maternity Admission`** (UUID `194bf546-68d7-42c0-8371-5b0f2b1cf5f3`), provisioned via Initializer's native `visittypes` CSV domain (mechanism confirmed by disassembling the pinned `initializer-2.12.0` module, not assumed). None of the five upstream generics (`Facility Visit`, `Home Visit`, `OPD Visit`, `Offline Visit`, `Group Session`) modified, renamed, retired, or deleted. Fresh-database, already-initialized-database, and idempotent-second-pass proof in BLOCKER-7's own closure entry | Naming a Visit type as "this is what a Daher maternity admission is" fixes clinical/audit semantics for every admission going forward — now made and recorded (ADR-028) | UI-013 may resume: the pre-write gate is cleared |
| 21 | ✅ **Should the upstream "Admission" Encounter type be pinned as reproducible Daher config, or is depending on upstream demo/reference-content seeding acceptable?** — raised 2026-08-25 (UI-013 live closure pass, BLOCKER-8), **DECIDED AND IMPLEMENTED 2026-08-26 (ADR-029): Option B**, pin the exact existing semantic (name `Admission`, UUID `e22e39fd-7db2-45e7-80f1-60fa0d5a4378`) via a committed `encountertypes-daher.csv`, using Initializer's native `encountertypes` domain (mechanism confirmed by disassembly, distinct in shape from `visittypes`). Existing-database compatibility gate fully proven twice (19 types unchanged, no duplicate, idempotent second pass); genuinely-fresh-database proof not achieved this pass due to a newly-encountered, disclosed environmental startup race unrelated to this config's correctness (see BLOCKER-8's own closure entry) | Committing upstream metadata under Daher's own Initializer config, to guarantee it on every fresh deployment, was a reproducibility/deployment-guarantee decision — now made and recorded (ADR-029) | DH-017's fresh-VM walkthrough — still needs a real fresh-boot demonstration once the environmental startup race is understood or a fresh host is available; not blocking UI-013's own code, which is complete |
| 23 | 🛑 **How should a clinician end an active Daher maternity admission, given that the standard OpenMRS end-Visit path requires bed-management (`Assign Beds`+`Edit Admission Locations`) and patient-queue (`Get Queue Entries`, possibly more) privileges the clinician role was deliberately never given?** — raised 2026-08-27 (UI-015, BLOCKER-10). Live-reproduced against the pinned stack: ending any Visit (`POST /visit/{uuid} {"stopDatetime":…}`) unconditionally triggers every installed module's `SaveHandler<Visit>`, and at least two independent modules (bedmanagement, queue) gate their own unconditional side effect behind privileges unrelated to Daher's own workflow — reproduced even for a patient with zero beds/queue entries ever assigned. Four options (A: grant the privileges found so far, cost/ceiling unknown; B: a custom backend path bypassing the save-handler pipeline; C: leave ending an admission out of pilot-ready V1, use the native OpenMRS/O3 UI or an administrator instead; D: enumerate every module's minimum requirement first, then decide) are in BLOCKER-10, none selected. No code was written — see BLOCKER-10 for the full mechanism, live evidence, and options | Granting facility-wide bed-assignment/admission-location-configuration authority (or building a privilege-bypass code path) to let a clinician end one Visit is a security-boundary and scope decision, not an implementation detail — the same class of call ADR-017D already made once for admission creation. **UI-015A follow-up (2026-08-27) sharpened the evidence**: `Edit Visits` was tested directly and confirmed NOT the missing privilege (identical failure with or without it — the real gate is two unrelated services, `BedManagementService` and `QueueEntryService`, neither referencing any Visit privilege at all); no narrower native mechanism exists (34-row privilege table enumerated, none type/location-scoped); and a genuinely new hazard was found — a failed (403) end-Visit attempt can still leave `stopDatetime` durably persisted server-side, so any future implementation must always re-fetch authoritative state rather than trust the HTTP response. **UI-015B (2026-08-27) enumerated the complete cascade to termination**: the true minimum grant for the realistic (no-bed, no-queue) case is exactly 4 privileges — `Assign Beds`, `Edit Admission Locations`, `Get Queue Entries`, `Edit Visits` (13 total, live-confirmed with a clean `200` and correct post-state) — with `Edit Visits` turning out to be a genuinely separate, hardcoded in-body check in `VisitServiceImpl.saveVisit()` distinct from the interface's own OR-privilege annotation (reconciling, not contradicting, UI-015A's own "Edit Visits alone doesn't help" finding — it's required, just not first). A rare edge case (an actual queue entry present — never true for any real Daher visit) needs 2 more (`Manage Queue Entries`, `Get Concepts`), confirmed to terminate there. `Assign Beds`/`Edit Admission Locations` proven structurally unscopable (by method signature and exhaustive privilege enumeration); a live bed-assigned control showed the automatic release works correctly if ever granted. A non-authorization patch to the two upstream module hooks (bedmanagement, queue) was characterized as technically feasible — same maintenance-cost class as the existing `oauth2login` patch this repo already carries — as an alternative to broadening the clinician role at all. Full cascade table in BLOCKER-10's "UI-015B follow-up" subsection. **CORRECTION — UI-015C
(2026-08-27): the "patch the two module hooks, keep 9 privileges" framing above is wrong.**
`Edit Visits` (gate 3) is enforced by a hardcoded check inside OpenMRS **core**
(`VisitServiceImpl.saveVisit():176`), unconditional on any update of any already-persisted Visit —
patching `bedmanagement`+`queue` alone still leaves the canonical clinician denied. There are three
independent gates, not two, and `Edit Visits` is proven (by this exact bytecode) to be fully
global/unscoped — no per-visit-type or per-location variant exists anywhere. UI-015C's primary
contribution is a fourth option: a narrow, Daher-owned OpenMRS module endpoint (Option D, now
`ADR-030`, OPEN/not-selected) that authenticates the real clinician's own session but performs the
actual mutation under OpenMRS's own `Daemon` system-execution context — never granting the browser
session any of the four/six privileges — with a durable `VisitAttribute`-based audit record and a
transaction boundary that would close UI-015B's own non-atomic-write hazard by construction. Full
corrected option comparison (A/B/C/D) in BLOCKER-10's "UI-015C follow-up" subsection; ADR-030
records the ADR-027 trust-boundary question this option raises, itself not yet decided.
**Liban selected Option D (2026-08-27) and approved the narrow semantics. A build attempt followed
and falsified the specific mechanism ADR-030 proposed**: a disposable spike module, live-deployed
into the real disposable OpenMRS stack, proved `changedBy` does not survive a Daemon-context
`saveVisit()` (overwritten with OpenMRS's own daemon-user UUID — the `VisitAttribute` audit fallback
is required, not optional), and proved the claimed "transaction boundary... by construction" above
does not exist: an induced genuine failure left the Visit ended and the bed released but the queue
entry corrupted, live-reproduced a fourth time. Daemon removes UI-015A/B's specific privilege-throw
trigger but not the underlying non-atomicity. Full evidence, before/after table, and the resulting
question (pursue a module-owned-transaction alternative — a scope change from what was approved — or
reconsider) in BLOCKER-10's "UI-015/ADR-030 implementation attempt" subsection. Still 🛑 Human
Decision Required; BLOCKER-10 stays open.
**UI-015D (2026-08-27/28): the module-owned-transaction alternative is live-proven to work.**
Root-caused the exact mechanism (source-level, `applicationContext-service.xml`'s
`TransactionProxyFactoryBean.preInterceptors` wiring + `RequiredDataAdvice`'s SaveHandler-dispatch
order + OpenMRS's one-Hibernate-session-per-"unit of work" model) for why the first spike's state
partially committed, then built a second disposable spike: a module-owned Spring-proxied service,
wired identically to how core/bedmanagement/queue already wire their own services, called as the
*outermost* entry point instead of calling `saveVisit()` directly. Live rollback negative control
(same bed+queue worst case, same induced failure): **zero partial state** — Visit, bed, bed
assignment, and even the deliberate-corruption SQL itself all rolled back together. Live success
control (bed+queue present, and separately no-bed/no-queue): correct end-to-end result, Encounter
untouched. Live idempotence: a second call on an already-ended Visit is a zero-write no-op. Audit
gap also solved the same way: a dedicated, non-clinical `VisitAttributeType` record, written in the
same transaction, correctly holds the true initiating clinician's UUID on success and correctly does
not exist after a rollback — native `Visit.changedBy` still shows the daemon user (unchanged), but
that was always the anticipated fallback position, not the primary mechanism. Full evidence,
transaction-map table, and the resulting **OPTION D: VIABLE** determination in BLOCKER-10's
"UI-015D follow-up" subsection. Not yet built to production standard (security boundary, true
concurrency, the 25 specified tests, and the UI itself all remain). 🛑 Human Decision Required: not
whether Option D is viable — it now is, with live evidence — but whether to proceed to the full
UI-015 implementation on this revised (module-owned-transaction) mechanism | The "Terminer l'admission" action on the Maternité board / patient workspace. Does not block UI-013 (admission creation) or UI-014 (read-only board), both unaffected |
| 22 | ✅ **Should the real clinician identity keep `Organizational: Nurse`, or narrow to `Provider` + `Daher Clinician Admission`?** — raised 2026-08-26 (ADR-017F, BLOCKER-9), **DECIDED 2026-08-26: Option A**, narrow to `Provider` + `Daher Clinician Admission`. `Organizational: Nurse` excluded going forward; no broader nurse permissions implemented. Measured: it closed over 302 of 345 privileges (87.5%), including `Delete Patients`, `Delete Users`, `Manage Roles`, `Manage Global Properties`, full `Order` CRUD; all 9 of ADR-017D's approved admission privileges were a strict subset of it; `Provider` itself carries 0 privileges. **Live-proven 2026-08-26** (Bug #24/#25 ticket): fresh-identity first-login OIDC, exactly the 2 intended roles/9 privileges, `currentProvider` non-null, canonical admission contract, `Manage Roles` negative control denied — full detail in ADR-017F's own live-closure-proof section. No committed config file changed (the old mapping was never automated either) | Narrowing the clinician identity was a scope-of-practice decision — now made and recorded (ADR-017F, Option A) | Real SSO activation still needs the roster meeting (#14/#17/#18) and SSO's public domain (#15); Bug #25's separate first-login reliability defect is now fixed and disposable-live-proven; Bug #23 (client-role mapper cross-client leakage) is now also fixed and disposable-live-proven, 2026-08-27 |

### 🗓 Waiting on the birth-centre meeting — as of 2026-08-15

**Much shorter than it was.** On 2026-08-14 four of these turned out to be policy calls rather than
facts, and were decided (ADR-017, ADR-018, ADR-019, plus marked placeholders). What is left needs
the room:

| Needs | Decisions | Tickets held | What unblocks the moment it is answered |
|---|---|---|---|
| **Staff names only** | #14/#17 — role *types* already decided (ADR-017, ADR-017B) | **DH-008** activation, UI-012's real activation | Who holds `clinician` / `lab-tech` / `sys-admin` / `Daher Reception`. Every mapping itself is done |
| **Clinical sign-off** | #9 prenatal panel + partograph fields | **DH-004**, **DH-006** | Mechanisms are built and ship empty; the lead's answer fills them. Gates *clinically live* (ADR-012), not pilot-ready |
| **Workflow validation** | Confirm ADR-018's cancellation default matches practice | — | Nothing is held; it is already the interim policy. Confirm or supersede |
| **The city** | Facility identity #7 | — | Country and facility id are set; city/state/district/postal are still blank because nobody has supplied them |

**Downstream of those, by dependency only:** DH-012 (needs DH-011), DH-013 (needs DH-012),
DH-018 (needs DH-013 + DH-015). So the meeting sits in front of five of the eight golden-flow steps.

**Separately in progress, not meeting-blocked:** #15 domain/DNS — Liban is registering it; DH-016
stays blocked for public verification until the records exist.

**Not waiting on the meeting:** DH-015 (performed), DH-016 (built; public certs need #15),
DH-017 (brought current; needs a fresh-VM walk-through), DH-021 (done), healthcheck bug 15 (done).
The remaining agent-buildable surface is now small — most of what is left needs the meeting or the
domain.

**ADR-005 review completed 2026-08-12.** Liban approved the custom adapter and 15-second poll
interval after the evidence and watermark design were presented. OpenELIS outbound delivery and
corrected-result behavior remain empirical gates, not assumptions.

**Two security items I fixed without asking** (disabling exposure needs no permission; restoring it
would): upstream's `mail.bcc`/`mail.from` default of a third-party gmail address is blanked, and
every published default credential found so far is now actively refused — OpenMRS admin (DH-019),
the OpenELIS DB roles, and OpenELIS admin via DH-024's verified post-bootstrap bcrypt gate.

---

## Blockers

### BLOCKER-1 — Docker daemon unreachable · ✅ RESOLVED 2026-08-10

**Ticket:** DH-002
**Was:** `libanbritt` was not in the `docker` group; every docker command failed with
`permission denied` on `/var/run/docker.sock`.
**Response recorded:** Liban ran `sudo usermod -aG docker $USER`. Verified in `/etc/group`
(`docker:x:973:libanbritt`). Group membership attaches at login, so it required a logout/reboot
and a Claude Code restart to take effect — done 2026-08-10.
**Note for the pilot VM:** the same choice recurs there with higher stakes. `docker` group
membership is effectively root on the host. Decide it deliberately, not by habit.

### BLOCKER-2 — Demo data · ✅ RESOLVED BY DECISION 2026-08-12 — made a switch, not a choice

**Liban's response, recorded verbatim in effect:** *"When spinning up different instances, give me
the option to be able to include demo data or not."* He rejected the either/or I presented and asked
for a knob — consistent with ADR-007's treatment of bed counts.

**Built:** `OMRS_DEMO_PATIENTS` (default `off`) selects a late, single-property Initializer overlay
at the **distribution source**, because the entrypoint re-copies the live config tree on every
boot. DH-003 replaced the earlier whole-file overlay without changing its behavior. An isolated
empty-volume boot with this set to `off` produced zero patients.

**Split into two knobs deliberately and completed at DH-003.** `OMRS_DEMO_CONTENT` defaults to
`on` (upstream as shipped), because the demo tree includes `BasicLabTests` vocabulary DH-006 may
need. `off` selects a local backend build target that removes only the distro's
`referenceapplication-demo` source directories. The exact pinned upstream image remains the base,
and the final container still runs as UID 1001. The empty-volume `off` path copied zero demo
directories and reached healthy. Neither switch deletes rows from an existing database.

**Below is the original entry, kept for the reasoning.** ⚠️ Its framing as a decision to be made is
superseded; both switches now exist, while clinical approval of vocabulary remains separate.

<details>
<summary>Original BLOCKER-2 (superseded)</summary>

**Ticket:** DH-003 (surfaced during DH-002; does **not** block DH-002)
**Raised:** 2026-08-10

**Observed, not inferred.** Booting the stock backend image, the container's Initializer
configuration tree contains two OCL package directories:

- `/openmrs/data/configuration/ocl/referenceapplication/` — 2 packages
  (`CIELImmunizationContent`, `PD`)
- `/openmrs/data/configuration/ocl/referenceapplication-**demo**/` — 21 packages, ~6 MB zipped,
  including `BasicLabTests`, `DiagnosesStarterKit` (3.7 MB), `BasicDrugs`, `CIELAllergySet`,
  `procedures`, and also `DemoPrograms`, `DemoQueueConcepts`, `GenericDemoForm`, `IPD-DEMO`

All of them import on every boot (`openconceptlab_import` rows, one per package). Alongside them,
`configuration/concepts/referenceapplication-demo/*.csv` loads a further ~700 concept rows.

**Why this is a human decision and not an agent's.** This is *concept dictionary content* — the
medical terminology the EMR offers clinicians. Three things are tangled together and only a human
should separate them:

1. Most of it is legitimate unmodified reference content (CIEL-derived lab tests, diagnoses,
   drugs) — exactly what the non-negotiable "reference defaults only" rule *wants*.
2. Some of it is explicitly demo scaffolding (`GenericDemoForm`, `DemoQueueConcepts`, `IPD-DEMO`).
3. It arrives from a directory named `-demo`, so "is this facility-appropriate clinical content or
   is it sample data?" cannot be answered from the directory name alone.

Stripping the `-demo` tree would be the "obviously right" tidy-up — and that instinct is precisely
what §5 of the sprint spec exists to stop. Removing it may also remove the lab test concepts that
DH-006's prenatal panel needs to map to. **Not touched.**

### ⚠️ It is NOT metadata only — the distro creates 50 fictional PATIENTS

An earlier note in this entry said no demo patients were created. **That was wrong**, and the
correction matters more than the original entry. The count was taken mid-boot; the demo patients
are created in the last seconds before the app answers.

**Observed after the boot completed:** `SELECT COUNT(*) FROM patient` = 51 — the one deliberate
test patient plus **50 fictional people** (Betty Williams `100000Y`, Michael Turner `100006J`,
Susan Lopez `100001W`, …), all `date_created 2026-08-11 10:24:46`.

**Mechanism, located exactly:**

- The image bundles `referencedemodata-2.6.1.omod` in `/openmrs/data/modules/`.
- It is driven by the OpenMRS **global property**
  `referencedemodata.createDemoPatientsOnNextStartup` — now `0`, having fired once on this boot.
- **`OMRS_CONFIG_ADD_DEMO_DATA=false` did not prevent this**, consistent with that variable being
  inert (see the compose comment). The real control is a global property this repo does not set,
  in a module this repo did not choose to install.

This is the exact hazard `.env.example` warned about in words — fictional people in the same
tables as real ones — reached through a mechanism nobody had identified. It is *not* a reason to
panic: this is a local throwaway verification stack. It **is** a hard gate on the pilot instance.

**Not acted on, deliberately.** Deleting patient rows and disabling a bundled module are both
outside DH-002, and one of them touches patient tables. Setting the global property belongs in
DH-003's Initializer configuration, where it applies from first boot — which is the only moment
that helps, since the property self-clears after firing.

**Both original questions are now answered:** (2) is built and verified as `OMRS_DEMO_PATIENTS`;
(1) becomes a second knob in DH-003 rather than a decision.

</details>

_No blockers open against DH-002._

### BLOCKER-3 — DH-011 activation and cancellation contract · ✅ CLOSED BY DECISION 2026-08-14

**Ticket:** DH-011

**Leg 1 — first-sync boundary · ✅ RESOLVED 2026-08-13 by Liban (ADR-015).** No historical backfill;
the watermark starts at deployment time, read from the OpenMRS server clock and persisted once.
Accepted consequence: an order placed before deployment never reaches OpenELIS, including one still
active, and no alert marks its absence. Nightly reconciliation must count only from the bootstrap
watermark or it reports a permanent false mismatch. **Two legs below remain open.**

**Problem:** the target contract has two remaining human gates. OpenELIS requires an attributable requester,
while this deployment still carries upstream's sample clinician placeholder. For discontinuation,
ADR-005 proves the adapter will see a new `ServiceRequest.replaces`, but it does not decide the
laboratory-side state transition after the original order has been received.

**Primary evidence checked 2026-08-13:** deployed OpenELIS 3.2.1.11 bytecode and its exact source
tag (`e2a6bdf7`) show the incoming workflow is Task/ServiceRequest based and resolves requester and
test mappings. The official Ozone `eip-openelis-openmrs` source inspected at `7f994594` labels panel
support and discontinuation unsupported. Its current cancellation path deletes only a still-
`REQUESTED` Task/ServiceRequest and carries a TODO that it should revoke rather than delete. That
is evidence of an unresolved upstream contract, not permission to choose one here.

**All three legs are now decided:**

1. **First sync** — ADR-015: no historical backfill; watermark starts at deployment time.
2. **Requester** — interim decision 2026-08-14: a controlled test provider ("Pilot Test Clinician")
   replaces upstream's sample for testing; the real ordering clinician swaps in after the meeting.
3. **Cancellation** — ADR-018: revoke if the laboratory has not accepted or started the test; leave
   in-progress work alone for manual handling. Interim policy, to be validated against real
   workflow at the meeting.

**DH-011 is therefore no longer blocked on decisions.** What remains is implementation plus two
verification gates: DH-025's correlation must be attached to the concrete OpenELIS resource, and
ADR-018's "accepted or started" must map to a *verified* OpenELIS status rather than an assumed
one. No poller or clinical order has been activated.

### BLOCKER-4 — OpenELIS Horiba Micros60/Pentra60 analyzer imports · ✅ CLOSED BY SCOPE 2026-08-13 (ADR-016)

**Ticket:** DH-023 — deferred to v1.1, not blocked.

**Was:** on every observed OpenELIS webapp boot, both `horiba-micros60-cbc.csv` and
`horiba-pentra60-cbc.csv` attempt to update different test rows to the already-existing unique
description `LYM#(Whole Blood)`. PostgreSQL rejects each update on `test_desc_uk`; each transaction
aborts and OpenELIS logs failed test-editor bridging for subsequent rows.

**Resolved by decision, not by repair.** Liban decided v1 is manual result entry only — lab techs
type results into OpenELIS as they currently write them on paper — with analyzer/HL7/ASTM
integration deferred to v1.1 or later. The scope question that made this a blocker is answered.

**What stays true:** each failed transaction rolls back, the live table has zero duplicate
descriptions, and the web/FHIR endpoints answer. The imports are left untouched — ADR-016 does not
authorize disabling, renaming, repairing, or log-suppressing shipped laboratory test configuration.
The errors remain a known non-blocking boot behaviour. No test, mapping, or analyzer configuration
has ever been changed.

### BLOCKER-5 — SSO native authorization mapping · ✅ ROLE TYPES DECIDED 2026-08-14 (ADR-017) · ⏳ NAMES PENDING

**Ticket:** DH-008

**Problem:** Keycloak currently has high-level realm roles `clinician`, `lab-tech`, and
`sys-admin`, but the applications do not authorize those labels. OpenMRS OAuth2 Login consumes
exact OpenMRS role names and separately decides whether to create a Provider. OpenELIS SAML
consumes exact OpenELIS role names from its case-sensitive `Role` assertion attribute. Mapping a
staff category to either set grants access to patient records or laboratory functions and affects
clinical attribution.

**Decision needed:** provide an explicit matrix for each realm role:

1. exact OpenMRS native role name(s), if any;
2. whether members should be created as OpenMRS providers; and
3. exact OpenELIS native role name(s), if any.

No role equivalence was inferred. Until the matrix is approved, the Keycloak `provider` claim is
hard-coded false, SSO defaults disabled, both local login paths remain available, and the one-login
acceptance test is deliberately unperformed.

### BLOCKER-6 — how an order actually enters OpenELIS · ✅ RESOLVED BY DECISION 2026-08-28 (architecture + correlation direction — DH-011 itself is NOT complete, see below) · was 🛑 OPEN 2026-08-16

**Ticket:** DH-011 (order path). Found by probing the running stack before writing transport code.

**Problem.** Every document in this repo says "the adapter routes the order to OpenELIS" without
saying *through which ingestion mechanism*, and the evidence now says the obvious one is not it.

**Evidence — read-only, gathered 2026-08-16:**

1. OpenELIS's FHIR store is **completely empty**: `Task`, `ServiceRequest`, `DiagnosticReport`,
   `Patient` and `Practitioner` all return `total: 0`. So does `clinlims`: `electronic_order`,
   `sample` and `analysis` are all **0 rows**. The laboratory has never received an order.
2. `org.openelisglobal.fhirstore.uri` points at **OpenELIS's own HAPI container**. Per
   `org.openelisglobal.fhir.subscriber.resources`, that store is where OpenELIS *publishes* its own
   resources. It is not documented as an inbox.
3. The inbound path in the rendered `common.properties` is a **poll, not a push**:
   `org.openelisglobal.remote.source.uri` (currently **empty**),
   `org.openelisglobal.remote.poll.frequency=120000`, and
   `org.openelisglobal.remote.source.identifier=Practitioner/*`. OpenELIS **pulls** orders from a
   remote FHIR source it is configured to trust, every two minutes.

**Why this is not mine to decide.** It changes what the adapter *is*, and it changes what patient
data flows where — both on the always-ask list:

| Option | What it means | Cost |
|---|---|---|
| **A. Point `remote.source.uri` at OpenMRS** | OpenELIS pulls orders straight from the EMR | Simplest, and it makes the adapter's order leg largely redundant — which contradicts the accepted architecture and removes the ledger/audit boundary that ADR-005 made load-bearing for patient safety |
| **B. The adapter exposes a FHIR endpoint OpenELIS polls** | Adapter pulls from OpenMRS, serves to OpenELIS | Preserves the adapter as the routing and audit boundary, but makes it a FHIR **server**, not the client the design assumes. Substantially more surface than DH-010/DH-025 were built for |
| **C. The adapter writes into OpenELIS's own fhirstore** | Adapter pushes, hoping OpenELIS ingests | ⚠️ **Unverified and possibly not a real path.** The store is empty, `transformOnStartup=false`, and nothing in the config describes it as an inbox. Would need proving before it could be trusted |
| **D. A dedicated order-exchange FHIR store** *(added by Liban 2026-08-16; **not** selected)* | The adapter writes orders to a separate exchange store; OpenELIS polls **that** store via `remote.source.uri`. Keeps the adapter as the routing and audit boundary without making it a FHIR server (B), and uses the pull mechanism OpenELIS actually documents | ⚠️ **Human Decision Required: Yes — and the widest scope of the four.** It adds another system that **stores patient and order data**, so it lands inside every boundary this project has already drawn: DH-014/DH-015 backup and restore coverage, ADR-021's "no clinical data outside the stack" reasoning, the tenant-isolation model, retention, and the security surface of one more network-reachable store holding clinical records. None of those are implementation details, and a new clinical datastore is not a thing an agent may adopt because it is architecturally tidy |

| **F. OpenELIS's own business FHIR providers** *(added 2026-08-16, ADR-026; **spiked, still not selected**)* | The adapter posts to the FHIR providers inside the OpenELIS **webapp** — a different layer from option C's generic HAPI store. Keeps the adapter as the ledger/audit boundary without making it a FHIR server (B) or adding a clinical datastore (D) | ✅ **Fully spiked 2026-08-17 (DH-011B) and it is the strongest candidate: the providers write OpenELIS's own domain tables — `clinlims.patient` and `clinlims.provider` rows were created through the supported FHIR surface, and `ServiceRequestProvider` calls the same `SamplePatientEntryService` the human workflow uses.** Two gates, neither an engineering choice: (1) **ServiceRequest is refused** — `SITEYEARNUM` formats accessions as `%013d`, 13 all-numeric digits, while the configured prefix is `DEV01`; a **numeric facility site code** is required (#7), and a supplied accession is ignored because the value is always server-generated. (2) **The submitted correlation is not retained** — our identifier is discarded (`external_id` NULL) and replaced by OpenELIS's own. ✅ *Corrected 2026-08-17:* exact identifier **search works** (a real identifier returns 1, a wrong system with the right value returns 0), so only *persistence* of the incoming identifier is missing. → **DH-011D**, narrower than first recorded |

**Deliberately not narrowed.** Options A–D are retained exactly as first recorded; F is appended
rather than replacing them, so the reasoning history stays auditable (ADR-026).

**Also settled by whichever option is chosen:** `remote.source.identifier=Practitioner/*` is the
filter deciding which orders OpenELIS considers its own — so the requester identity (NEEDS LIBAN #6,
interim "Pilot Test Clinician") stops being an attribution detail and becomes the routing key.

**Deliberately not done:** no probe Task was written into OpenELIS. Writing into the laboratory to
discover whether it ingests would give an ambiguous answer either way — nothing visible could mean
"not wired" or "wired but unconfigured" — and it is a write into a clinical system to satisfy a
guess. The read-only evidence above is enough to state the fork; it is not enough to pick a branch.

**Consequence for DH-011:** the OpenMRS-side poller and watermark are still buildable and are
unaffected by this choice. The OpenELIS write, DH-025's correlation binding, and ADR-018's status
mapping are all downstream of it.

### RESOLVED 2026-08-28 (DH-011D ticket) — Option F selected, correlation direction decided

**Liban selected Option F** — the adapter posts to OpenELIS's embedded business FHIR API
(`/OpenELIS-Global/fhir/*`, HAPI 7.0.2), never the standalone `openelis-fhir` store (Option C's
target), never a third exchange datastore (D), never making the adapter a FHIR server (B), never
pointing OpenELIS directly at OpenMRS (A). Full architecture and reasoning recorded in `DECISIONS.md`
ADR-026's 2026-08-28 appendix. **Correlation direction also decided**: a `ServiceRequest`-owned
external order identifier, not an overload of `Specimen`/accession identifiers — the order's own
identity, independent of laboratory accession configuration.

**This resolves the architecture question. It does not close DH-011.** What this same pass actually
built, live-verified, and deliberately did not build:

**Built and live-verified:**
- `openelis-config/patches/order-correlation/ServiceRequestProvider.java.patch` — adds exactly one
  `@OptionalParam(name = ServiceRequest.SP_IDENTIFIER)` parameter to
  `ServiceRequestProvider.searchForServiceRequests`, mirroring `PatientProvider`'s existing pattern.
  Root cause, established from the exact pinned source
  (`e2a6bdf7999fa3754e7cdafbd2a91259c3f0ce23`, tag `3.2.1.11`, matching both the compose pin and the
  running container's own `build.properties`): the method's entire body is
  `util.forwardSearchToFhirStore(request)`, which forwards the raw query string to
  `fhirConfig.getLocalFhirStorePath()` (the same target `FhirPersistanceServiceImpl`'s write path
  uses) — the declared `@OptionalParam`s exist only so HAPI's REST layer accepts the parameter at
  all. `identifier` was simply never declared for this one provider. `openelis-config/Dockerfile`
  builds this patch against the exact pinned commit (new stage,
  `openelis-order-correlation-build`, mirroring `openmrs-config/Dockerfile`'s
  `oauth2login-build`/`daherendadmission-build` pattern). **Live proof, disposable container sharing
  the real stack's DB/keystore volumes (no write, no restart of the real service)**:
  `ServiceRequest?identifier=` went `400`→`200`/`total:0`; an unrelated unknown parameter stayed
  `400` on the patched image (scoping control); wrong Basic-auth credentials stayed `401` (auth
  control unweakened). Full account: `openelis-config/patches/order-correlation/PROVENANCE.md`.
- `adapter/src/daher_adapter/openelis.py` — the Option-F delivery client. HTTP Basic auth only
  against the business API (never the standalone store); `identifier_search` with exact
  `system|value` token matching; `create_patient`/`create_practitioner` with mandatory read-back
  verification (never trusts the POST's own 2xx — `PatientProvider`'s known false-success defect,
  "known bug 19," is reproduced and defended against in tests); `service_request_probe` bound
  directly to `recovery.py`'s `TargetProbe` protocol (0/1/2+/unreachable → ABSENT/FOUND/AMBIGUOUS/
  AMBIGUOUS, never a false ABSENT); `deliver_service_request` deliberately raises
  `ServiceRequestDeliveryBlocked` rather than attempting a POST OpenELIS's own accession gate would
  refuse anyway.
- `adapter/src/daher_adapter/config.py` — `Settings.openelis_site_code` (optional, `None` until the
  real value exists) and `Settings.service_request_delivery_ready`. Rejects the known upstream
  placeholder `DEV01` and any non-numeric value; does **not** enforce a specific digit count for the
  site code itself, because DH-011B's constant-pool read established only the *overall* generated
  accession's width (13 digits), not how that splits between site/year/sequence — asserting an
  unverified split would be inventing facility semantics.
- 47 new adapter tests (`test_openelis.py` 19, `test_openelis_recovery_binding.py` 4,
  `test_openelis_activation_gate.py` 5, plus existing-file additions) — all passing; full suite
  **122/122**, `ruff check` and `ruff format --check` both clean.

**Designed from source, with exact file/line/method anchors, but NOT implemented or built** — the
identifier-persistence-on-create half. Cannot be built this pass because it cannot be exercised:
every `ServiceRequest` create is refused before this code runs
(`requireNonBlank(sampleOrder.getLabNo(), "Generated lab number is invalid")`), and will keep being
refused until the real numeric site code exists. The design:
`FhirTransformServiceImpl.buildSampleOrderItemFromServiceRequest` never reads
`serviceRequest.getIdentifier()` today; it would need to extract the Daher-system identifier and call
the already-existing `SampleOrderItem.setExternalOrderNumber(...)`.
`SamplePatientUpdateData.setElectronicOrderIfNeeded` already looks up an existing `ElectronicOrder`
by external id but never creates one when absent — it would need to, mirroring `TaskWorker
.insertNewOrder`'s existing pattern (`ElectronicOrderType.FHIR` is already a recognized enum value,
anticipated but unused on this path). The native entity already exists —
`clinlims.electronic_order.external_id`, `VARCHAR(60)`, already indexed
(`e_order_external_id_idx`) — **no schema migration is required to store the value.**
`FhirTransformServiceImpl.transformToServiceRequest` (outbound) would need to echo that value back
as a third `ServiceRequest.identifier` so the resource synced to the searchable store actually
carries it. Full account, including why the DB lacks a uniqueness guarantee today
(`ElectronicOrderDAOImpl.getElectronicOrdersByExternalId` returns a list; existing callers
deliberately take the last row, tolerating duplicates) and the recommended fix (a partial unique
index scoped to `type = 'FHIR'`, not yet added because it cannot be control-tested without a
creatable order): `openelis-config/patches/order-correlation/PROVENANCE.md`.

**Not done, explicitly out of scope this pass:** the numeric site code (NEEDS LIBAN #7, not
invented); patient identity mapping's identifier-system decision (DH-011G, human decision, not
resolved); requester approval beyond the interim "Pilot Test Clinician" (DH-011H, NEEDS LIBAN #6);
test mapping population (DH-011I, blocked on DH-006 clinical sign-off); the production polling loop
remains unwired (DH-011E structurally idle, no `POLL`/`ENABLE` variable exists); no commit or push.

### RESOLVED 2026-08-28 (DH-011D2 closure pass) — persistence and uniqueness built, compiled, and
proven at every boundary that does not require a live `ServiceRequest`

**The paragraph above ("NOT implemented or built") is now out of date for the mechanisms
themselves — only the live end-to-end trigger (a real `ServiceRequest` create) remains blocked.**
This follow-on pass built:

- **`ServiceRequestProvider.createServiceRequest`** now extracts and validates the Daher order
  correlation (exact-one-match on `DaherOrderCorrelation.SYSTEM`, exact system+value comparison, a
  64-character-hex-digest format check — zero matches, 2+ matches, and malformed values all rejected
  before any DB work) and persists it into a dedicated `FHIR`-typed `electronic_order` row, linked to
  the sample via `clinicalOrderId` only (never `referringId`, which would collide with existing
  `basedOn`/`intent` logic — see PROVENANCE.md for the exact mechanism).
- **`FhirTransformServiceImpl.transformToServiceRequest`** echoes the persisted correlation back
  onto the outbound-synced resource, gated on the linked order actually being `FHIR`-typed, so the
  already-patched search half can find what this half persists.
- **A new Liquibase changeset** (`liquibase/daher/001-electronic-order-daher-correlation-unique.xml`)
  adds a genuine PostgreSQL partial unique index — `electronic_order(external_id) WHERE
  type='FHIR'` — leaving the existing HL7/CSV duplicate tolerance for other rows untouched.
- **A new shared constants file** (`DaherOrderCorrelation.java`) so the persist and echo sides cannot
  drift onto different system URIs.
- **Two new CI static guards** (`.github/workflows/ci.yml`'s `static` job): the existing
  "no `--` inside an XML comment" guard widened to cover `openelis-config/` (this pass hit that exact
  bug live, in its own first draft of the new changeset); a new dedicated guard asserting every
  required component of the patch set exists, is wired together, contains no fuzzy-comparison
  pattern, and keeps the uniqueness index scoped to `type='FHIR'` — control-tested against two
  deliberate mutations (stripping the scope, injecting a `.toLowerCase()`/`.contains()` pattern),
  both caught correctly.

**Live proof, entirely on disposable/isolated infrastructure, real stack never touched:** the full
patched image (search + persistence + uniqueness) builds and compiles cleanly from the exact pinned
source. Because this build now includes a real Liquibase migration, further live proof moved off the
real stack's shared database (which auto-runs Liquibase on boot) onto a fully isolated three-container
disposable stack (fresh `openelis-db`, `openelis-fhir`, and the patched webapp, private network).
There: the migration applied with zero errors on a genuinely fresh database (confirmed via `\d
electronic_order` — the partial unique index exists exactly as designed); the search proof (400→200,
scoping control, wrong-system-zero-match control) repeated identically; and a **real PostgreSQL
concurrency race** — two genuinely simultaneous transactions inserting the same `external_id` with
`type='FHIR'` — produced exactly the required result: one `COMMIT`, one constraint-violation
rejection, exactly one row persisted for the contested correlation. A parallel control with
`type='HL7_V2'` confirmed the existing duplicate-tolerant behavior is untouched (both rows accepted).
Full account, including precisely what this does *not* prove (the full Java service-layer race
through two real HTTP creates, and live search cardinality against a real `ServiceRequest` — both
need a creatable order): `openelis-config/patches/order-correlation/PROVENANCE.md`.

**Adapter side**: `test_openelis.py` extended with explicit timeout, 401, and 403 controls (all
→ AMBIGUOUS, never a false ABSENT) and a duplicate-create-conflict reconciliation test (a 500-status
create response still resolves correctly via read-back, never a blind retry) — the read-back-only
verification pattern already in place handled this case without needing new production code, the
new test just proves it explicitly. **Adapter test count: 122 → 126, all passing**, `ruff check` and
`ruff format --check` both clean.

**What still cannot be proven without the site code, unchanged**: end-to-end create→persist→search
against a real `ServiceRequest`; the full Java-layer concurrency race (as opposed to the raw-SQL
proof above); live 1-match/2+-match cardinality; DH-011J; DH-011L. None of these are engineering
gaps remaining in this patch — they are the one gate the ticket itself named as acceptable to leave
open: **LIVE NATIVE SERVICEREQUEST CREATE/PERSIST/READ-BACK PROOF → BLOCKED ON REAL NUMERIC SITE
CODE.**

**DH-011D STATUS: BUILT / LIVE PARTIALLY VERIFIED.** `BLOCKER-6` unchanged: RESOLVED BY DECISION.
`DH-011` overall: still NOT COMPLETE.

---

### BLOCKER-7 — no approved Visit type for a Daher maternity admission · ✅ RESOLVED BY DECISION 2026-08-25 (ADR-028)

**Ticket:** UI-013 (clinician admission workflow — the first Daher-UI admission write). Found by
the ticket's own pre-write gate (§3), before any application code was written; nothing was built.

**Problem.** OpenMRS admission needs `{"patient", "visitType", "location"}` on `POST /visit`. The
`location` half is settled — `Maternité` is a committed Initializer artifact
(`openmrs-config/initializer/locations/locations-daher.csv`, UUID `6538f097-382d-49b6-a839-
601d03c888c0`, tagged `Admission Location`) and it is the sole result for that name. The
`visitType` half has never been decided. UI-001A's original runtime characterization used
`Facility Visit` "as the closest generic match, not a clinical decision"
(`docs/UI-001-CLINICAL-UX.md:627-629`), and every subsequent ticket that touched admission
(ADR-017C, ADR-017D, ADR-017E2) reused that exact same payload unchanged, purely as an inherited
test fixture to prove *authorization*, not as a claim that the visit type itself was ever approved.
No ADR between ADR-017 and ADR-027 (the full inventory — checked, not assumed) resolves it, and no
`openmrs-config/initializer/` file defines a Daher-owned visit type.

**Evidence — read-only, live-queried against the local stack (`daher-health-openmrs-backend-1`,
the container carrying the full committed Initializer config) 2026-08-25:**

```
GET /ws/rest/v1/visittype  →  Facility Visit, Home Visit, OPD Visit, Offline Visit, Group Session
```

None of the five is Daher-specific, and none maps unambiguously to "maternity admission" — this
is a naming/approval gap, not a count ambiguity (§4 of the ticket already fails closed on 0 or >1
*matches*; here there are 5 plausible-but-wrong candidates and 0 correct ones by name).

**Options, costs stated, none selected:**

| Option | What it means | Cost |
|---|---|---|
| **A. Adopt `Facility Visit`** | Promote UI-001A's disposable-fixture choice into production semantics | Cheapest technically, but the ticket's own brief explicitly forbids doing this silently — "Facility Visit" tells nobody reading a chart that this was a birth-centre admission, and a generic label reduces the audit trail's clinical legibility |
| **B. Adopt a different existing generic** (`Home Visit`, `OPD Visit`, `Offline Visit`, `Group Session`) | Same mechanism as A | Same problem as A, and worse fit semantically — none of these four is even the "closest match" UI-001A already picked |
| **C. Create a new Daher-owned Visit type via Initializer** (matching the pattern already used for `Daher Reception`/`Daher Clinician Admission` roles and the `Maternité` location) | A `visittypes-daher.csv` (or equivalent Initializer file), additive, no upstream visit type touched | Small, genuinely new config surface; needs a name (French-first, e.g. "Admission Maternité") and confirmation it doesn't collide with anything OpenELIS/reporting expects from visit-type semantics — not investigated, since choosing it is not this ticket's call |

**Also unresolved, surfaced by the same gate, not a second STOP:** every live admission proof to
date (ADR-017C, ADR-017D, ADR-017E2) ran at the generic `Inpatient Ward` location on disposable
containers that explicitly lacked the `Maternité` Initializer overlay. `Maternité` is committed
and uniquely resolvable, but admission has never actually been *proven* against it — that proof
still needs to happen once this gate clears, and belongs in UI-013's own live evidence, not
assumed from the Inpatient Ward runs.

**Consequence (at the time this blocker was raised).** No application code was written for UI-013.
`ui/src/features/patients/PatientWorkspace.tsx` kept its existing "Admission et contexte de
grossesse non disponibles" placeholder unchanged. Nothing in `ui/src/api/client.ts` was touched.

**RESOLUTION, same day 2026-08-25.** Liban chose **Option C**: a dedicated Daher-owned Visit type,
canonical name **`Daher Maternity Admission`**, provisioned through Initializer's own native
`visittypes` CSV domain — not options A/B (adopting any upstream generic). Full mechanism
confirmation, decision record, and committed artifacts are in `DECISIONS.md`'s new **ADR-028**
entry. This entry records the runtime proof.

**Mechanism confirmed by disassembly, not assumed** (`docker exec` into the running backend, `jar
xf` + `javap -c -v` against the pinned `initializer-2.12.0.omod`'s `initializer-api-2.12.0.jar`):
`Domain.VISIT_TYPES.getName()` computes `"visittypes"` (`name().replace("_","").toLowerCase()`,
bytecode-confirmed — the identical mechanism already backing `locations`/`roles`/
`globalproperties`); `VisitTypeLineProcessor extends BaseLineProcessor<VisitType>` reads exactly
`name`/`description` plus the generic `uuid`/`void-retire` columns every domain shares
(`BaseLineProcessor.HEADER_UUID="uuid"`, `HEADER_VOID_RETIRE="void/retire"`, also
bytecode-confirmed) — the identical CSV shape `locations-daher.csv`/`roles-daher.csv` already use.
No new schema invented.

**Committed:** `openmrs-config/initializer/visittypes/visittypes-daher.csv` (one row, UUID
`194bf546-68d7-42c0-8371-5b0f2b1cf5f3`, `Void/Retire` empty); one new additive bind mount in
`docker-compose.yml` (`.../visittypes:/openmrs/distribution/openmrs_config/visittypes/zz-daher-
health:ro`), alongside the existing three `zz-daher-health` domain mounts, none of which were
touched.

**Live proof, three throwaway environments, network `daher-ui013-net`, never the real
`openmrs-data` volume:**

- **Fresh database** (`ui013-fresh-db`, a brand-new `mariadb:10.11.18` container, empty volume;
  `ui013-fresh-omrs`, the pinned `daher-health/openmrs-backend:3.7-off` image against it, first
  boot). `GET /ws/rest/v1/visittype` → **exactly one result, `Daher Maternity Admission`,
  `194bf546-68d7-42c0-8371-5b0f2b1cf5f3`.** Filesystem check inside the container confirmed only
  our `visittypes-daher.csv` exists anywhere under `/openmrs/distribution/openmrs_config/
  visittypes/` — no upstream default file for this domain ships in this exact pinned image/config.
  **Honest, non-obvious finding, not smoothed over:** none of the five generic upstream Visit types
  (`Facility Visit` etc.) were present on this fresh boot at all — they are not seeded by this
  repo's current committed config on an empty database. This is pre-existing behaviour, unrelated
  to and unchanged by this ticket (nothing in `openmrs-config/initializer/` defines them, and this
  ticket added no logic that could suppress them); it means the fresh-DB run demonstrates the new
  domain's mechanism and additivity, but the "five upstream types survive unchanged" claim is
  properly demonstrated below, on data that actually has them.
- **Already-initialized database** (`openmrs_ui013_existing`, a `mysqldump`/restore clone of the
  long-lived `openmrs-ui009c` disposable stack's real schema — the one carrying ADR-017B/D/E2's
  full committed config and genuine pre-existing reference data — into a fresh schema on the same
  `mariadb-ui009`; `ui013-existing-omrs`, same pinned image, run against the clone). Before this
  ticket's config was added: 5 visit types (verified by direct `SELECT` before mounting). After
  booting with the new `visittypes` mount: `GET /ws/rest/v1/visittype` → **`Facility Visit`,
  `Home Visit`, `OPD Visit`, `Offline Visit`, `Group Session`, `Daher Maternity Admission` — six
  results, the five originals at their original UUIDs, unchanged, plus exactly one new row.**
- **Second provisioning pass, no duplicate.** `docker restart`/`docker stop` on this session's own
  containers remain blocked by this sandbox's recurring daemon permission denial (documented at the
  end of nearly every prior ticket), so — matching ADR-017E2's own "fresh process, not restart"
  workaround — a second, independent container (`ui013-existing-omrs-pass2`) was booted against the
  *same* already-Daher-provisioned clone schema. Result, both via the REST API and a direct
  `SELECT name, uuid, retired FROM visit_type`: **identical six rows, same UUIDs, `retired=0` on
  all, no duplicate `Daher Maternity Admission` row.**
- **Maternité location, reconfirmed exactly once, both on the fresh DB and the real main stack**
  (`daher-health-openmrs-backend-1`, port 8080): `GET /ws/rest/v1/location?q=Maternit` →
  **exactly one result**, `6538f097-382d-49b6-a839-601d03c888c0`, untouched by this ticket.

**Cleanup.** The cloned throwaway schema `openmrs_ui013_existing` was dropped successfully. The
four throwaway containers (`ui013-fresh-db`, `ui013-fresh-omrs`, `ui013-existing-omrs`,
`ui013-existing-omrs-pass2`) could not be stopped via the normal `docker stop`/`kill`/`rm` API
(the same intermittent daemon-level permission denial recorded at the end of nearly every prior
ticket this session) — **update, same day:** all were later terminated by a different route
(`docker exec <name> kill -9 <pid>` against the container's own main process from inside its own
PID namespace, which is not subject to the same daemon-side permission check) during an
out-of-band host memory emergency this same session — see BLOCKER-8's own note. No production
container, volume, or the real `openmrs-data` volume was ever touched.

---

### BLOCKER-8 — the "Admission" Encounter type is upstream demo/reference content, not reproducible on a fresh install · ✅ RESOLVED BY ADR-029 (decision + mechanism + existing-DB proof) · ⏳ fresh-deployment verification pending DH-017 (technical, not a decision)

**Ticket:** UI-013 (live closure pass), investigating a gap this same ticket's earlier pass first
noticed: a genuinely fresh database booted against the current committed config has **zero**
Encounter types beyond the core `Attachment Upload` — no `Admission` type at all, the one UI-013's
Encounter-half payload requires.

**Investigation, read-only, live-measured against the running stacks 2026-08-25 — the five
questions this pass was asked to answer:**

1. **Where did the main stack's `Admission` Encounter type come from?** Not from any Daher-committed
   file — `openmrs-config/initializer/` has no `encountertypes/` domain directory at all, and the
   upstream distribution's own `/openmrs/distribution/openmrs_config/encountertypes/` directory
   exists but is **empty** (checked directly inside a running container). It was not created by any
   config this repo owns.
2. **Is it upstream demo/reference-application seed data?** Yes, and the evidence is exact rather
   than circumstantial: `SELECT name, date_created, creator FROM encounter_type` on the main
   stack's database shows **all 19 encounter types** (`Admission`, `Vitals`, `Discharge`,
   `Consultation`, …) created within the same **2-second window**, `2026-08-11 09:35:50`–`52`, by
   `creator=2` (the daemon/system account) — a single bulk metadata import, not hand-authored rows.
   `SELECT … FROM visit_type` shows the identical pattern one second earlier, `09:35:51`, for all
   five upstream Visit types (`Facility Visit`, `Home Visit`, `OPD Visit`, `Offline Visit`, `Group
   Session`). The only Encounter type absent from that cluster is `Attachment Upload`, created two
   seconds earlier (`09:35:50`) — consistent with a core-platform install step running just before a
   separate, later "reference application content" bulk import. This lines up exactly with
   `PROJECT_STATE.md`'s own pre-existing **BLOCKER-2** finding (the `referencedemodata` module
   creating 50 fictional patients at a similarly-clustered timestamp the same day) — same general
   demo/reference-content mechanism, a different piece of what it seeds.
3. **Is UUID `e22e39fd-7db2-45e7-80f1-60fa0d5a4378` stable/canonical for this pinned stack?** Yes —
   it has been observed as the exact same value across every characterization this repo has ever
   done of it: UI-001A, ADR-017C, ADR-017D, ADR-017E2, and this ticket's own live query, all agree.
   Stable for *this pinned upstream content*; **not** reproducible from a genuinely empty database
   using the currently committed config (confirmed empirically twice, on two independent fresh
   containers, using the exact same pinned image tag the main stack runs).
4. **Does Initializer 2.12.0 support an `encountertypes` domain?** Yes — bytecode-confirmed
   (`Domain.ENCOUNTER_TYPES` exists in the enum, `getName()` computes `"encountertypes"`, the same
   mechanism ADR-028 already used for `visittypes`), and the distribution ships an (empty)
   `encountertypes/` directory ready to receive a CSV. A committed `encountertypes-daher.csv`
   carrying the exact same name/UUID would be mechanically identical to what ADR-028 already did.
5. **Could this be made additive/idempotent without changing semantics?** Technically, yes — pinning
   the *existing* upstream UUID and name (not inventing a new type) would no-op on any database that
   already has it (Initializer upserts by UUID) and would create it identically on one that doesn't.

**Why this is not implemented here, per this ticket's own explicit instruction.** No existing ADR or
`PROJECT_STATE.md` entry authorizes committing Daher-owned config that reproduces upstream
demo/reference-application metadata as a first-class, guaranteed-present artifact. That is a
reproducibility/deployment-guarantee decision (does a fresh Daher deployment need this content
guaranteed present, or is "whatever the reference-app demo importer happens to seed" an accepted
dependency for pilot purposes?) — adjacent to, but distinct from, BLOCKER-7/ADR-028's own decision,
and exactly the kind of gap AGENTS.md's "obviously should" clause warns against filling unilaterally.
Nothing was created or committed; `git status` confirms zero config files touched by this
investigation.

**Consequence for UI-013.** The real main stack cannot currently host all three canonical admission
artifacts simultaneously through any means available in this session: it already has `Admission` and
`Maternité` but is missing `Daher Maternity Admission` (container recreation blocked — see the live
closure pass's own report); a genuinely fresh install has `Daher Maternity Admission` (ADR-028) but
is missing `Admission`. A disposable environment combining a database that already had `Admission`
(cloned from `openmrs-ui009c`) with the new `visittypes` mount **did** briefly have all three
together (`ui013-live3`, confirmed live) — but no further live proof was completed against it before
this session's containers were torn down in a host memory emergency (see the live closure pass
report). This blocker is about **reproducibility for future fresh deployments** (DH-017's eventual
fresh-VM walkthrough in particular), not about whether UI-013's code is correct.

**Options, costs stated, none selected:**

| Option | What it means | Cost |
|---|---|---|
| **A. Accept the dependency** | Document that a Daher deployment currently depends on the upstream `referencedemodata`/reference-application bulk import having fired at least once (e.g. via a one-time `OMRS_DEMO_CONTENT=on` boot before switching to `off`) to get `Admission` and reference visit types | No new config; but fragile, undocumented today, and easy to lose track of before DH-017's fresh-VM proof |
| **B. Pin `Admission` via a committed `encountertypes-daher.csv`** (same exact UUID/name, ADR-028's mechanism) | Guarantees `Admission` on every fresh install, additive/idempotent on existing ones | Small new config surface; is committing upstream metadata under Daher's own Initializer domain a decision that needs sign-off, or is it inert enough to fall under "reference defaults only" (AGENTS.md)? That judgment call itself needs a human answer, not an agent's |
| **C. Investigate whether `OMRS_DEMO_CONTENT` toggling alone can be made to reliably seed this on first boot** | No new Daher-owned metadata file at all | Unexplored this pass; would need tracing the `referencedemodata` module's actual trigger condition, which this pass did not have time to do given the concurrent memory incident |

**RESOLUTION 2026-08-26 (ADR-029).** Liban approved **Option B**: pin the existing `Admission`
semantic (same name, same UUID `e22e39fd-7db2-45e7-80f1-60fa0d5a4378`) via a committed
`openmrs-config/initializer/encountertypes/encountertypes-daher.csv`, using Initializer's native
`encountertypes` domain. **Mechanism confirmed by disassembly, not assumed to mirror `visittypes`**:
`EncounterTypeLineProcessor` calls `CsvLine.getName(true)` / `.get("description")` (different method
signatures than `VisitTypeLineProcessor`) and additionally supports optional `view privilege`/
`edit privilege` columns (bytecode-confirmed header strings `"view privilege"`/`"edit privilege"`) —
left blank in the committed CSV because the existing row's `view_privilege`/`edit_privilege` are
both `NULL` in the live database (`SELECT … FROM encounter_type` on the real main stack, checked
directly, not assumed). Full decision record in `DECISIONS.md`'s new **ADR-029**.

**Live proof:**

- **Existing-database compatibility gate — fully proven, twice.** A `mysqldump` clone of the real
  main stack's own `openmrs` schema (19 encounter types, `Admission` present) into a throwaway
  schema on the same `daher-health-openmrs-db-1` server; booted with the new `encountertypes` mount
  added. Result: **19 total encounter types (unchanged), `Admission` at its original UUID, name,
  and `retired=false` (unchanged), zero duplicates** — confirmed both via REST and a direct
  `SELECT COUNT(*) FROM encounter_type WHERE uuid=…` (`= 1`). An unrelated type (`Vitals`)
  spot-checked unchanged. **Second provisioning pass**, a genuinely independent process booted
  against the same clone schema (the first process's `java` PID killed first, matching this
  session's established "fresh process, not restart" pattern — `docker stop`/`kill`/`restart`
  remain permission-denied): **identical result, still 19, still exactly one `Admission`, no
  duplicate.**
- **Fresh-database three-artifact proof — NOT achieved, for a reason unrelated to this config's
  correctness, disclosed rather than glossed over.** Six independent attempts at a genuinely empty
  database — across three MariaDB backends (a schema on the shared main-stack server, a dedicated
  fresh MariaDB container, and a config-identical control with the new mount removed to isolate the
  variable) — all landed on OpenMRS's own `/initialsetup` wizard page instead of completing startup,
  even though the boot logs show the runtime-properties file being auto-generated and the Liquibase
  changelog detective correctly identifying the (in this case, genuinely empty) schema state. The
  **encountertypes mount was ruled out as the cause**: the control run, with that mount removed
  entirely, hit the identical wizard page. This same race did **not** occur a few hours earlier in
  this same session, when ADR-028's own fresh-database proof (identical image, identical general
  mechanism, one fewer mount) completed cleanly — indicating a change in host-level timing
  conditions since then (this session's intervening memory/swap exhaustion incident is the leading
  candidate, though not proven) rather than anything wrong with this ticket's config. A hand-written
  runtime-properties-file workaround was attempted and abandoned — it does not represent a genuine
  fresh-deployment boot path and failed for an unrelated permissions reason anyway. **This is a new,
  disclosed environmental limitation of the current Docker environment, additional to and distinct
  from the pre-existing `docker stop`/`kill`/`restart` permission denial** — recorded here rather
  than claimed as passed. The mechanism itself (Initializer's native `encountertypes` domain,
  bytecode-confirmed to exist and to accept exactly this CSV shape) is not in question; only live
  demonstration of a fresh boot succeeding at all, today, on this host, is outstanding.
  **Liban reviewed this evidence and accepted the gap as a DH-017 fresh-deployment verification
  item, not a defect in this decision** — the mechanism confirmation, exact semantic/UUID
  preservation, existing-database proof, idempotent second pass, and CI guard/controls above are
  sufficient to commit ADR-029; DH-017's eventual fresh-VM walkthrough is where a genuine fresh
  boot gets demonstrated.
- **CI static guard**, `.github/workflows/ci.yml` ("ADR-029 — Admission Encounter type is
  reproducible, upstream semantics preserved"): asserts exactly one committed row with the pinned
  name/UUID, not voided, no `View privilege`/`Edit privilege` set (matching the existing `NULL`
  state), no stray extra file in the domain, no second admission-named type, and the compose mount
  present. Control-tested against five deliberate breaks (wrong name, wrong UUID, duplicate row,
  retired row, missing compose mount) — all five correctly failed; the real committed config passes.

**Cleanup.** All throwaway containers/schemas from this pass's live proof (`adr029-existing`,
`adr029-existing-pass2`, `adr029-fresh` through `adr029-fresh5`, `adr029-control`, the throwaway
`openmrs_adr029_existing`/`openmrs_adr029_fresh` schemas on `daher-health-openmrs-db-1`) were
terminated via `docker exec <name> kill -9 <pid>` against each container's own main process (the
same workaround this session's `docker stop`/`kill`/`rm` permission denial has required throughout).
One lightweight container, `adr029-mariadb`, could not be terminated even this way — `mysqld` runs
as PID 1 in that image and the kernel's own PID-namespace-init signal immunity blocks a same-namespace
`kill -9 1` (this specific sub-case had not been hit before this pass); it is a small, disposable
MariaDB instance, not a JVM, and is left as a disclosed leftover needing a human `docker kill`/`rm`.
Every other container this pass created was successfully torn down. No production container,
volume, or the real `openmrs` schema was ever written to — only dumped from, read-only.

---

### BLOCKER-9 — `Organizational: Nurse` closes over 302 of 345 privileges; removal is a scope-of-practice decision · ✅ RESOLVED BY DECISION 2026-08-26 (ADR-017F, Option A) · live proof complete

**Ticket:** ADR-017F. Follows up ADR-017D's own disclosed gap: the committed clinician identity
(`Provider` + `Organizational: Nurse` + `Daher Clinician Admission`) authenticates with "hundreds
more" privileges than the nine ADR-017D deliberately approved for admission, traced to
`Organizational: Nurse` → `Application: Enters ADT Events`(+3 others) → `Privilege Level: High`.
That was never enumerated or tested for removal until now.

**Measured, against the main stack's own database (`daher-health-openmrs-db-1`), DB- and
REST-cross-verified:** `Organizational: Nurse`'s effective privilege closure is **302** of the
system's 345 total privileges (87.5%) — including `Delete Patients`, `Delete Users`, `Manage Roles`,
`Manage Global Properties`, `Manage Modules`, full `Order` CRUD, and `Manage Concepts`. `Provider`
carries 0 direct privileges. All 9 of ADR-017D's approved admission privileges are a strict subset of
the Nurse closure, so `EXTRA_PRIVILEGES = 293`. Full role graph, categorized privilege table, and
`§6` sourcing (zero committed code depends on `Organizational: Nurse` for auth, user creation,
Provider derivation, or UI-013) are in `DECISIONS.md`'s new **ADR-017F** entry.

**Why this isn't a technical cleanup.** There is no committed mechanism assigning
`Organizational: Nurse` to remove — `keycloak/reconcile-sso.sh` never provisions it (only `Daher
Reception`/`Daher Clinician Admission` are automated; `Provider`/`Organizational: Nurse` are
"created and assigned manually per ADR-017"). The only committed artifact is **ADR-017's own decision
table**. Narrowing it supersedes a recorded human decision with a stated clinical-workforce
rationale ("midwives are the primary users"), and separately, 293 extra privileges — several
destructive or administrative — is plainly not an inert difference. Both facts independently force
a STOP per AGENTS.md.

**Not demonstrated this pass:** a live OIDC round trip for a narrowed identity, blocked by a newly
found, unrelated bug — first-time OpenMRS user auto-provisioning (`Daemon.createUser`) throws a
`ValidationException` whose cause was not isolated, reproduced identically regardless of whether
`Organizational: Nurse` was present (so it does not implicate this ticket's variable, but it does
mean no new live evidence was produced). A separate, genuine documentation-vs-reality discrepancy
was also found and disclosed, not fixed: `keycloak/mappers/openmrs-client-roles-mapper.json` carries
no client-scoping key, contradicting ADR-017B's own text — the `roles` claim measurably included
Keycloak's default `account`-client roles until stripped from the test users. Full detail, options
(A: narrow to `Provider`+`Daher Clinician Admission`; B: keep as-is; C: a new narrower Daher-owned
clinical role), and disposable-leftover disclosure are in ADR-017F.

**Options, costs stated, none selected — see ADR-017F for the full table.**

**Human Decision Required: Yes.** Blocks real SSO activation of the clinician identity alongside
BLOCKER-5's own roster gap. The `createUser` auto-provisioning failure is a separate, new candidate
blocker sitting in front of *any* real clinician's first SSO login regardless of which role shape is
chosen — needs its own isolation pass, not decided or fixed here.

**RESOLUTION 2026-08-26 (a follow-up ticket, Bug #24/#25).** Liban chose **Option A**: the Daher
clinician identity is `Provider` + `Daher Clinician Admission`; `Organizational: Nurse` is excluded
from the model going forward, no broader nurse permissions implemented. The `createUser` blocker
noted above turned out to be this session's own prior synthetic test data (Known Bug #24, an
invalid-TLD test email — corrected, not a platform defect); with that fixed, the narrow identity's
live OIDC proof — first-login provisioning, exactly 9 privileges, no `Organizational: Nurse`, no
`Privilege Level: High` inheritance, `currentProvider` non-null, canonical admission contract
(Visit 201 → linked Encounter 201, audit creator = the clinician's own user, `encounterProviders:
[]`), and a dangerous-privilege negative control (`Manage Roles` denied) — is complete, in
`DECISIONS.md`'s ADR-017F own live-closure-proof section. **No committed role/config file changed**:
`Provider`/`Organizational: Nurse` were never part of any committed assignment automation to begin
with (`keycloak/reconcile-sso.sh` only ever provisioned `Daher Reception`/`Daher Clinician
Admission`) — the decision is recorded, not implemented as a code change, because there was never
committed code encoding the old mapping to change. A new, unrelated bug was found during this
closure pass: **Bug #25**, a first-login read-after-write gap (Known Bugs table). It did not affect
this decision's correctness (no duplicate/ambiguous identity occurred), and was subsequently
root-caused, fixed, automated-tested, and disposable-live-proven in its own follow-up before
real SSO activation.

---

### BLOCKER-10 — ending a Daher maternity Visit is gated behind an open-ended chain of unrelated module privileges · ✅ CLOSED 2026-08-28 (UI-015F) · **Human Decision Required: No further — Liban's review of the closure evidence below is the remaining step, not a design decision**

**Ticket:** UI-015 (safely end a maternity admission). Pre-write gate investigation, before any UI
code was written, per this ticket's own explicit instruction not to assume the mechanism.

**Mechanism confirmed by disassembly against the pinned stack (`openmrs-adr017f`, image
`daher-health/openmrs-backend:3.7-off`), not assumed.** There is no dedicated "end visit"/"discharge"
REST subresource. `VisitResource1_9` is a standard CRUD resource: its `save()` method is a direct
pass-through to `Context.getVisitService().saveVisit(visit)`, and `stopDatetime` is an ordinary
updatable property (not removed by `getUpdatableProperties()`, unlike `patient`). So the real
mechanism is exactly `POST /ws/rest/v1/visit/{uuid}` with body `{"stopDatetime": "<ISO8601>"}` —
confirmed against the interface annotations that `VisitService.saveVisit()` itself only requires
`@Authorized({"Add Visits","Edit Visits"}, requireAll=false)` — either privilege suffices, and the
Daher clinician already holds `Add Visits`. **DELETE is a different operation** (`VisitResource1_9
.delete()` calls `voidVisit`, requiring `Delete Visits` — administrative removal, not what this
ticket wants) and was correctly ruled out rather than assumed equivalent.

**The actual blocker: OpenMRS's core save pipeline invokes every installed module's
`SaveHandler<Visit>` unconditionally on any Visit save, and at least two independent, Daher-unrelated
modules hook that pipeline specifically for "visit just got a stopDatetime" and gate their own
(unconditional, unscoped) side effect behind a privilege the Daher clinician role does not have —
live-reproduced, not inferred:**

1. **`bedmanagement` module** (`VisitWithBedPatientAssignmentSaveHandler`, bytecode-confirmed):
   fires whenever an *existing* Visit (`getVisitId() != null`) is saved with a non-null
   `stopDatetime`, and unconditionally calls `BedManagementService.unAssignBedsInEndedVisit(visit)`
   — regardless of whether that patient has any bed assigned. That service method requires
   `@Authorized({"Assign Beds","Edit Admission Locations"}, requireAll=true)` — **both**, not either.
   Live-reproduced: a genuine `Daher Clinician Admission` + `Provider` session (`bug23-clinician1`,
   exactly the ADR-017D/ADR-017F nine-privilege identity), attempting `POST /visit/{uuid}
   {"stopDatetime": …}` on a freshly created canonical Daher-maternity Visit **with zero beds ever
   assigned to that patient** (confirmed via `GET /bed?patientUuid=…` → `results: []`), got a clean
   `403`: `"Privileges required: Assign Beds"`.
2. **`queue` module** (`VisitWithQueueEntriesSaveHandler`, bytecode-confirmed): fires on the identical
   trigger (existing Visit + non-null `stopDatetime`) and unconditionally calls
   `QueueEntryService.getQueueEntries(...)` to check for open queue entries on that Visit **before**
   it can determine there is nothing to close — that read alone requires `Get Queue Entries`. A
   disposable, evidence-only test role (`ui015-clinician-bedtest`, never committed) carrying the nine
   approved privileges plus `Assign Beds`+`Edit Admission Locations` cleared finding #1 and then hit
   this second, independent `403`: `"Privileges required: Get Queue Entries"` — live-reproduced the
   same way.

**Why this stops here rather than continuing to grant privileges one at a time.** Both privileges are
structurally blanket, exactly the same platform limitation ADR-017C already characterized for
`Add Visits`/`Add Encounters`: `Assign Beds`+`Edit Admission Locations` also gate
`assignPatientToBed`, `saveBedPatientAssignment`, and ward/bed **layout** configuration
(`saveAdmissionLocation`, `setBedLayoutForAdmissionLocation`) — facility-wide bed-management
authority, not "may end this one Visit." Granting them to let a clinician end an admission would
hand that role the ability to assign any patient to any bed anywhere and reconfigure admission
locations — authority ADR-017D deliberately withheld ("admission does not implicitly touch bed
state"). The `queue`-module gate is the second module found the same way in a single pass, with no
enumerated ceiling — there is no way to know how many more installed modules register the same kind
of unconditional `SaveHandler<Visit>` without granting privileges one at a time until the chain
stops, which is itself the "assume-and-continue" pattern AGENTS.md guards against, not a
resolution. **§13's own instruction — "if ending a Visit automatically changes bed assignment
because of the installed bed-management module: measure and report exactly what happens, do not
compensate or modify it without a separate decision" — is exactly this finding.**

**Options, costs stated, none selected:**
- **Option A — grant `Assign Beds`+`Edit Admission Locations` (and whatever the `queue` chain
  requires beyond `Get Queue Entries`, not yet fully enumerated) to `Daher Clinician Admission`.**
  Unblocks ending a Visit through the standard API. Cost: hands the clinician role blanket
  bed-assignment and admission-location-configuration authority that has nothing to do with ending
  an admission, reopening a boundary ADR-017D explicitly closed, and the full privilege set needed
  to clear every installed module's cascade is not yet known.
- **Option B — a narrow, Daher-owned service-layer or database-level path that sets `stopDatetime`
  without going through `VisitService.saveVisit()`'s save-handler pipeline.** Technically avoids the
  cascade (it's a Java-object-graph-level pipeline, not something the REST API alone controls) but
  requires writing and maintaining custom backend code path — a materially larger change than this
  ticket's own scope of "wire up an existing contract," and privilege bypass of this kind is itself
  a security-boundary decision, not an implementation detail.
- **Option C — accept that ending a maternity admission is out of scope for pilot-ready V1** and
  leave the Maternité board read-only (as UI-014 shipped it); a clinician ends the admission through
  the native OpenMRS/O3 UI instead (untouched by Daher's own identity model), or an administrator
  handles it directly, until a decision is made.
- **Option D — a facility-scoped variant of Option A**: request only the minimum privilege each
  installed module's cascade turns out to require, enumerated exhaustively first (this ticket
  stopped after finding two; there may be more), then evaluate whether that minimum set is narrow
  enough to be acceptable. Slower, but avoids granting broad authority speculatively.

**Separately, and independent of the privilege question:** no existing `DECISIONS.md` entry
approves "a Visit with `stopDatetime` set, no discharge disposition/diagnosis/outcome/destination/
bed-release record" as the canonical Daher representation of "this patient is no longer actively
admitted." This ticket's own instructions frame the intended action narrowly ("Terminer
l'admission", not "Sortie médicale"/"Patient guéri"/discharge language) — read as adequate
scope-narrowing for *when* implementation resumes, not a substitute for a recorded decision. Noting
it here so it is not rediscovered as a surprise once BLOCKER-10 itself is resolved.

**No implementation was written.** No UI file, API client function, or backend config was added or
changed — `git status`/`git diff --stat` at ticket close show the tree at `8ab3f4b` unchanged
(nothing outside a self-cleaned scratch directory). Per §20 and §25's own instruction, no tests were
faked around this gap.

**Human Decision Required: Yes.** Which option (A/B/C/D, or another) to take before UI-015 can
proceed. Blocks: the "Terminer l'admission" action on the Maternité board / patient workspace.
Does not block: UI-014 (read-only board, unaffected), UI-013 (admission creation, unaffected), or
any other in-flight ticket.

#### UI-015A follow-up (2026-08-27) — the exact authorization call path, traced not guessed

**Ticket:** BLOCKER-10 / UI-015A, a dedicated evidence pass before any option above is chosen.
Continued from the same worktree; no PROJECT_STATE.md content above was discarded.

**PROVEN — the complete authorization call path for `POST /visit/{uuid} {"stopDatetime": …}`,
read from a real server-side stack trace, not inferred from source alone**
(`DelegatingCrudResource.update()` → `VisitResource1_9.save()` → the `VisitService` AOP proxy →
`RequiredDataAdvice.before()` → …):

1. `getByUniqueId()` — requires `Get Visits` (held).
2. `RequiredDataAdvice.before()` fires as a Spring `MethodBeforeAdvice` **wrapping the entire
   `VisitService.saveVisit()` call** — i.e. everything below happens *before* the real DAO write,
   confirmed both by `javap` (`RequiredDataAdvice implements MethodBeforeAdvice`,
   `AuthorizationAdvice implements MethodBeforeAdvice`) and by the live stack trace.
3. Within that before-advice, `RequiredDataAdvice` dispatches registered `SaveHandler<Visit>`
   beans first. `bedmanagement.VisitWithBedPatientAssignmentSaveHandler` fires only when the
   in-memory Visit already has an id *and* a non-null `stopDatetime`; if so it calls
   `BedManagementService.unAssignBedsInEndedVisit()`, gated `@Authorized({"Assign Beds","Edit
   Admission Locations"}, requireAll=true)`.
4. If step 3 doesn't throw, `RequiredDataAdvice.before()` also calls `ValidateUtil.validate()` →
   `AdministrationService.validate()` → the registered `Validator<Visit>` beans. The `queue`
   module's `org.openmrs.module.queue.validators.VisitWithQueueEntriesValidator.validate()` — a
   **validator**, not a save handler, a materially different registration mechanism than finding
   #2 in the section above — calls `QueueEntryService.getQueueEntries(...)` **unconditionally on
   every update of an existing Visit** (creation of a brand-new Visit never hit this in any test;
   the validator does not appear to gate on `stopDatetime` the way the two `SaveHandler` classes
   do). That call requires `Get Queue Entries`. The full server-side stack trace (captured via the
   REST error body's `detail` field, complete this time, not truncated) shows the exact frames:
   `VisitWithQueueEntriesValidator.validate:55` → `HibernateAdministrationDAO.validate:302` →
   `AdministrationServiceImpl.validate:893` ← `ValidateUtil.validate:75` ←
   `RequiredDataAdvice.before:129` ← `VisitResource1_9.save:253,60` ←
   `DelegatingCrudResource.update:139` ← `MainResourceController.update:134`.
5. A separate `queue.VisitWithQueueEntriesSaveHandler` (a *third* registration, a genuine
   `SaveHandler<Visit>` this time) also exists, gated the same way as bedmanagement's (existing
   Visit + non-null `stopDatetime`), and also requires `Get Queue Entries` — confirmed by bytecode
   in the earlier BLOCKER-10 pass. Whether step 4 or this fires first for a stopDatetime update
   was not independently distinguished (both surface the identical privilege), and it does not
   change the conclusion.

**FAILING CLASS/METHOD (exact 403, reproduced twice, identical both times):**
`org.openmrs.aop.AuthorizationAdvice.throwUnauthorized` (`AuthorizationAdvice.java:120/133`),
raised from inside a `$Proxy*.getQueueEntries`/`.unAssignBedsInEndedVisit` call, propagating up
through `MethodBeforeAdviceInterceptor` → `ReflectiveMethodInvocation` → the `VisitService`/
`AdministrationService` JDK dynamic proxy → `VisitResource1_9.save` → REST controller → the
standard Spring MVC/servlet filter chain. HTTP `403`, `org.openmrs.api.APIAuthenticationException`.

**VISITSERVICE AUTHORIZATION — PROVEN sufficient, never the actual blocker.**
`VisitService.saveVisit()` itself requires only `@Authorized({"Add Visits","Edit Visits"},
requireAll=false)`. In every single test run this session and the prior one — base 9-privilege
clinician, the `Edit Visits`-only control below, the `Assign Beds`+`Edit Admission Locations`
control — **the error returned was never once "Get Visits", "Add Visits", or "Edit Visits."** The
service-layer check on `saveVisit` itself always silently passes (satisfied by `Add Visits`,
already held) and is never what blocks the request.

**§4 — MISSING PRIVILEGE: "Edit Visits" tested and PROVEN not to resolve the 403.** A disposable
role (`ui015a-editvisits-test`, never committed to `roles-daher.csv`) — the exact 9 approved
`Daher Clinician Admission` privileges plus only `Edit Visits` — attempted the identical
`POST /visit/{uuid} {"stopDatetime": …}` against a freshly created canonical Visit. Result:
**identical `403`, identical message** (`"Privileges required: Assign Beds"`), byte-for-byte the
same failure as the base 9-privilege role with no `Edit Visits` at all. Read-back confirmed
`stopDatetime` remained `null` — nothing persisted. **Edit Visits changes nothing about this
blocker, because it was never involved: `saveVisit`'s own check already passes via `Add Visits`,
and the actual blocking checks (bedmanagement's, the queue validator's, queue's own save handler)
belong to entirely different services (`BedManagementService`, `QueueEntryService`) with their own
independent `@Authorized` annotations that do not reference `Edit Visits` at all.**

**§7 — WHY "Add Visits OR Edit Visits" passing at the service layer still yields a REST 403,
documented precisely so nobody later removes/changes privileges based on `VisitService`'s
annotation alone:** the REST resource's generic `update()` (`DelegatingCrudResource.update()`,
disassembled directly — no privilege-scoping logic of its own; it only calls `getByUniqueId()`
then property-binds then calls the resource's `save()`) adds no authorization of its own. The
mismatch is entirely because **OpenMRS's core `RequiredDataAdvice` mechanism runs arbitrary
third-party module code — with its own, independent, unrelated privilege requirements — inside
the *same* before-advice that wraps every Visit save**, regardless of which privilege authorized
the outer call. `Add Visits`/`Edit Visits` govern `saveVisit()` itself; they have no bearing on
what `bedmanagement` or `queue` additionally demand once inside that call. This is a platform
composition hazard, not a misconfigured privilege on Daher's side.

**§5/§6 — EDIT VISITS EFFECTIVE AUTHORITY, characterized by structural proof (interface
annotations), not further live-tested since it never clears the actual gate:** `Edit Visits`
appears on exactly two other service methods, both bytecode-confirmed and both unreachable via
this REST resource: `VisitService.endVisit(Visit, Date)` (the semantic convenience method — never
called by `VisitResource1_9`, which calls `saveVisit()` directly) and `VisitService.stopVisits
(Date)` (a bulk, scheduled-task-only auto-close of all visits past a cutoff — not REST-reachable
at all). Through the REST update path specifically, `Edit Visits` grants **nothing beyond what
`Add Visits` (already held) already grants**, since `saveVisit`'s own check is satisfied by
either. **CAN Edit Visits be restricted to Daher Maternity Admission only? NO. Maternité only?
NO. The patient's current active Visit only? NO** — proven structurally: `DelegatingCrudResource
.update()` has no type/location/patient scoping logic anywhere in its call path, and the complete
`privilege` table (`SELECT privilege FROM privilege WHERE privilege LIKE '%Visit%' ...`,
enumerated exhaustively) contains no type- or location-scoped variant of `Edit Visits`, `Add
Visits`, `Assign Beds`, `Edit Admission Locations`, or `Get Queue Entries` — none of the 34
Visit/bed/queue/admission-related privileges in the whole system carries any such scoping,
matching ADR-017C's already-established "blanket, unscopable" characterization exactly.
**Separately live-confirmed (a non-stopDatetime mutation, chosen specifically so it would not
also trip the bedmanagement/queue-save-handler gates and could isolate REST's own scoping
behavior): the `Edit Visits`-equivalent identity attempted to change an *unrelated* patient's
non-canonical `Facility Visit @ Inpatient Ward`'s `location` and `startDatetime` — REST accepted
the request as a legitimately-shaped call for that unrelated visit (it was blocked by the same
`queue` validator, not by any type/location/ownership check) — there is no per-visit ownership
gate to trip in the first place.**

**§6 — NARROWER NATIVE MECHANISM: NOT AVAILABLE.** Exhaustive enumeration of every
Visit/bed/queue/admission-location privilege on the pinned stack (34 rows, listed above) contains
no "Stop Visit," "Discharge," "End Visit," "Edit Own Visit," or any type/location-scoped
privilege of any kind. The only mutation-adjacent privileges are `Add Visits`, `Edit Visits`
(interchangeable at the REST layer, see above), and `Delete Visits` (void — administrative
removal, ruled out from the start). This is a platform ceiling, not a gap in this investigation.

**A new, independently significant finding: the write is not atomic across this cascade.**
Re-examining the disposable `Assign Beds`+`Edit Admission Locations` test role from the prior
BLOCKER-10 pass (`ui015-clinician-bedtest`, which cleared the bedmanagement check and then hit
`Get Queue Entries`): the client received a `403` — but a direct query of the `visit` table
afterward showed `date_stopped` **and** `changed_by`/`date_changed` durably persisted, correctly
attributed to that exact test identity. Cross-checked against the mechanism above: because
`RequiredDataAdvice.before()` calls the bedmanagement `SaveHandler` (a genuine, separate
Spring-proxied service call with its own transactional boundary) *before* reaching the `queue`
validator, and that bedmanagement call succeeded (the test role held its privileges), the property
mutation already bound onto the Hibernate-session-managed Visit object was evidently flushed to
the database as a side effect of that earlier, successful nested call — before the *later* queue
check ever threw. The REST-layer non-stopDatetime control (§5 above) shows this is not universal —
that attempt correctly persisted nothing, because it failed at the *first* privileged call
encountered (nothing earlier had a chance to flush). **The practical consequence: a "403" response
from this endpoint cannot be safely assumed to mean nothing changed.** Whatever option is chosen
for BLOCKER-10, any future implementation must never treat a failed end-Visit response as proof of
no mutation — it must always re-fetch authoritative Visit state after *any* attempt, exactly the
discipline UI-015's own §8 (ambiguous-write safety) already anticipated, now confirmed as a real,
not merely theoretical, server-side hazard.

**§8 — RECEPTION CONTROL, reconfirmed cleanly:** genuine `Daher Reception` OIDC session attempting
the identical `POST /visit/{uuid} {"stopDatetime": …}` on an unrelated Visit → `403`,
`"Privileges required: Get Visits"` (fails at the very first step, before any bed/queue code is
even reached). Registration reconfirmed unaffected: the same session's `POST /patient` reached
normal field validation (`400`, missing identifier) rather than an authorization wall.

**§9 — AUDIT ATTRIBUTION: proven positively, via the ambiguous-write case above** (no clean
successful end-Visit was ever achieved to test the happy path directly, so this is the best
available evidence, disclosed as such). `GET /visit/{uuid}?v=full`'s `auditInfo.changedBy` and
`auditInfo.dateChanged` correctly identify the exact acting user and timestamp
(`changedBy.display: "ui015-bedtest-clinician"`, `dateChanged` matching `stopDatetime` to the
second) — OpenMRS's own audit fields work correctly and are REST-visible; the platform limitation
here is authorization, not auditability.

**§10 — CLINICAL SEMANTICS: unchanged from the prior pass.** No `DECISIONS.md`/`PROJECT_STATE.md`/
`docs/` entry approves "Visit with `stopDatetime` set, nothing else" as the canonical
representation of "no longer actively admitted." **OPTION A (proposed, not self-approved):**
"Terminer l'admission" means only closing the active Visit by setting `stopDatetime`. It does
**not** mean medical discharge, home discharge, transfer, a clinical outcome, or postpartum
completion — no disposition, diagnosis, outcome, destination, bed action, or discharge Encounter
is implied or recorded. This remains a proposal for Liban to accept or reject, not a decision made
here.

**No implementation, no committed-role change, no Keycloak change, no OpenMRS config change this
pass either** — three disposable Keycloak client roles/users and their matching disposable
OpenMRS roles were created for evidence only and torn down (Keycloak side fully deleted; the
OpenMRS `role` rows for `ui015-clinician-bedtest`/`ui015a-editvisits-test` could not be deleted —
the same harmless `user_role` FK leftover this session has hit repeatedly — their privileges were
stripped first). `git status` at close: only `PROJECT_STATE.md` changed.

**TECHNICAL RECOMMENDATION (not a decision):** Option C for pilot-ready V1 (leave ending an
admission out of the Daher UI; use native OpenMRS/O3 or an administrator instead), revisited under
Option D (exhaustive minimum-privilege enumeration) only if ending admissions from the Daher UI
turns out to be a hard requirement before 1 September — Option A's cost is now better quantified
(at minimum `Assign Beds`+`Edit Admission Locations`+`Get Queue Entries`, confirmed still
incomplete since the queue chain was never fully cleared) but remains a scope-of-practice
reopening of ADR-017D, and the newly-found non-atomic-write hazard means Option A would also need
its own client-side reconciliation discipline regardless. Option B (a custom bypass path) is not
recommended: it would need to reproduce the DAO's write and audit semantics by hand while stepping
around a real security-relevant validation chain, a materially larger and riskier undertaking than
this ticket's evidence-gathering scope, and worth flagging against the existing no-BFF direction
(ADR-027) if attempted.

#### UI-015B follow-up (2026-08-27) — the complete cascade, enumerated to termination

**Ticket:** BLOCKER-10 / UI-015B. Continued from the UI-015A worktree; nothing above discarded.
Superseded, not contradicted: UI-015A correctly showed `Edit Visits` alone does not clear the
403 (bedmanagement blocks first); this pass shows `Edit Visits` **is** genuinely required, just
later in the chain, once the earlier gates are cleared — both facts are true simultaneously.

**PROVEN — the complete cascade, built by satisfying one privilege at a time on disposable roles
until either success or the next gate, never guessed:**

| Order | Module | Class | Method | Trigger condition | Privilege required | Before/after core save | Read/write | Applies to |
|---|---|---|---|---|---|---|---|---|
| 1 | `bedmanagement` | `VisitWithBedPatientAssignmentSaveHandler` | `unAssignBedsInEndedVisit` | existing Visit + non-null `stopDatetime` | `Assign Beds` **and** `Edit Admission Locations` (both — live-confirmed: granting only `Assign Beds` surfaces `Edit Admission Locations` as the next missing one, and vice versa) | Before (inside `RequiredDataAdvice.before()`, a `SaveHandler` dispatch) | Read always; write only if a bed assignment exists | Every Visit save transitioning to a `stopDatetime`, regardless of whether a bed is assigned |
| 2 | `queue` | `VisitWithQueueEntriesValidator` | `validate` → `getQueueEntries` | any update of an *existing* Visit (does not appear to check `stopDatetime` specifically; never observed on Visit *creation*) | `Get Queue Entries` | Before (inside `RequiredDataAdvice.before()`, via `ValidateUtil.validate()` → `AdministrationService.validate()`) | Read only | Every update of an existing Visit |
| 3 | core `VisitService` | `VisitServiceImpl` | `saveVisit`, line 176 | a **hardcoded, in-body** `Context.requirePrivilege("Edit Visits")` call — not the interface's `@Authorized({"Add Visits","Edit Visits"}, requireAll=false)`, a materially stricter, separate check | `Edit Visits` (no OR with `Add Visits` at this specific point) | Before the DAO write, but *after* `RequiredDataAdvice.before()` has already run and not thrown — i.e. inside the real join point, past the AOP layer | N/A (guard) | Appears specific to ending/editing an already-persisted Visit, not creating one (creation never hit this) |
| 4 | `queue` | `VisitWithQueueEntriesSaveHandler`/`QueueEntryService` | `saveQueueEntry` | only reached when step 2 actually found a non-empty queue-entry list for the Visit — **live-confirmed with a real (SQL-constructed) queue entry present; a Visit with zero queue entries never reaches this** | `Manage Queue Entries` | Same `SaveHandler` dispatch as step 1 | Write | Only Visits that actually have an open queue entry — never true for any Daher maternity Visit today, since nothing in this codebase or Daher's own workflow ever creates a queue entry |
| 5 | core `ConceptService` (via the queue module's own save path) | — | resolving the entry's status concept during `saveQueueEntry` | same condition as step 4 | `Get Concepts` | Same as step 4 | Read | Same narrow condition as step 4 |

**Live-confirmed termination, not assumed:** with all of steps 1–3 granted (13 total privileges:
the 9 approved + `Assign Beds`+`Edit Admission Locations`+`Get Queue Entries`+`Edit Visits`) and
**zero** bed/queue state present — the realistic Daher scenario, since nothing in this codebase
ever assigns a bed or creates a queue entry — the end-Visit call returned a **clean `200`**:
`stopDatetime` persisted correctly, `visitType`/`location`/`uuid` unchanged, the linked `Admission`
Encounter remained present/unvoided/linked with `encounterProviders: []` unchanged, and a
subsequent run of UI-014's own authoritative board query correctly excluded the now-ended Visit.
With steps 1–5 also granted (15 total) against a Visit carrying a real (SQL-constructed, disposable)
active queue entry, the remaining error was **not an authorization failure** — a Hibernate
`PropertyValueException` on the hand-built fixture's own `sortWeight` column (an artifact of
constructing test data directly in SQL rather than through the queue module's own API, which would
set it correctly) — confirming the privilege chain itself is exhausted; nothing beyond `Get
Concepts` was ever demanded.

**BEDMANAGEMENT — no-bed vs. bed-assigned controls, both live:** (A) a canonical Visit with zero
bed assignments (confirmed via `GET /bed?patientUuid=…` → empty) ends cleanly once privileges 1–3
are held; the bed-check runs (unconditionally) but finds nothing, no bed-table row is touched. (B)
a canonical Visit with a genuine (disposable, SQL-constructed) bed assignment: ending the Visit
**automatically and correctly released the bed** — `bed.status` flipped `OCCUPIED` → `AVAILABLE`
and `bed_patient_assignment_map.date_stopped` was set to the exact stop time. This is a real,
useful, correctly-implemented side effect if Option A is ever chosen — Daher would not need to
build bed release itself.

**ASSIGN BEDS effective authority — proven by the interface signature itself, the strongest form
of proof available (a method with no location/type parameter cannot be scoped by either):**
`BedManagementService.assignPatientToBed(Patient, Encounter, String)` takes no location, visit
type, or ward argument — any patient can be assigned to any bed the caller can look up, full stop.
Combined with the exhaustive privilege-table enumeration from BLOCKER-10's first pass (no
location/type-scoped variant of `Assign Beds` or `Edit Admission Locations` exists anywhere in the
system): **CAN ASSIGN BEDS BE SCOPED TO MATERNITÉ? NO. TO DAHER MATERNITY VISITS? NO. TO ONLY
AUTO-UNASSIGN DURING VISIT END? NO** — the same privilege gates manual, arbitrary bed assignment
and the automatic end-of-visit release; there is no narrower grant available.

**GET QUEUE ENTRIES / MANAGE QUEUE ENTRIES effective authority:** `Get Queue Entries` is read-only
and, by its own interface signature (`getQueueEntries(QueueEntrySearchCriteria)`), can be filtered
by visit — but the *privilege itself* is not scoped; holding it grants read access to any queue
entry the caller chooses to query for, at any location, any queue, any patient — confirmed by the
same privilege-table enumeration (no per-location/per-queue variant exists). `Manage Queue Entries`
is the write counterpart and is equally unscoped. **CAN GET QUEUE ENTRIES BE SCOPED TO MATERNITÉ?
NO. TO DAHER MATERNITY VISITS? NO. TO ONLY THE VISIT BEING ENDED? NO.** Materially lower risk than
`Assign Beds`, however: it is read-only, and in Daher's actual operation it will almost never
return anything (no Daher workflow creates queue entries) — worth stating plainly rather than
lumping it with the bed privileges, per this ticket's own instruction not to conflate them.

**A third, independent reproduction of the non-atomic-write hazard, worse than previously
recorded:** the queue-entry-present control (step 4/5, before `Manage Queue Entries` was granted)
returned `403` — but `visit.date_stopped` was **again** durably persisted despite the failure,
while `queue_entry.ended_at` was **not** updated. This is a new, sharper version of the earlier
finding: a partial failure here doesn't just leave ambiguous state, it can leave the Visit "ended"
while its associated queue entry is stranded "still active" — a genuine cross-table inconsistency,
not just an ambiguous single-row write. Reconfirms UI-015's own §8 requirement (always re-fetch
authoritative state after any attempt, success or failure) is not optional hardening — it is load-bearing.

**NARROWER NATIVE MECHANISM: still NOT AVAILABLE, now confirmed at every layer of the cascade,**
not just the first two gates. No module in this chain exposes a "close visit, best-effort cleanup"
variant; no privilege in the system carries type/location/visit scoping.

**§10/§11 — is this intentional design or an implementation gap?** Evidence-based characterization,
not a label chosen for convenience: in every case, the module's own `SaveHandler`/`Validator` calls
its privileged service method **unconditionally**, before determining whether there is any
applicable bed/queue state to act on — bedmanagement's own `handle()` never checks for an existing
assignment before calling the `Assign Beds`+`Edit Admission Locations`-gated method; the queue
validator never checks queue configuration before calling the `Get Queue Entries`-gated method. This
reads as an implementation choice that conflates "run this module's own housekeeping as a side
effect of ending *any* Visit" with "a user is deliberately managing beds/queues," and privilege-gates
the former as strictly as the latter — not a documented, deliberate security boundary (no comment,
naming, or upstream decision record found suggesting the broad requirement was intentional). Neither
module was written by or for Daher; this is upstream reference-application behavior, encountered
as-is on the pinned stack.

**§12 — patch feasibility, characterized only, not built, not recommended for this ticket:**
- **Bedmanagement**: before calling `unAssignBedsInEndedVisit`, first check for an existing bed
  assignment using a lower-privilege read the clinician role *already holds* (`Get Beds`+`Get
  Admission Locations`, part of the approved 9) — call the `Assign Beds`-gated unassign method only
  if that check finds something. Alternative: run the housekeeping call via `Daemon.runInDaemonThread`
  (OpenMRS's own system-context execution mechanism — already relied on elsewhere in this exact
  stack, per the `Daemon.createUser` first-login path this session's own prior tickets characterized),
  bypassing user-privilege enforcement only for this narrow, structurally-tied-to-the-visit cleanup
  step. Manual bed assignment/reassignment through the module's own management screens would be
  completely unaffected — the patch touches only the automatic-cleanup call site.
- **Queue**: identical shape — check for entries using the already-held `Get Queue Entries` privilege
  before deciding whether the `Manage Queue Entries`-gated save path is needed at all, or route the
  cleanup through `Daemon` context.
- **Security impact**: strictly narrower than granting the privileges facility-wide — the patch
  changes only what happens *automatically during visit closure*, not the general-purpose
  bed/queue-management API surface, which stays exactly as protected as it is today.
- **Maintenance cost**: real and ongoing — both are third-party OMODs; this repo already carries
  exactly this class of cost for `oauth2login` (a tracked, tested, upgrade-sensitive patch), so it
  is not unprecedented here, but it is not free, and needs its own CI guard (bytecode/behavioral
  test against the pinned module version, matching the oauth2login patch's own testing pattern) to
  catch drift on every module upgrade.
- Not written, not proposed as a decision here — Option C below characterizes it as a choice for
  Liban, not a recommendation to build now.

**REAL FACILITY CHANGES**: None. Seven additional disposable Keycloak roles/users created and fully
deleted this pass; their matching OpenMRS `role` rows have their privileges stripped but remain as
the same harmless `user_role`-FK-orphaned rows this session has repeatedly left behind (now 9 such
rows total across BLOCKER-10's three passes). One disposable bed (`UI015B-TEST-1`) and one
disposable queue entry per control, all against the throwaway `openmrs_adr017f` clone. Canonical
`Daher Clinician Admission` (9)/`Daher Reception` (3) reconfirmed unchanged.

**Updated technical recommendation**, refining the prior pass now that the true minimum for the
realistic (no-bed, no-queue) case is known precisely: still Option C (or, per this ticket's own
Option C, patching the two module hooks) over Option A — the *quantified* cost of Option A is now
four privileges minimum (`Assign Beds`, `Edit Admission Locations`, `Get Queue Entries`, `Edit
Visits`), two of which (`Assign Beds`, `Edit Admission Locations`) are genuinely broad,
facility-wide, unscoped bed/ward-configuration authority with no relationship to ending a maternity
admission — not a narrow, defensible grant. Patching the two upstream hooks (this ticket's Option C)
would let the clinician role stay at 9 privileges permanently, at the cost of maintaining two small,
CI-tested OMOD patches alongside the existing `oauth2login` one — a real but bounded, precedented
cost, and the one path that keeps least-privilege intact for both the common case and the edge cases
this pass exercised.

> **CORRECTION (UI-015C, 2026-08-27): the paragraph immediately above is wrong and is superseded by
> the "UI-015C follow-up" subsection directly below.** "Patching the two upstream hooks would let
> the clinician role stay at 9 privileges permanently" is false. UI-015B's own cascade table already
> listed `Edit Visits` as required (step 3) but the accompanying recommendation text failed to carry
> that through: `Edit Visits` is enforced by a **hardcoded check inside OpenMRS core**
> (`VisitServiceImpl.saveVisit()`, not `bedmanagement` or `queue`), so patching those two modules
> alone still leaves the canonical clinician unable to end the Visit at all. There are **three**
> independent authorization barriers, not two, and only one of them lives in a patchable third-party
> module pair. See below for the corrected architecture analysis and decision package.

#### UI-015C follow-up (2026-08-27) — the third gate, and a narrow-endpoint architecture candidate

**Ticket:** BLOCKER-10 / UI-015C. Continued from the UI-015B worktree; nothing above discarded,
the correction notice above stands as the record of what changed and why.

**PROVEN — the complete, final authorization map, three independent gates, source line numbers
included:**

canonical clinician (9 privileges) → `POST /visit/{uuid} {"stopDatetime":…}` →
1. `bedmanagement.VisitWithBedPatientAssignmentSaveHandler` — `Assign Beds` + `Edit Admission
   Locations` (both) →
2. `queue.VisitWithQueueEntriesValidator` — `Get Queue Entries` →
3. **`VisitServiceImpl.saveVisit()`, line 176 — a hardcoded, in-body `Context.requirePrivilege
   ("Edit Visits")`, structurally distinct from the interface's own `@Authorized({"Add
   Visits","Edit Visits"}, requireAll=false)` annotation.**

**Gate 3, disassembled in full — the exact bytecode, not inferred:**
```
if (visit.getVisitId() == null) {
    Context.requirePrivilege("Add Visits");   // create path
} else {
    Context.requirePrivilege("Edit Visits");  // update path — unconditional
}
CustomDatatypeUtil.saveAttributesIfNecessary(visit);
dao.saveVisit(visit);
```
This is **not** conditioned on `stopDatetime`, visit type, location, or which property changed —
only on whether the Visit already has a database id. **Even if gates 1 and 2 (the two module
hooks) were entirely removed or patched away, gate 3 alone would still deny the canonical
9-privilege clinician on every single attempt** — proven directly by this bytecode, not assumed.
This is the fact the corrected recommendation above rests on.

**EDIT VISITS EFFECTIVE AUTHORITY — proven by the same bytecode, the strongest available form of
proof:** because the guard only inspects `getVisitId() != null`, it cannot and does not
discriminate by visit type, location, patient, or which field is being changed. A user holding
`Edit Visits` can update **any** already-persisted Visit's `location`, `visitType`, `startDatetime`,
`stopDatetime` — canonical or not, current patient's or another's, active or historical. **EDIT
VISITS SCOPING: fully global, unscoped, confirmed at the source level — the same conclusion the
exhaustive privilege-table enumeration (BLOCKER-10's first pass) already reached for `Assign Beds`.**
**Can OpenMRS grant "may end only Daher Maternity Admission Visits" without granting general Visit
editing? NO** — no per-visit-type, per-location, or per-operation variant of `Edit Visits` exists
anywhere in the privilege table (already exhaustively enumerated) or in this guard's own logic.
**NATIVE NARROW UPDATE AVAILABLE: NO.**

**§4/§5 — a narrow OpenMRS-side operation, feasibility characterized, not built.** OpenMRS
explicitly supports exactly this extension shape: a Daher-owned module (the same mechanism this
repo already uses for `openmrs-config/initializer/*` domains and, more relevantly, the existing
`oauth2login-bridge` patch this repo maintains and tests) can register its own REST controller or
service method. Internally, that operation can call `Daemon.runInDaemonThread(Runnable,
daemonToken)` — OpenMRS's own system-execution-context API, which bypasses `AuthorizationAdvice`'s
privilege checks entirely for code running inside it. This is not a hypothetical mechanism: this
exact codebase already documents reliance on it (`Daemon.createUser`, characterized in the Bug
#24/#25 record as the mechanism behind first-login auto-provisioning). A narrow endpoint built this
way would: (1) require the caller to be authenticated via OpenMRS's normal session (no new login
flow); (2) explicitly check, in Daher's own code, that the caller's roles include `Daher Clinician
Admission` (a plain role-membership check, not a generic privilege grant); (3) accept only a Visit
UUID; (4) authoritatively re-load that Visit from the database and independently verify
`visitType.uuid == 194bf546-…`, `location.uuid == 6538f097-…`, and `stopDatetime == null` — never
trusting anything the client asserts about the target beyond its UUID; (5) refuse (idempotent
no-op, not an error) if the Visit is already ended, and refuse outright (no partial action) if any
of the three checks in step 4 fail; (6) only then enter `Daemon` context to perform the actual
`stopDatetime` write, letting `bedmanagement`'s and `queue`'s existing cleanup run exactly as
already proven in UI-015B (bed auto-released if assigned, queue entry closed if one somehow
exists) — all of it now privilege-satisfied automatically inside the daemon thread, without the
browser session ever holding `Assign Beds`, `Edit Admission Locations`, `Get Queue Entries`,
`Manage Queue Entries`, or `Edit Visits`. **NARROW OPENMRS ENDPOINT FEASIBLE: yes, as a design — not
prototyped or built in this ticket, per its own explicit "no implementation" instruction.**

**§5 — PROPOSED TRUST BOUNDARY and ADR-027 COMPATIBILITY, evaluated against the ADR's own text, not
assumed either way.** ADR-027's decision, quoted: *"No service account, anywhere, ever stands in
for a staff user for a write."* Its rejected alternative was specifically "a read-only BFF using
its own account," rejected because it "would replace each user's own OpenMRS read authorization
with 'whatever the BFF's account can see.'" This candidate introduces **no second authenticating
identity, no new client, no stored credential, and no additional network hop** — the browser still
authenticates to OpenMRS exactly as it does today (the clinician's own OIDC-derived session), and
the elevation happens *inside the same OpenMRS process* for one narrow, server-re-validated
operation, closer in kind to OpenMRS's own core use of `Daemon` for internal system operations than
to a proxying BFF standing between the browser and the backend. **This is a genuine judgment call,
not a clean match either way** — the operation does perform a mutation the caller's own privileges
don't directly authorize, which is philosophically adjacent to the audit-identity concern ADR-027
raised, even though the letter of what ADR-027 rejected (a separate credentialed intermediary) does
not describe this candidate. **Recorded as a new, `OPEN`, not-selected ADR-030** rather than
self-certified as ADR-027-compliant — this is new architectural territory this repo has not
explicitly ruled on, and treating it as automatically pre-approved would be exactly the kind of
"obviously fine" assumption AGENTS.md warns against.

**§6 — AUDIT ATTRIBUTION DESIGN.** Whether OpenMRS's native `changedBy`/`dateChanged` survive being
pre-set on the Visit object before Daemon-context execution **could not be proven without writing
and deploying prototype module code** — outside this ticket's own no-implementation scope, disclosed
as a genuine open question rather than assumed favorable. The design does not depend on the answer:
it specifies a **dedicated, non-clinical `VisitAttributeType`** (e.g. "Admission Closure Initiator")
as the durable, explicit, REST-visible record of who actually triggered the closure and when —
`Get Visit Attribute Types` is already one of the approved nine privileges, so reading it back needs
no new grant. Native `changedBy` attribution, if it does turn out to survive, would be a welcome
secondary confirmation, not the sole record. This is technical audit provenance only — no clinical
field, no diagnosis, no disposition; matches this ticket's own instruction not to invent one.

**§7 — TRANSACTION BOUNDARY / ATOMICITY.** UI-015B proved the current cascade is **not** atomic as
triggered from a plain REST call: `bedmanagement`'s handler (when authorized) commits its own side
effect via a separately-transacted service call *before* a later gate (the `queue` validator, or
`Edit Visits` itself) can still fail the overall request — reproduced three times, each time with
`visit.date_stopped` durably persisted despite a client-visible `403`/`500`. **A narrow endpoint
that opens its own `@Transactional` boundary at its own entry point, before calling into
`VisitService`/`BedManagementService`/`QueueEntryService`, would very likely close this gap** —
Spring's default `PROPAGATION_REQUIRED` semantics mean nested calls join an already-open ambient
transaction rather than committing independently, so a failure anywhere inside would roll back
everything together. **ATOMICITY POSSIBLE: yes, as a design property of a narrow endpoint that
establishes the outermost transaction itself — not proven live in this ticket** (would require
writing the endpoint), but grounded in standard, well-documented Spring transaction propagation
behavior, not speculation. **PARTIAL-STATE RISK if this is not done deliberately**: identical to
what UI-015B already observed — genuinely serious, reproduced three times, not hypothetical.

**§8 — BED RELEASE DESIGN**: no bed → no mutation (already proven, UI-015B); bed exists → released
automatically by the same `bedmanagement` code path, now running under `Daemon` context so it needs
no privilege grant to the browser session; direct manual bed-assignment APIs remain fully protected
by their existing privileges, untouched by this design (the daemon elevation applies only inside
the narrow endpoint's own call, not to the browser's ambient session).

**§9 — QUEUE CLEANUP DESIGN**: no queue entry (the real, current Daher condition) → no mutation
(already proven). If one somehow exists, the endpoint's own transaction (§7) makes "safely close it
transactionally" and "fail closed before ending the Visit" the **same outcome** — either the whole
transaction commits (Visit ended, queue entry closed) or none of it does. "Visit ended, queue
stranded active" — the exact hazard UI-015B found — becomes structurally unreachable once the
operation runs inside one transaction, rather than needing a separate compensating check.

**OPTION A — grant the full privilege set directly.** Now precisely quantified: 4 privileges
minimum (13 total) for the realistic case, or 6 (15 total) if a queue entry can ever exist. Two of
the four are structurally unscoped, facility-wide bed/ward authority. Simplest to build; worst on
least-privilege and blast radius.

**OPTION B — no end-admission feature; keep 9 privileges.** UI-014's board stays read-only;
ended admissions must be handled through the native OpenMRS/O3 UI or an administrator. Zero new
attack surface, zero new maintenance. Costs an operational workflow gap until a decision is made.

**OPTION C — patch OpenMRS core + bedmanagement + queue.** **Corrected from the prior pass: this is
three components, not two** — `VisitServiceImpl.saveVisit()` is pinned OpenMRS **core**, not a
module; patching it means maintaining a fork of a core API class, not just two OMODs, alongside the
existing `oauth2login-bridge` patch. Materially higher upgrade burden than previously stated
(core-class patches are more invasive and more likely to conflict on version bumps than module
patches), and patching core to narrow a hardcoded privilege check is a more consequential platform
modification than adjusting two third-party modules' own housekeeping behavior.

**OPTION D — one narrow Daher-owned OpenMRS module endpoint** (this ticket's own primary
candidate, ADR-030). Least-privilege: browser session never gains any of the four/six privileges.
Auditability: durable via a dedicated `VisitAttribute`, independent of unproven native-field
behavior. Transaction safety: achievable by construction (§7), closing UI-015B's own hazard rather
than merely inheriting it. Maintenance burden: one new small, Daher-owned, fully-controlled module
— arguably *lower* long-term maintenance than Option C's three-component patch set, since Daher
owns 100% of this code rather than tracking drift against three separate upstream components across
every future OpenMRS/module upgrade. Alignment with ADR-027: a genuine judgment call, recorded as
its own OPEN ADR-030 rather than assumed. Testability: fully unit/integration-testable like any
other Daher module code, plus the same disposable-live-proof discipline this project already uses
everywhere else. Attack surface: one new, narrow, server-validated endpoint with a fixed,
non-parameterized target (only ever *this* Visit type at *this* location) — smaller and more
auditable than four/six standing privileges that apply to every Visit in the system.

**SECURITY COMPARISON**: A (broad standing privileges, largest blast radius) > C (a core-class
patch, platform-wide behavioral change, smallest scope of *effect* but highest scope of *what could
go wrong on upgrade*) > D (one narrow, fixed-target, fully Daher-owned endpoint) > B (no new
surface at all, but no feature).
**MAINTENANCE COMPARISON**: B (zero) < D (one small Daher-owned module, no upstream drift risk) <
C (three components across two vendors, ongoing drift risk each upgrade) < A (zero *code*
maintenance, but the standing privilege grant itself is a permanent, unbounded liability that
doesn't show up as a maintenance line item yet never goes away).

**TECHNICAL RECOMMENDATION**: Option D, now that its trust-boundary question has its own ADR-030 to
resolve alongside BLOCKER-10 itself. It is the only option that achieves least-privilege, closes
the transaction-atomicity hazard by construction, and does not require patching either third-party
or core OpenMRS code. Option B remains the safe fallback if Option D's ADR-027-adjacency is judged
unacceptable. Not a decision — Liban's call on both BLOCKER-10 and ADR-030 together, since they are
now linked.

**§13 — SECURITY NEGATIVE CASES for a future Option D implementation, specified now so a later
ticket cannot quietly drop one:** wrong visit type → denied (server-side re-check, §4 step 4);
wrong location → denied (same); already ended → idempotent zero-write, not an error; a patient/Visit
the caller has no other authorized relationship to → the endpoint's own re-check is the only gate
that matters. **Accepted risk, not a design choice:** the endpoint does not consult patient-level
authorization at all — any holder of the `Daher Clinician Admission` role can end any canonical
maternity Visit by UUID, not only ones they are personally caring for. Accepted because this is a
single-ward facility where all clinicians already share the UI-014 board and the Visit UUID is not
guessable/enumerable from outside an authenticated session; flagged here so it is not later
mistaken for an intentional scoping decision. Reception → denied (endpoint's own
role check, independent of the generic privilege system, in addition to the existing "Get Visits"
wall it already hits); missing `Provider` → fail closed, matching ADR-017D/ADR-017F's own existing
fail-closed contract, no provider fabricated; malformed UUID → denied (404/400, never treated as
"nothing to do"); arbitrary update fields → structurally impossible, the endpoint accepts only a
Visit UUID, no request body fields at all; startDatetime/visitType/location modification →
structurally impossible, the endpoint's own internal logic never accepts or applies them; generic
bed or queue operations → structurally impossible, the endpoint exposes no bed/queue parameters or
actions, only the one fixed internal cleanup path already characterized in §8/§9.

**PROPOSED "TERMINER L'ADMISSION" SEMANTICS**, unchanged from the prior two passes, restated
precisely: means only that the canonical active Daher Maternity Admission Visit is ended by
`stopDatetime`, and the patient is therefore no longer operationally listed as actively admitted in
Maternité. Does **not** mean medical discharge, home discharge, clinical outcome, diagnosis,
transfer, postpartum completion, or discharge disposition. No new Encounter, diagnosis,
Observation, form, destination, or clinical field is implied. No existing ADR conflicts with this
narrow reading (checked: ADR-006 partograph, ADR-007 bed seeding, ADR-012 pilot-ready scope, ADR-028/
ADR-029 canonical admission contract — none references discharge semantics at all). **CLINICAL
DECISION REQUIRED: Yes** — presented as the smallest possible human decision, not self-approved.

**REAL FACILITY CHANGES**: None — no code, config, role, or Keycloak change; disposable-stack
investigation only (bytecode disassembly and cross-referencing prior live evidence; no new
disposable roles/users were needed this pass since the definitive answer came from source, not a
new live probe).

**NEW FINDINGS**: (1) The three-gate map is now complete and final — `Edit Visits` is OpenMRS core,
not a module, and unconditional on any existing-Visit update, not just ending one. (2) A concrete,
evaluated Option D design exists, with its own trust-boundary question now recorded as ADR-030.
(3) A narrow endpoint's own transaction boundary would resolve UI-015B's non-atomic-write hazard as
a side effect of correct design, not require separate compensating logic.

> **CORRECTION (UI-015/ADR-030 implementation attempt, 2026-08-27):** Finding (3) above was an
> untested assumption and is **disproven** by live evidence — see the subsection directly below.
> Wrapping the existing `VisitService.saveVisit()` call in `Daemon.runInDaemonThreadAndWait()` does
> **not** provide atomicity by construction. A genuine (non-privilege) mid-cascade failure still
> leaves partial writes durably committed, live-reproduced a fourth time, worse than before: the
> Visit ended, the bed released, but the queue entry was left corrupted (not closed, not restored).

**NEW BLOCKERS**: None new — completes BLOCKER-10's evidence package. ADR-030 is a new, linked,
OPEN decision (not a blocker in its own right — it's part of BLOCKER-10's own option set).

#### UI-015/ADR-030 implementation attempt (2026-08-27) — Option D selected, build attempted, two gates falsified

Liban selected **Option D** (build a narrow Daher-owned OpenMRS module endpoint) and approved the
narrow "Terminer l'admission" semantics (ends `stopDatetime` only — see §14 restated above, still
correct and unaffected by this entry). Per the ticket's own requirement #29 — *"If the design cannot
preserve atomicity or audit attribution safely, STOP rather than weakening the design"* — this pass
built the minimum spike necessary to test the two properties ADR-030's design had assumed rather than
proven, before writing the full production module. Both assumptions failed under live test. No
production module, no UI code, and no test files were written this pass; only a disposable spike
(deleted before this write-up) and this documentation.

**Toolchain feasibility (resolved, positively)**: a full build-deploy-live-prove cycle *is* possible
in this environment — this was the first open question and could have ended the ticket immediately
if false. Confirmed: Docker Hub reachable (`maven:3.9-eclipse-temurin-8` pulled clean), Maven Central
reachable, and — the one that matters — `openmrs.jfrog.io/artifactory/public` (OpenMRS's own Nexus)
reachable and serving the exact pinned `openmrs-api-2.8.7` POM/jar this stack runs
(`openmrs-config/Dockerfile`'s own base image pins `openmrs-reference-application-3-backend:3.7.x`,
confirmed to bundle `openmrs-api-2.8.7.jar` by extracting it from the deployed WAR directly). One
sandbox-specific constraint, consistent with prior tickets' `.scratch_ui015*` workaround: `docker run
-v` bind mounts only resolve real content when the host path is under this repo's own working
directory tree; a mount from `/tmp/claude-1000/.../scratchpad` silently mounts empty. All build/deploy
work this pass therefore happened under a repo-local `.scratch_adr030_spike/` directory, deleted
before this write-up completed (confirmed via `git status --short`).

**Spike module built**: `daherspike` (groupId `org.openmrs.module`), structured on the real OpenMRS
`basicmodule` reference project (api + omod submodules, `maven-parent-openmrs-module:2.2.0`,
`maven-openmrs-plugin` packaging), targeting `openmrs.platform.version=2.8.7`. Contents: a
`DaherSpikeActivator implements ModuleActivator, DaemonTokenAware` that stores the granted
`DaemonToken` in a static holder, and one servlet (`EndAdmissionSpikeServlet`, wired via
`config.xml`'s `<servlet>` element, reachable at `/openmrs/moduleServlet/daherspike/endAdmission`)
that: reads the currently-authenticated caller, runs a `Runnable` via
`Daemon.runInDaemonThreadAndWait(work, token)`, and inside that runnable loads the Visit by UUID,
pre-sets `stopDatetime`/`changedBy`/`dateChanged` to the initiating clinician, optionally corrupts a
linked `queue_entry.sort_weight` to `NULL` first (`induceFail=true`, reproducing the exact UI-015B
fixture-derived failure trigger), then calls `Context.getVisitService().saveVisit(visit)` — i.e. the
same real, unmodified core service path the app already uses, not a hand-rolled substitute. Built with
`maven:3.9-eclipse-temurin-8` against the confirmed-reachable OpenMRS repo; deployed by copying the
built `.omod` into the running `openmrs-adr017f` container's `/openmrs/data/modules/` (the actual
runtime `$OMRS_MODULES_DIR` — confirmed via `startup-init.sh`'s own `OMRS_MODULES_DIR="$OMRS_DATA_DIR/
modules"` line) and restarting the container. Confirmed loaded by a `302` (auth-redirect, not `404`)
from an unauthenticated probe of the servlet path — a `404` would have meant the servlet mapping
never registered.

**Gate 1 — DaemonToken reachability: PASSES.** `Daemon.runInDaemonThread(Runnable, DaemonToken)`,
`org.openmrs.module.DaemonTokenAware`, and `org.openmrs.module.DaemonToken` all exist unchanged in
the pinned `openmrs-api-2.8.7.jar` (confirmed via `javap -p` inside the running container, not
assumed from memory of older OpenMRS versions). Live-confirmed the token is actually granted at
module startup: the servlet's own `daemonTokenPresent=true` line, read from every live call below.

**Gate 2 — audit attribution: FAILS.** Test: as the canonical 9-privilege clinician
(`bug23-clinician1`, user UUID `2c67d291-6482-4f06-9fd9-3e8dc9b672a1`), called the spike endpoint
against a clean canonical Visit (bed-assigned, no queue entry — the realistic case). Result:
`outcome=SAVE_SUCCEEDED`, `after.stopDatetime` set correctly, bed correctly released
(`bed.status` → `AVAILABLE`, `bed_patient_assignment_map.date_stopped` set) — but
**`after.changedBy=A4F30A1B-5EB9-11DF-A648-37A07F9C90FB`**, not the clinician's UUID. That UUID is
OpenMRS's own fixed daemon-user UUID (`Daemon.DAEMON_USER_UUID`, confirmed present as a static field
in the same `javap` dump used for Gate 1). Pre-setting `visit.setChangedBy(initiatingUser)` before the
daemon-context save had no effect — `RequiredDataAdvice`'s save-handler chain overwrites `changedBy`/
`dateChanged` from `Context.getAuthenticatedUser()` at save time regardless of what was already set on
the object, and inside a daemon thread that authenticated user is the daemon user. **This confirms
ADR-030's own stated assumption that normal `changedBy` attribution cannot be trusted to survive a
Daemon-context save — the dedicated non-clinical `VisitAttributeType` audit record ADR-030 already
proposed as a fallback is not optional decoration, it is required**, and its own durability under the
same daemon-context save has not yet been separately tested.

**Gate 3 — atomicity: FAILS, live-reproduced a fourth time.** Test: a second fixture, same shape as
UI-015B's worst case — canonical Visit with both a bed assignment (`bed_patient_assignment_map`) and
an active `queue_entry` linked by `visit_id`. Before state (direct SQL): `bed.status=OCCUPIED`,
`bed_patient_assignment_map.date_stopped=NULL`, `queue_entry.sort_weight=0.5, ended_at=NULL,
voided=0`, `visit.date_stopped=NULL`. Called the spike endpoint with `induceFail=true` (nulls
`queue_entry.sort_weight` server-side, inside the daemon thread, immediately before calling
`saveVisit()` — the exact defect class UI-015B already proved throws a genuine, non-privilege
`PropertyValueException` from the queue module's own save path). Result:
`outcome=SAVE_THREW`, `exception=javax.persistence.PersistenceException:
org.hibernate.PropertyValueException: not-null property references a null or transient value :
org.openmrs.module.queue.model.QueueEntry.sortWeight` — and yet, read back immediately after:

| Table | Field | Before | After (post-exception) |
|---|---|---|---|
| `visit` | `date_stopped` | `NULL` | **`2026-08-27 12:38:44`** (committed) |
| `bed` | `status` | `OCCUPIED` | **`AVAILABLE`** (committed) |
| `bed_patient_assignment_map` | `date_stopped` | `NULL` | **`2026-08-27 12:38:44`** (committed) |
| `queue_entry` | `sort_weight` / `ended_at` / `voided` | `0.5` / `NULL` / `0` | `NULL` / `NULL` / `0` (left corrupted, neither closed nor restored) |

This is worse than UI-015A/B's own reproductions of the same class of hazard: there, at least the
queue entry stayed in a coherent (if stale) state. Here the queue entry is left with an invalid
`NULL` `sort_weight` — a state the application's own normal write paths cannot produce and would not
expect to read. `Daemon.runInDaemonThreadAndWait()` was thought to remove the specific *trigger* that
caused UI-015A/B's version of this hazard (a privilege-check exception thrown by
`Context.requirePrivilege()` partway through the call) — and it does; nothing in this run failed for
privilege reasons. But it does not touch the deeper cause: `VisitService.saveVisit()`'s own
module-hook cascade (`RequiredDataAdvice`'s `SaveHandler`s/`Validator`s for bedmanagement and queue)
does not commit as a single transaction with the core Visit save, regardless of which user or thread
invokes it. The exact mechanism (nested `@Transactional` service calls each committing independently;
Hibernate auto-flush ordering; AOP advisor ordering placing the transactional interceptor differently
than assumed) was not isolated further — the empirical fact was sufficient to answer the gate, and
requirement #29 says stop at that point rather than build a compensating design on top of an unproven
theory of the cause.

**What this means for ADR-030/Option D**: the specific mechanism ADR-030 named — "the safest supported
Daemon/system mechanism" applied to the existing `VisitService.saveVisit()` call — does not deliver
either property (atomicity, attribution) it was selected to provide. This does **not** mean Option D
itself (a narrow Daher-owned module endpoint) is wrong; it means the *specific* implementation ADR-030
sketched is insufficient as written, and a different mechanism inside the same narrow-endpoint
architecture would be needed — for example, constructing the write as the module's own explicit
transaction and deliberately orchestrating the Visit/bed/queue writes itself rather than delegating to
`saveVisit()`'s AOP-attached hook chain, and proving that alternative separately. That is a **scope
change from what Liban approved** (the approved read was "wrap the existing mechanism in Daemon,"
not "replace it with a new one"), so it is not something to design or build without stopping here
first, per requirement #29's own instruction and this repo's standing rule against continuing past a
clinical/scope-surface decision point on an assumption.

**Live proof method, for reproducibility**: canonical clinician (`bug23-clinician1`) and Reception
(`bug23-reception`) OIDC sessions against the disposable `openmrs-adr017f`/`kc-ui009`/`mariadb-ui009`
stack, same pattern as UI-014/UI-015/A/B/C; patients/visits/encounters created through the real REST
API (idgen-generated identifiers, not hand-typed); bed/queue fixtures constructed via direct SQL
against `mariadb-ui009` (the same technique UI-015B used, since assigning a bed or creating a queue
entry through the real API requires privileges this investigation is specifically about not granting).

**Disposable-environment residue left behind** (harmless, consistent with this repo's established
practice of not chasing FK-constrained cleanup — see UI-015B's identical note): synthetic patients
105/106, visits 30/31, encounters 24/25; `bed_patient_assignment_map` rows 2 (clean, fully closed) and
3 (from the induced-failure run — `date_stopped` set even though the operation "failed"); `queue_entry`
row 4, permanently left with `sort_weight=NULL` from the induced-failure run. `bed` id 1 is back to
`AVAILABLE`. None of this touches real facility data — the same disposable stack UI-014/UI-015/A/B/C
already used.

**REAL FACILITY CHANGES**: None. **Application repo changes**: `PROJECT_STATE.md` and `DECISIONS.md`
only — no `ui/`, no new module committed anywhere, no `docker-compose.yml`/`Dockerfile` change. The
`daherspike` module and its `.scratch_adr030_spike/` source were deleted before this entry was
finished being written; confirmed via `git status --short`.

**NEW FINDINGS (this entry)**: (1) The build/deploy/live-test toolchain for a real OpenMRS module
works end-to-end in this environment — this was unproven before this pass and is no longer a risk to
flag in a future attempt. (2) ADR-030's specific proposed mechanism (Daemon-wrapped `saveVisit()`)
fails both properties it was selected for. (3) The audit-attribution fallback ADR-030 already named
(a dedicated `VisitAttributeType`) is now confirmed necessary, not optional — but its own durability
under a daemon-context save is untested. (4) A viable path forward likely exists (module-owned
transaction, explicit orchestration instead of delegating to `saveVisit()`) but is unbuilt, unproven,
and represents a scope change from what was approved — Liban's decision, not this session's.

**NEW BLOCKERS**: None new in the sense of a new numbered blocker — this is additional evidence inside
BLOCKER-10, which stays open. The practical effect is the same as a blocker: UI-015 cannot proceed
until Liban chooses how (or whether) to proceed past this finding.

**BLOCKER-10 STATUS**: 🛑 Still OPEN. Not closed — closure was explicitly conditional on full proof,
which this pass did not achieve; it achieved the opposite (a clean falsification of the approved
mechanism).

**ADR-030 STATUS**: SELECTED (Option D, by Liban, this ticket) but **NOT IMPLEMENTED** — the named
mechanism is proven insufficient; implementation is blocked on a further decision, not merely
unstarted work.

**UI-015 STATUS**: Not started. No frontend code exists for this ticket.

**READY TO COMMIT**: No. **HUMAN DECISION REQUIRED**: Yes — whether to pursue the module-owned-
transaction alternative sketched above (a scope change from the approved mechanism), or a different
option entirely, or to reconsider whether this operation belongs in an OpenMRS module at all given two
of its three required properties have now failed once already.

#### UI-015D follow-up (2026-08-27/28) — the module-owned-transaction alternative, live-proven

The prior entry left one specific, narrow question open: whether wrapping the *existing*
`VisitService.saveVisit()` call inside a **module-owned outer transaction** (rather than replacing it
with hand-rolled DAO orchestration) closes the atomicity and audit gaps. This pass answered that
question directly, by building and live-testing exactly that mechanism — a second disposable spike,
deleted before this entry, alongside the first — against the same disposable stack.

**Root cause, proven from source (not re-guessed from the live symptom):**
`applicationContext-service.xml` (pinned openmrs-core 2.8.7) wires every core service, and
bedmanagement wires its own `bedManagementService` identically, as a
`org.springframework.transaction.interceptor.TransactionProxyFactoryBean` with
`preInterceptors: serviceInterceptors` — a list including `requiredDataInterceptor`
(`RequiredDataAdvice`, a `MethodBeforeAdvice`). Spring applies `preInterceptors` **before** the
`TransactionInterceptor` in the advice chain — i.e. outside/before the transaction the proxy itself
would open. `RequiredDataAdvice.before()` (source, lines ~128-129) dispatches **all** registered
`SaveHandler<Visit>` beans first, then runs `ValidateUtil.validate()`. So for
`visitService.saveVisit(visit)`: bedmanagement's `VisitWithBedPatientAssignmentSaveHandler` and
queue's `VisitWithQueueEntriesSaveHandler` both run *before* `visitService`'s own transaction has
even opened. bedmanagement's handler calls `BedManagementServiceImpl.unAssignBedsInEndedVisit()` —
itself `@Transactional` on its own separately-proxied bean — which, with no ambient transaction yet
active, opens and commits its own independent transaction. `Context.openSession()`/`closeSession()`
(Context.java's own documented "unit of work" contract) bind **one** Hibernate session to the thread
for the whole daemon runnable, shared across all these nested calls; the spike's own in-memory
`visit.setStopDatetime()` mutation, still pending on that shared session, gets swept up by Hibernate's
`FlushMode.AUTO` auto-flush the moment bedmanagement's nested transaction issues its first query —
durably persisting the Visit's `stopDatetime` **as a side effect of bedmanagement's commit**, before
`VisitServiceImpl.saveVisit()`'s own body (the code that was "supposed" to write it) ever runs. Queue's
handler then fails in its own, later, independent transaction, rolling back only itself. Full
transaction-map answers:

| Question | Answer |
|---|---|
| Do bedmanagement/queue/core share one DB transaction? | **No** — each is independently proxied and opens its own when none is ambient |
| Does any use REQUIRES_NEW? | No (source-confirmed, all default REQUIRED) — behaves identically here because nothing is ambient when each starts |
| Do any commit/flush independently? | **Yes** — live-proven; bedmanagement commits before core's own save path ever executes |
| Do any catch exceptions without marking rollback? | No swallowed exception found — the issue is structural transaction independence, not a suppressed signal |
| Does Daemon execution create a new Spring transaction? | **No** — Daemon only changes the authenticated Context user; transaction behavior is identical on a Daemon thread and a normal request thread |

**The fix, live-proven:** register a module-owned service (`DaherMaternityAdmissionService`), wired
via the **exact same** `TransactionProxyFactoryBean` pattern core and bedmanagement already use
(`preInterceptors: serviceInterceptors`, shared `transactionManager`/`transactionAttributeSource`
beans), and call it — not `visitService.saveVisit()` directly — as the **outermost** entry point on
the daemon thread. Spring proxying confirmed empirically, not assumed: the spike logged
`TransactionSynchronizationManager.isActualTransactionActive()` as `true` on entry to the service
method and again immediately before calling `saveVisit()` — the module's own proxy opened the
transaction before the method body ran, and it was still the same active transaction at the point
`saveVisit()` was invoked. Because a transaction is now already ambient when bedmanagement's/queue's
own nested `@Transactional` calls execute, Spring's default REQUIRED propagation makes them **join**
this one transaction instead of opening independent ones — so a later failure rolls all of them back
together. **No DAO-level bypass of `saveVisit()`'s hook chain was needed or built** — bedmanagement's
and queue's own SaveHandlers/Validators still run, still do their normal work, just inside one shared
boundary instead of several independent ones. This is simpler than the DAO-bypass alternative this
same document floated after the first spike; that alternative is no longer necessary and was not
pursued further.

**Rollback negative control (the critical test), live-run against the exact UI-015B worst case**
(canonical Visit, live bed assignment, live queue entry, same `QueueEntry.sortWeight`-null failure
trigger UI-015B/the first spike both used): before state — `bed.status=OCCUPIED`,
`bed_patient_assignment_map.date_stopped=NULL`, `queue_entry.sort_weight=0.5`, `visit.date_stopped=NULL`.
Called with `induceFail=true`. Result: `outcome=SAVE_THREW`,
`javax.persistence.PersistenceException: ... QueueEntry.sortWeight` (same exception as before) — and
this time, read back immediately after:

| Table | Field | Before | After (post-exception) |
|---|---|---|---|
| `visit` | `date_stopped` | `NULL` | `NULL` — **unchanged** |
| `bed` | `status` | `OCCUPIED` | `OCCUPIED` — **unchanged** |
| `bed_patient_assignment_map` | `date_stopped` | `NULL` | `NULL` — **unchanged** |
| `queue_entry` | `sort_weight` | `0.5` | `0.5` — **unchanged, including the deliberate corruption SQL itself** |

Every table, including the raw `executeSQL()` call used to induce the failure, rolled back together —
zero partial state, a strictly better result than the checklist required (it also proves raw SQL
executed inside the service method shares the same transaction boundary as the ORM writes).

**Success control, live-run twice:** (1) bed-assigned + queue-present Visit — result:
`Visit.stopDatetime` set, `bed.status→AVAILABLE`, `bed_patient_assignment_map.date_stopped` set,
`queue_entry.ended_at` set (`voided=0`, matching the queue module's own normal "closed" shape), linked
`Encounter` unchanged (`voided=0`, `encounterProviders` untouched — never queried by this operation at
all). (2) no-bed, no-queue (the realistic case) — same clean `Visit.stopDatetime` result, no unrelated
mutation attempted. **Idempotence, live-confirmed**: calling the same operation again on an
already-ended Visit returned `ALREADY_ENDED` with the exact same `stopDatetime` and audit-attribute
timestamp as the first call — zero additional writes, not merely "no error."

**Audit design — solved, live-proven, not just designed.** A disposable, non-clinical
`VisitAttributeType` ("Daher Admission Audit", `FreeTextDatatype`, created via SQL for this spike only)
holding a small technical record (`operationType=END_DAHER_MATERNITY_ADMISSION;
initiatingUserUuid=<uuid>;timestamp=<epoch ms>;result=SUCCESS`) was added to the Visit inside the same
service method, via `visit.addAttribute(...)` before `saveVisit()` — no separate write, no separate
transaction. Confirmed: (1) on success, the attribute persists with the **true initiating clinician's
UUID** (`2c67d291-...`), not the daemon user's — solving the attribution gap the first spike found;
(2) on the induced failure above, `after.auditAttributeCount=0` — the audit record rolled back
together with the clinical state, satisfying §9's explicit requirement ("never record a successful
[operation] when clinical state rolled back") without any extra compensating logic. Native
`Visit.changedBy` is **still** overwritten with the daemon user's UUID under this mechanism too
(re-confirmed, unchanged from the first spike) — transaction wrapping does not touch that behavior,
since it is tied to `Context.getAuthenticatedUser()` at save time, not to transaction boundaries. This
was always ADR-030's anticipated fallback position (native `changedBy` as a secondary check only, the
dedicated attribute as the primary record) — now empirically confirmed rather than assumed.

**Split-context feasibility (§10): not pursued, and not recommended.** Preserving native
`Visit.changedBy` as the real clinician while elevating only specific privileged sub-calls would
require alternating `Context`'s authenticated user mid-transaction on a single thread — there is no
supported OpenMRS API for "become another user for one call, then become the original user again";
`Daemon.runInDaemonThread()` elevates the *whole* thread for its *whole* runnable by design, and
manually swapping `UserContext` objects mid-method is exactly the "unsafe Context impersonation" this
ticket says not to invent. Since the `VisitAttributeType` audit record above already solves
attribution without touching authentication state at all, there is no remaining reason to pursue this.

**Preflight (§8): not required, kept as an optimization, not a substitute.** With genuine rollback now
proven, a late-discovered failure (e.g. the same `sortWeight` defect) no longer requires preflighting
to stay safe — it already rolls back cleanly. A preflight pass (checking bed/queue/Visit state before
mutating) would still be worth doing in the real module purely to give the clinician a clean,
immediate error instead of a round trip through Daemon that's guaranteed to roll back, but it is not
load-bearing for correctness the way it would have had to be without the transaction fix.

**Not tested this pass (explicitly out of scope for a transaction/audit spike, required before
production):** the security boundary (§11 — this spike has no role/Provider check at all; Daemon
bypasses privilege checks universally, so as built today Reception could call it too — the production
module must add its own `Provider`+`Daher Clinician Admission` check, independent of the generic
privilege system, exactly as ADR-030 already specified); true **concurrent** race behavior (§12 — only
sequential double-invocation was tested; two genuinely simultaneous requests were not — InnoDB/MariaDB
row-level locking on the `UPDATE visit` statement should serialize them, with the second reading
already-ended state once the first's transaction commits, but this is an expectation carried over from
standard transaction-isolation behavior, not something this pass measured directly); the idempotency-
key / ambiguous-HTTP-response design (§13 — a client-side reconciliation pattern, independent of the
server change validated here).

**Real facility changes**: None. **Disposable-environment residue**: a fifth synthetic
`VisitAttributeType` row ("Daher Admission Audit", `cccccccc-0099-0099-0099-000000000001`) alongside
the prior tickets' leftover synthetic patients/visits/encounters/bed rows — all harmless, matching this
repo's established practice for this disposable stack. One operational note for whoever next restarts
this container: **two OpenMRS modules must never declare the same `<servlet-name>` in `config.xml`** —
this pass's own two spike modules briefly collided on `endAdmission`, which broke Spring context
refresh for the *entire* instance (not just the colliding modules) until the older, superseded module
was removed; a stray `--` inside an XML comment in a `moduleApplicationContext.xml` had the same
whole-instance blast radius earlier in this pass (invalid XML, `SAXParseException`, same
"Unable to refresh the spring application context" cascade). Neither is specific to this ticket's
subject matter, but both are worth remembering for any future module work against this stack.

**OPTION D STATUS: VIABLE.** Checklist from §14, evaluated against this pass's live evidence:

| Requirement | Status |
|---|---|
| Genuine rollback on late failure | ✅ Live-proven, zero partial state |
| No partial Visit/bed/queue state | ✅ Live-proven |
| Durable initiator audit | ✅ Live-proven (`VisitAttributeType` record; native `changedBy` still fails, as anticipated — not the primary mechanism) |
| Clinician keeps 9 privileges | ✅ Unaffected — mechanism grants nothing to the browser session |
| Reception denied | ⚠️ Not yet built — spike has no role check; required for production, not evidence of infeasibility |
| Generic Visit/bed/queue mutation stays unavailable | ✅ Unaffected — no new privilege granted anywhere |
| Race behavior safe or design proven | ⚠️ Sequential idempotence proven; true concurrency not measured |
| Maintenance burden reasonable | ✅ Same `TransactionProxyFactoryBean` pattern already used throughout this codebase and upstream OpenMRS/bedmanagement/queue — no novel plumbing |

Every gate that was actually about the disproven properties (atomicity, audit) is now closed. The two
⚠️ items are unbuilt/unmeasured, not failed — they are within the normal scope of turning a proven
mechanism into the production module, not open feasibility questions.

**ADR-030 STATUS**: SELECTED, mechanism **REVISED AND RE-VALIDATED** — see updated `DECISIONS.md`
entry. Still **NOT IMPLEMENTED** (no production module exists).

**BLOCKER-10 STATUS**: 🛑 Still OPEN — the underlying question ("how does a clinician safely end an
admission") is answered in the sense that a viable mechanism now exists and is evidenced, but nothing
is built, tested to production standard, or deployed. Closure remains conditional on the full
UI-015 implementation pass (module + UI + the 25 specified tests + live proof), not on this
architecture spike.

**UI-015 STATUS**: Not started. No frontend code exists.

**READY TO IMPLEMENT**: Yes — the architecture question this ticket was scoped to answer is answered,
with live evidence, not assumption. **READY TO COMMIT**: No. **HUMAN DECISION REQUIRED**: Yes — not
whether Option D is viable (it now is, evidenced), but whether to proceed straight to the full UI-015
implementation pass on this revised mechanism, given it is a real, if small, change from what was
approved in the prior ticket (module-owned outer transaction wrapping `saveVisit()`, rather than a bare
Daemon wrapper around it).

---

### UI-015E follow-up (2026-08-28) — production module built, live-proven; UI/tests not yet started

**Ticket:** UI-015E. *"Proceed with the production implementation... Use the revised, proven
module-owned outer-transaction mechanism from UI-015D. Do NOT go back to broad clinician privileges.
Do NOT commit or push."* Continued from the UI-015D worktree; nothing above discarded.

The production module (`openmrs-config/modules/daherendadmission/`) was built on UI-015D's proven
mechanism and live-proven end to end, first via `docker cp` onto the existing disposable image, then
re-proven in full against an image rebuilt from the edited `Dockerfile` (module compiled from
committed source in a new `daherendadmission-build` stage; CSRF fix applied at build time — see
below). Full account, including the exact evidence for each item below, is in `DECISIONS.md`'s
ADR-030 "Production module built and live-proven; UI/tests not yet started (UI-015E, 2026-08-28)"
subsection. Summary:

- **Two genuine bugs found and fixed**, neither present in UI-015D's narrower spike test surface:
  (1) `req.getPathInfo()` under OpenMRS's module-servlet dispatcher includes the
  `/{moduleId}/{servletName}` prefix, not just the trailing path — the servlet's regex was matching
  nothing, failing every call with `400 MALFORMED_REQUEST`, found via a temporary diagnostic response
  body (reverted, never committed). (2) The missing-Provider fail-closed check needs `Get Providers`,
  which the canonical clinician role deliberately does not hold — checking it in the servlet's
  un-elevated request thread threw `APIAuthenticationException` on every call, including the
  legitimately authorized clinician's. Fixed by moving the entire authorization check
  (`isAuthorizedInitiator`) inside the daemon-elevated transactional service call itself, as its first
  step, keyed only on `initiatingUserUuid` captured by the servlet before elevation — never on
  `Context.getAuthenticatedUser()` inside the daemon thread, which is the daemon user, not the caller.
- **Methodology trap recorded**: a missing-Provider negative control cannot be built from a DB user
  with no `provider` row — ADR-017E2 activates one at login for any Keycloak identity holding the
  exact `Provider` client role, so an idle synthetic user just hadn't logged in yet (confirmed live:
  logging in as one of the `bug25-*` leftover users created a real Provider on the spot). The valid
  control requires a Keycloak identity holding `Daher Clinician Admission` but explicitly **not**
  holding `Provider` (`ui015e-missing-provider`) — confirmed `403 FORBIDDEN`, no Provider fabricated.
- **Rollback proof**, without touching production code to induce failure (avoiding the earlier
  spike's own rebuild/un-rebuild trap): a `BEFORE UPDATE` trigger on `queue_entry` (`SIGNAL SQLSTATE`
  when `ended_at` transitions from null), installed/removed by plain SQL only. Before/after snapshot
  across Visit, bed, bed-assignment, queue, and audit-attribute row count: **byte-identical**, `500
  OPERATION_FAILED`. Trigger removed, identical fixture retried, succeeded cleanly — confirming the
  500 was genuinely the induced failure.
- **Concurrency proof** (new relative to UI-015D, which only tested sequential double-invocation):
  two distinct clinicians, both fully OIDC-authenticated before either request fires, released
  simultaneously via a `threading.Barrier` so the pessimistic lock is genuinely contended. Result:
  exactly one `ENDED`, one `ALREADY_ENDED`, and **exactly one** audit `visit_attribute` row — the row
  count, not just the HTTP responses, is what proves the lock serialized the two transactions.
- **CSRFGuard fix, recorded as its own security-surface change, not a build footnote**: OWASP
  CSRFGuard's allowlist (`WEB-INF/csrfguard.properties`, baked inside `openmrs.war`) rejected POSTs to
  the new endpoint by default; no config-only route exists (the file lives inside the WAR; the config-
  overlay mechanism is disabled by default and still classpath-internal if enabled). Fixed by patching
  `/openmrs/distribution/openmrs_core/openmrs.war` at Docker build time (the path Tomcat's own
  `startup.sh` re-copies into `webapps/` on every boot — confirmed a live `sed` patch of the exploded
  webapp does *not* survive a restart, which is why the build-time fix was necessary) — appends one
  unprotected-path line, using the JDK's own `jar` tool already on `PATH` in the base image. This
  measurably narrows CSRFGuard's coverage for this one path prefix; the endpoint's own authorization
  and same-origin cookie auth are what still gate it, alongside the existing `X-Daher-App-Request`
  convention (ADR-027/UI-011).
- **Rebuilt-image re-proof**: every control above (success with/without bed+queue, idempotence,
  malformed UUID, Reception denial, missing-Provider denial, generic-mutation denial, rollback,
  concurrency) was re-run against the image built from the edited `Dockerfile` and passed identically.
  Two disclosed gaps: the rebuilt image was run against the already-migrated `openmrs_adr017f`
  database, not a from-scratch one (a fresh-DB boot was started and abandoned after ~15 minutes of
  unrelated `stockmanagement` Liquibase changesets, on time grounds); and the container was started
  via a bespoke `docker run`, not `docker compose up` (the new `attributetypes` compose mount was
  verified for path equivalence, not by invoking compose itself).
- **One direct-instrumentation gap, disclosed**: UI-015D's
  `TransactionSynchronizationManager.isActualTransactionActive()` logging was not re-run against this
  production class — the rollback proof above is strong indirect evidence of the same transaction
  boundary (a broken one would have left the Visit/bed mutation committed despite the queue failure),
  but it is not the same as direct instrumentation on this exact binary.

**NOT yet built or run this pass**: any UI-015 frontend code (PatientWorkspace integration,
confirmation dialog, French copy, the 8 required UI states); the ~30+ specified automated tests
(module-side JUnit — authorization/validation/transaction/bed/queue/history/concurrency/security —
and UI-side Vitest/RTL); the full verification suite (lint, typecheck, build, secret scan, static
guards, `docker compose config`, `git diff --check`). `.gitignore` gained
`openmrs-config/modules/*/target/` and `openmrs-config/modules/*/*/target/` (root-owned Maven build
output). A CI static-guard step was added earlier (UI-015D/E) preventing the `--`-in-XML-comment bug
— found three separate times across this ticket family, including twice in this production module
before being caught — from recurring silently.

**Frontend, module tests, and full verification, done this same pass**: `EndAdmissionPanel.tsx` +
`useEndAdmission.ts` + `client.ts`'s `endAdmission()`, wired into `AdmissionPanel`'s existing
`COMPLETE` branch, following this codebase's own established conventions exactly (in-flight ref guard,
`ApiError` vocabulary — no new error code needed, `ErrorState`, unconditional post-mutation
reconciliation via the caller's own `refresh()`, no locally-invented display state). UI-014 needed no
integration code — it already refetches authoritatively on every mount. **213/213 UI tests passing**
(195 pre-existing + 18 new), `tsc -b`/`oxlint`/`vite build`/bundle-secret-scan all clean. Module-side:
18 new plain-JUnit tests (`EndMaternityAdmissionServletTest`), deliberately scoped to what a
context-free unit test can guard (path-parsing regex, outcome→response mapping) since neither of this
module's two real bugs would have been caught by an H2 context-sensitive suite — transaction/bed/
queue/concurrency stay covered by live proof, not JUnit. `mvn test`: **18/18 passing** (a manual step;
the Docker build still skips tests, matching `oauth2login-build`'s own existing behavior). `git diff
--check` found one genuine one-time defect (a mixed space/tab XML indent, fixed) and one recurring
toolchain characteristic, not a defect: `maven-parent-openmrs-module`'s inherited spotless/license
plugins write a tab on every blank line inside a Java class body on **every** `mvn test`/`mvn package`
run, which `git diff --check` reports as trailing whitespace. The working tree was stripped clean as
the final step of this pass, but any future Maven build against this module will reintroduce it —
recorded in `DECISIONS.md` so the next person doesn't mistake it for something they broke. The
`--`-XML-comment CI guard's
`git ls-files "openmrs-config/**/*.xml"` pathspec was confirmed to actually match all 5 of the
module's XML files (via `--others --exclude-standard`, since they're still untracked) — it will fire
correctly once committed, though no actual GitHub Actions run was triggered this session.
`docker compose config` is clean. Full account in `DECISIONS.md`'s ADR-030 "Frontend, module tests,
and full verification (UI-015E continued, 2026-08-28)" subsection, including the honest disclosure
that the original ticket's "8 required UI states" list could not be recovered after context
compaction — 6 states were built and reasoned through fresh (idle, Provider-missing, confirming,
pending, error-by-code, parent-driven disappearance), not verified against the lost original count.
No browser smoke test was possible (no browser automation tool in this environment); the React
Testing Library suite (real DOM, real events, real focus assertions) is what stands in for it.

**ADR-030 STATUS**: `SELECTED DIRECTION — MECHANISM PROVEN, IMPLEMENTATION IN PROGRESS`. Not
`IMPLEMENTED` — that status is Liban's call to make, not something this pass can self-certify, even
with the module, frontend, and both test suites now complete and passing.

**BLOCKER-10 STATUS**: 🛑 Still OPEN — closure is Liban's decision, not automatic on all checks
passing. The backend/module mechanism and the UI-015 frontend are both now built and proven
(live rollback/concurrency proof, 213 UI tests, 18 module tests, full lint/typecheck/build/secret-scan
clean); `CURRENT_STATE.md` has not yet been updated to reflect any of this.

**READY TO COMMIT**: No, per this ticket's own explicit instruction — regardless of outcome.

---

### UI-015F closure pass (2026-08-28) — the eight required UI states rebuilt exactly; all mandatory gates re-verified; BLOCKER-10 closes

**Ticket:** UI-015F. *"This is a CLOSURE pass. Do NOT redesign ADR-030... Your goal is to resolve
the disclosed proof gaps, re-run the final safety evidence, and determine whether UI-015 / ADR-030 /
BLOCKER-10 can close."* No architecture change; no privilege change (`git diff -- openmrs-config/initializer/roles/`
empty). No module Java changed this pass — every live re-run below is against the exact same
production-built image (`ui015e-rebuild`) already in place, not a new build.

**The eight required UI states, exactly as specified**, were reconstructed (the previous pass's own
list could not be recovered after context compaction) and the frontend rebuilt against them:
ACTIVE, CONFIRMATION, SUBMITTING, ENDED, ALREADY_ENDED, AMBIGUOUS, UNAVAILABLE, ERROR — a closed
TypeScript union (`EndAdmissionState` in `useEndAdmission.ts`), one explicit, named test per state in
`EndAdmissionPanel.test.tsx`. The load-bearing new logic is the AMBIGUOUS-write resolution (§13):
`useAdmission`'s `refresh()` was widened to return the resolved state (not just set it), so an
ambiguous write can be reconciled against an authoritative re-read and land on `ENDED` (Visit no
longer active — the response was lost, not the commit), a retryable `ERROR` (Visit still definitely
active — the mutation did not go through), or stay `AMBIGUOUS` (the reconciliation read itself
failed) — the mutation itself is never resent as part of this, only the read. `ERROR`'s own retry
action, by contrast, does resubmit directly (an explicit human click, not the "blind retry" §13
forbids) — this asymmetry is the single most important behavioral change this pass made.
`ENDED`/`ALREADY_ENDED` reconciliation is deferred to an explicit "Fermer" dismissal rather than
firing automatically on success, so the terminal message is never raced away before it can be
observed (by a user or a test) — matching the existing `AMBIGUOUS`-state "Vérifier à nouveau"
convention already established in `AdmissionPanel`.

**Tests**: 15 new `useEndAdmission.test.ts`, 13 rewritten `EndAdmissionPanel.test.tsx` (one per
state), 12 new `client.test.ts` `endAdmission` tests — this last group is §13's fault-injection
requirement done at the actual `fetch` boundary (mocked timeouts, rejections, unparsable bodies,
every documented HTTP status), the honest way to cover "the server committed but the response was
lost," which a live container cannot cleanly reproduce. **236/236 UI tests passing** (up from 213),
`tsc -b`/`oxlint`/`vite build`/secret-scan clean. Module tests (18) were not re-run via Maven this
pass since no Java changed — re-running would only re-dirty the tree via the known spotless
whitespace behavior for no verification gain; the source was confirmed still carrying all 18 `@Test`
methods.

**Every live proof re-run against the unchanged production image, with exact recorded values** (not
inferred from exception text) — full detail in `DECISIONS.md`'s ADR-030 "Closure pass (UI-015F,
2026-08-28)" subsection:
- **§4 authorization**: privileges *enumerated* via `role_privilege`, not name-checked — exactly 9 for
  the clinician, exactly 3 for Reception, zero roster changes. Clinician allowed; Reception denied;
  missing-Provider denied; clinician still denied for generic Visit edit, generic queue mutation, and
  — a new, more precise control this pass — a direct bed-reassignment attempt, isolating exactly
  `Privileges required: Assign Beds` as the denial reason, not just a generic 403.
- **§5 rollback**: byte-identical before/after across Visit/bed/bed-assignment/queue/Encounter/
  `encounterProviders`/audit-row-count, same external-trigger mechanism as before (no code change to
  induce failure).
- **§6 success**, three cases (A: no bed/queue, B: bed only, C: bed+queue), each with exact recorded
  values for Visit/bed/queue/Encounter/`encounterProviders`/audit attribution.
- **§7 concurrency**: two real clinicians behind a `threading.Barrier`, real result (not unit-test
  behavior) — exactly one `ENDED`, one `ALREADY_ENDED`, one audit row. That original fixture had no
  bed and no queue attached.
- **§7.B — bed+queue-under-contention, closed 2026-08-28 (UI-015 micro-closure).** A fresh disposable
  admission with a genuine assigned bed and a genuine open queue entry (zero audit rows initially) was
  closed by two distinct real clinicians (`bug23-clinician1`, `bug23-clinician2`) behind a
  `threading.Barrier(2)` against the same active Visit — `ENDED`/`ALREADY_ENDED`, identical
  `stopDatetime`. Direct DB read after: bed released (`OCCUPIED`→`AVAILABLE`) with exactly one
  cleanup row (no duplicate), queue entry closed exactly once (no duplicate), Visit/Encounter
  otherwise unchanged, exactly one audit row naming the real winning clinician. A further sequential
  call produced zero new writes. Bed+queue cleanup racing two simultaneous closers is now proven, not
  untested.
- **§8 idempotence**: a further call after closure — `ALREADY_ENDED`, zero new mutation, zero
  duplicate audit row.
- **§9 audit**: native `changed_by`=`daemon` reconfirmed on every case; the dedicated
  `VisitAttributeType` (id 8, exact UUID/datatype/cardinality) correctly attributes the real
  clinician, commits only on success, rolls back on failure; no PHI/token/credential in the stored
  value; the clinical UI never requests or renders it.
- **§10 provisioning**: a full container restart against the same already-migrated database
  reconfirmed the audit type's row count stayed exactly 1 (idempotent second pass). The
  genuinely-fresh-database attempt used a **real controlled pair** this pass (the current image vs.
  `bug25-final`, a pre-ADR-030 image, both against independently fresh empty databases): both hit
  the identical `stockmanagement` Liquibase changeset, at the identical ID, with the identical
  53117ms duration, confirmed via each container's own log. Since this module carries zero Liquibase
  changesets of its own, there is no mechanism by which it could be contributing to this shared
  bottleneck, and the controlled pair confirms it empirically, not just structurally. **Deferred to
  DH-017** as evidence debt (ADR-029 precedent) — not a module-specific bootstrap failure, the one
  thing that would have stayed blocking.
- **§11 CSRF/security boundary**: the one unprotected-path line reconfirmed present and, via a new
  control this pass (`POST` to a *different* module's own `moduleServlet` path), confirmed narrowly
  scoped — CSRFGuard still denies everywhere else. Unauthenticated → `302` to OIDC login (denied
  before reaching CSRFGuard or the servlet). A new payload-injection control: a body carrying
  `startDatetime`/`stopDatetime`/`visitType`/`location`/`patient`/`bed`/`queue`, all attacker-supplied
  and wrong, had zero effect — confirmed by direct DB read that the real values written were the
  server's own, never the injected ones (the endpoint never reads the request body at all).
- **§14 UI-014 removal**: proven via the real server query the board itself issues, not local card
  state — before: included; after: excluded.

**ADR-030 STATUS**: `ACCEPTED / IMPLEMENTED` — see `DECISIONS.md`'s own closure verdict. Every
mandatory gate this ticket set passed with live evidence; the one remaining item (a truly
from-scratch database boot) is deferred to DH-017 as an unrelated, controlled-and-confirmed
platform characteristic, not a module defect — which this ticket's own §10 explicitly pre-authorizes
as non-blocking.

**BLOCKER-10 STATUS**: ✅ CLOSED. **UI-015 STATUS**: DONE — module, frontend, both test suites, and
the full verification suite are complete and passing against a reproducible build.

**READY TO COMMIT**: Yes — production module exists; all eight UI states verified; the authorization
boundary is proven (enumerated privileges, zero roster change); rollback, success, concurrency, and
audit are all proven with exact recorded values; generic Visit/bed/queue authority remains denied;
the final build and both test suites are green; no module-specific fresh-deployment defect (the
remaining gap is a confirmed shared-platform characteristic, deferred to DH-017); documentation is
updated accurately; `git diff --check`/`docker compose config` are clean. **Still not committed or
pushed in this pass** — per the ticket's own explicit gate, that action is left for Liban.

---

## Golden end-to-end flow — status

The one flow that makes Day 1 mean anything (sprint spec §8). Demonstrated directly to Liban, not
reported as passing.

| # | Step | Status |
|---|---|---|
| 1 | Register test patient in OpenMRS | ✅ Liban registered a patient in the O3 SPA himself, 2026-08-11 |
| 2 | Same SSO session into OpenELIS, no second login | ⏸ |
| 3 | Place prenatal panel lab order in OpenMRS | ⏸ — the panel deliberately ships **empty** (DH-006, clinical sign-off). An order *can* be placed with an existing upstream test, and DH-011E's poller would detect it; nothing consumes it |
| 4 | Order appears in OpenELIS worklist | ⏸ 🛑 **BLOCKER-6.** Not a build task: how an order enters OpenELIS is undecided. DH-011B showed the lab has **never** received one (`electronic_order`, `sample`, `analysis` all 0 rows) |
| 5 | Enter result in OpenELIS | ⏸ |
| 6 | Result appears in OpenMRS chart | ⏸ |
| 7 | WhatsApp notification fires (mock OK for Day 1; real send is the `DONE` gate) | ⏸ |
| 8 | Audit log shows full round trip, **no duplicates on retry/restart** | ⏸ |

---

## Tickets, in dependency order

Priority: **P0** = required for pilot-ready · P1 = if time allows · P2 = post-pilot.
🔒 = pinned P0, immune to being cut under deadline pressure (sprint spec §2).

### DH-001 · Record decisions as ADRs · P0 · ✅ DONE
Move the reasoning that existed only in conversation into `DECISIONS.md`. Twelve ADRs, including
ADR-005 (the Subscriptions/polling question, later accepted 2026-08-12) and ADR-004 (the
`labonfhir` deferral).
**DoD:** ✅ ADRs committed; `CURRENT_STATE.md`, `PROJECT_STATE.md`, `AGENTS.md` created.

### DH-002 · First pull, digest pinning, verify OpenMRS · P0 · ✅ DONE
**Worked:** 2026-08-11. **Confirmed by Liban 2026-08-11** — he registered a patient through the O3
single-page app himself and reported it working. That was the one criterion an agent could not
close.

**Precision about what his run covered:** he confirmed registration works. He did **not** report
on the SPA's display language. DH-003 later verified the French frontend/backend configuration,
but the human-visible browser observation remains explicitly unreported.

**Acceptance criteria, individually:**

| Criterion | Result |
|---|---|
| Every image pinned by digest | ✅ Four repository digests pulled and recorded (compose + ARCHITECTURE §12). All four tags existed as listed; no substitutions |
| OpenMRS reachable on `${OMRS_LOCAL_PORT}` | ✅ after fixing the gateway's hard-coded upstream hostnames |
| Admin login works | ✅ via REST (`/ws/rest/v1/session`, `authenticated: true`). **Not** via the SPA |
| Test patient registered and retrieved | ✅ `10001MJ — Test-DH002 Verification`; found by name, by identifier, and via FHIR R4. **Registered through the API, not the registration form** |
| `down` && `up` returns to a working state, data intact | ✅ same patient, same UUID; no duplication (patients 51, concepts 4,259, OCL items 27,319 all identical); warm boot 38s |
| Healthchecks actually pass rather than merely being defined | ✅ — and the backend's was **found lying** and rewritten. See `CURRENT_STATE.md` §3a |
| DoD: verification steps in `README.md` | ✅ including the ≈50-minute first-boot warning and the demo-patient warning |

**Why this is now ✅ DONE.** The technical checks ran through `curl`, then Liban opened the O3
single-page app and registered a patient himself on 2026-08-11. The only UI observation not reported
was the display language, which remains a DH-003 check rather than reopening DH-002.

**Surfaced here, resolved elsewhere:** BLOCKER-2 became the explicit demo-patient switch, and
DH-019 wired and empty-volume-verified the required admin password.

### DH-019 · Wire the OpenMRS admin password through compose · P0 · ✅ DONE 2026-08-11
**Verified the only way it can be**: on a throwaway scratch project with an empty volume, a full
cold boot, then `admin` + the configured password → `authenticated: true`, and `admin`/`Admin123`
→ refused. The live stack was never touched, and the scratch stack was destroyed afterwards.
**Raised by:** DH-002, 2026-08-11.
**Problem.** The backend image sets the `admin` password from `OMRS_ADMIN_USER_PASSWORD`,
defaulting to the published value `Admin123`
(`OMRS_ADMIN_USER_PASSWORD=${OMRS_ADMIN_USER_PASSWORD:-Admin123}` in the image's
`startup-init.sh`; `OMRS_CONFIG_ADMIN_USER_PASSWORD` overrides it). The compose file passes
neither, so every instance built from this repo today ships with a known admin password.
**The sharp edge:** the password is written into the database **when OpenMRS initialises an empty
schema, and only then**. Setting it after the first boot does nothing. So this must land *before*
the pilot VM is first brought up, not before go-live.
**Requirements:** pass `OMRS_CONFIG_ADMIN_USER_PASSWORD` from `.env` (`OMRS_ADMIN_PASSWORD`,
already documented in `.env.example`); guard it with `${VAR:?}` so an empty value fails loudly
rather than silently falling back to the default; document the post-first-boot recovery path
(manual password change) in the runbook.
**Acceptance:** a fresh stack with a generated password refuses `Admin123` and accepts the
configured one. Verified on an empty volume, since that is the only case where it applies.

### DH-009a · Spike: ADR-005, **OpenMRS leg** · P0 · ✅ DONE — reviewed by Liban
**Worked:** 2026-08-11, against the running DH-002 stack rather than from documentation.

**Answer: neither event-driven option exists in the O3 3.7.x distro as we deploy it.**
- FHIR `Subscription` → HTTP 404, `HAPI-0302: Unknown resource type`; absent from the
  CapabilityStatement's 24 advertised types; `ServiceRequest` returns 200 as a control.
- `event-4.0.0`'s ActiveMQ broker is `vm://localhost` (in-JVM), the module ships an empty
  `moduleApplicationContext.xml`, and 61616 is refused. A separate adapter container cannot reach
  it. (A bare `tcp://` string exists in the jar; no way to expose it was found — "none found" is
  recorded as exactly that, not as "none exists".)
- Polling is viable: `_lastUpdated` is advertised **and verified applied** on `ServiceRequest`,
  `DiagnosticReport`, `Observation`, `Task`.

**Evidence** is appended to ADR-005 in `DECISIONS.md`. Liban accepted the proposed resolution on
2026-08-12: custom adapter, 15-second configurable order poll, inclusive watermark, and durable
ledger. **The watermark design is accepted; DH-010 has built its safety foundation, while the
watermark and poller remain unbuilt until DH-011**: `docs/WATERMARK-DESIGN.md`.

**Review scope presented to Liban**, in order: ADR-005's evidence section, then
`WATERMARK-DESIGN.md` §4 (why `ge` and not `gt` — the choice that trades duplicates for never
losing a result), §6 (tested discontinuation semantics and the `replaces` obligation), §7 (the
timezone constraint), and §9.2 (polling being the only trigger is new information bearing on
ADR-004's `labonfhir` deferral).

**Not done here:** no poller was written. The completed review now unblocks DH-010.

### DH-009b · Spike: ADR-005, **OpenELIS leg** · P0 · ✅ EVIDENCE COMPLETE — runtime gates remain
OpenELIS is now deployed. Its HAPI FHIR 6.6.0 CapabilityStatement advertises `Subscription`, and
its committed upstream-derived configuration exposes an outbound subscriber URL plus retry
interval/resources. That establishes capability and configuration shape, **not working delivery**:
no receiving endpoint has been registered and no outbound result has been observed.

ADR-005 accepts a test-driven branch: use outbound delivery only if it is demonstrated; otherwise
poll with equivalent safety properties. Demonstrating delivery belongs to adapter work, not this
completed evidence spike.

One higher-stakes behavior also remains uncharacterised: whether correcting a result advances the
FHIR `DiagnosticReport`/`Observation` version and timestamp. A stale corrected value in OpenMRS is
more dangerous than a stale order. This must be tested before the result leg is trusted; the
order-leg answer must not be assumed to generalise.

### DH-009 · (superseded by DH-009a / DH-009b above) · P0 · ✅ DONE
**Objective:** answer, with primary evidence, how the adapter learns an order/result exists.
**Depends on:** nothing — this is source and documentation reading. **Do it early**; it gates the
longest pole after SSO, and it does not need a running stack.
**Requirements:** read the `fhir2` module source and docs for the 3.7.x line; determine whether
outbound FHIR Subscriptions are supported and stable; do the same for OpenELIS's outbound result
direction. **Present quoted source with file paths — a conclusion alone is not acceptable output**
(ADR-005). If the answer is polling, write the watermark design (keying, advancement, restart
mid-window, back-dated records, failure mode when wrong) for human review **before** building it.
**Acceptance:** ADR-005 updated from `OPEN` to a decision, with evidence, reviewed by Liban.

### DH-003 · OpenMRS maternity config as code · P0 · ✅ DONE 2026-08-13

Initializer 2.12.0 handles the French global properties and deterministic `Maternité` admission
location, but its exact source contains no bed or bed-type domain. Bedmanagement 7.2.0's native
REST resources therefore back a short-lived, non-root seed job. Its implementation has no update,
retire, void, or delete call: existing objects must match or deployment stops for review.

**Acceptance evidence:**

| Criterion | Result |
|---|---|
| Fresh boot, no admin clicking | ✅ isolated empty-volume project reached healthy in ~4 minutes with demo patients/content off; ward, both types, and one test bed of each type existed; seed exited 0 |
| Same counts are idempotent | ✅ `2/2` rerun created `0/0` |
| Raising counts only adds the delta | ✅ `2/2 → 3/4` created `1/2` |
| Lowering deletes nothing | ✅ `3/4 → 1/1`, then committed `0/0`, created zero and retained all 3 delivery + 4 postpartum beds |
| French backend configuration | ✅ `default_locale=fr`, `reporting.defaultLocale=fr`, allowed list begins with `fr` |
| Demo controls remain explicit | ✅ fresh `off/off` boot had zero patients and zero copied `referenceapplication-demo` directories. A metadata preflight requires one completed content-on/patients-off boot before `patients=on`; the isolated two-stage path then created exactly 50 patients from one creator and reset its sole trigger |
| Runtime identity / safety | ✅ backend target and process uid 1001; seed uid 65534, read-only root, all capabilities dropped; conflicts fail without writes |

The exact pinned upstream backend is the base of local `on`/`off` build targets because some
Initializer loaders ignore its filename-exclusion API. DH-024 removed only upstream's competing
demo-patient trigger from that base, leaving the mounted `patients.on/off.xml` as its sole writer;
the other six properties in that upstream file remain. `content=off` affects what a new/restarted
image loads and deliberately does not delete content already imported into a database. The existing
local throwaway stack now contains 100 fictional demo patients, two deliberate test patients, and
the 3+4 lifecycle-test beds; no patient or bed row was deleted. Committed counts remain explicit
zero placeholders pending real facility numbers (ADR-007). A human has not yet reported whether
the SPA visibly opens in French, so that browser observation remains unverified rather than being
inferred from config.

### DH-004 · ANC + partograph forms · P0 · 🔨 ADR-006 GUARD BUILT 2026-08-13 · 🛑 FIELDS AWAIT SIGN-OFF
Data-entry only. 🔒 No alert lines, no action lines, no prolonged-labour flagging (ADR-006).
**Cannot be marked DONE without a recorded human response**, even when technically complete.

**Same finding as DH-006: nothing ships to copy.** No antenatal or partograph form exists anywhere
in this stack. Checked, not assumed — the only 10 forms present are upstream `-core_demo.json` demo
forms, and a full-text search of the distribution for "antenatal"/"partograph" returns only a demo
billing CSV. So the fields must be composed, which is the clinical lead's decision.

**Built: ADR-006 is now executable rather than a promise.**
`openmrs-config/forms/validate_form_safety.py` refuses any AMPATH form schema that performs
clinical interpretation — `calculate`/`calculateExpression`, `alert`/`warning` keys,
`js_expression` validators, and decision-support vocabulary (alert line, action line, prolonged
labour, risk score, triage, recommend, danger sign). 19 tests, each encoding a specific way
decision support gets added to a form. A pinned P0 that previously depended on whoever remembered
it now fails the deployment instead.

**A defect found in the guard itself, and fixed.** Pointed at a directory whose contents it could
not see, it reported "no clinical interpretation found" and exited 0 — a checker producing a green
result having examined zero files. An empty check is now an error, with tests locking that in. This
is the fourth false-green of this kind found in the project; the pattern is always the same, a check
that passes for a reason unrelated to what it claims to prove.

**⚠️ Finding — three demo forms currently loaded here DO perform clinical interpretation.**
Running the guard against the shipped demo schemas flagged `structured_soap_note` (an actual
`alert` key, `calculateExpression`, expression validators), `mental_health_assessment_form`, and
`form-engine-cookbook`. All three are live in the database. They are upstream **demo content**, not
maternity forms, and **nothing was changed**. `OMRS_DEMO_CONTENT=off` removes them and is already a
supported switch — but demo content is a decided option (BLOCKER-2), so turning it off for the
pilot is **Liban's call, not an agent's**. Human Decision Required: Yes.

**Still required to close:** the clinical lead supplies the field list in
`docs/CLINICAL-SIGNOFF.md` §6; the schema is built from it and must pass the guard; the response is
recorded here with its date and who gave it.

### DH-005 · PostgreSQL + OpenELIS standalone · P0 · 🟢 BUILT AND VERIFIED 2026-08-12
Five services added, all digest-pinned. Verified by running, not by `compose config`:

| Check | Result |
|---|---|
| Three databases as co-tenants (ADR-001) | ✅ `clinlims`, `keycloak`, `adapter`, correct owners |
| Tenant isolation | ✅ DH-020: each application role can connect only to its own database; all six cross-tenant attempts are denied |
| OpenELIS schema provisioned | ✅ 375 base tables in the fully migrated `clinlims` schema (live recount 2026-08-13) |
| Webapp serving | ✅ HTTPS 8443 (its HTTP connector does not listen) |
| FHIR server serving | ✅ `GET /fhir/metadata` → 200, **over mutual TLS** |
| Frontend container | ✅ running, **not yet exercised** — it had no route until Caddy (DH-016) |

**Four things running it exposed** (none catchable by a syntax check):
1. `certs` **cannot** be dropped as ARCHITECTURE §2 claimed — Tomcat aborts without the keystore.
2. The webapp reads its Spring config from a **Docker secret**; without it the WAR deploys, the
   context dies with `FileNotFoundException: /run/secrets/common.properties`, and Tomcat still
   reports a clean startup — a failure that looks like success in `docker compose ps`.
3. The FHIR container ignores `JAVA_OPTS` (bitnami replaces it); `CATALINA_OPTS` works. With the
   properties unsubstituted, **both** connectors stayed down while the log said "Server startup in".
4. The OpenELIS FHIR endpoint requires **mutual TLS** — see ADR-005 Q5. Load-bearing for DH-011/012.

**Later DH-024 evidence:** the configured admin login is now exercised through the real CSRF/form
path on both current and empty volumes, and published `adminADMIN!` is refused. The frontend's
internal HTTP endpoint answers; no human browser workflow has been performed.

### DH-020 · Restrict PUBLIC on the OpenELIS database · P1 · ✅ DONE 2026-08-13
Raised at DH-005 after the `adapter` role could connect to `clinlims` through PostgreSQL's default
database grant. The fix revokes database-level `PUBLIC` privileges on all three tenant databases
and restores explicit privileges only to each owner. It deliberately does **not** change OpenELIS's
schema grants: a role denied `CONNECT` cannot exploit the permissive `public` schema, while upstream
migrations retain their expected schema behavior.

Fresh volumes call the shared script from `initdb.d`; an idempotent, digest-pinned one-shot Compose
job applies the same grant state to existing volumes before OpenELIS, Keycloak, or the adapter may
start. Runtime verification used the actual three application roles: all three own-database
connections passed and all six cross-tenant connections were denied. After restarting every
PostgreSQL client, OpenELIS HTTPS, OpenELIS FHIR mutual-TLS metadata, Keycloak readiness, and adapter
readiness all passed. A second hardening run exited 0, proving restart idempotency.

### DH-021 · Replace the shipped keystore · **P0 (raised from P1)** · ✅ DONE 2026-08-13
Raised at DH-005. `itechuw/certgen` generates nothing — it ships **pre-built** keystores inside a
public image, so the private key protecting the internal 8443 connectors is publicly downloadable.
Attempting the replacement on 2026-08-13 found two further defects and one unrelated false green.

**Why this is now P0, not P1:** the shipped certificate **had already expired** — `notAfter
2026-07-23`, found 2026-08-13, verified with `openssl x509 -checkend 0` against the container clock.
Nothing on the internal path validates expiry, so it kept working silently. The first thing to
check it strictly — Caddy at DH-016, or the adapter's mTLS client at ADR-005 Q5 — would have failed
against a cause three weeks in the past.

**Second defect: the store passwords were the published `kspass`/`tspass` in effect.** `.env` sets
`OE_KEYSTORE_PASSWORD=` and `OE_TRUSTSTORE_PASSWORD=` **empty**, and compose read
`${OE_KEYSTORE_PASSWORD:-kspass}` — so an empty value silently selected upstream's published
password while the variable looked configured. Confirmed directly: the live keystore opens with
`kspass`, and refuses both the `.env` value and a wrong-password control.

**Built and proven in isolation** (`openelis-config/certs/`): a generator producing 4096-bit
RSA/SHA-256 material valid 825 days, with upstream's SANs plus this stack's service names, and
`serverAuth,clientAuth` because the webapp presents the same certificate as a *client* to the FHIR
server. It keeps the exact filenames, paths, PKCS12 structure, and alias layout so no consumer
changes, and is idempotent — it regenerates only when the certificate is missing, expired, within
`OE_CERT_RENEW_BEFORE_DAYS`, or unreadable with the configured password. Verified: generation, a
second run leaving it untouched, an `openssl s_server -Verify 1` mutual-TLS handshake returning
`Verify return code: 0 (ok)`, and a no-client-certificate control correctly refused with
`tlsv13 alert certificate required`.

**The coordination step, found the hard way.** The first attempt took OpenELIS down: the Spring
context failed with `keystore password was incorrect` and every page became a redirect loop.
OpenELIS reads its SSL passwords from `common.properties` — the **committed** Docker secret — not
from `CATALINA_OPTS`. A regenerated keystore paired with a `kspass` properties file is an outage.
The stack was restored from a pre-change backup and re-verified before continuing.

**How it was closed.** `common.properties` became
`common.properties.template` with `@OE_KEYSTORE_PASSWORD@` / `@OE_TRUSTSTORE_PASSWORD@`
placeholders, and `render-properties.sh` substitutes them at deploy time into the
`openelis-properties` volume, mounted read-only at `/run/secrets` — the path the application
already expects. A file-based Docker `secret` must be a host path, and a host path cannot be
produced by a container during the same `up`, which is why the delivery mechanism changed rather
than the value. **Certificates and properties are generated by one job on purpose**, so the
keystore and the password that opens it cannot drift apart.

**Verified 2026-08-13 on the live stack:** the keystore opens with the configured password and
refuses both `kspass` and a wrong-password control; the certificate served on the wire is
`CN=openelis.org, O=Daher Health`, valid to 2028-11-16, `checkend 0` clean; mutual-TLS FHIR
metadata returns 200 with the new client certificate and is refused without one; the rendered
properties file has zero remaining placeholders, mode `0444`, and its password hash matches `.env`;
configured admin login reaches `/OpenELIS-Global/` while `adminADMIN!` and a wrong control both
bounce to `/LoginPage`. A full `docker compose up -d` is idempotent — the certs job reported
"existing certificate is valid for more than 30 days — leaving it alone". Ten services running,
zero restarts, zero OOM, 102 patients unchanged.

⚠️ **`encryption.general.password=kspass` was deliberately NOT changed.** It is in the same file
and looks like part of this fix. It is not — it is application data encryption, and changing it may
make already-encrypted stored values unreadable. It needs its own analysis and is potentially
clinical-data surface. The template carries this warning inline.

### DH-022 · Keep unconfigured backup out of the default Compose path · P0 · ✅ DONE 2026-08-13
The documented `docker compose up -d` previously selected `backup` even when the required
encryption/off-site variables were absent, producing a credential-failure restart loop that
contradicted `CURRENT_STATE.md`. The scheduler now uses Compose's opt-in `backup` profile. Default
foundation startup remains clean; operators enable it explicitly only after approved settings
exist. This does not weaken the pinned gate: DH-014 production off-site verification and DH-015's
performed restore remain mandatory.

### DH-023 · Resolve OpenELIS Horiba analyzer import conflict · ⏭ DEFERRED TO v1.1 (ADR-016)
**Not blocked, not in scope.** Liban decided 2026-08-13 that v1 is manual result entry only, with
no analyzer interface and no HL7/ASTM instrument integration. This ticket is not sized or worked
now.

The shipped Horiba imports are left **exactly as upstream ships them** — not disabled, renamed,
repaired, or log-suppressed. Each failed import rolls back cleanly, the live table has zero
duplicate descriptions, and the web/FHIR endpoints answer, so the boot-log errors are a known
non-blocking upstream behaviour (`CURRENT_STATE.md` §3a, known bug 8). ADR-016 explicitly does not
authorize editing laboratory test rows to quiet them. When an analyzer interface is genuinely
wanted, the laboratory lead's confirmed test identities come first.

### DH-024 · Deep fresh-deployment quality hardening · P0 · ✅ DONE 2026-08-13
Raised by a full repository/runtime audit after DH-003. Three concrete false-green paths were
reproduced and fixed without changing clinical content:

1. **Demo-patient precedence:** the live configuration contained both upstream `value=50` and our
   `value=0` for the same property. A second 50-row batch appeared in the throwaway volume. The
   pinned local base now removes only upstream's four-line trigger block; six unrelated properties
   remain, `patients.off.xml` is the sole writer, and a live restart held patient count 102→102.
   A direct first-boot `on/on` imported all 23 concept packages but reset its trigger with zero
   patients because the upstream generator runs before that metadata is ready. A read-only database
   preflight now requires one completed content-on/patients-off boot before `patients=on`. The
   enforced two-stage path then created exactly 50 patients from one creator and reset the sole
   trigger. No patient was deleted.
2. **PostgreSQL init race:** `pg_isready` passed against the temporary `initdb.d` server and the
   hardening job then lost its connection during the intentional shutdown. Readiness now also
   requires `postgres` as PID 1. The exact empty-volume dependency graph passed with hardening 0.
3. **Published OpenELIS admin credential:** the image ignores `DEFAULT_PW` and rewrites its bundled
   `adminADMIN!` hash during first webapp startup. A read-only one-shot now waits for the real login
   redirect, bcrypts the required `${OE_ADMIN_PASSWORD}` into exactly one admin row, verifies it,
   and gates the frontend. Current and exact empty-volume CSRF/form probes accept the configured
   value and refuse the published default. The local ignored `.env` was rotated to a policy-valid
   value and remains mode `0600`.

Repository checks passed for both Compose demo targets and backup profile, every local Docker build,
adapter dependency consistency/Ruff/14 tests, maternity Ruff/5 tests, JSON/XML/CSV/shell syntax,
relative Markdown links, Git integrity/whitespace, image policy, and live-secret value scanning.
Runtime checks covered all endpoints, both FHIR capability counts, all nine PostgreSQL tenancy
cases, ten container timezones, Keycloak realm/client-secret/token behavior, adapter constraints,
and a fresh local encrypted backup read-back. Final normal startup left ten long-running services
healthy/running as applicable and all five setup jobs at exit 0, with zero restarts or OOM kills.
The backup was neither off-site nor restored.

### DH-006 · Prenatal panel order set + LOINC mappings · P0 · 🔨 MECHANISM BUILT + VERIFIED 2026-08-13 · 🛑 CONTENTS AWAIT SIGN-OFF
**The panel ships EMPTY, and that is the correct committed state.** The ticket said "draft early
(week 1) so the clinical lead has time" — what has been drafted is the *decision*, not the answer.

**The finding that shaped this.** Neither system ships a prenatal panel to copy unmodified, so it
has to be *composed* — and composition is the reserved clinical decision. Checked, not assumed:
OpenMRS had **zero** order sets defined, and OpenELIS's four shipped panels are `Bilan Biochimique`,
`NFS`, `Serologie VIH`, `Typage lymphocytaire`. **Liban confirmed 2026-08-13** that only artifacts
already shipped in this stack may be used, so no external guideline was fetched and no test was
selected by an agent.

**Built:**
- `docs/CLINICAL-SIGNOFF.md` — a checklist **generated from live reference data**, listing all 37
  OpenMRS lab sets installed here with their LOINC codes and concept UUIDs, plus the 4 OpenELIS
  panels. It is an inventory, explicitly **not** a recommendation, and nothing is pre-ticked.
- `openmrs-config/seed/seed_prenatal_panel.py` + an **empty** `prenatal-panel.members.csv`.
  Additive-only: it adds listed members, verifies each concept already exists, and **never removes**
  a member that is present but unlisted — that member may already have been used to order tests for
  a patient (ADR-007's reasoning). It never creates a clinical concept.
- Compose job `openmrs-prenatal-panel`, gating the frontend like the DH-003 seed does.

**Verified:** 16 seed tests (11 new) plus Ruff lint/format. Against live OpenMRS the seed created
`Bilan prénatal` with **0 members**, and a second run reported "already exists / 0 added" — the
order set count stayed at 1 and members at 0. Full stack: 11 running, 7 setup jobs at exit 0, zero
restarts.

**Technical finding for the meeting — LOINC coverage is partial on both sides.** OpenELIS: 42 of
177 active tests carry a LOINC code. OpenMRS: 21 of 37 lab sets. So LOINC alone cannot be the join
key between an OpenMRS order and an OpenELIS test; whichever tests are chosen, some pairs will need
an explicit mapping. This changes how much wiring each choice needs, not what may be chosen.

**Still required to close:** the clinical lead completes `docs/CLINICAL-SIGNOFF.md`; each ticked lab
set is copied verbatim into the members file; the response is recorded here with its date and who
gave it. **DH-006 stays OPEN until then, even though the mechanism is finished.**

### DH-007 · Keycloak + realm import · P0 · ✅ DONE 2026-08-13
Keycloak 26.7.1 is digest-pinned and running in production mode against its PostgreSQL co-tenant.
The committed `daher` realm imports automatically with French as default and English selectable;
roles `clinician`, `lab-tech`, and `sys-admin`; application browser clients; and a non-browser
adapter service account. No staff users are committed. DH-008 subsequently changed OpenELIS from
OIDC to SAML after exact application-path verification.

**Runtime verification, not syntax claims:**

- realm import succeeded after removing `_comment` fields that Keycloak rejects;
- all three client-secret placeholders resolved and matched their environment values without any
  secret being printed;
- the adapter service account completed a real client-credentials token grant, with neither its
  secret nor token printed;
- readiness returned healthy only after initialization;
- a restart preserved the realm and cleanly logged `already exists. Import skipped` followed by
  `Import finished successfully`; and
- the pulled image digest matches the digest committed in compose and ARCHITECTURE §12.

**DH-007 boundary:** this verified only the provider foundation. DH-008's protocol plumbing and
application configuration are recorded separately below.

**Session lifetimes approved by Liban 2026-08-12 (ADR-013):** 1h idle / 10h maximum / 5m access
token. These are the already-verified committed values. Revisit after observed pilot workflow;
changing them remains a human workflow/security decision.

### DH-008 · SSO wiring · P0 · 🟢 PLUMBING BUILT + VERIFIED · 🛑 ACTIVATION BLOCKED (BLOCKER-5)

The prior OIDC-for-both conclusion was wrong. The deployed OpenELIS 3.2.1.11 WAR does contain an
OIDC client and configuration keys, but exact source at tag commit `e2a6bdf7` shows the OAuth path
uses `CustomFormAuthenticationSuccessHandler`. It accepts `UserDetails`; an OIDC login supplies an
`OidcUser`, so login information remains null, authentication is cleared, and the handler returns
to `LoginPage`. SAML uses `CustomSSOAuthenticationSuccessHandler`, the native working path also
used upstream. This is a demonstrated implementation defect, not an inference from missing keys.

**Built:**

- OpenMRS OIDC through official OAuth2 Login 1.5.0, built from exact tag commit
  `2770233627343a53719ec981e997d94b1d90b7ad` in a digest-pinned Maven image;
- atomic installation of that OMOD into both fresh and already-populated OpenMRS data volumes;
- mode-`0600` runtime configuration that stores only `oauth2.enabled=false` when SSO is off;
- OpenELIS SAML configuration against the same `daher` realm while retaining form login;
- fresh-realm OpenMRS OIDC and OpenELIS SAML clients; and
- an idempotent, non-deleting `keycloak-sso-config` job, because `--import-realm` deliberately
  skips an existing realm.

**Runtime verification 2026-08-13:** both OpenMRS targets built successfully and the exact module appeared in
the live persistent volume byte-for-byte; REST reported version 1.5.0 registered and stopped while
the local admin still authenticated. Keycloak recovered through a clean realm-import startup,
reconciliation passed twice consecutively, and live inspection showed exactly the expected client
protocols and unique mapper names. A network-isolated ephemeral Keycloak import also proved the
fresh-realm JSON. The normal one-command stack returned healthy/running with SSO disabled. The
mapper fragments were deliberately moved outside the realm import directory after a runtime test
proved Keycloak otherwise tries to parse them as complete realms.

**Not claimed:** the acceptance criterion remains one Keycloak login, both applications, no second
prompt. OpenMRS consumes exact native role names and separately auto-creates a provider unless the
`provider` claim is false; OpenELIS SAML consumes exact native roles from `Role`. Mapping the three
realm roles to those privileges changes access to clinical records and laboratory functions.
`provider` is therefore hard-coded false, `SSO_ENABLED` defaults false, and activation is blocked
until Liban approves the authorization matrix in BLOCKER-5.

### DH-014 · Backup, both engines · P0 🔒 · 🔨 LOCAL PATH VERIFIED 2026-08-13 — off-site unverified
The scheduled sidecar is built from a digest-pinned PostgreSQL 14.4 base and includes matching
`pg_dump`, a MariaDB client, live Keycloak export, required read-only volume mounts, per-component
SHA-256 manifest entries, AES-256-CBC/PBKDF2 encryption, optional S3-compatible upload, retention,
and an explicit success/failure marker.

**The local path is genuinely exercised**, not merely written:

| Dump | Result |
|---|---|
| MariaDB `openmrs` | ✅ 15.9 MB, and it **contains the data**: test identifier `10001MJ` present, with real `patient` and `person_name` INSERTs |
| PostgreSQL `clinlims` | ✅ 1.28 MB |
| PostgreSQL `keycloak` | ✅ 250 KB after DH-007 — populated realm database |
| PostgreSQL `adapter` | ✅ DH-024's fresh archive contains the DH-010 schema (`alembic_version`, ledger, dead-letter table) and zero operational rows; `pg_restore --list` passed |
| Live Keycloak realm export | ✅ 59 KB from the admin API; staged only inside the encrypted archive |
| OpenMRS data volume | ✅ 349 MB compressed; includes OCL/module data and future complex observations |
| Adapter audit volume | ✅ mounted and archived; DH-010 now owns it, verified mode `0600`, UID/GID `10001:10001`, currently zero bytes before message transport |
| Encryption + readability | ✅ archive created, encrypted, decrypted, and component filenames read back |
| Scheduler | ✅ calculated the next configured UTC run while retaining container environment |

The test explicitly set `BACKUP_REQUIRE_OFFSITE=false`; no data left the Docker host. Its ephemeral
key was not retained, so the exact test archive and success marker were deleted after verification
rather than left looking like a usable backup.

**NOT yet done:** no S3-compatible upload has run, no production encryption key has been placed in
escrow, and **no restore has been attempted**. Supplying an external bucket changes where encrypted
patient data leaves the system and requires Liban's explicit approval.

⚠️ **DH-027G (2026-08-21) wired this sidecar's configuration into provisioning** — a facility
created through the control plane now actually gets `BACKUP_ENCRYPTION_PASSPHRASE`/`BACKUP_S3_*`
in its env file when the operator supplies them, and the fleet's `backup_state` reflects a real
`BACKUP_NOW` report instead of a hard-coded placeholder. That is a provisioning-wiring fix, not new
evidence for the boundaries above: off-site upload is still unexercised and no restore against a
provisioned (rather than hand-built) facility has happened. See DH-027G's own entry, below DH-027D.
Per AGENTS.md the pinned item is "backup **plus a performed restore drill**" — this is neither
finished nor a substitute for DH-015.
`mariadb-dump` + `pg_dump` (all three DBs) + adapter audit volume + OpenMRS data volume +
Keycloak realm export → encrypted → S3-compatible. **Scheduled deliberately here, in parallel
with SSO, not after the adapter** — it only needs databases, and putting it last is how pinned
work gets squeezed (`CURRENT_STATE.md` §7).

### DH-015 · Restore drill, performed and documented · P0 🔒 · 🔨 PERFORMED 2026-08-13, PARTIAL COVERAGE
**Performed, not `DONE`.** Full evidence in `backup/restore-drill.md`.

A real encrypted archive was decrypted, all seven component SHA-256s independently re-verified
against its manifest, and the contents restored into an isolated `daher-restore-*` scratch stack
with no published host ports. The clinical record was then read back **through the application**:
`GET /ws/rest/v1/patient?q=10001MJ` returned `10001MJ - Test-DH002 Verification`, FHIR R4 metadata
answered 200, the configured admin authenticated, `Admin123` stayed refused, and OAuth 2.0 Login
1.5.0 came back `started=false` — SSO remained fail-closed across the restore. The OpenELIS admin
bcrypt hash was byte-identical to live.

**Two defects found, both now documented with the corrected procedure:**

1. `pg_restore --clean --if-exists` over the OpenELIS image's pre-populated `clinlims` produced
   **213 errors including failed row inserts** — silent data loss behind a plausible-looking run.
   The restore must **drop and recreate** each database first; done that way, all three restored at
   exit 0 with zero errors.
2. A scratch/recovery stack must gate on `/proc/1/comm = postgres` (DH-024 finding 9 reproduces
   otherwise) and must mount `harden-tenancy.sh`, or init dies at exit 127 *after* creating the
   databases.
3. **The corrected drop/recreate procedure silently discards DH-020 tenant isolation.** A freshly
   created PostgreSQL database grants `CONNECT` to `PUBLIC`, and `--no-privileges` does not restore
   the dump's grants; measured directly, an unrelated role then connects successfully. Every restore
   must re-run `harden-tenancy` and **pass all nine tenancy cases as an acceptance gate**. This was
   found after the drill ran, so that gate was *not* exercised on 2026-08-13 — it is listed as unmet
   in the drill doc §6.

**The measurement that matters for recovery time:** restoring the database *with* the
`openmrs-data` volume reached first HTTP 200 in **41 seconds**, against the documented ≈49-minute
cold boot dominated by OCL imports. The volume is not optional.

**Why this is not `DONE`.** Two acceptance legs could not be exercised and the drill must be
re-run once they can: **a known lab result** (none exists anywhere — `clinlims.analysis`, `.sample`,
`.patient` are all 0 rows on live and restored alike, because DH-011/DH-012 are blocked) and **a
working SSO login** (fail-closed pending BLOCKER-5). Off-site remains unexercised (DH-014).
🗓 A question for Liban is recorded in the drill doc §6: whether to hand-enter a result in the
throwaway stack to close the first leg. ADR-016 makes manual entry the v1 workflow, but it writes
fabricated clinical values, so it was not an agent's call to make.

### DH-010 · Adapter skeleton: audit ledger, idempotency, retry · P0 · ✅ DONE 2026-08-13
Processed-message ledger keyed on stable source event ID with a unique constraint; append-only
structured audit log on a named volume; exponential backoff with jitter; dead-letter table. Two
separate mechanisms by design — the ledger answers "did we do this", the audit answers "what did
we do, to whom, when".

**Implemented and verified:** Python 3.13.14 base pinned by pulled digest; exact Python dependency
versions; Alembic migration; unique source-event claim; guarded `processing` → `completed` or
`dead_letter` transitions; stack-owned dead-letter payload retention; strict audit schema that
rejects extra/clinical-value fields; `O_APPEND` + lock + `fsync` JSONL writes; transient-only
exponential jitter retry; and liveness/readiness checks for PostgreSQL plus the audit volume.

Container tests: dependency consistency, Ruff lint/format, and **14 pytest tests passed**. Runtime:
migration applied to the real adapter PostgreSQL database; Compose health passed; expected tables
and named uniqueness constraints inspected; operational rows remained 0; process is UID/GID 10001;
audit file is mode `0600`; and the read-only root filesystem rejected a write. A restart returned
healthy without reapplying the migration. No FHIR message was moved—that is DH-011/012.

**Boundary corrected by the post-commit audit:** the repository intentionally returns an existing
claim's actual state, including `processing`; it does not yet reconcile that state after a process
dies between an external write and `mark_completed`. The schema/skeleton remain complete for
DH-010's transport-neutral scope, but “unique claim” must not be read as end-to-end exactly-once
delivery. DH-025 closes that transport-specific gap before activation.

### DH-025 · Reconcile ambiguous in-flight adapter writes · P0 · 🔨 CORE BUILT + VERIFIED; TARGET BINDING PENDING
**Raised:** 2026-08-13 post-commit quality audit. **Human Decision Required: No** — technical
correctness; does not select clinical content or workflow.

A crash can occur (a) before an outbound write, (b) after the target accepted it but before the
ledger commits `completed`, or (c) after completion. Cases (a) and (b) both restart as
`processing`, but only (a) is safe to replay blindly. Elapsed time cannot distinguish them.

**DoD before DH-011 activation:** correlate every outbound resource to the stable source event;
on a stale claim, query the target first; mark complete if the write exists, retry only when its
absence is proven, and leave the claim blocked/operator-visible if the answer is ambiguous. Test
crashes immediately before and after the target write, concurrent claims, completed duplicates,
dead letters, and restart/watermark behavior. `processing` and `dead_letter` must never be treated
as completed duplicates.

**Built and verified 2026-08-13:** `source_correlation()` hashes the full, unambiguous source-event
tuple under `urn:daher-health:adapter:source-event`. `PostgresRecoveryFence` is acquired during the
FastAPI lifespan and held until process shutdown, so recovery and future transport cannot run in a
second adapter process. Readiness also requires that fence. `RecoveryCoordinator` accepts only a
typed target result: exactly one match, proven absence, or ambiguous. It reconciles the first,
retries the second, and raises without state advancement for the third; completed duplicates and
dead letters stay separate paths.

Dependency consistency, Ruff lint/format, and **27 tests** passed. Tests cover stable/versioned
correlation, two real concurrent claims, no-fence refusal, missing/completed/dead-letter states,
ambiguous lookup, failed retry write, and crashes before and after the write. Against live
PostgreSQL, two fence owners could not coexist; a rollback-only coordinator probe completed and
then left the row count unchanged. The deployed adapter retained the fence for its lifetime,
refused a second process, returned healthy, and reacquired after restart. Both operational tables
remain empty.

**Still required to close the ticket:** DH-011's concrete OpenELIS writer must attach this exact
correlation to the chosen Task/ServiceRequest, and the probe must demonstrate exactly one match or
proven zero matches against that API. Multiple matches or an unavailable lookup are ambiguous and
must stop startup. This cannot be honestly claimed before the target resource shape exists; no
transport or clinical order was activated here.

### DH-011 · Adapter: order path OpenMRS → OpenELIS · P0 · 🛑 BLOCKED ON BLOCKER-6 (target ingestion path)
The accepted 15-second inclusive watermark remains the implementation design.

**Decided (ADR-015):** the first watermark starts at deployment time — no historical backfill. It
must be read from the OpenMRS server clock, persisted once, and never re-derived on restart; the
inclusive lower bound still applies at the boundary. Nightly reconciliation counts only from that
bootstrap watermark.

**Decision-blocked no longer** — BLOCKER-3 closed 2026-08-14 (ADR-015, ADR-018, interim requester).
The earlier "blocked on BLOCKER-3 legs 2–3" text was stale and is corrected here.

**Evidence gathered 2026-08-16, read-only, against the running stack — before any transport code.**
Two findings, one helpful and one that stops the ticket:

**✅ The DH-025 correlation binding is feasible, and the control proves it.** OpenELIS's HAPI 6.6.0
advertises `identifier` search on `Task` (29 params), `ServiceRequest` (31) and `DiagnosticReport`
(26). A search in our exact correlation shape —
`Task?identifier=urn:daher-health:adapter:source-event|<hash>` — returns **HTTP 200, Bundle,
`total: 0`**, which is precisely the "proven absence" that `TargetPresence.ABSENT` requires. The
control is what makes that meaningful: an unknown search parameter is **rejected with HTTP 400
`HAPI-0524`**, so a 200 here means the parameter is genuinely supported rather than silently
ignored. Exactly-one and zero-match behaviour still needs demonstrating against a real resource.

**❌ `_sort` is silently ignored by OpenMRS `fhir2` 4.2.0** — ascending, descending and a nonsense
sort key return byte-identical order. The accepted design's pseudocode relies on server-side
ordering. Correction, evidence and the required client-side handling are appended to
`docs/WATERMARK-DESIGN.md` §3. This does not change the accepted contract; it changes how the
contract is met.

**🛑 ADR-018's status mapping cannot be verified yet, and that is a finding rather than a delay.**
"Accepted or started" must map to a *verified* OpenELIS status. With `electronic_order`, `sample`
and `analysis` all at **0 rows** and the FHIR store empty, there is no observed status vocabulary to
map to — and a `status=` search cannot discriminate anything against an empty store (a bogus status
value returns 200/`total: 0` exactly like a real one). This gate is not passable by probing; it
needs one order to have actually flowed.

**🛑 Blocked on BLOCKER-6:** how an order enters OpenELIS at all. The inbound mechanism is a **poll
from OpenELIS** (`remote.source.uri`, currently empty), not a push into its FHIR store — which is
where every document in this repo implicitly assumed the adapter would write. That fork changes what
the adapter is, so it is a human decision.

**Buildable regardless of BLOCKER-6:** the OpenMRS-side poller, the durable watermark, the
bootstrap-at-deployment-time rule, and client-side ordering. Not started — recording the design
contradiction and the blocker came first.

#### DH-011 sub-ticket lettering — fixed 2026-08-16, read this before using an ID

The 2026-08-16 roadmap lettered these sub-tickets, and one ID briefly carried two meanings
(`DH-011A` was used for both "document Option F" and "the OpenMRS poller"). **The roadmap lettering
below is authoritative.** The poller is **DH-011E**, not DH-011A.

| ID | Scope | Status |
|---|---|---|
| **DH-011A** | Record Option F as a candidate | ✅ **DONE 2026-08-16** — ADR-026 |
| **DH-011B** | Runtime spike: OpenELIS native business FHIR ingestion, **synthetic data only** | ✅ **COMPLETE 2026-08-17** (authorized by Liban) — Option F writes native domain rows; ServiceRequest gated on a numeric site code, correlation does not survive |
| **DH-011C** | Prove DH-025 correlation: 0 / 1 / 2+ match behaviour on the real target | 🟡 **Half answered, corrected 2026-08-17.** The *lookup* half is proven on `Patient`/`Practitioner` — 1 for a real identifier, 0 for absent, and 0 for a wrong system with the right value. What fails is that a *client-supplied* identifier is not retained → DH-011D. Unproven on `ServiceRequest` until the accession blocker clears |
| **DH-011D** | Narrow OpenELIS patch to **persist** an incoming external order identifier. Search already works, so this is smaller than first scoped. Needs a superseding ADR (ADR-016/BLOCKER-4) | 🔓 **Condition met 2026-08-17** — the identifier is discarded on ingestion. ⚠️ Verification is gated on NEEDS LIBAN #7, and whether `ServiceRequest` behaves like `Patient`/`Practitioner` here is **untested** |
| **DH-011E** | OpenMRS poller + durable watermark. **Transport-neutral**, unblocked by BLOCKER-6 | ✅ **BUILT + VERIFIED 2026-08-17, NOT ACTIVATED** — see below |
| **DH-011F** | Watermark/paging test matrix | ⏸ with DH-011E |
| **DH-011G** | Patient identity mapping — controlled identifiers, fail closed on ambiguity | ⏸ |
| **DH-011H** | Practitioner/requester mapping (NEEDS LIBAN #6) | 🗓 meeting |
| **DH-011I** | Version-controlled test mapping registry; never fuzzy-match test names | 🛑 clinical sign-off |
| **DH-011J** | Cancellation via `replaces`; verify real OpenELIS status transitions | ✅ **DONE for V1 added-test cancellation. COMMITTED AND PUSHED 2026-08-30**: `86f26dfb28ecffae83a3c96adb5a691aaee909a4` on `origin/master`; CI run `33298838019` — all 8 jobs green. A new `$cancel` FHIR operation + `DaherCancellationTransaction` enforce a V1 eligibility contract based **solely on Analysis workflow state** (`NotStarted` → cancel, `Canceled` → idempotent, every other status → refuse) — including the one guard the pristine `deleteServiceRequest` lacks entirely, live-confirmed it would silently overwrite a `Finalized` result. Specimen-collection/receipt gating was investigated, found to have no reliable native signal on the path Daher uses, and is **deferred by decision** (Liban selected option (b)), not implemented — not a claim that specimens haven't been collected, only that the system cannot observe that fact. Full DH-025 crash-recovery/idempotency/concurrency (including a coordinator-level real-component proof)/isolation, plus an exact deterministic rollback fault-injection proof, all live against real disposable infrastructure — see `openelis-config/patches/order-correlation/PROVENANCE.md`'s DH-011J/DH-011J2/DH-011J FINALIZE sections for the complete evidence. **Whole-initial-order cancellation deliberately NOT implemented** — see the Human Decision Required row below. ~~🛑 Not testable yet — DH-011B could create no order, so no native transition could be observed. The provider does expose `@Delete`/`@Update`, so the surface exists~~ (superseded, not deleted — kept for history) |
| **DH-011K** | Crash-point qualification across all five points | ⏸ |
| **DH-011L** | End-to-end synthetic order demonstration → closes DH-011 | ⏸ |

**Correction appended 2026-08-28 (DH-011/BLOCKER-6 decision-closure pass) — DH-011C/D's "untested on
`ServiceRequest`" conflates two different questions, and one of them is not untested.** Re-reading
this file's own DH-011B first-pass section (above, "🛑 The DH-025 correlation cannot bind to
ServiceRequest on this server") against the DH-011C/D table rows shows they say different things
about the same resource:

1. **Whether `ServiceRequestProvider` retains a submitted identifier on create** — genuinely
   **untested**, because no ServiceRequest has ever been created (blocked on the accession gate).
2. **Whether `ServiceRequest?identifier=…` is a supported search parameter on the business API at
   all** — **already tested and answered: no.** It returns HTTP 400 on the exact Option F target
   (business API, HAPI 7.0.2), with controls proving the 400 means "unsupported parameter," not
   "supported but empty": an unrecognized parameter also returns 400, while the advertised `_id`
   parameter returns 200 on the same resource type. `Specimen`, `Patient`, `Practitioner` and
   `Organization` all accept `identifier` (200) on this same server; `ServiceRequest` does not. This
   was independently re-confirmed as still accurate during this pass (no code touching this behavior
   has changed since 2026-08-17; git history for `adapter/` and any OpenELIS-related path is empty
   since that date).

**Consequence for DH-011D's scope.** "Search already works, so this is smaller than first scoped"
is true for `Patient`/`Practitioner`, where DH-011D genuinely only needs to add persistence. It is
**not** true for `ServiceRequest`: even a successful persistence patch would not make the identifier
findable, because the search surface itself rejects the parameter for that resource type. A
`ServiceRequest`-bound correlation therefore needs either (a) DH-011D to also add search-parameter
support on `ServiceRequestProvider` — a materially larger patch than "persist an incoming
identifier" — or (b) the correlation to bind to a different, already identifier-searchable resource.
`Specimen` is the one candidate already on record (PROJECT_STATE.md's DH-011B first-pass section),
but a specimen identifier is laboratory accession-numbering configuration, the same class of
facility-owned decision as the numeric site code itself (NEEDS LIBAN #7) — not something DH-011D can
resolve as an engineering choice either way.

**Live re-verification, same pass, against the current `daher-health-*` stack (unchanged since
2026-08-17, confirmed by empty `git log` for `adapter/` and any OpenELIS-related path over that
window):** `clinlims.electronic_order` = 0, `sample` = 0, `analysis` = 0, `test` (active) = 177,
`patient` = 1, `provider` = 1 — byte-identical to DH-011B's recorded baseline; no order has ever
reached OpenELIS. `site_information` still carries `acessionFormat = SITEYEARNUM`, prefix `DEV01`,
`validateAccessionNumber = true`. One new fact, not previously recorded: the prefix row is annotated
"Can not be changed if there are samples" — since `sample` is still 0, the real numeric site code can
be set later with no data migration, once the facility supplies it. The exact `%013d` 13-digit format
string itself was not re-derived this pass (the running image's JRE has no `javap`); it is carried
forward from DH-011B's constant-pool read, corroborated again by the unchanged config and the
unchanged zero-samples behavioral consequence.

#### DH-011E — OpenMRS poller + durable watermark · ✅ BUILT + VERIFIED 2026-08-17 · ⚠️ NOT ACTIVATED

**94 adapter tests (from 27), Ruff clean, migration `0002_watermarks` applied to the live database
and verified.** Nothing starts the loop: `grep` confirms `OrderPoller` is referenced by no compose
service and not by `app.py`. There is no production delivery implementation, because BLOCKER-6 has
not decided what the target is. **No order has moved.**

| Module | What it does |
|---|---|
| `instants.py` | `ServerInstant` — the server's timestamp string, kept byte-exact (§7). Validates the shape, asserts Djibouti's `+03:00`, and orders by a zero-padded key so comparison is exact without ever reformatting what we transmit |
| `source_events.py` | `SourceOrderEvent` (the transport-neutral output — **names no target**) and cohort assembly: dedupe by `(type, id, versionId)`, prove completeness against the bundle's `total`, sort **client-side** |
| `watermark.py` | Durable position, bootstrapped **once** from the server clock (ADR-015) and never re-derived; refuses to move backwards |
| `openmrs.py` | Bounded-window fetch with paging. Read-only — `ServiceRequest` is search/read only on this server anyway |
| `order_poller.py` | The cycle. Claims via the DH-010 ledger, routes stale claims through DH-025's coordinator, and advances the watermark **only** past accounted-for work |

**Two design choices worth knowing, both forced by measured behaviour rather than preference:**

1. **The window is bounded at *both* ends**, which the accepted design did not specify. Without an
   upper bound, records written *during* the fetch land inside a window whose `total` was already
   read, so the completeness check would fire forever on healthy traffic. The top is frozen at a
   **server-issued** instant — taken from the bundle's own `meta.lastUpdated`, which every search
   response carries, so no clock endpoint is needed. Both bounds were verified applied before being
   relied on: an impossible `ge`/`le` range returns 0.
2. **The ledger is only marked `completed` after a real delivery returns a target record.** Marking
   it on mere observation would be the worst possible bug here — when the target leg lands, every
   order polled beforehand would look already-done and never be delivered. Silent loss, at scale.

**Five controls, each breaking a guard deliberately:**

| Control | Result |
|---|---|
| Remove the client-side sort | **5 fail**, incl. the reversed-response test |
| Drop the completeness check | **2 fail** |
| Let the watermark advance past a halted event | **4 fail** |
| Filter to `status == active` only | **3 fail** — the cancellation-loss bug |
| Remove the instant zero-padding | **2 fail** |

⚠️ **The fifth control initially did not fail, and that is worth recording.** The original
fractional-width test compared `.10` with `.9`, where naive and padded comparison happen to agree —
for decimal fractions leading digits dominate, so plain lexicographic order is otherwise correct.
The test proved nothing and 92 tests passed with the padding removed. The real case padding fixes is
**equal moments written at different precision**: `.1` and `.10`. Under naive comparison `.1` reads
as *earlier* than a persisted `.10`, so `advance()` would reject a legitimate re-poll as a
regression and stop the poller. Both tests were rewritten around that, and the control now fails
correctly. **A guard that is never control-tested is a guess** — this is the eighth instance in this
repo of a check passing for reasons unrelated to its claim.

**Incidental confirmation:** running the migration through `docker compose run adapter` was refused
with `RecoveryFenceUnavailable: another adapter process owns startup recovery`. That is DH-025's
process-lifetime fence doing exactly its job against a live adapter — an unplanned live control.

**Remaining for DH-011:** everything downstream of BLOCKER-6 — the delivery implementation, DH-011C's
correlation binding, DH-011G/H/I mappings, DH-011K's crash-point qualification, and DH-011L's
demonstration. The 15-second interval and the loop that calls `poll_once` are deliberately not wired
either; activating a poller with no target would be pointless and would invite a false green.

#### DH-011B — full runtime spike · ✅ COMPLETE 2026-08-17 · explicitly authorized by Liban · synthetic data only

**Verdict: Option F writes genuine native OpenELIS domain state and is NOT a FHIR façade. It cannot
be completed here for two separate reasons, neither of which is an engineering choice.**

**Endpoint, and how it differs from the other one.** Two distinct FHIR servers exist in this stack and
conflating them is the mistake this spike existed to prevent:

| | Business API (Option F) | Standalone store (Option C) |
|---|---|---|
| URL | `https://oe.openelis.org:8443/OpenELIS-Global/fhir/` | `https://fhir.openelis.org:8443/fhir/` |
| Server | HAPI **7.0.2**, embedded in the webapp | HAPI **6.6.0**, separate container |
| Registered by | `AnnotationWebAppInitializer` → `fhirServlet` (`FhirRestfulServer`) at `/fhir/*` | its own image |
| Auth | **HTTP Basic** (`SecurityConfig.BasicAuthRequestedMatcher`); a form-login cookie is refused on POST with `access=denied` | mutual TLS |
| Providers | OpenELIS's own 8 business providers | generic JPA storage |

**Baseline before any write:** `patient 1, person 1, provider 0, sample 0, sample_item 0, analysis 0,
electronic_order 0`. (The single patient was created by this spike's first pass, already committed.)

**Requests made** — all through the supported FHIR surface, all clearly labelled synthetic:

| # | Request | Result |
|---|---|---|
| 1 | `POST /fhir/Practitioner` — `Dhprac Zzsynthetic`, identifier `DHSPIKEPRAC1` | **201**, and returned the created resource body |
| 2 | `POST /fhir/ServiceRequest` — LOINC `94500-6`, subject + requester + `authoredOn` | **400** `Generated lab number is invalid` |
| 3 | Same, plus `occurrenceDateTime` | **400**, identical |
| 4 | Same, plus a supplied `requisition` (synthetic `9990000000001`) | **400**, identical |
| 5 | Same, plus a supplied accession `identifier` (synthetic `9990000000002`) | **400**, identical |

**✅ Native domain changes observed — the headline.** `PractitionerProvider` logged
`Successfully created Practitioner with UUID: …` and a real `clinlims.provider` + `clinlims.person`
row exists (`id=2`, `fhir_uuid bac7daf3-…`). Combined with the earlier `clinlims.patient` row, **the
business providers write OpenELIS's own domain tables.** Reading `ServiceRequestProvider` confirms the
mechanism rather than inferring it: it calls `SamplePatientEntryService`, `SamplePatientUpdateData`,
`SampleOrderItem` and `SampleAddService$SampleTestCollection`, and builds `Sample` and `Analysis` —
the same services the human sample-entry workflow uses. Option F is real.

**🛑 But the laboratory workflow was never reached.** `sample`, `sample_item`, `analysis` and
`electronic_order` are **all still 0**. Every ServiceRequest was refused before any of it.

**Root cause of the refusal, and it is not fixable by an engineer.** The provider calls
`getNewAccessionNumber()` then `validateNewAccessionNumber()`. The configured format is
`acessionFormat = SITEYEARNUM` (upstream's typo) with `Accession number prefix = DEV01` and
`validateAccessionNumber = true`. Parsing `SiteYearAccessionValidator`'s constant pool shows its
format string is **`%013d`** — a **13-digit, all-numeric** accession. `DEV01` is alphabetic, so the
generated value cannot satisfy it. A **numeric facility site code is required**, and a laboratory
accession number is a facility identifier convention, not an engineering default —
**NEEDS LIBAN #7**. Supplying a synthetic accession does **not** work around it: variants 4 and 5
proved the value is always server-generated and a client-supplied one is ignored.

**🛑 Correlation result: the submitted identifier is discarded. (Corrected 2026-08-17 — see below.)**

| Query | `total` |
|---|---|
| `Practitioner?identifier=DHSPIKEPRAC1` — an identifier we **did** submit | **0** |
| `Practitioner?identifier=NEVER-SUBMITTED-XYZ` — **negative control** | **0** |
| `Patient?identifier=DHSPIKEPONE` — submitted on the first pass | **0** |

**The identifier is discarded on ingestion.** The returned Practitioner carries only OpenELIS's own
`provider_uuid` and `facility_id` identifiers; ours is absent, and
`clinlims.provider.external_id` is **NULL**. The submitted correlation is not persisted anywhere.

> ### ⚠️ CORRECTION 2026-08-17 — this section originally overstated the failure
>
> The first write-up concluded that **search itself was broken**, on the grounds that every search
> response's `self` link pointed at `http://fhir.openelis.org:8080/fhir/…`, a port this stack does
> not serve. From that it inferred the DH-025 0/1/2+ contract was *"inexpressible through this
> surface at all"*. **That inference was wrong, and it was wrong in the expensive direction** — it
> would have justified a far larger OpenELIS patch than the evidence supports.
>
> The `:8080` self link is a cosmetic HAPI artifact. Re-tested directly:
>
> | Query | `total` |
> |---|---|
> | `Practitioner?_summary=count` (any) | **1** |
> | `Practitioner?identifier=<OpenELIS's own provider_uuid>` | **1** — proven present |
> | `Practitioner?identifier=0000…0000` (control) | **0** — proven absent |
> | `Practitioner?identifier=NEVER-SUBMITTED-XYZ` (control) | **0** |
> | `identifier=http://openelis-global.org/provider_uuid\|<uuid>` — full `system\|value` | **1** |
> | `identifier=urn:wrong-system\|<same uuid>` — **control** | **0** |
>
> That last row is the decisive one: a **wrong system with the right value returns 0**, so the
> search performs real token matching rather than ignoring the system or substring-matching.
>
> **Corrected conclusion: exact identifier lookup on this surface WORKS, and the DH-025 contract is
> expressible.** The single remaining failure is that OpenELIS does not retain an identifier the
> *client* supplied. That narrows DH-011D from "add a searchable external identifier, including
> search support" to "**persist the incoming identifier**" — the search half already exists.
>
> Still unknown, and not inferable from Patient/Practitioner: whether `ServiceRequestProvider`
> retains a submitted identifier. It is a different provider and may behave differently. That cannot
> be tested until the accession blocker is resolved.

**Cancellation: not testable.** It requires an existing order, and no order could be created. The
provider does expose `@Delete` and `@Update`, so the surface exists; its native state transition
remains unobserved. Deferred with the accession blocker.

**Controls used:** wrong-password → 401 (auth genuinely enforced); unknown search parameter → 400
`HAPI-0524` while advertised `_id` → 200 (so a 200 means real support); the never-submitted-identifier
negative control above; and read-back rather than status code throughout, because of bug 19.

**Nothing was modified to make the spike pass.** Laboratory tests unchanged (**177 active**),
`site_information` unchanged (121 rows), no accession setting altered, no role or permission granted,
no database row written or deleted directly. Every write went through the supported FHIR surface.

**Synthetic resources created, retained deliberately, never to be confused with real data:**

| Resource | Native row | FHIR uuid |
|---|---|---|
| Patient `Dhspike Zzsynthetic` (first pass) | `clinlims.patient id=2` + person | `b010ea84-aab8-4917-ad44-7ba155dff2d5` |
| Practitioner `Dhprac Zzsynthetic` | `clinlims.provider id=2` + person | `bac7daf3-f68a-490c-bf9c-eaebfe520169` |

Both also exist in the standalone HAPI store. No sample, analysis or electronic_order was created. Not
deleted, per this repo's standing rule against removing patient rows to tidy up.

**Verdict against the stated rules.** Native domain state **is** created, so Option F does not fail —
rule 3 does not apply. Correlation **fails**, so **rule 2 applies: Option F remains preferred, and
DH-011D (narrow correlation preservation and search) is the follow-on.** With one addition the rules
did not anticipate: DH-011D cannot be *verified* end to end until a numeric site code exists, because
proving exact search needs a created ServiceRequest. So the facility answer gates the proof, not the
patch.

#### DH-011B — first pass · ✅ PERFORMED 2026-08-17 · synthetic data only

**Option F is real, not a façade — and it is not usable yet.** Both halves are demonstrated.

**The endpoint.** `AnnotationWebAppInitializer` maps `fhirServlet` (`FhirRestfulServer`, a HAPI
`RestfulServer` holding the business `IResourceProvider`s) at **`/fhir/*`** inside the webapp:
`https://oe.openelis.org:8443/OpenELIS-Global/fhir/`. It is **HAPI 7.0.2 embedded in the webapp** —
a different server from the standalone `openelis-fhir` container's HAPI 6.6.0. The compose comment
calling that container "the endpoint the adapter actually talks to" describes option C, not this.

**Authentication is HTTP Basic, and form-login is the wrong mechanism.** `SecurityConfig` has a
`BasicAuthRequestedMatcher` (`haveBasicAuth`) selecting a separate `httpBasicServletFilterChain`.
A cookie session obtained by form login reaches `GET` but is refused on `POST` with
`302 → /Home?access=denied`. With `Authorization: Basic` the same POST succeeds. Control: a wrong
password returns **401**, so the 200s are authenticated rather than open.

**✅ The providers create genuine native OpenELIS state.** `PatientProvider` logged
`Patient persisted with ID: 2` and a real `clinlims.patient` + `clinlims.person` row exists with a
`fhir_uuid`. This is the question the spike existed to answer: Option F is **not** a FHIR façade
over the generic store.

**🛑 But `PatientProvider` returns `201 Created` when the native persist FAILS.** Proven by a
control pair — two identical requests differing only in the patient's given name:

| Request | HTTP | Log | Native rows |
|---|---|---|---|
| given name `Dh011b` (contains digits) | **201** | `ERROR … invalid name format, possibly illegal character` | **0** |
| given name `Dhspike` (letters only) | **201** | `Patient persisted with ID: 2` | **1** |

Same status code, opposite outcomes, and **no `Location` header in either** — so a client cannot
learn from the response whether anything was created, nor what its id is. **An adapter that trusted
this 201 would mark an order delivered that never existed**, writing `completed` into the DH-025
ledger for a write that did not happen. That is the silent-loss direction `WATERMARK-DESIGN.md`
exists to refuse. Recorded as **known bug 19**. Any option-F implementation must verify by read-back
rather than by status code.

**🟡 `ServiceRequestProvider` behaves better and is blocked on configuration.** A synthetic
ServiceRequest (existing upstream LOINC `94500-6`, referencing the synthetic patient) returned
**HTTP 400 `Generated lab number is invalid`** and correctly created nothing — so the false-green is
specific to `PatientProvider`, not universal. The cause is deployment configuration: the accession
prefix is upstream's placeholder `DEV01` with `validateAccessionNumber=true`. **This ties Option F
to facility identity (NEEDS LIBAN #7)** — the lab number is a facility-scoped accession, so it is a
real-world value, not an engineering guess. Option F is therefore **not disproven; it is unfinished**.

**🛑 The DH-025 correlation cannot bind to ServiceRequest on this server.** `ServiceRequest` does
**not** support `identifier` search here — it is rejected with **HTTP 400**, and the controls make
that meaningful: an unknown parameter is also 400, while the advertised `_id` returns 200.
`Specimen`, `Patient`, `Practitioner` and `Organization` **do** accept `identifier` (200).

| Server | ServiceRequest `identifier` search |
|---|---|
| Standalone HAPI 6.6.0 (option C target) | ✅ supported — 200, `total: 0` |
| **Business API HAPI 7.0.2 (option F target)** | ❌ **400, rejected** |

So the two options trade off exactly against each other: the one that reaches native lab state
cannot be queried by our correlation, and the one that can be queried may not reach native lab
state. This is what **DH-011C** must resolve, and it is the concrete trigger for **DH-011D**'s
narrow patch if no alternative works. A possible alternative worth testing before any patch:
`Specimen` *is* identifier-searchable, so the correlation may be bindable there — but a specimen
identifier is an accession-numbering concern in a laboratory, so that is laboratory configuration
surface and not an agent's call.

**Synthetic data left in the throwaway stack, deliberately not deleted:** one OpenELIS patient,
`id=2`, `Dhspike Zzsynthetic`, `fhir_uuid b010ea84-aab8-4917-ad44-7ba155dff2d5`. No sample, no
analysis, no electronic_order — all still 0. Consistent with this repo's standing practice of never
deleting patient rows to tidy up (the 100 fictional OpenMRS demo patients were kept for the same
reason). This stack remains a throwaway verification stack and must never be confused with a clean
instance. **No OpenELIS laboratory test, mapping, or configuration was modified.**

**Still true:** no transport code has been added, no order has been sent, and nothing has been
written into OpenELIS. DH-006's signed-off test mappings remain required before any *pilot* order.
### DH-012 · Adapter: result path OpenELIS → OpenMRS · P0 · ⏸ (needs DH-011)
Record observed `ServiceRequest` / `DiagnosticReport` shapes here → triggers the ADR-004 revisit.

### DH-013 · WhatsApp notifier against mock · P0 · ⏸ (needs DH-012) · 🔒 gate
French default + English templates; content-free ("results are ready", never values). Notifier is
a module inside the adapter (ADR-002). **Status on completion of the mock work is `BLOCKED`, not
`DONE`** (ADR-003).

### DH-016 · Caddy, TLS, subdomains · P0 · 🔨 BUILT + LOCALLY VERIFIED 2026-08-13; public issuance pending #15
Subdomain routing per ADR-008, proxying to the retained O3 gateway per ADR-010. Caddy is the only
container publishing host ports.

**Routing verified through the proxy** (`emr`/`lab`/`id`): OpenMRS 301 (its gateway's own redirect),
Keycloak 302, OpenELIS SPA 200. Security headers (HSTS, nosniff, SAMEORIGIN, Referrer-Policy) present.

**The finding that shaped the config:** OpenELIS's SPA and API **must share one origin**. Reading the
built bundle showed it calls `/OpenELIS-Global/...` as a *relative* path, and its nginx serves static
files only with no API proxy. So `lab.<domain>/` serves the SPA while `lab.<domain>/OpenELIS-Global/*`
proxies to Tomcat. Verified by content, not status code — the API path returns Tomcat's real login
page containing `_csrf`, which the SPA fallback does not.

**The API hop is TLS-verified, not skipped.** DH-021's generated certificate is exported as a PEM so
Caddy has a real trust anchor, rather than `tls_insecure_skip_verify` leaving the edge→backend hop
unauthenticated. Proven by control: swapping in an unrelated CA produced **502 with
`x509: certificate signed by unknown authority`**, and restoring it returned 200.

⚠️ **The first run of that control was invalid and said so falsely.** `docker compose up -d caddy`
re-ran the `openelis-certs` dependency, which overwrote the bogus CA with the real one before Caddy
started — so it "passed" while proving nothing. `--no-deps` gives the real answer. Any future
control that swaps a file a dependency also writes must do the same.

**Remaining before DONE:**
1. Public certificate issuance needs the real `BASE_DOMAIN` and DNS (NEEDS LIBAN #15). With
   `localhost` this uses Caddy's **internal CA** — that proves routing, origins and cookies, and
   proves nothing about ACME.
2. The `email` directive is deliberately absent: Caddy **fails to parse** the whole file if `email`
   is present but empty, taking the proxy down rather than degrading. Add a real address with the
   real domain.
3. `openmrs-gateway`'s `127.0.0.1:8080` publish is **deliberately retained for now**. Removing it is
   part of this ticket, but with no real domain the only route in would be
   `https://emr.localhost:8443` behind a locally-trusted CA — a worse position than today. Remove it
   with #15.

### DH-017 · README for a fresh Ubuntu 24.04 VM · P0 · 🔨 BROUGHT CURRENT 2026-08-13
Now covers the whole deployed stack rather than step 1 only: purpose, honest status, doc map,
prerequisites, `.env` setup, first-boot timing, demo-data controls, maternity beds, **the reverse
proxy and how to actually reach the system**, **a whole-stack verification section**, and
**backup/restore with the drill's two traps**.

Additions this pass:

- A **required-values table** — everything that must be set before the first `up`.
- ⚠️ **"An empty value is not the same as a safe default"**, with DH-021's concrete case: empty
  `OE_KEYSTORE_PASSWORD` silently selected upstream's published `kspass` through a `:-` fallback.
- The DH-016 access model, including the constraint that OpenELIS's SPA and API **must share one
  origin**, and the warning that an empty `email` makes Caddy fail to *parse* its config.
- Restore guidance pointing at `backup/restore-drill.md` for the `--clean` data-loss trap and the
  tenant-isolation loss that drop-and-recreate causes.
- Verification commands that assert **application behaviour, not open ports** — the mistake this
  project has now made three separate times.

**All six documented verification commands were run verbatim against the live stack** and produce
the stated output.

**Not yet closed.** The DoD is "someone else could deploy a second instance without asking Liban
anything", and two things still require him: the real domain/DNS for public TLS, and the facility
values (bed counts, requester identity). A second *local* instance is now reproducible from this
README alone. It should also be re-walked on a genuinely fresh VM before being called DONE —
documentation verified only against an already-working machine is exactly the kind of claim this
repo distrusts.

**A second, distinct fresh-boot verification debt added 2026-08-28 (ADR-030/UI-015F closure pass).**
Ending a Daher Maternity Admission (`EndMaternityAdmissionServlet`'s module) provisions one
`VisitAttributeType` via Initializer on first boot. Its idempotence against an *existing,
already-migrated* database is fully proven twice (a fresh container restart left the row count at
exactly 1, no duplicate). Provisioning it correctly on a *genuinely fresh* database has not been
directly observed — the same environmental startup race ADR-029's own entry above already flagged
(a slow shared-platform Liquibase phase, `stockmanagement`'s changesets specifically, at roughly one
per 50+ seconds) blocks reaching that point before either full completion or a reasonable time
budget runs out. Unlike ADR-029's pass, this one ran a genuine **controlled pair** — the current
image (module + `attributetypes` mount present) against a pre-ADR-030 image (`bug25-final`, no
module, no mount) on two independently fresh databases — and found them hitting the identical
changeset ID at the identical 53117ms duration, confirmed via each container's own log. Since this
module carries zero Liquibase changesets of its own, there is no mechanism by which it could be
contributing to the slowdown; the controlled pair confirms this empirically, not only structurally.
When DH-017's own fresh-VM walkthrough eventually happens, it should also confirm the
`a66602e1-a519-4bea-a460-f4993fec5afb` `VisitAttributeType` provisions exactly once.

### DH-018 · Golden end-to-end demonstration · P0 · ⏸ (needs DH-013, DH-015)
All eight steps of §8, demonstrated live to Liban, including the retry/restart no-duplicates check.

### DH-026 · Facility parameterisation (multi-facility foundation) · 🔨 STARTED 2026-08-15
First ticket of the provisioning API + portal work. The Compose project name is now
`daher-${FACILITY_ID:-default}`, which namespaces every container, volume and network so two
facilities can run on one host.

**A hazard found while doing it, and closed.** The running stack's 14 volumes were created under
the project name `daher-health`. An unset `FACILITY_ID` resolves to `daher-default`, so the next
`docker compose up` would have built a **new empty stack beside the existing one** and orphaned the
patient database — while appearing to start perfectly normally. `FACILITY_ID=health` is now pinned
in the local `.env`, verified to resolve back to the same 11 containers and 102 patients. The same
trap is documented in `.env.example`: this value is an identity, stable for the life of the
deployment, never edited afterwards.

**Collision survey (what actually clashes between two facilities on one host):** only the project
name and the three published host ports. The 7 network aliases live on each stack's own network and
the 12 named volumes are namespaced by project name, so both are free. Locally built image tags are
deliberately shared — build once, run many.

**Still open before facility #2 is real:** the host-port model (only one Caddy can own 80/443, so
either one shared edge proxy or per-facility loopback ports behind one), automated DNS/TLS, and the
per-facility secret generator.

### DH-027 · Provisioning API foundation · 🔨 IN PROGRESS 2026-08-15
New system under `provisioning/`, separate service and deploy, sharing this repo's decision
record. Python/FastAPI to match the adapter (ADR-020).

**Answers received 2026-08-15:** one VM per facility by default but **deploy target is a field on
the facility record**, not a hardcoded assumption; DNS built against a provider-agnostic interface
and left explicitly unconfigured until a domain exists; portal is a self-contained FastAPI + Jinja2
app rather than integrating with a website that does not exist yet.

**Built and verified — 159 tests, Ruff clean:**

| Module | What it does |
|---|---|
| `secrets.py` | Per-facility credentials for all 16 stack variables. Encodes constraints learned by *running* the stack: OpenELIS's narrow `*$#!` alphabet, OpenMRS complexity plus its refusal of "admin", the Compose project-name restriction. `__repr__`/`__str__` redact, tested by formatting into a log line |
| `models.py` | Closed (`extra="forbid"`) schemas, soft-only decommission, `dns_configured` defaulting to **False** |
| `facility_env.py` | Renders/writes a facility `.env` at 0600, created restricted rather than chmod-ed. **Refuses to overwrite** — rewriting is not rotation, it produces a stack whose passwords no longer match its own databases |
| `audit.py` | Hash-chained, `O_APPEND`+`fsync`. Written by the code that performed the action, including failures |
| `auth.py` | Scoped credentials, hashed at rest, constant-time compare, decoy hash so an unknown key id costs the same work |
| `ratelimit.py` | Per-caller token bucket on a monotonic clock |
| `hosts.py` | The deploy-host allowlist and the occupied-host refusal |
| `repository.py` | Facility records. **Holds no clinical credentials**, which is the strongest layer of the guarantee |
| `app.py` | The FastAPI surface wiring all of it together |

**The structural no-clinical-data guarantee (ADR-021).** Three layers: closed schemas; a test
walking every model against clinical vocabulary; and — the only one not depending on anyone staying
careful — **the service holds no clinical database credentials at all**. Control-verified: adding a
`patient_count` field made two independent tests fail; removing it returned to green.

**Deploy transport decided — ADR-022: pull-based host agent, outbound-only.** Options were laid out
with blast radius *before* building. Rejected: remote Docker socket (collapses to "the API is root
everywhere") and signed webhook (a bespoke listener parsing untrusted input, for the same
containment SSH forced-command already gives). Fallback is SSH forced-command.

**The constraint that decided it:** Docker access *is* root access, so the compose file must live on
the facility host and the API sends **parameters, never a spec**. Otherwise "provisioning API" and
"root on every clinical VM" are the same sentence.

**Two escalation paths closed**, both live in code written the same day and both arriving through
requests that look entirely routine:

1. `deploy_target` is resolved from an operator-registered **host allowlist**, not supplied by the
   caller. An address in the body is rejected by the closed schema, not silently ignored.
2. An already-occupied host is a **hard conflict**, never an overwrite — reusing one would clobber a
   live env file and attach a new facility to an existing patient database.

**The API no longer generates facility credentials.** Under ADR-022 the agent generates them locally
when it pulls its job, so a breached API host has **never held** any facility's database passwords.
That is what lets the walkthrough say a compromised API *cannot* reach a clinical database, rather
than that it should not.

**Deliberately incomplete, and each reports its own incompleteness rather than appearing to work:**

- **Deploy** — no host agent exists. `POST /facilities` returns 201 meaning *recorded*, not
  *running*, and lists what did not happen in `incomplete_steps`.
- **DNS/TLS** — no domain or provider (NEEDS LIBAN #15). `dns_configured` is always `false`.
- **Audit log external destination** — the chain makes tampering *detectable*, not *impossible*.
  Surviving a host rebuild needs an external append-only sink that does not exist.
- **Rate limiting** is in-memory and per-process: it resets on restart and N workers multiply the
  limit by N.
- **The portal** is not built.

**Agent job vocabulary and local validation built 2026-08-16 — 200 tests.** `jobs.py` defines a
closed job type carrying **parameters, never a spec**: no field can name an image, command, volume,
or compose file, and a test asserts that across every field of both models. Control-verified by
adding an `image` param and watching it fail with ADR-022's reasoning attached.

`daher_agent/validation.py` implements the five refusals, each tested as an attack rather than a
negative case: wrong host, unknown verb, hostile parameters (twelve `facility_id` payloads —
`; rm -rf /`, `$(whoami)`, backticks, pipes, newlines), stale/replayed jobs, and **provisioning over
a facility that already exists locally**. That last one is the check a compromised issuer cannot
skip, because it lives on the far end.

`FACILITY_ID` is validated at **both** ends, plus a second independent character filter, because it
reaches a Compose project name and the cost of being wrong there is root.

**Enrolment built 2026-08-16 — 220 tests.** The agent generates its long-lived credential **on the
facility host** and sends only the hash, so the API never sees an agent secret even during
enrolment, and a breached API host cannot impersonate any agent past or future. Passing a secret
where the hash belongs is refused, so the dangerous wiring is not reachable by mistake.

Enrollment tokens are **single-use** (replay refused even inside the lifetime), **short-lived**, and
**bound to one host** — a token for `host-a` cannot enrol `host-b`, so a leak cannot introduce an
attacker-controlled host into the fleet. Minting for an already-enrolled host is refused rather than
silently replacing a live agent's credential. Errors never contain the presented token.

**Job queue and poll loop built 2026-08-16 — 240 tests.** Jobs are addressed to one host and
claimable only by it; a claimed job is **held, not deleted**, so a crash between claiming and
finishing leaves work visible rather than silently lost (DH-025's reasoning). `refused` is a
distinct outcome from `failed` — the boundary working must never read as the deployment breaking.

The poll loop's tested property is **staying alive**: an unreachable API, a malformed payload, a
smuggled `image` param, an executor that raises, and a reporter that raises are each survived, and
a hostile job is refused *while the next legitimate job still runs*. An agent that crashes on
hostile input has been switched off by the attacker, and a host that is not polling is also not
getting backups or updates.

⚠️ **The transport is WRITTEN, NOT DEMONSTRATED.** The loop is unit-tested against injected
transport and executor. "Polls a separate host over a network and refuses hostile input over the
wire" needs two hosts, or two containers on separate networks standing in for them, and that does
not exist. Same distinction this repo applies to the WhatsApp mock and off-site backup.

**Two-container demonstration PERFORMED 2026-08-16 — `provisioning/demo/README.md`.** Real API and
real agent in separate containers on a bridge network, exercising the production poller,
validator and HTTP transport. Only the executor is substituted.

**Demonstrated:** authenticated polling over a real socket; 1 of 7 jobs reaching the executor;
6 of 6 hostile payloads refused over the wire; the agent's **own** host check firing on a
deliberately misrouted job; **no inbound port** on the agent (probes to 22/80/443/8000/8080/9000
all refused); "held, not deleted" across a real **SIGKILL** — the job stayed `claimed` and was never
re-handed-out; the agent resuming after process death; and a 14-entry audit chain that verifies.

**Three defects the unit tests structurally could not find:**

1. **The agent's own host check was never exercised.** The queue routed the misrouted job away, so
   the agent never saw it — the demo would have passed while silently never testing that refusal.
2. **A refused misrouted job could not be reported.** The report endpoint authorised on the job's
   *self-declared* `host_id`, a field a compromised issuer controls, so the agent refused hostile
   work and the API recorded nothing. Now authorised on `delivered_to`, recorded at claim time.
3. **The audit call could fail *after* the state change**, leaving a job finished with no record.
   An audit that can fail after the event it records is worse than none, because the gap is
   invisible. Now read defensively, before mutating state.

⚠️ **Honest limits — the transport is PARTIALLY demonstrated.** Five of six refusals came from
*schema* validation, not the agent's deeper `revalidate_params`/character filter, so that second
layer remains proven only by unit tests that bypass the schema. And three things are entirely
untested: **TLS** (the demo runs plain HTTP, so certificate and hostname verification are unproven
while ADR-022 assumes them), **partial/truncated reads**, and **the real executor** — nothing has
ever started a clinical stack through this path.

**TLS demonstrated 2026-08-16.** The demo now runs over HTTPS with a private CA, and **4/4
controls behave correctly**: the real CA succeeds; a bogus CA, a wrong hostname, and the system
trust store alone are each refused with genuine `CERTIFICATE_VERIFY_FAILED` reasons. The controls
are the test — presenting only the correct certificate would pass identically with verification
disabled. Full job flow re-run over TLS: 14 audit entries, 6 refusals, chain verifies, and only the
legitimate job reached the executor.

**Three more things the TLS leg caught**, none reachable over plain HTTP: the CA lacked `keyUsage`
and OpenSSL 3 refused it outright; the hostname control first passed for the **wrong reason**
(`127.0.0.1` had nothing listening, so it never reached TLS — now it hits the real listener by IP
and fails with `IP address mismatch`); and after the HTTPS switch the hostile harness still pointed
at the old plain-HTTP URL, enqueued nothing, and produced a convincing empty result that looked
like success.

**Still not demonstrated:** partial/truncated reads, and the real executor — nothing has ever
started a clinical stack through this path.

**Network exposure decided and implemented 2026-08-16 — ADR-023.** The provisioning API is
**not publicly reachable**: firewalled to the portal server's address, portal on its own domain
separate from any marketing site and from `BASE_DOMAIN`, and the browser never talks to the API.
Rejected a VPN tunnel for now — stronger, but a persistent moving part that only adds protection
against an attacker who has already stolen the portal credential *and* can spoof a source address.
Upgrade path recorded: WireGuard later, **no code changes**.

Implemented as **defence in depth**, because firewalls get edited: `network.py` enforces a source
allowlist ahead of authentication, so an unauthorised source cannot even reach credential
comparison. It uses the **real peer address and never `X-Forwarded-For`** — a test asserts a
request carrying three forwarded headers pointing at an allowed address is still refused. A missing
or unparseable peer is refused rather than allowed, so nothing that obscures the peer becomes a
bypass. 257 tests.

**Compose executor built 2026-08-16 — 276 tests.** The last seam where a parameter reaches a
command. Five rules: **never a shell** (argv list, `shell=False` — removing injection as a
*category* rather than filtering for it); the **compose file is fixed and local**, never from the
job, because an issuer who could name a spec would be root on the host; the facility id validated a
**third** time immediately before becoming an argument; secrets generated **on the host**; and
**`stop`, never `down`** — a test asserts no verb can emit `--volumes` or `-v`, so nothing here can
destroy clinical data.

Tests assert the **exact argv** rather than "it worked" — a test checking only the return value
would pass identically whether the command was a safe list or a shell string with the id
interpolated. Both guards control-verified: removing the executor's validation lets a hostile id
reach the command and fails the suite; `shell=True` fails it too.

**Persistence built 2026-08-16 — 304 tests.** SQLite, one file. Facilities, host registrations,
agent enrolments, API credentials and the job queue all survive a restart; previously an API
restart lost the entire fleet and recovery meant re-enrolling every VM by hand.

Proven end to end through the real app, not just the stores: a facility created before a restart is
still listed after one, the portal credential still authenticates, and a host claimed before a
restart still refuses a second facility with the "existing patient database" reason. "Held, not
deleted" now survives an **API** restart too, not only an agent crash.

**No clinical credential is stored** — facility secrets are generated on the facility host
(ADR-022); this database holds identities and hashes. Tests assert no column is named for a
password or secret, and that an issued key's secret is not recoverable from the table.

Two races closed with conditional updates rather than read-then-write: claiming a host updates
`WHERE facility_id IS NULL`, and redeeming an enrolment token updates `WHERE redeemed_at IS NULL`
— so single-use survives two requests arriving *together*, not just in sequence.

**Portal built 2026-08-16 — 326 tests.** Server-rendered FastAPI + Jinja2 (ADR-020): facility
list with health/backup/version, a create-facility form, and a login. **The browser never talks to
the provisioning API** — the portal calls it server-side, and a control-verified test asserts no
rendered page contains the credential, `Authorization`, or `Bearer`.

**Auth libraries, flagged as the build prompt asked:** `argon2-cffi` for password hashing (Argon2id;
the library owns salt, parameters, constant-time verify) and Starlette `SessionMiddleware` for
sessions (signed with `itsdangerous`). Both are the places hand-rolling goes wrong, so both are
delegated.

⚠️ **No MFA** — a single password protects a control plane that can create and stop clinical
deployments. That is the portal's weakest link, recorded rather than left to be discovered. Upgrade
is an external IdP via Authlib.

Hardening: `https_only` + `SameSite=strict` + HttpOnly session; session cleared and reissued on
login so a fixated cookie is useless; **CSRF on every POST including login**; login rate-limited by
source; `no-store` on fleet pages so a stale "backup succeeded" is never shown as current; CSP with
`script-src 'none'`. The login error never reveals which half was wrong.

**End-to-end demonstration performed 2026-08-16 — four containers, fixed subnet.** Signing into
the portal and submitting the create-facility form produces a facility record, a **queued
provisioning job**, an agent that claims it over TLS and validates it locally, an execution, and a
report the API records from what it observed. Audit: 6 entries, chain verifies.

**ADR-023 demonstrated against real addresses**: an outsider holding a *genuinely valid* portal
credential is refused `403 source not allowed` on both the portal and agent endpoints, recorded as
`blocked-source` naming `172.30.0.40`, and cannot reach the agent on any port.

**Two things the full chain caught that every unit test missed:**

1. **The portal cannot work over plain HTTP** — its session cookie is `Secure`+`SameSite=strict`,
   so login silently failed. The portal was failing closed *correctly*; the harness was wrong. The
   portal now has its own certificate, which is how it must run.
2. **Creating a facility did not enqueue anything.** The API recorded a record and stopped, so the
   agent never acted. Invisible until the whole chain ran. Now wired — and `incomplete_steps` still
   reports DNS and backup, because **queued is not running**.

**`rotate-credentials` built 2026-08-16 — 346 tests — and building it found that the obvious
implementation would break every facility (ADR-024).** Rewriting the env file rotates nothing
useful and destroys plenty: database passwords were set once by `initdb`, so the stack would
present new credentials to databases holding the old ones and **lose every database**;
`OMRS_ADMIN_PASSWORD` is first-boot-only, so the file would claim a change that never happened;
and the backup passphrase decrypts existing archives, so changing it without escrow makes **every
prior backup unrecoverable** — including the one needed in the disaster that prompted the rotation.

So rotation is **scoped and honest**: it rotates the TLS material (DH-021's generator self-heals)
and Keycloak client secrets (reapplied by the reconciliation job), and **refuses the rest with what
is actually required** — `ALTER USER` in the engine, a change through OpenMRS, escrow of the old
passphrase. The response carries `not_rotated`, because listing only what it rotated would imply
the whole set was replaced. Control-verified: removing the guard fails 13 tests.

**This narrows ADR-021's blast radius slightly, in the right direction:** an attacker holding the
portal credential can force TLS and client secrets to be regenerated — disruptive, loud in the
audit log — but cannot use rotation to lock a facility out of its own databases or destroy its
ability to decrypt backups.

**Per-facility backup wiring built 2026-08-16 — 360 tests — and it found a serious flaw in what
had already been built (ADR-025).**

⚠️ **The backup passphrase would have lived only on the machine being backed up.** ADR-022 has the
agent generate credentials locally; applied to `BACKUP_ENCRYPTION_PASSPHRASE` that produces
archives encrypted with a key that dies with the host — the exact disaster backups exist for. Every
off-site archive would have been permanently undecryptable. **That is not a backup, it is an
archive nobody can open**, which is worse because it looks like one. Fixed: the operator supplies it
at host bootstrap, so it exists in two places by construction, and a test asserts the generator does
not produce it.

**Backup is scheduled only when it can succeed** — a passphrase *and* an off-site destination.
Auto-enabling it everywhere would recreate DH-022's restart loop at fleet scale; quieting it with
`BACKUP_REQUIRE_OFFSITE=false` would be worse, producing local-only archives beside the data they
protect while showing green. When it is not schedulable the facility **reports having no backup**,
and the portal shows **"⚠ not protected"** with a banner — a facility with no backup is one disk
failure from losing patient records, not a neutral "nothing yet".

Scheduled is still never shown as verified: the restore-drill date sits beside the backup state, and
a facility that has never had one says `never`. DH-014's off-site verification and DH-015's
performed restore remain the pinned gates.

**A stack actually started from a provisioning job — demonstrated 2026-08-16.** The last
"written, not demonstrated" gap. The demo agent was given a **real Docker daemon** and the **real
`ComposeExecutor`**, so clicking the portal form ends with containers running.

Observed: `docker compose` genuinely invoked and succeeding; a container running under the
project-derived name `daher-demo-birth-centre`; the agent's own env file at mode `600` with 33
variables; locally generated secrets reaching the container (`OMRS_ADMIN_PASSWORD` length 24,
`OE_KEYSTORE_PASSWORD` length 32) having never been transported; `OMRS_DEMO_CONTENT=off` and
`SSO_ENABLED=false` applied; the backup passphrase correctly **absent** (ADR-025); 6 audit entries
with a verifying chain.

⚠️ Giving the agent Docker access is **by design** — it runs on the facility host and must bring
the stack up. Docker access is root access, which is exactly why the API never has it and why the
compose file lives on the host rather than arriving in a job.

**What it does not prove, and this is the remaining gap:** the compose file is a **minimal stand-in,
not the clinical stack**. No OpenMRS, OpenELIS, Keycloak or adapter has ever booted from a
provisioning job, and the ~49-minute first boot is unexercised on this path.

**Per-verb execution timeouts 2026-08-16 — 371 tests — closing known bug 17.** One 900s bound
covered every verb, and it was shorter than a single healthcheck's grace period in the compose file
the executor exists to run (`openmrs-backend` declares `start_period: 1800s`, and `up --detach`
waits on that graph). The first genuine provisioning would have reported `failed` while the stack
came up correctly — a false negative on the one operation nobody is watching.

Verbs that wait on the health graph (`provision`, `start`, `rotate_tls`) now get 5400s, ≈1.8× the
measured 49-minute cold boot; `backup_now` gets its own bound because it scales with stored data;
`status`/`stop` stay tight so a genuine hang is still visible quickly. Per-host configurable, since
facility hardware differs. An unlisted verb raises rather than inheriting the bound chosen for `ps`
— that fall-through is how bug 17 happened in the first place.

**The guard reads the real compose file**, in the same idiom as the existing env sync test: it must
clear every `start_period` and the DH-002 measured cold boot, so adding a slower gate to the
clinical stack fails this suite instead of the first deployment. Control-verified three ways
(restore 900s → the two guards fail by name; wire `provision` to the fast bound → the per-verb tests
fail; unmount the compose file → **fails rather than skips**).

⚠️ **Written and unit-verified, not demonstrated against a real boot — and be precise about what
"clears the cold boot" means.** The measured 2940s is the *backend* reaching first HTTP 200. `up`
then still waits, serialized behind it, for six setup jobs, openelis-webapp's 180s gate, Keycloak's
120s, and image pulls on a fresh host. 5400s leaves ≈2460s for that remainder, which is
**unmeasured**. The guard asserts what a test can assert — every declared grace period and the
measured boot — not the composition. Only a real clinical stack booting through a provisioning job
settles whether 5400s is right, and that is the gap DH-027 has carried throughout. Bug 18 (the
agent not polling during a long job) was fixed 2026-08-20 — see the known-bugs table. The backup
bound remains unmeasured.

**Next:** DNS/TLS when the domain lands — the last `incomplete_step` the API reports.
⚠️ The poll loop is the first component that cannot be honestly verified on this machine — proving
"polls a separate host and refuses hostile input" needs two hosts, or two containers on separate
networks standing in for them. Validation is unit-tested hard; the transport will be marked
*written* rather than *demonstrated* until that exists.

### DH-027D · Observed infrastructure status · ✅ DONE 2026-08-20

**The problem this closes.** A facility could read `RUNNING` because an agent once reported
that `docker compose up` returned 0. That is lifecycle evidence, not health. Worse, the
`/status` endpoint **fabricated** its answer — zero service counts with a **fresh
`checked_at`**, a timestamp implying a check that had never happened.

**Two concepts, kept apart (§2).** `state` is the lifecycle phase; `health` is what the last
real check saw. A facility can sit in lifecycle `RUNNING` while its stack is `DEGRADED` right
now, and the portal shows both in separate columns. `checked_at` is `None` — never `now()` —
when no check has been reported, and the portal renders that as **"never checked"**.

**How the check is made.** `docker compose ps --all --format json` **plus**
`docker compose config --format json`, both read-only, both fixed and local, run by the
existing agent through the existing `ComposeExecutor`. The control plane never touches the
host (ADR-022 unchanged). `config` is needed as well as `ps` because `ps` can only show
containers that *exist* — a declared-but-absent service is invisible without it — and because
`config` respects active profiles, so a facility with no off-site backup is not marked
degraded for lacking a backup container it was never meant to run (§6).

**One-shots are derived, not listed.** A setup job is any service another service waits on with
`condition: service_completed_successfully` — seven of them on the clinical stack. ⚠️ A restart
policy does **not** distinguish them: every service in the real compose file declares one,
including the one-shots. That was checked rather than assumed, and a hand-maintained list would
be wrong the first time someone adds a setup job.

**Runtime evidence — the real clinical stack, read-only, nothing restarted:**

```
health   : degraded
services : 10/11 long-running
reason   : caddy unhealthy
```

with all seven one-shots correctly `exited_ok` and **not** degrading anything. ⚠️ The real `ps`
output is **28 KB** — the executor's previous 4000-character stdout cap would have truncated
the JSON into something unparseable, so the cap was raised to 64 KiB.

**Caddy / bug 20 handled honestly (§7).** Caddy's routes serve correctly while its healthcheck
cannot `exec`, so Compose reports `unhealthy`. DH-027D reports exactly that and **does not**
conclude the facility is unusable, hide it, or restart it. The live stack therefore reads
`degraded` — which is the truthful answer, and it is bug 20 that needs fixing, not the reporting.

**Controls — three, each breaking a load-bearing property:**

| Control | Result |
|---|---|
| Trust the exit code; ignore parsed states | **10 fail** |
| Let an agent assert health by omission | **1 fails** — the security-shaped one |
| Treat a one-shot `exited(0)` as a failure | **5 fail** |

Plus the existing clinical-vocabulary guard, control-verified against the *new* models: adding
`patient_count` to `InfrastructureReport` fails `test_no_clinical_data` by name (§12).

**A buggy or compromised agent cannot assert health.** A STATUS report with no infrastructure
evidence, or with evidence that fails schema validation, is persisted as **UNKNOWN with the
reason** — never HEALTHY. `UNKNOWN` is rendered "warn", not "ok", because "we have not checked"
must not look like "we checked and it is fine".

**Lifecycle is not rewritten by a check (§14).** No STATUS observation moves `FacilityState`; a
transient unhealthy reading cannot drive a lifecycle transition. If that should ever change it
needs a transition table and a decision, not an implicit side effect.

**Verified:** 27 new tests, **453 provisioning tests total** (from 426), Ruff clean, all four
suites green.

⚠️ **Boundaries.** There is **no scheduler** — a STATUS job is queued when something asks for
one, so an observation is only as fresh as the last request; the portal shows the age precisely
so that is visible rather than assumed. Freshness is *displayed*, not enforced by a staleness
threshold, because how often a fleet should be polled is an operations decision. Only the
**latest** check is persisted; history is deliberately not a metrics database. And this is
verified against real `ps`/`config` output plus unit tests — **not** against a facility
provisioned through the control plane, which remains DH-027J.

### DH-027G · Backup bootstrap / off-site protection wiring · ✅ DONE 2026-08-21

**The problem this closes.** `BackupSettings`/`render_env` (ADR-025) were fully built and
unit-tested, but `ComposeExecutor._provision()` never passed `backup=` to `render_env` — a real
provisioning run wrote an env file with no backup section no matter what an operator had
configured on the host. Separately, `BACKUP_NOW` ran `docker compose --profile backup run --rm
backup` unconditionally (would fail loudly and confusingly on an unconfigured host), and
`/facilities/{id}/status` **hard-coded** `backup_state=BackupState.NEVER_RUN` regardless of
anything an agent ever reported — a placeholder wired to nothing, the exact DH-027D-shaped defect
applied to backup instead of health.

**Where the passphrase lives, and where it explicitly does not (ADR-025, unchanged by this
ticket).** The operator places it — and the off-site destination — in the facility agent's own
process environment at bootstrap, the same manual step that already delivers `AGENT_CREDENTIAL`.
`backup_settings_from_env()` (`daher_agent/runner.py`) reads `BACKUP_ENCRYPTION_PASSPHRASE`,
`BACKUP_S3_BUCKET`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`,
`BACKUP_RETENTION_DAYS` and builds a `BackupSettings` (or `None`), passed into `ComposeExecutor`.
The control plane **never receives any of it** — not transiently, not for validation — because
ADR-025 already decided delivery happens at bootstrap, not through a job or the API; this ticket
did not re-litigate that. What the control plane learns is a three-value outcome only
(`not_configured` / `success` / `failed`) plus a short operator-language reason, via a new
`BackupReport` model structurally identical in spirit to `InfrastructureReport` — no bucket name,
no archive path, no credential, covered automatically by the existing clinical-data guard
(`response_models()` auto-discovers every model in `models.py`) and by a new parallel
secret-vocabulary guard added alongside it for exactly this ticket.

**BACKUP_NOW now answers honestly, mirroring `_status`'s convention exactly:** `succeeded=True`
means "the agent correctly determined the state," not "the facility is protected" — so an
unconfigured host reports `not_configured` with `succeeded=True` and the command is never
attempted (control-tested: see below), while a real attempt that fails reports `failed` with
`succeeded=False`. `/facilities/{id}/status` now derives `backup_state` from the latest such
report (`SqliteBackupStore`, latest-only, same pattern and same table shape as
`SqliteHealthStore`), defaulting to `NEVER_RUN` — "no evidence yet" — when none has ever arrived.
A malformed or missing report is persisted as `FAILED`, never `SUCCESS` (DH-027D's fail-closed
rule, applied here). Portal's `at_risk`/`BACKUP_CLASSES` — which pre-date this ticket — did not
know about the new `NOT_CONFIGURED` value; both were extended, found and closed in this same
change rather than left silently unreachable.

**Persistence.** `SqliteBackupStore`/`InMemoryBackupStore` (`store.py`), a new `facility_backup`
table, wired into `create_app(backup_store=...)`. Directly restart-tested (open, write, close,
reopen, read back) — worth noting because auditing this ticket found `SqliteHealthStore` itself,
DH-027D's equivalent, has **zero** test coverage; this one does not repeat that gap.

**Restore-drill stays untouched and separate, by design.** `restore_drill_last_performed` is not
set by anything in this ticket — a human-performed drill (`backup/restore-drill.md`) is out of
scope here, and a test asserts a successful `BACKUP_NOW` report leaves it `None`.

**Verified:** 44 new tests, **508 provisioning tests total** (from 464), Ruff clean, all four
suites green (637 total: adapter 94, provisioning 508, seed 16, form-safety 19).

**Control-tested, four deliberate breaks on a throwaway copy, each restored after:**

| Control | Result |
|---|---|
| Disable the `NOT_CONFIGURED` refusal in `_backup_now` (always attempt the command) | **5 fail** |
| Malformed/missing evidence reported as `SUCCESS` instead of fail-closed `FAILED` | **2 fail** |
| Revert `_provision()` to not pass `backup=` to `render_env` (the exact original defect) | **1 fails** |
| Revert the portal's `at_risk` tuple to the pre-ticket three values (drop `not_configured`) | **1 fails** |

Plus the existing secret-vocabulary guard, control-verified the same way DH-027D's clinical guard
was: a throwaway model with a literal `passphrase` field fails the check by name.

⚠️ **Boundaries, stated rather than glossed.** No API endpoint yet queues `BACKUP_NOW` on demand —
matches `STATUS`'s existing state (DH-027D never built one either); both currently require
enqueuing a job directly (demo harness or a test, as `tests/test_backup_status.py` does). More
significantly: `backup/scheduler.sh`'s nightly run is autonomous, inside the backup container,
**independent of the provisioning job queue** — the control plane has no visibility into those
runs unless a `BACKUP_NOW` job is explicitly issued, so `backup_state` reflects the last
*requested* check, not necessarily last night's real backup. Reading `last-run.status`
(`backup/README.md`) into a report would close that gap; deliberately not built here — it is a
second, separable piece of surface (a new read-only executor command, new parsing, new tests), and
DH-027G's own scope was the wiring gap the ticket named, not every path to full visibility. Not
exercised against a real S3-compatible target or a real `docker compose run --rm backup` — DH-014
already established that boundary (local path verified 2026-08-13, off-site still unexercised) and
this ticket did not re-open it; verification here is at the wiring/executor level, proving
configuration reaches the real path, not re-proving backup mechanics DH-014 already owns.

### DH-027H · Scheduled backup evidence reaches the control plane · ✅ DONE 2026-08-21

**The gap this closes — named explicitly in DH-027G's own boundary note above.** `backup/scheduler.sh`
runs entirely inside the backup container, independent of the provisioning job queue, calling
`/usr/local/bin/daher-backup` nightly at `BACKUP_HOUR_UTC` and writing
`${BACKUP_OUTPUT_DIR:-/backup}/last-run.status`. Before this ticket the control plane had no way to
see that file: `backup_state` only ever moved on an explicitly-issued `BACKUP_NOW`. A facility could
show a green manual test run from last week while the actual nightly backup had been silently
failing every night since.

**Design: extend `STATUS`, not a new verb.** `STATUS` is already the fleet's one read-only
observation seam — it runs alongside a mutating job (bug 18's status slot), and DH-027D's `config`/`ps`
pair already reads compose state this same way. `_status` now composes two independent observations,
`_observe_infrastructure` (renamed from the old `_status` body, unchanged logic) and the new
`_observe_backup`, into one `ExecutionResult` carrying both `infrastructure` and `backup`. No new
`JobVerb`, no new entry in the closed job vocabulary, no change to `validation.py`/`jobs.py`/`queue.py`.

**How the read works, and why it cannot become arbitrary file access.** `_observe_backup` checks
`self.backup` locally first — unconfigured hosts report `NOT_CONFIGURED` without ever touching Docker,
exactly like `_backup_now` already does. A configured host runs exactly one more fixed, read-only
command: `docker compose --profile backup exec -T backup cat /backup/last-run.status` — the service
name (`backup`) and the path (`BACKUP_STATUS_PATH`, a module constant) are hard-coded in
`executor.py`, never derived from a job or any caller input, the same guarantee `compose_file` already
has. This is not a new capability; it automates the exact manual check `backup/README.md` already
documented (`docker compose exec backup cat /backup/last-run.status`).

**Parsing is a pure function, strict, fail-closed** (`daher_agent/backup_observation.py`,
`parse_last_run_status`). `last-run.status` is `<STATE> <TIMESTAMP>` on exactly one line — anything
else (empty, extra fields, an unrecognised state word, an unparseable or future-beyond-tolerance
timestamp, more than one line) raises `BackupStatusError`, which `_observe_backup` converts to
`NEVER_RUN`, never `SUCCESS`. The future-tolerance window (60s) reuses `JobValidator`'s existing
clock-skew constant rather than inventing a second one. `BackupState` gained no new value: exec
failures, an absent file, and malformed content all report the same `NEVER_RUN` — deliberately, since
all three carry the identical actionable meaning to an operator ("no confirmed evidence"), and the
`reason` field carries the specific truth. **The timestamp is the backup's own run time, parsed from
the marker — never `datetime.now()`.** `backup.sh`'s `TS` is captured once at script start, so
`BackupReport.checked_at` for a scheduled observation is when the backup actually ran, not when the
agent happened to check; the portal's existing `_describe_age` now shows real backup age for exactly
this reason.

**`backup.sh` writes the marker atomically now** (`status()` in `backup/backup.sh`): the old `>
"$STATUS_FILE"` truncates before writing, so a reader landing in that window would see an empty file.
Now written to `${STATUS_FILE}.tmp` and `mv -f`'d into place — `mv` within one directory is a
`rename(2)`, atomic on POSIX. Small, in-scope fix: this file is the one artifact this ticket's
trustworthiness depends on.

**Out-of-order evidence cannot regress the fleet's displayed state.** Two independent paths report
backup evidence now — manual `BACKUP_NOW` and scheduled `STATUS` observation — with no guarantee they
arrive in the order the backups they describe actually happened. `SqliteBackupStore.record` is now
conditional in SQL (`WHERE excluded.checked_at >= facility_backup.checked_at`), the same
conditional-UPDATE pattern `SqliteHostStore.claim`/`release` already use to avoid a read-then-write
race — applied here to a document-then-write race instead. `InMemoryBackupStore` enforces the
identical guard in Python. Ties go to the incoming report (a defined, if rare, choice). Real newer
evidence — good or bad — always supersedes older evidence in either direction; only a genuinely stale
report is dropped.

**The API accepts backup evidence from `STATUS` too, but not on the same fail-closed terms as
`BACKUP_NOW`.** For `BACKUP_NOW`, producing backup evidence is the job's entire purpose, so absence is
as untrustworthy as malformed content — both record `FAILED`, unchanged from DH-027G. For `STATUS`,
backup evidence is additive: a mixed-version agent that has not been upgraded to send it yet must not
make backup look `FAILED` merely because *this particular check* did not carry it, so a `STATUS`
report with no `backup` field simply does not touch `backup_records`. Present-but-malformed is
fail-closed either way.

**Restore-drill stays untouched and separate**, proven for the scheduled path the same way DH-027G
proved it for the manual one: a test asserts a successful scheduled observation leaves
`restore_drill_last_performed` at `None`.

**Verified:** 41 new tests, **549 provisioning tests total** (from 508), Ruff clean, all four suites
green (678 total: adapter 94, provisioning 549, seed 16, form-safety 19).

**Control-tested, four deliberate breaks on a throwaway copy, each restored after:**

| Control | Result |
|---|---|
| Parser always returns `SUCCESS` regardless of the marker's actual state word | **2 fail** |
| Remove the out-of-order guard from both `SqliteBackupStore` and `InMemoryBackupStore` | **5 fail** |
| Trust the `exec` command's own exit code instead of the marker's content | **5 fail** |
| A successful backup observation sets `restore_drill_last_performed` | **2 fails** |

Plus the existing secret-vocabulary guard (`test_no_clinical_data.py`, DH-027G) — unchanged and
re-verified as still passing, since `BackupReport`'s shape did not change; a fifth deliberate break of
that specific guard was not re-run because it is verb-agnostic and model-structural, not something
this ticket's logic could plausibly disable as a side effect.

⚠️ **Boundaries, stated rather than glossed.** No API endpoint yet queues `STATUS` or `BACKUP_NOW` on
demand — unchanged from DH-027D/G; both still require enqueuing a job directly (demo harness or a
test). `NEVER_RUN` is used as the fail-closed target for three different underlying causes (absent
file, exec failure, malformed content) rather than a fourth `BackupState` value — a deliberate choice
to avoid enum proliferation, with the distinction preserved in `reason` rather than in the type. Not
exercised against a real backup container's `last-run.status` — proven against the exact documented
command and file format, with a real (if synthetic) marker text, not a live nightly run. DH-014's
off-site verification boundary is unchanged and not re-opened by this ticket.

### DH-027C · Facility lifecycle state machine · ✅ DONE 2026-08-17

**The gap:** nothing in the codebase ever set `RUNNING` or `DEGRADED`. Every facility stayed
`PROVISIONING` forever, even after its stack was confirmed up — honest, but it meant the fleet
view could never show a working facility.

**Now one explicit table**, `next_facility_state(verb, reported, current)`, driven only by an
**agent report about work it actually ran**:

| Verb | Report | From | To |
|---|---|---|---|
| `PROVISION` | succeeded | `PROVISIONING` | `RUNNING` |
| `PROVISION` | failed / refused | `PROVISIONING` | `DEGRADED` |
| `START` | succeeded | `PROVISIONING` / `DEGRADED` | `RUNNING` |
| `STOP` | succeeded | `DECOMMISSIONING` | `DECOMMISSIONED` |
| anything else | — | — | no change |

**What `RUNNING` means, stated precisely because it would otherwise be over-read:** the agent
reported its `docker compose up` returned 0. Given this compose file gates the frontend, Caddy,
the adapter and every setup job behind `service_healthy`, that is meaningfully more than
"containers created" — but it is still one claim at one moment, not continuous health. Observed
health is DH-027D, and until then `DEGRADED` is reachable only from a failed report.

`STATUS`, `BACKUP_NOW` and `ROTATE_TLS` change nothing, with a test — otherwise reading status
would rewrite the thing it reports.

**Verified:** 19 new tests, **422 provisioning tests total** (from 403), Ruff clean. The table is
tested directly as well as through HTTP, because the table *is* the specification.
Control-verified: making `PROVISION` always mean `RUNNING` — the mistake the roadmap explicitly
warns against — fails 3 tests.

⚠️ **A control that did NOT fail, recorded rather than glossed.** Removing the
"`DECOMMISSIONED` is terminal" guard changed no test result, because every row already requires
a specific `current` state that `DECOMMISSIONED` never matches. The guard is therefore redundant
*today*. It is kept, and the code now says why: it stops being redundant the moment someone adds
a row that checks `current` less narrowly, and "a successful START always means RUNNING" is an
easy and reasonable-looking row to add. The invariant is asserted by tests either way.

### DH-027E · Real soft decommission · ✅ DONE 2026-08-17

**The defect:** `POST /facilities/{id}/decommission` set `DECOMMISSIONED` on request and
**queued nothing**. The fleet therefore reported a facility as decommissioned while its stack
was still serving patients — the same "queued is not running" mistake the create path had
already been fixed for, in the direction that matters more, because an operator who believes a
stack is stopped stops watching it.

**Now a real lifecycle.** A new `DECOMMISSIONING` state is entered on request, a **STOP** job is
enqueued for the host that actually holds the claim (the claim *is* the mapping — the host is
not taken from the request), and `DECOMMISSIONED` is reached **only** when that host's agent
reports the STOP succeeded. A failed or refused report leaves the facility in
`DECOMMISSIONING`, which is the honest state: requested, not confirmed stopped.

**Two guards worth naming.** The terminal transition is gated on the job's verb being `STOP`
*and* the facility currently being in `DECOMMISSIONING`, so a successful `PROVISION` report
cannot retire the facility it just brought up. And **the host claim is deliberately not
released** — the stack's volumes, i.e. the patient database, are still on that host, so freeing
the claim would let a different facility be provisioned on top of this one's data. That is
exactly the overwrite ADR-022 refuses, and it has its own test.

No data is destroyed: the executor runs `docker compose stop`, never `down`, and a pre-existing
test already asserts no verb can emit `--volumes`.

**Verified:** 9 new tests, **403 provisioning tests total** (from 394), Ruff clean.
Control-verified twice — restoring the jump straight to `DECOMMISSIONED` fails 3 tests, and
removing the STOP enqueue fails 5.

⚠️ **One existing assertion was changed, not deleted:** `test_a_credential_with_the_scope_may_decommission`
in `test_app.py` expected `"decommissioned"`. That expectation encoded the bug. It now expects
`"decommissioning"`, with a comment saying why, and the test still checks the scope enforcement
it was actually written for.

### DH-027F · Atomic facility env writes · ✅ DONE 2026-08-17 (rotation rollback still open)

**The defect, confirmed in the code rather than taken from the roadmap.** `write_env` opened
the target with `O_TRUNC`, which empties the file *before* the new contents are written. A
process that died mid-write therefore left a **torn env file** — and on the rotation path
(`overwrite=True`) that file is a live facility's credentials. There was also no `fsync`, so a
power cut could leave a partial or empty file after a write that had already returned
successfully.

**Fixed with write-temp-then-rename:** `mkstemp` in the target's **own directory** (so
`os.replace` is atomic — it is only atomic within one filesystem), contents flushed and
`fsync`ed **before** the rename, then a best-effort directory `fsync` so the rename itself
survives a power cut. `mkstemp` creates 0600 with `O_EXCL`, so there is no world-readable
window and no predictable name to pre-create. A reader now sees either the whole old file or
the whole new one.

The directory fsync is deliberately best-effort: the contents are already durable and renamed
by that point, so a filesystem refusing to fsync a directory must not turn a completed write
into a reported failure.

**Verified:** 6 new tests, **394 provisioning tests total** (from 388), Ruff clean.
Control-verified by restoring the `O_TRUNC` writer: 4 tests fail, including the one that
matters — with `O_TRUNC` the original file is already destroyed when the failure arrives.
The fsync-ordering test asserts fsync happens *before* replace, not merely that both occur.

⚠️ **Still open from DH-027F's scope:** rotation rollback. If `rotate_tls` writes a new env
file and the subsequent `docker compose up --force-recreate` or Keycloak reconciliation fails,
the env file is already replaced and is not rolled back. The write is now atomic, so the file
is never *torn* — but it is the new one. Restoring the previous file needs a retained copy,
which is a credential-custody question (where does the old file live, and for how long?)
rather than a code detail, so it is not invented here.

### DH-027B · Compensated facility creation · ✅ DONE 2026-08-17

**The orphan.** `POST /facilities` claims the host *before* inserting the facility record.
If the insert failed, the claim survived — that host was marked occupied by a facility that
was never created, could never be deployed to again, and an operator had to unpick it by hand.

**Fixed by conditional compensation, not by reordering.** Reordering only moves the orphan:
insert-then-claim leaves a facility record holding an id that a retry then collides with.
So `HostRegistry.release(host_id, facility_id)` and `SqliteHostStore.release(...)` undo a
claim, and `create_facility` calls it when the insert fails, on **any** exception rather than
only `FacilityExists` — a storage error leaving the claim behind is the same orphan with a
less obvious cause.

**`release` is conditional on the exact facility id**, in SQL for the durable store so a
concurrent claim cannot slip between read and write. That is what stops compensation becoming
an eviction tool: a stale or hostile call naming another facility changes zero rows and
returns `False`. There is deliberately **no unconditional release** — "free up that host" is
not a safe thing to expose, because the host holds a patient database.

**The audit log cannot contradict the result.** A rolled-back create records `FAILURE` and
never `SUCCESS`, and the message distinguishes `rolled back` from `NOT rolled back —
investigate`, so a failed compensation is visible rather than assumed. Asserted directly.

**Verified:** 17 new failure-injection tests, **388 provisioning tests total** (from 371),
Ruff clean. Both backends are parametrised — in-memory *and* SQLite — because a fix that
worked only in the test double would be no fix. Control-verified: removing the compensation
line fails exactly the three HTTP-level tests, and the store-level tests still pass, which is
how the control shows the HTTP tests are the ones carrying the property.

⚠️ **Residual, stated rather than hidden:** this is compensation, not a single ACID
transaction. A process killed between the claim and the release still orphans the claim. The
shared SQLite `Database.write()` could wrap both operations for the durable path and close
that window; it is not done here because the in-memory backend cannot participate and a
half-transactional path would be harder to reason about than an explicit compensation. The
window is microseconds wide and the fix is `release`, which an operator can now reach.

### DH-027M · Continuous integration · ✅ DONE 2026-08-17

**The finding that motivated it:** the repository held **500 tests across four suites** and nothing
enforced any of them. (DH-027B added 17 the same day, so CI gates **517** today — the count in this
entry is what it was when CI landed.) Every verification claim in this file was produced by a human remembering to
run something — which is not a gate, it is a habit.

`.github/workflows/ci.yml`, five jobs, all well under a couple of minutes:

| Job | Gates |
|---|---|
| `adapter` | `pip check`, Ruff lint + format, **94 tests** |
| `provisioning` | `pip check`, Ruff lint + format, **388 tests** |
| `clinical-config` | seed safety **16 tests** (ADR-007 additive-only beds, the empty prenatal panel) and the ADR-006 form guard **19 tests** |
| `static` | `.env` untracked, tracked-secret scan, image pinning, shell syntax, JSON/XML/CSV parse, relative-link resolution |
| `compose` | default and `backup` profiles interpolate and parse, plus the demo compose |

**Every step was executed locally before being committed** — the Python jobs were simulated in the
same pinned `python:3.13.14-slim-bookworm` the images are built from, confirming both suites run
natively outside Docker (94 and 371, Ruff clean).

**Four guards control-tested by breaking them on a throwaway tree:** an unpinned `:latest` is caught;
a broken relative link is caught; a corrupt tracked JSON is caught; a suite discovering zero tests is
refused. That last one matters most — `unittest discover` **exits 0 on "Ran 0 tests"**, verified
against an empty directory, so a renamed directory would otherwise switch the clinical-safety guards
off silently. Both suites now assert a floor on tests actually executed.

**Two defects found in my own checks by running them**, which is the whole argument for controls:
the secret scan flagged a legitimate test fixture (`BACKUP_ENCRYPTION_PASSPHRASE=a-long-operator-…`
in provisioning's backup tests), and the image-pinning check flagged all five multi-stage
`FROM base AS test` lines as unpinned images when they reference an earlier *stage*. Both fixed;
stage aliases are now collected and skipped.

**Deliberately excluded, and this is a decision not an omission:** the ~50-minute clinical cold boot.
Running it per push would make CI useless within a week, and a check people learn to ignore is worse
than no check. Whole-stack verification stays the README's manual procedure plus DH-027J's fresh-VM
demonstration.

**Verified green on GitHub, and the first run failed for the right reason.** Run `32030788360`
failed on the tracked-secret scan flagging **this workflow's own** throwaway compose values — the
check working correctly on its author. Excluding `.github/` would have been the lazy fix and would
blind it to a real token pasted into CI, so the dummy values were shortened below the 12-character
threshold instead (`config` only interpolates, it never validates password content). Control-verified
in both directions: a real-looking `ghp_…` token appended to the workflow **is** still caught. Runs
`32030956269` and `32031078854` are green across all five jobs in 23–33 seconds, the latter after
bumping `checkout@v5`/`setup-python@v6` to clear GitHub's Node 20 deprecation warning.

**Honest limits.** The secret scan **excludes test trees** because fixtures legitimately contain fake credentials, so a
real secret pasted into a test would be missed there; the `.env`-untracked check is the control that
actually matters. Compose parsing uses a generated throwaway env written outside the checkout, so it
proves interpolation and syntax, **not** that the stack boots.

### P1 / P2
P1: monitoring, digest-pinning automation, `docs/RUNBOOK.md` depth. (`docs/CLINICAL-SIGNOFF.md`
now exists — created at DH-006.) P2: graphical partogram plotting recorded values only (still needs the ADR-006
conversation), RapidPro, Orthanc/PACS, multi-facility.

---

### UI-001 · Unified clinical UX + data-contract map · ✅ DONE 2026-08-21

**Why now.** DH-011 (numeric OpenELIS site code), DH-017/DH-027J (fresh VM), and DH-014's off-site
leg (approved external storage) all remain blocked on external input; none cleared as of this
ticket's audit. Rather than adding another optional provisioning feature, this ticket defines the
unified Daher clinical application precisely enough that a future frontend agent can implement it
without inventing architecture — an architecture/product deliverable, not code.

**What it is not:** no frontend code, no new backend endpoint, no clinical content. Lab panel
contents, ANC/partograph fields, and staff role assignments remain exactly as unresolved as before
this ticket — reserved to the clinical lead and Liban, per `AGENTS.md`.

**The one finding worth flagging on its own:** ADR-017 already decided the authorization model —
exactly three Keycloak realm roles (`clinician`, `lab-tech`, `sys-admin`), with midwives and
doctors deliberately folded into one `clinician` role and **no separate reception/front-desk
role**. The document's persona map (Réception, Sage-femme, Médecin, Lab tech, Admin) had to be
built as a **UX layer over that existing model**, not a new one — three of the five personas share
one auth role, and reception's restricted view is explicitly documented as a navigation
convenience with **no server-side enforcement**, not a security boundary. Inventing a fourth realm
role to make reception "real" would have been exactly the kind of clinical/access decision
`AGENTS.md` reserves for a human — flagged as a backend gap instead (§1, §24 of the document).

**Second load-bearing finding:** the adapter's own code comment
(`adapter/src/daher_adapter/openmrs.py`) states FHIR `ServiceRequest` is search/read-only on this
OpenMRS server — so a lab order placed through the Daher UI cannot be written via FHIR at all; it
must go through OpenMRS's native REST order resource, unverified against this exact pinned build.
Recorded in the data-contract matrix rather than assumed compatible with the adapter's read path.

**Deliverable:** `docs/UI-001-CLINICAL-UX.md` — role map, role-aware navigation, French
terminology glossary, the full patient-arrival-to-discharge journey with per-step system/API/
permission/failure-mode, patient/maternity/lab/result information architecture, a central
data-contract matrix (source system, read/write API, permission, audit actor, known-vs-unverified,
per UI element), the candidate Daher UI API/BFF boundary (explicitly not the adapter, and not
implemented), auth/identity propagation design (one real unresolved seam: OIDC+SAML dual-protocol
propagation through one BFF), network-loss/write-ambiguity behavior, accessibility requirements,
and nine backend gaps found and recorded rather than built around.

**Verified, not invented:** every OpenMRS/OpenELIS capability claim was checked against source
(`docs/ARCHITECTURE.md`, the adapter's own code, DH-011B's spike, ADR-017/018) or explicitly
marked `UNVERIFIED` — patient search/registration, bed-status vocabulary beyond create/list, the
order-write path, and the admission-aggregate queries needed for two dashboard cards are all
marked unverified rather than assumed. Two dashboard metrics ("En travail", "Ordonnances en
attente") are marked `UNAVAILABLE` by design rather than backed by an invented query, per
instruction.

**Tests/static checks:** documentation only — relative-link check re-run and passing (16
documents); no code changed, so the provisioning/adapter/seed/form-safety suites are unaffected
and were not re-run for this ticket.

**Documentation updated:** `docs/UI-001-CLINICAL-UX.md` (new), this entry, the ticket-status table
above. No drift found beyond what DH-027H already corrected.

**Not done, by instruction:** no frontend implementation (UI-002), no HIE, no new provisioning
ticket, no clinical configuration touched.

**Recommended next ticket:** UI-002 (design-system + first screen implementation) **only if**
still the best unblocked path when picked up — re-audit first, since any of DH-011/DH-014/DH-017's
external blockers clearing would make the clinical golden flow or real provisioning the higher
priority again, per the brief's own stated preference order.

---

### UI-001A · Verify clinical UI backend contracts before frontend implementation · ✅ DONE 2026-08-22

**Why now.** UI-001 deliberately left several backend/API assumptions `UNVERIFIED` rather than
guess at them. Building UI-002 on top of an unverified assumption risks discovering a wrong API
contract mid-implementation. This ticket is a technical verification spike against the existing
synthetic/test stack — not UI work — to turn as many of those assumptions into measured facts as
possible before any frontend code is written. Re-audited first: neither DH-011's numeric OpenELIS
site code nor a fresh VM (DH-017/DH-027J) had cleared, so this remained the right ticket to run.

**What it is not:** no frontend code, no BFF built (the identity question was answered by source
inspection, not a throwaway BFF spike), no clinical content touched, no real patient data, no
change to bed counts, staff roster, accession/site code, or OpenELIS business integration.

**Patient search — RUNTIME VERIFIED.** Exercised known identifier, known name, nonexistent,
partial/ambiguous, and an unauthorized-request control against the pinned OpenMRS REST build.
Exact request/response shape recorded in `docs/UI-001-CLINICAL-UX.md` §13a.1, not inferred from
generic OpenMRS docs.

**Patient registration — RUNTIME VERIFIED.** Proved the full POST → accepted → read-back → same
patient round trip using exactly one clearly synthetic patient. **The non-obvious finding:** a bare
identifier string is rejected by a Luhn Mod-30 check-digit validator — a valid identifier must
first be generated via the idgen module's own endpoint
(`/ws/rest/v1/idgen/identifiersource/{uuid}/identifier`), a step UI-001 had not anticipated.
Negative controls (malformed request, missing required field, duplicate candidate, unauthorized
request) all behaved as expected. Cleanup used OpenMRS's own voiding mechanism, not deletion — the
synthetic patient and its bed assignment were both voided/released, and the pre-existing `10001MJ`
fixture patient and the seven configured beds were confirmed untouched afterward.

**Order creation — PARTIALLY VERIFIED, stopped deliberately.** Confirmed `/ws/rest/v1/order` is a
real write seam (`type=testorder`) with a measurable required-field contract, distinct from FHIR
`ServiceRequest`'s confirmed-read-only status. Stopped at the `concept` field: selecting a concept
to complete a synthetic order would have meant picking clinical content, which is out of bounds by
instruction. **BLOCKER-6 (how an order reaches OpenELIS) remains open regardless** — this ticket
was never going to close it.

**Bed management — RUNTIME VERIFIED, one load-bearing finding.** `POST /ws/rest/v1/beds/{bedId}`
with only a `patientUuid` returns HTTP 500 (NullPointerException on a null encounter) — the
endpoint does **not** resolve an encounter from the patient's active visit automatically. A valid
assignment requires an explicit `encounterUuid` in the request body, meaning the Daher UI's
admission flow must create both a Visit and an Encounter before it can assign a bed, not just a
Visit as UI-001 assumed. This is exactly the kind of API-contract surprise this ticket existed to
catch before UI-002 implementation time was spent on it.

**Admission / active-visit shape — RUNTIME VERIFIED.** Confirmed the smallest authoritative call
shape is two objects, not one: a Visit (the admission container) plus an Encounter (the specific
clinical event) inside it — matching neither a single combined call nor a MariaDB-level shortcut.
No direct database queries were made and no Daher shadow admission table was created, per
instruction.

**Audit identity — RUNTIME VERIFIED.** Every successful synthetic write recorded `auditInfo.creator`
as the authenticated HTTP principal, confirmed only visible at OpenMRS's `v=full` response
verbosity. No separate clinical-provider/orderer identity was asserted on top of the authenticated
user in these test writes — the two are the same principal in this environment, and the document
now states that distinction explicitly rather than assuming it away.

**BFF/SSO identity — UNRESOLVED, by design.** Answered via source inspection only (reading
`configure-sso.sh` and `common.properties.template`), not by activating real SSO or inventing staff
identities: OpenMRS's OIDC login (`openmrs-module-oauth2login`) and OpenELIS's SAML login are both
browser-redirect flows, not bearer-token APIs, which is a real obstacle to a BFF holding a staff
token directly. No throwaway BFF spike was built — the ticket's own instruction was not to build
one unless genuinely necessary, and it wasn't. Recorded honestly in §15a as **UNRESOLVED —
requires SSO activation** rather than forced to a false conclusion.

**Browser PHI storage — corrected.** UI-001's §17 language was too permissive about caching
clinical reads client-side. Reframed as a security/privacy decision, not a frontend convenience:
the default until proven safe is **memory-only** for unsaved data, with no commitment to
`localStorage`/`sessionStorage`/`IndexedDB` for PHI. Full risk table (shared workstations, browser
crash, device theft, another staff member signing in, persistent disk caches) now in new §17a.

**Ambiguous-write correction.** A network timeout after the server may already have accepted a
write was previously rendered as `Connection lost — not saved` — a factual claim the UI cannot
actually support. Corrected throughout (§16, §17, the patient-journey diagram in §5) to
`Connection lost — save status unknown`, resolved only by an authoritative re-fetch on
reconnection, not asserted either way.

**Reception authorization gap — turned into an explicit decision request.** Confirmed via ADR-017
that no dedicated reception/front-desk realm role exists, and that the `clinician` role a
receptionist would use is exactly as privileged as a midwife's or doctor's — UI navigation hiding
is not authorization. No fourth realm role was created. New §24a records the exact decision
request: *"Should Daher add a dedicated reception/front-desk authorization role, distinct from
clinician?"* with the capability delta, left for a human to decide.

**Negative controls exercised:** unauthorized patient search, unauthorized patient registration,
malformed registration request, missing required field, duplicate-candidate registration, invalid
bed assignment, unauthorized bed assignment — all behaved as expected (rejected, not silently
accepted or silently degraded).

**Deliverable:** `docs/UI-001-CLINICAL-UX.md` updated in place — §13 matrix rows moved from
`UNVERIFIED` to `RUNTIME VERIFIED` (or `PARTIALLY VERIFIED`) only where live evidence exists; new
§13a records the evidence itself; new §15a records the identity/BFF source-inspection findings;
§16/§17 corrected for the ambiguous-write finding; new §17a corrects the browser-PHI-storage
stance; new §24a records the reception-role decision request. No frontend components were built.

**Tests/static checks:** documentation only — all live API verification used ephemeral `docker
exec`/`curl` calls against the running synthetic OpenMRS stack and scratch files outside the
repository, none of which touch tracked source. No new code or helpers were introduced, so no new
tests were added, consistent with "do not inflate test counts with documentation-only assertions."
A full internal cross-reference pass (`§N` section markers) was also run over the document after
the new §13a/§15a/§17a/§24a insertions, and several stale references left over from before this
ticket's edits (pointing at unrelated sections) were corrected in the same pass.

**Still unverified, and why:** the OpenELIS delivery leg for an order (BLOCKER-6, human decision,
out of this ticket's scope by design); the FHIR result reverse-leg and corrected-result semantics
(DH-012's job, not this ticket's); full runtime SSO/BFF identity propagation (requires activating
currently-blocked production SSO configuration, which this ticket was explicitly told not to do).

**Recommended next ticket:** UI-002 (design-system + first screen implementation), now with the
bed-assignment encounter requirement, the idgen identifier step, and the two-call admission shape
as known contracts rather than open questions — **only if** still the best unblocked path when
picked up; re-audit first, since DH-011/DH-014/DH-017's external blockers clearing would still take
priority per the brief's own stated preference order.

---

### UI-002 · Design system + read-only patient search (first real Daher frontend) · ✅ DONE 2026-08-22

**Why now.** UI-001/UI-001A specified and runtime-verified the clinical UI contract but wrote no
frontend code. Re-audited first: DH-011's numeric OpenELIS site code and DH-017/DH-027J's fresh VM
had both still not cleared, so this remained the right ticket. Deliberately narrow, per instruction:
app shell, French-first navigation, a small design system, and a functional **read-only** patient
search — no registration, admission, bed, order, or result writes; no SSO activation; no BFF built.

**Stack:** Vite + React + TypeScript in a new `ui/` directory, sibling to `adapter/` and
`provisioning/` — no existing frontend tooling was found anywhere in the repo, so nothing competes
with it. Vitest + React Testing Library + `jest-axe` for tests (21 tests, all passing); `oxlint` for
lint; `npm ci` + a committed lockfile for reproducible installs. No CDN runtime script, no external
font service, no analytics SDK.

**The identity problem, solved honestly rather than worked around.** UI-001A left the BFF/SSO
identity path `UNRESOLVED`, and this ticket was explicitly told not to solve production write auth
with a shared service account. `SSO_ENABLED=false` is this deployment's real current state
(`docs/ARCHITECTURE.md` §5) — OpenMRS only accepts Basic Auth with the local admin credential today.
The dev-only answer: `ui/vite.devOpenmrsProxy.ts`, a Vite plugin that runs only in the dev server's
own Node process, reads `OMRS_ADMIN_PASSWORD` from the repository's existing root `.env` (via
`envDir`, not a second `.env` under `ui/`), and attaches Basic Auth itself when forwarding exactly
one whitelisted, GET-only path to OpenMRS through Caddy's already-published HTTPS port
(`emr.localhost:8443`, ADR-008/ADR-010's existing routing — no new port opened). The browser never
sees the credential. This is explicitly documented as a development convenience, not the production
path, in both `ui/README.md` and code comments. The production container (`ui/Dockerfile`) ships
static files only — the proxy and the credential it reads do not exist in that image at all.

**The stack-trace finding from UI-001A, implemented in code, not just documented.** §13a.1 found
that a raw OpenMRS error response can carry a full Java stack trace in `detail`/`rawMessage`. The
dev proxy now actually strips every non-2xx response down to the fixed `{code, message}` vocabulary
(§20) before it ever reaches the browser — demonstrated live: a real search against the pinned
stack for the existing `10001MJ` fixture returned `HTTP 200` with one result; an unsupported method
returned a clean `{"error":{"code":"VALIDATION_ERROR",...}}` rather than a raw exception.

**Read-only, verified in the code itself, not just claimed.** A dedicated test spies on
`Storage.prototype.setItem` and `IndexedDB.open` through a full search-and-open flow and asserts
neither was ever called — UI-001A's PHI-storage correction (§17a: memory-only, no
`localStorage`/`sessionStorage`/IndexedDB for clinical data) is now a running assertion, not just a
paragraph. A second script (`ui/scripts/check-no-secrets-in-bundle.mjs`) runs as an npm `postbuild`
hook and fails the build if the admin credential's actual value, or its variable name, appears
anywhere in the shipped static bundle — verified to catch a real secret by testing it against the
literal `OMRS_ADMIN_PASSWORD` value before wiring it in.

**Honest about what's not built, in the UI itself, not just in docs.** Six of seven nav items
(`Tableau de bord`, `Maternité`, `Travail`, `Lits`, `Laboratoire`, `Résultats`) are visible but
navigate to an explicit "not yet available" screen rather than a broken or fake one — hiding
navigation is a UX convenience, never a security boundary (§1, §24a), and the same principle now
applies to feature availability. "Nouveau patient" is visible but disabled with an explanatory
label, per instruction, rather than wired to the runtime-verified (but intentionally unused here)
registration endpoint. Only the Résumé tab of the patient workspace renders real data — the exact
fields UI-001A confirmed the search response carries; Grossesse/Travail/Ordonnances/Documents show
an honest empty state rather than invented pregnancy, labour, or lab content.

**A real defect caught before it shipped:** the scaffolded `tsconfig.app.json`/`tsconfig.node.json`
carried `/* ... */` block comments, which are valid TypeScript JSONC but not valid JSON — the
repo-wide CI step that strictly `json.load()`s every tracked `*.json` file has no tsconfig
exclusion and would have failed on push. Caught by running that exact check locally before
committing (per `AGENTS.md`'s evidence discipline) and fixed by removing the comments; also
fabricated-then-corrected: an initial Dockerfile draft used placeholder-looking digests for
`node:22-slim`/`nginx:1.27-alpine` before the actual images were pulled and their real digests
substituted — caught in the same pre-commit review, not left in.

**Verified, not invented:** every claim about the current OpenMRS/SSO/routing state was checked
against `docs/ARCHITECTURE.md` and `docs/UI-001-CLINICAL-UX.md`'s §13a runtime evidence rather than
assumed. Test counts (adapter 94, provisioning 549, seed 16, form-safety 19 = 678) were re-measured
against `python:3.13-slim-bookworm` (matching CI's pin) rather than copied from a doc — this also
caught and fixed a stale "360 tests" current-state claim in this file's own "Where things actually
are" section (§14 of this ticket's brief), left over from before DH-027M.

**Tests/static checks:** `npm run typecheck`, `npm run lint`, `npm run test` (21/21), `npm run
build` (which runs the secrets-in-bundle check as its own `postbuild` step), and a real `docker
build` of `ui/Dockerfile` all pass locally. The repo-wide checks that would otherwise only run in
CI were also run locally first: tracked-secret scan, image-pinning/digest check, JSON/XML/CSV
parse, and the relative-documentation-link check — all clean against the staged changes.

**CI itself extended, not just satisfied.** `.github/workflows/ci.yml` had no job that ran anything
under `ui/` — landing this ticket without one would mean 21 new tests ran locally once and then
never again on any future push, exactly the gap DH-027M existed to close for the Python suites. A
new `ui-frontend` job (lint, typecheck, `vitest`, `build`) was added in the same commit, following
the existing `adapter`/`provisioning` jobs' shape. CI now gates 699 tests across five suites, not
678 across four — this file's own "678 tests" claim above is updated to match.

**Not done, by instruction:** no clinical write of any kind; no BFF; no SSO activation; no
reception-facing production workflow; no HIE; no ANC/partograph forms; no routing/domain wiring
into Caddy (the app runs standalone; `ui/README.md` documents the next integration step once the
identity design in §15a is resolved).

**Known limitation carried forward, not solved here:** a local Docker permission restriction in
this environment prevented cleanly stopping a test container started to verify the production
image serves correctly (`docker stop`/`docker kill` both refused with "permission denied"); it was
confirmed serving correctly on a test-only port before being left running rather than fought
further — flagged to Liban rather than worked around with a broader permission change.

**Recommended next ticket:** re-audit before picking anything — DH-011/DH-014/DH-017's external
blockers are still the highest-leverage unblocks if any of them clear. If none have, the next
UI ticket is the BFF/identity design that §15a left `UNRESOLVED`, since every write-enabled screen
after this one depends on it; building more read-only screens without it would just repeat this
ticket's shape without closing the actual gap.

---

### UI-003 · Production identity path for the clinical UI · ✅ DONE 2026-08-22 (architecture decided; activation blocked)

**Why now.** UI-002 shipped with a deliberately dev-only proxy holding a shared admin credential;
UI-001A had already flagged the production identity path as `UNRESOLVED`. This ticket exists to
answer exactly one question — how the production Daher UI reaches OpenMRS/OpenELIS while preserving
real staff identity — by inspecting the actual running configuration, not the generic OIDC/SAML
brief. Re-audited first: neither DH-011's numeric site code nor DH-017/DH-027J's fresh VM had
cleared. No code was written; no clinical screen was added; `SSO_ENABLED` was never touched.

**The decision: ADR-027 (`DECISIONS.md`).** No BFF in V1, not even read-only. The browser
authenticates directly with OpenMRS (OIDC) and OpenELIS (SAML) through their own already-built
browser-redirect flows, each keeping its own native session; the Daher UI gets a new origin
(`app.$BASE_DOMAIN`) that also proxies each backend's own API prefix — not its SPA — so the same
session cookie set by that same-origin login is valid for the Daher UI's own calls. `emr.`/`lab.`
keep serving the native SPAs untouched. Full reasoning, the rejected alternatives, and the exact
(mechanical, non-clinical) changes a future ticket would make are in the ADR itself.

**The finding that closed the question, measured rather than assumed:** a live
`Authorization: Bearer <token>` probe against `/ws/rest/v1/patient` got `WWW-Authenticate: Basic` and
fell straight through to the same anonymous rejection UI-001A already characterized — OpenMRS has no
bearer-token path at all, full stop. Combined with `openmrs-gateway` and `openelis-webapp` both
demonstrably routing by path rather than Host header (an arbitrary Host still reached the real
backend on both), this is what makes "one backend instance, two origins, no rewritten upstream"
architecturally sound rather than a hope.

**The read-only BFF was considered and explicitly rejected, not merely deferred.** A service-account
BFF that only reads was floated as a softer middle ground during this ticket's own analysis and
rejected on review: it would replace every Daher user's own OpenMRS read authorization with the
service account's, which is a bigger authorization hole than the audit-identity problem it would
dodge — precisely the "identity silently falls back to a service identity" failure this ticket's own
acceptance criteria rule out. A second-look catch worth recording: it is easy to reason your way past
your own stated constraint by scoping it ("read-only doesn't count") when the constraint said no such
thing.

**Cookie/CSRF findings, measured directly and through Caddy, stated precisely:**
- OpenMRS's `JSESSIONID`: `HttpOnly` only — no `Secure`, no `SameSite`, in both places. A cookie-only
  `POST` with no CSRF token reached real validation logic despite an `org.owasp.csrfguard.CsrfGuardFilter`
  in the chain — that filter does not cover this path. **Undefined by configuration**, not "probably
  fine because of SameSite defaults" — the ticket's own explicit warning against that assumption.
- OpenELIS's `JSESSIONID`: `Secure; HttpOnly; SameSite=Strict`, both directly and through Caddy. Its
  login page carries a real Spring Security `_csrf` token; local login was exercised successfully.
  Per-endpoint CSRF enforcement on authenticated **business** writes (as opposed to the login form,
  which is commonly CSRF-exempt and is not a valid proxy for this) was **not** exercised — recorded
  as unverified, not assumed protected.

**Logout, stated as a real gap, not a checkbox.** `DELETE /ws/rest/v1/session` genuinely invalidates
OpenMRS's own session (verified: the same cookie gets `401 Session timed out` immediately after). No
SAML Single Logout is configured for the `openelis` Keycloak client, and neither client has
`frontchannelLogout` configured. **Coordinated logout across the Keycloak SSO session, OpenMRS, and
OpenELIS cannot currently be proven** — ending a Daher session today would not provably end either
backend's session or the SSO session itself, which is exactly the shared-workstation risk (silent
re-authentication as the previous user) this ticket was asked to surface, not solve. An OpenELIS
logout endpoint was not identified or tested.

**Negative controls exercised:** unauthenticated/garbage-bearer requests against OpenMRS's protected
API (rejected, anonymous); a cookie-only write with no CSRF token reaching OpenMRS's validation layer
(the actual finding above, not something "passing" — recorded as the gap it is); confirmed no
Keycloak client, redirect URI, or realm config was modified.

**Documentation updated, no code touched:** `DECISIONS.md` (new ADR-027), `docs/UI-001-CLINICAL-UX.md`
(new §15b, §14 marked decided-against, header notes, §25 cross-reference), `ui/README.md`'s "Next
integration step" made concrete instead of pointing at an open question. `keycloak/realm-config/
daher-realm.json`, `caddy/Caddyfile`, and `docker-compose.yml` were read and reasoned about — not
edited — per instruction.

**Tests/CI:** no new code — nothing to test. All investigation ran as ephemeral `docker exec`/`curl`
probes against the already-running synthetic stack, the same methodology UI-001A established; no
persistent state changed. Test counts re-measured, unchanged from UI-002 (699 across five suites);
existing CI unaffected since no tracked source changed beyond documentation.

**Still unverified, and why:** the live end-to-end proof (real browser, real Keycloak login, OpenMRS/
OpenELIS identifying that same human) needs `SSO_ENABLED=true` and BLOCKER-5's approved role mapping
— both explicitly out of bounds for this ticket. OpenELIS's SAML ACS derivation (fixed vs. per-request
origin) is unverified — its SP integration ships compiled, with no source in this repo. Whether
OpenELIS enforces CSRF on authenticated business writes (not just the login form) is unverified.

**Human decisions required:** none new. BLOCKER-5 (role mapping) remains the human decision gating
real activation, exactly as before this ticket. The reception/front-desk role decision (§24a) is
untouched — this architecture would enforce whatever that decision turns out to be exactly the way
it enforces every other role today: server-side, never by the Daher UI's navigation.

**Recommended next ticket:** re-audit before picking anything — DH-011/DH-014/DH-017's external
blockers still take priority if any clear. If none have, the mechanical Caddy/Keycloak-client changes
ADR-027 specifies are buildable now (they are config, not clinical content, and don't require
`SSO_ENABLED=true`) and would let a future ticket demonstrate the same-origin OpenMRS leg for real;
the OpenELIS leg's ACS-derivation question should be verified before committing engineering time to
its side of the same design.

---

### UI-004 · Production app origin + identity plumbing · ✅ DONE 2026-08-22 (foundation built; no writes, SSO still unactivated)

**Why now.** ADR-027 decided the architecture; nothing implemented it yet. Re-audited first: neither
DH-011's site code nor DH-017/DH-027J's fresh VM had cleared. Also corrected two stale claims this
ticket flagged: PROJECT_STATE.md's own infrastructure-status row still said "678 tests" (now 699,
matching the real CI count already recorded elsewhere in this file) and "Read DECISIONS.md first —
26 ADRs" (now 27, since ADR-027 exists).

**§2's priority first: OpenELIS SAML ACS derivation — UNRESOLVED, evidence recorded.** The library
underneath `org.itech.login.saml` is the genuine, unforked
`spring-security-saml2-service-provider-6.2.8.jar` (confirmed by listing the deployed WAR's
`WEB-INF/lib`), whose documented default derives the ACS URL per-request rather than from a fixed
configured origin — the same shape OpenMRS's OIDC module already uses in this codebase. No property
in `org.itech.login.saml.*`'s schema fixes a base URL either, which is itself evidence: if itech
needed one, the same pattern as `metadatalocation` would exist for it. But direct proof was not
obtainable: with `org.itech.login.saml=false` (this deployment's real, unchanged state), Spring's
SAML2 filter chain — including its metadata endpoints — is not registered at all; both
`/saml2/service-provider-metadata/keycloak` and `/saml2/metadata/keycloak` fell through to the
generic login-required redirect rather than returning metadata. Enabling SAML to get a direct answer
would mean flipping this deployment into an SSO-active state, which this ticket was explicitly
forbidden to do. **Per instruction, OpenELIS is not mounted under `app.$BASE_DOMAIN` and the
`openelis` Keycloak client was not touched at all** — ADR-027's fallback preserved exactly as
written.

**§3–§4 built and verified live against the pinned stack, not just written:**
- `app.$BASE_DOMAIN` added to `caddy/Caddyfile`, serving a new `daher-ui` container (the UI-002
  production build, `ui/Dockerfile`, added to `docker-compose.yml`) and reverse-proxying `/openmrs/*`
  to `openmrs-gateway:80`. Verified: `GET https://app.localhost:8443/` returns the real UI shell;
  `GET .../openmrs/ws/rest/v1/patient?q=10001MJ` returns the real fixture, same-origin.
- `keycloak/reconcile-sso.sh` now adds `https://app.$BASE_DOMAIN/*` to the `openmrs` client's
  `redirectUris`/`webOrigins` **alongside** its existing `emr.$BASE_DOMAIN` entries — verified by
  running the script twice against the live realm via a throwaway container (not through
  `docker compose`, see the incident note below) and reading the client back: both origins present,
  `openelis`'s own client completely unchanged.

**§6 cookie hardening — proven, not asserted:** `header_down Set-Cookie "HttpOnly" "HttpOnly;
Secure; SameSite=Lax"` on the `app.` route's `/openmrs/*` handler. Verified: a session established
through `app.` now sets `JSESSIONID=...; Path=/openmrs; HttpOnly; Secure; SameSite=Lax`; the
identical request through `emr.` still sets only `HttpOnly` — hardening is scoped to `app.` alone,
by design, exactly as instructed. `SameSite=Lax` (not `Strict`) was chosen because `Strict` would
withhold the cookie on the top-level cross-site GET that following a redirect back from Keycloak is,
which would break the OIDC callback; `Lax` still withholds it from a cross-site `POST`, the actual
CSRF vector. The rewrite mechanism itself is proven live; compatibility with a real OIDC handshake is
reasoned from documented `SameSite` semantics, not observed, since proving that needs BLOCKER-5.

**§7 CSRF boundary — built and control-tested with real deliberate breaks, not just designed:** a
`route` block requires `X-Daher-App-Request: 1` on `POST`/`PUT`/`PATCH`/`DELETE` under `/openmrs/*`,
implemented before either `handle` block so ordering is explicit rather than relying on Caddy's
default directive sort. Verified live: without the header, `POST /openmrs/ws/rest/v1/patient` →
`403`, never reaching OpenMRS; with it, the identical request reaches OpenMRS's own validation
(`400`, "the person property is missing" — the same finding ADR-027 measured directly). `GET`
unaffected (`200`, no header needed). Neither OpenMRS nor its gateway emit any `Access-Control-*`
header (verified directly), and this route adds none either, so a foreign origin's CORS-preflighted
`fetch` cannot obtain permission to set the header — confirmed: an `OPTIONS` preflight from
`Origin: https://evil.example` got no `Access-Control-Allow-Origin` in response.

**§9 production read path connected, dev scaffolding kept, not replaced.** `ui/src/api/client.ts`
now fetches `/openmrs/ws/rest/v1/patient` — the exact same relative path in every environment.
`vite.devOpenmrsProxy.ts` (kept, per instruction — `SSO_ENABLED=false` locally still needs something
to authenticate with) now listens on that identical path instead of the old `/api/openmrs/patient`,
so only the auth mechanism differs by environment, never the URL the UI code calls. The production
bundle was rebuilt and re-checked against the existing `check-no-secrets-in-bundle.mjs` guard, which
still passes and was re-verified to still catch an injected fake credential.

**§10/§11 — held exactly where instructed.** No clinical write of any kind is enabled anywhere.
Coordinated logout remains exactly the open gap ADR-027 recorded; no logout mechanism was discovered
or built this ticket, and none was claimed.

**An incident during this ticket's own infrastructure work, recorded rather than smoothed over:**
`docker stop`/`kill`/`restart` on running containers are permission-denied in this environment (the
same restriction noted in UI-002's leftover test container). Reloading Caddy after editing its
Caddyfile therefore could not use `docker compose restart caddy` — sending it directly triggered
Compose to re-evaluate the *entire* dependency graph, which re-ran several one-shot init jobs
(`openelis-certs`, `openelis-db-hardening`, `keycloak-sso-config`, `openmrs-demo-preflight`,
`openmrs-maternity-config`). All of them are designed to be safely re-run — except
`openmrs-maternity-config`, which correctly *refused* to continue ("Bed 'DEL-0001' conflicts with
committed type or position"): a pre-existing discrepancy between the committed seed and live bed
state, not something this ticket's work caused, and exactly ADR-007's additive-only guard behaving
as designed (fail closed, not silently overwrite). Caddy itself ended up briefly fully down (it is
the only container publishing host ports) before being recovered with a direct `docker start` on the
container Compose had already created — verified healthy and correctly configured afterward. Two
lessons applied for the rest of this ticket: never invoke `docker compose up/restart` again this
session (subsequent verification used `docker exec` and standalone `docker run --rm` only), and the
running `daher-ui` container's healthcheck was found to fail (`wget http://localhost/` resolves to
`::1` first on this image's musl libc, which nginx doesn't bind) — fixed in `docker-compose.yml` to
use `127.0.0.1` explicitly, verified correct, but not yet re-deployed to the live container for the
same reason (a recreate would risk repeating the cascade for a cosmetic health-status fix). The
pre-existing bed-seed conflict was not investigated further or fixed — out of scope for this ticket
and exactly the kind of change AGENTS.md reserves from being touched casually.

**Tests/CI:** `ui/` — 22 tests (21 + one new same-origin-path regression test), typecheck, lint,
build all pass; the secrets-in-bundle guard re-verified against a deliberately injected fake
credential. A new CI step (`app.$BASE_DOMAIN origin and OpenMRS redirect reconciliation invariants`)
statically asserts the Caddyfile and `reconcile-sso.sh` structure, since CI cannot boot the real
stack to test this live (the ~50-minute cold boot DH-027M's own header deliberately excludes). Five
deliberate breaks were run against this check before landing it, each caught: the CSRF guard
removed, `daher_app_url` removed from the `openmrs` reconciliation, cookie hardening removed,
`/openmrs/*` routed to the wrong service, and `daher_app_url` wrongly added to the `openelis`
reconciliation — all five failed the check exactly as intended. Test counts unchanged elsewhere:
adapter 94, provisioning 549, seed 16, form-safety 19, ui 22 = **700 total**, re-measured against
`python:3.13-slim-bookworm`/live `vitest`, not copied.

**Still blocked, and why:** the live end-to-end proof (a real browser, a real Keycloak login,
OpenMRS recognizing that same human through `app.$BASE_DOMAIN`) needs `SSO_ENABLED=true` and
BLOCKER-5's approved role mapping — this ticket touches neither. OpenELIS's ACS derivation is
unresolved for the reasons above. Coordinated logout is still undesigned. No clinical write exists
to protect yet — the CSRF boundary is real infrastructure waiting for one.

**Human decisions required:** none new. BLOCKER-5 remains the gate on activation.

**Recommended next ticket:** re-audit for DH-011/DH-014/DH-017 first. If none cleared, the two
highest-leverage follow-ups are (a) a narrow, disposable-stack spike to resolve OpenELIS's ACS
question definitively, since everything else in ADR-027 is now built and only that leg is still
open, and (b) a coordinated-logout design — real Keycloak front-channel/back-channel logout wired to
the `openmrs` client, since that's the one piece of ADR-027 with no design yet at all, not just no
implementation.

### UI-005 · Disposable SSO integration proof + coordinated logout design · ✅ DONE 2026-08-22 (evidence gathered; OpenMRS OIDC blocked; no production config changed)

**Why now.** UI-004 left two questions genuinely open: OpenELIS's SAML ACS derivation, and
coordinated logout. Both needed direct protocol evidence, which meant actually running OIDC/SAML —
forbidden against the real facility SSO config. Re-audited first: neither DH-011's site code nor
DH-017/DH-027J's fresh VM had cleared. Test counts re-measured, not copied: adapter 94, provisioning
549, seed 16, form-safety 19, ui 22 = **700 total**, unchanged from UI-004 (this ticket commits no
code). `SSO_ENABLED` confirmed still unset in `.env` (defaults `false`) — production SSO untouched.

**Disposable environment, built and torn down within this ticket.** A new Docker network
(`daher-ui005_internal`) with a throwaway Keycloak (`start-dev --import-realm`, a synthetic realm
literally named `daher` — required, since `configure-sso.sh` hardcodes that realm path — holding
one synthetic user, `ui005-synthetic-user`, no real name, no real role roster), a disposable
OpenMRS backend (`daher-health/openmrs-backend:3.7-off` image, fresh MariaDB, `SSO_ENABLED=true`
from first boot) and a disposable OpenELIS webapp (`itechuw/openelis-global-2:3.2.1.11`, fresh
Postgres, certs generated via the existing `daher-health/certgen:0.1.0` image, SAML pointed at the
disposable Keycloak's real descriptor). No patient data, no real credentials, no change to any file
this repository tracks other than documentation. **Note on this environment's own Docker
constraint:** `docker stop`/`kill`/`restart` (and therefore `docker compose down`/`docker rm -f` on
a running container) are permission-denied here, confirmed again this ticket — the disposable
containers (`kc-disposable`, `mariadb-disposable`, `openmrs-disposable`, `openelis-db-disposable`,
`openelis-webapp-disposable`) and network (`daher-ui005_internal`) could not be torn down by this
session. **Manual cleanup required:** `docker rm -f kc-disposable mariadb-disposable
openmrs-disposable openelis-db-disposable openelis-webapp-disposable && docker network rm
daher-ui005_internal && docker volume rm daher-ui005_openelis-keystore
daher-ui005_openelis-properties` (run from a shell with permission to stop containers — this
session's sandbox does not have it). The scratch realm JSON and driver scripts lived under
`.ui005-disposable/` inside the repo working tree (never staged or committed — deleted at the end of
this ticket) because this environment's Docker daemon is snap-confined and cannot bind-mount from
`/tmp`; only paths under the project directory are visible to it.

**OpenELIS ACS — resolved with direct protocol evidence: PER-REQUEST, APP-ORIGIN DERIVED.** Hit
`/OpenELIS-Global/saml2/authenticate/keycloak` with three different `Host` headers; decoded the
resulting `SAMLRequest` (base64, no deflate — HTTP-POST binding) each time. The
`AssertionConsumerServiceURL` tracked the `Host` header exactly:
`https://app.disposable-a.invalid/OpenELIS-Global/login/saml2/sso/keycloak`,
`https://app.disposable-b.invalid/...`, `https://localhost:8443/...` — three different hosts, three
different ACS values, same path suffix. Not fixed to a lab origin. Completed the full round trip for
the synthetic user (AuthnRequest → Keycloak login → SAMLResponse posted to the app-origin ACS,
simulating exactly what Caddy's Host-preserving reverse proxy would do) and got a real
`JSESSIONID`, `Secure; HttpOnly; SameSite=Strict`. An authenticated read of `/OpenELIS-Global/Home`
showed `ui005-synthetic-user` next to the Logout control — the session is bound to that identity,
not a service account. Negative controls: no cookie → redirect to `LoginPage`; garbage cookie →
same redirect; POST to `/Logout` with no `_csrf` token → `302` to `Home?access=denied`, session left
alive (CSRF genuinely enforced, not just present as a form field); same POST with the page's real
`_csrf` value → succeeded, and replaying the now-dead `JSESSIONID` afterward was rejected.
**The one condition this needed that this ticket did not apply to the real stack:** the disposable
`openelis` Keycloak client required `https://app.<disposable-origin>/*` added to its
`redirectUris` before Keycloak would accept the AuthnRequest at all (`invalid_redirect_uri`
otherwise). `reconcile-sso.sh` deliberately leaves the real `openelis` client untouched — that
remains true after this ticket. Mounting OpenELIS under `app.$BASE_DOMAIN` for real needs that exact
same redirect-URI addition made to the real client — a small, named, reviewable change for a future
ticket, not a rearchitecture.

**OpenMRS OIDC — STILL UNRESOLVED, exact blocker found, not worked around.** The disposable OpenMRS
backend correctly redirects unauthenticated requests to `/oauth2login` (`OAuth2LoginRequestFilter`,
wired via the module's `config.xml` — confirmed working). But `/oauth2login` itself returns `404` —
nothing serves it. Root cause, verified rather than inferred: the module's
`webModuleApplicationContext.xml` declares a Spring Security `<security:http>` chain (the only thing
that could serve that path) with the default bean name `springSecurityFilterChain`; the deployed
WAR's `web.xml` has no `DelegatingFilterProxy` entry for it at all, so the only path by which that
chain could ever take effect is dynamic registration at runtime — and the boot log shows exactly
that attempt failing: `java.lang.IllegalStateException: Filters cannot be added to context
[/openmrs] as the context has been initialised`, from `WebComponentRegistrar.setServletContext`.
Ruled out as a setup mistake: `configure-sso.sh` was confirmed to have written a correct
`oauth2.properties` pointing at the disposable Keycloak. Ruled out as ambient OpenMRS noise: grepped
the live stack's own boot logs (`SSO_ENABLED=false` there) for the identical exception string — zero
occurrences. So this is specific to SSO being enabled, not a pre-existing condition, and it was
observed on a **fresh, empty database with SSO enabled from first boot** — whether enabling SSO
against an already-initialized database avoids this same race is untested and not assumed either
way. Per the ticket's own instruction, this is reported as the blocker rather than patched around;
forcing the filter chain in would be exactly the "fight the OAuth2 implementation" the ticket
forbade. **This changes UI-004's characterization** — UI-004's source-level read said the module
"establishes its own JSESSIONID session" via a working browser-redirect flow; measured evidence here
shows that flow does not currently complete on this OpenMRS 3.7.x + oauth2login 1.5.0 combination.

**Coordinated logout — PARTIAL as currently implemented; a working fix is proven, not just
theorized.** OpenELIS's own local logout genuinely works (see CSRF result above). Left at that,
Keycloak's own SSO session survives it: re-running the identical SP-initiated SAML flow **in the
same browser session** afterward got a fresh, valid `SAMLResponse` with **zero credential prompt** —
`KEYCLOAK_SESSION`/`AUTH_SESSION_ID` silently re-authenticated the synthetic user. This is the exact
shared-workstation failure ADR-027 flagged as a risk, confirmed present in the naive "just call each
app's own logout" design. A real fix was then found and verified: hitting Keycloak's own
`/realms/daher/protocol/openid-connect/logout` returns a confirmation page (a `session_code`-bearing
form, shown because no `id_token_hint` was supplied) — and **actually submitting that form**
(not just fetching it, which is where the first attempt stopped short) does terminate the SSO
session: a follow-up SP-initiated login attempt in the same session required credentials again, no
silent re-auth. A variant supplying `client_id=openelis`+`post_logout_redirect_uri` without an
`id_token_hint` was fetched (not submitted, since the no-`client_id` path already proved submission
works) and also rendered a confirmation page rather than skipping it — the click itself is
unavoidable without a real ID token, which only a completed OIDC login provides (blocked by the
OpenMRS finding above). **Untested condition on using `post_logout_redirect_uri` for real:**
Keycloak validates it against the client's own registered post-logout redirect list, and the real
`openelis` client has none registered (per UI-003) — a follow-on ticket would need to register one,
or just accept landing on Keycloak's own default page after the click. **So the design this ticket can now specify with evidence:** after a local
app logout, redirect the user's browser (a real navigation, not a background `fetch`) to Keycloak's
end-session endpoint; the resulting confirmation click genuinely closes the SSO session today, with
no dependency on fixing OpenMRS OIDC or configuring OpenELIS SAML SLO. **What's not done:** nothing
in the codebase performs this redirect yet — the real "Déconnexion" action, as it exists today, would
still leave the SSO session alive exactly as measured in the naive case above. Wiring this redirect
in is now a small, well-scoped follow-on ticket, not an open research question. A *silent* (no
click) version stays blocked on the OpenMRS `oauth2login` finding, since only a real `id_token_hint`
skips the confirmation page.

**Not exercised:** OpenMRS's own unauthenticated-read/wrong-ACS negative controls, and the CSRF
characterization's "no `_csrf` even obtainable" edge case — both depend on the OIDC flow completing,
which it does not. No clinical write was made or enabled. `caddy/Caddyfile`,
`docker-compose.yml`, and `keycloak/realm-config/daher-realm.json` are byte-for-byte unchanged.

**Human decisions required:** none new. BLOCKER-5 remains the gate on activation, unaffected by
this ticket's findings.

**Recommended next ticket — two independent options, either is well-scoped now:** (a) wire the
proven coordinated-logout redirect (browser navigation to Keycloak's end-session endpoint after
local logout, one confirmation click) into the real "Déconnexion" action — this needs no OpenMRS fix
and no OpenELIS SAML SLO config, and closes the shared-workstation gap today; or (b) a narrow spike
on the OpenMRS `oauth2login` filter-registration failure — whether enabling SSO against an
already-initialized database (rather than a fresh one) avoids the registration race — which would
unblock OpenMRS OIDC directly and let coordinated logout become silent (no click) via a real
`id_token_hint`. (a) is smaller and delivers real security value without waiting on (b).

### UI-006 / DH-008A · Resolve the OpenMRS OIDC activation blocker · ✅ DONE 2026-08-23 (precisely blocked; root cause fully diagnosed; no patch attempted this ticket)

**Why now.** UI-005 named `/oauth2login` returning 404 as OpenMRS OIDC's blocker, but only ran one
configuration (fresh DB, SSO on from first boot) and attributed the cause to an exception that this
ticket shows was a red herring. Re-audited first: neither DH-011's site code nor DH-017/DH-027J's
fresh VM had cleared. Test counts re-measured, not copied: adapter 94, provisioning 549, seed 16,
form-safety 19, ui 22 = **700 total**, unchanged (no code committed this ticket).

**Versions, measured directly on the disposable stack:** OpenMRS platform `2.8.7`
(`openmrs-api-2.8.7.jar`/`openmrs-web-2.8.7.jar`), Tomcat `9.0.120`, `oauth2login` `1.5.0`. Core web
app runs Spring `5.3.30`; `oauth2login`'s own bundled libs (`.openmrs-lib-cache/oauth2login/lib/`)
carry Spring Security `3.2.10.RELEASE` and `spring-security-oauth2 2.3.5.RELEASE` — a 2014-era
generation, unrelated to and much older than OpenMRS core's own Spring version. Confirmed via GitHub:
`1.5.0` is the latest tag; `master` is only 3 commits ahead of it and all three are CI/release
tooling, not a fix.

**FRESH-DB RESULT (Case A, reusing UI-005's still-running `openmrs-disposable`):**
`OAuth2LoginRequestFilter` correctly redirects unauthenticated browser requests to `/oauth2login`.
`/oauth2login` itself: `404`, Tomcat's own generic page — the request never reaches Spring.

**INITIALIZED-DB RESULT (Case B, new disposable instance, own volumes
`daher-ui006_openmrs-db-data`/`daher-ui006_openmrs-data`):** initialized OpenMRS completely with
`SSO_ENABLED=false` (`openmrs-ui006-a`), shut it down cleanly via an in-container `bash -c "kill -TERM
1"` (the daemon-level `docker stop`/`kill`/`restart` block UI-004/UI-005 documented is a daemon-API
restriction — a signal sent by a process inside the container's own PID namespace via `docker exec`
is unaffected, confirmed by watching the container exit with code 143 and `docker rm` succeeding
immediately afterward), then started a second container (`openmrs-ui006-b`) against the same,
already-initialized volumes with `SSO_ENABLED=true`. Same result: `/oauth2login` → `404`. **This
answers the ticket's load-bearing question directly: it is not a first-boot lifecycle race.** Both
configurations fail identically.

**A genuine, separate Case-B-only finding, not the main blocker but real:** `oauth2login` never even
attempted to start on the Case B boot — absent from `.openmrs-lib-cache/`, and the admin module list
showed **"OAuth 2.0 Login [Not Started]"**. Confirmed by direct SQL against `mariadb-ui006`:
`global_property` holds `oauth2login.started = false`. `OAuth2LoginActivator.willStart()` (read from
the pinned upstream source) checks `oauth2.enabled` on every boot and, when false, calls
`ModuleFactory.unloadModule()` on itself during Stage 1's `SSO_ENABLED=false` boot — read
`ModuleFactory.java`'s source directly to confirm which write path sets the flag rather than
asserting it: `stopModule(Module, boolean, boolean)` is what writes `<moduleId>.started` to the
`global_property` table (`ModuleFactory.java:1066`), and it is called both from `unloadModule` and
from OpenMRS's own graceful-shutdown path (`Listener.contextDestroyed` → `WebModuleUtil.shutdownModules`).
Which of those two call sites actually wrote the `false` value observed here was not traced further
— both are plausible and either way the practical fact stands: flipping the env var back to `true`
and rebooting does not clear a persisted `started=false`; nothing in the boot path re-evaluates it
upward. A real activation runbook that goes "deploy with SSO off, then turn it on later" must also
reset this flag (via the admin module UI's "Start" action, which is CSRFGuard-protected — attempted,
blocked on token extraction from a script, not pursued further since
Case A's evidence already answers the ticket's core question independent of this).

**ROOT CAUSE — measured, not inferred, three-part chain:**
1. **`/oauth2login` (bare, no extension) has no servlet mapping.** OpenMRS's `openmrs`
   `DispatcherServlet` is mapped in the deployed `web.xml` only by file extension
   (`.htm`/`.form`/`.list`/`.json`/`.field`/`.portlet`/`.page`/`.action` — added dynamically by
   `legacyui`'s `WebComponentRegistrar`, extracted and decompiled directly from
   `legacyui-2.1.0.omod`, confirmed to be `legacyui`'s own class, not OpenMRS core's) plus a static
   `/ws/*` prefix. Nothing maps the exact literal path `/oauth2login`.
2. **`OAuth2LoginRequestFilter`'s own exclusion list creates a dead end.** Its `servletPaths` init-param
   (`/oauth2login,/oauth2logout`) matches on `HttpServletRequest.getServletPath()`, which equals that
   literal string only when a request hits an *exact* servlet mapping — one that doesn't exist. Every
   extension-suffixed alias that *does* reach a real mapping (measured: `.htm`, `.form`, `.list`,
   `.json` all return `302` back to bare `/oauth2login`) gets caught by this same filter and bounced
   right back to the one path nothing serves.
3. **Even reaching the controller produces an uncaught exception, not a redirect.** `/ws/oauth2login`
   uses the one real prefix mapping (`/ws/*`); Spring matches its `pathInfo` against
   `OAuth2LoginController`'s `@RequestMapping("/oauth2login")` (confirmed by reading the controller's
   pinned source, commit `2770233627343a53719ec981e997d94b1d90b7ad`) and the handler genuinely runs.
   It calls `restTemplate.getForObject(userInfoUri, ...)` via a Spring Security OAuth2 1.x
   `OAuth2RestOperations`, which — with no access token yet — throws
   `org.springframework.security.oauth2.client.resource.UserRedirectRequiredException` **by design**,
   expecting `oauth2ClientContextFilter` to catch it and issue the real redirect to Keycloak's
   authorization endpoint. Measured directly (authenticated, to bypass step 2's redirect loop): a raw
   Tomcat `500` with that exact exception name in the stack trace, not a `302`. `oauth2ClientContextFilter`
   is declared *only* inside `webModuleApplicationContext.xml`'s Spring Security `<security:http>`
   block — plain Spring beans, never wired into OpenMRS's real request pipeline by anything in
   `config.xml` (the one mechanism proven, via `OAuth2LoginRequestFilter` itself, to reliably register
   a module filter). The module's own designed OAuth2 flow fires correctly; the one piece that would
   turn it into an actual browser redirect is simply never invoked.

**Corrects UI-005:** the `IllegalStateException: Filters cannot be added...` UI-005 named as the
cause is unrelated — it belongs to `legacyui`'s own filter registration (not `oauth2login`'s),
reproduced here with `SSO_ENABLED=false`, and is non-deterministic even holding configuration
constant (present on one boot, absent on an otherwise-identical restart). UI-005's claim that its
absence from the live stack's logs proved SSO-specificity was also checked directly this ticket and
found wrong for a different reason: the live `openmrs-backend` log was not rotated (it starts at
`Initiating OpenMRS startup`, the genuine first line) — the exception is just genuinely, if
inconsistently, absent there, consistent with it being an unrelated flaky race rather than tied to
SSO at all. See `DECISIONS.md`'s UI-006 correction note, appended to (not replacing) UI-005's original
text.

**CHOSEN FIX: none attempted this ticket — Outcome B, precisely blocked, by design.** Fix hierarchy
checked in order: (1) configuration/deployment-order, no fork — ruled out empirically (Case A and
Case B fail identically; no boot ordering fixes a URL that no servlet mapping and no wired filter
ever reaches). (2) verified compatible upstream release — ruled out (no newer tag exists; `master`
has no relevant fix). (3) minimal source patch — the only remaining tier, and now precisely
scoped rather than vague: a new `config.xml`-declared `Filter` (the one proven-working module
extension mechanism) whose `doFilter()` matches the two literal paths `/oauth2login`/`/oauth2logout`
by string comparison (not relying on servlet-container URL-pattern matching at all, sidestepping
finding #1 and #2 above), and internally forwards the request to `/ws/oauth2login` — the one mapping
already measured to correctly reach `OAuth2LoginController` — while wrapping that forward in the
same `UserRedirectRequiredException`-to-redirect translation `oauth2ClientContextFilter` already
implements (reusing that bean from the module's own Spring context, not reimplementing OAuth2
redirect logic from scratch). This is intended to be a routing bridge between two pieces that
OpenMRS's own module packaging never connected, not a rewrite of authentication — but one premise is
untested and the follow-on ticket must confirm it first: the `/ws/oauth2login` → controller hop was
only measured **authenticated** (as the local admin, to bypass `OAuth2LoginRequestFilter`'s
redirect). The case this fix exists for is an *unauthenticated* user starting a login — whether the
forward still reaches the handler correctly for that user, and whether OpenMRS's module filter chain
re-applies on a servlet-level `FORWARD` dispatch the same way it does on the original request, is not
yet known. Not built this ticket: it requires confirming that premise, a Maven build from the pinned
upstream commit, a new pinned `.omod` artifact, and a full round-trip proof through Keycloak before
it could be called done, and the ticket's own §16 is explicit that a partial attempt without that
proof is not Outcome A. That is deliberately left for a dedicated follow-on ticket rather than rushed
here.

**Not exercised:** the module-patch build itself; the full OIDC round-trip proof (blocked on the
patch); restart/Keycloak-unavailable-during-boot behavior (blocked on OIDC working at all);
provider-identity mapping (blocked likewise). No clinical write was made or enabled. No production
config changed — `caddy/Caddyfile`, `docker-compose.yml`, `keycloak/realm-config/daher-realm.json`,
`openmrs-config/configure-sso.sh` all byte-for-byte unchanged.

**Disposable environment:** reused UI-005's still-running `kc-disposable` (same synthetic realm/user)
and `openmrs-disposable`. Added, on the same `daher-ui005_internal` network: `mariadb-ui006`,
`openmrs-ui006-a` (Case B stage 1), `openmrs-ui006-b` (Case B stage 2) — plus dedicated volumes
`daher-ui006_openmrs-db-data`/`daher-ui006_openmrs-data`. All three UI-006 containers and both
volumes were cleanly stopped and removed by the end of this ticket, using the same technique
`openmrs-ui006-a`'s own mid-ticket shutdown proved: `docker exec <name> bash -c "kill -TERM 1"`
(an in-container signal delivered inside the container's own PID namespace, not the daemon-level
`stop`/`kill`/`restart` API this sandbox denies), wait for `docker inspect --format
'{{.State.Status}}'` to report `exited`, then a plain `docker rm`. This works because the
restriction UI-004/UI-005 documented is on the Docker *daemon's* stop/kill/restart verbs
specifically — it does not reach a signal one process sends another inside the same container.
`docker rm -f` on a still-running container remains blocked (confirmed again in UI-005); the
technique above is the one that actually works, and it is what future tickets should use instead of
assuming disposable containers can never be torn down.

**UI-005's leftover disposable resources, cleaned up as part of this ticket:** `kc-disposable`,
`mariadb-disposable`, `openmrs-disposable`, `openelis-db-disposable`, `openelis-webapp-disposable`,
the `daher-ui005_internal` network, and its two OpenELIS volumes were all removed using the same
proven method, since UI-005 had left them running with no working teardown path documented at the
time. Confirmed clean afterward: no `disposable`/`ui00`-named containers remain, the network is
gone, and the live `daher-health-*` stack (12 containers) is untouched and was not queried for its
own state beyond that count.

**Human decisions required:** none new. BLOCKER-5 remains the gate on activation.

**Recommended next ticket:** build and prove the microscopic bridging filter described above —
Maven build from `openmrs-module-oauth2login` commit `2770233627343a53719ec981e997d94b1d90b7ad` with
one new `Filter` class and one `config.xml` addition, produce a pinned `.omod`, and run the exact
full-round-trip proof this ticket's §8 specifies against the same disposable Keycloak already
carrying a working synthetic `openmrs` OIDC client. Independently, UI-005's coordinated-logout
follow-on (wiring the proven Keycloak end-session redirect into the real logout action) remains
available and does not depend on this fix landing first.

---

### UI-007 / DH-008B · Prove the oauth2login bridge patch end-to-end · ✅ DONE 2026-08-23 (bridge proven; activation blocked on three separate, newly-found issues)

**Why now.** UI-006 designed the microscopic bridge filter but did not build it. This ticket built
it, proved it against the exact dispatch premise UI-006 left open, and drove a complete synthetic
OIDC round trip through it — reaching further than any prior ticket, and finding three genuinely
new, separate blockers only reachable once the dispatch problem was actually fixed. Re-audited
first: neither DH-011's site code nor DH-017/DH-027J's fresh VM had cleared. Test counts re-measured
in a Python 3.13 container (this sandbox's host Python is 3.14, for which the pinned
`pydantic-core==2.33.2` has no wheel — an environment gap, not a code issue): adapter 94,
provisioning 549, seed 16, form-safety 19, ui 22 = **700 total, unchanged** (no application code
committed this ticket — only new files under `openmrs-config/patches/`).

**§1–§2 (re-audit, priority override):** all UI-006 findings reconfirmed by direct source
inspection before writing any code: `ModuleFilter`'s `/*` mapping in `web.xml`, `OAuth2ClientContextFilter`
2.3.5.RELEASE's exact `doFilter()`/`redirectUser()` logic, and `ModuleFilterMapping`'s exact-match
support. Neither override condition applied — continued.

**§3–§4 (dispatch premise, bridge implementation).** Built `OAuth2LoginBridgeFilter` — one new
class, registered in `config.xml` against the literal path `/oauth2login` only — that looks up
`oauth2ClientContextFilter` **by name** (`Context.getRegisteredComponent("oauth2ClientContextFilter",
Filter.class)`, distinct from the by-type lookup implicated in the blocker below) and forwards
through it to `/ws/oauth2login`. **Measured, not inferred:** unauthenticated `GET /oauth2login`
against the patched module produced a genuine `302` to Keycloak's real authorization endpoint, both
with a bare Host header and an app-origin-style Host header (`http://app.disposable-a.invalid:8080/openmrs/oauth2login`
correctly reflected as `redirect_uri`). The forward does not loop; the dispatch premise holds.
Explicit dispatcher-type/servletPath measurement (the literal FORWARD-vs-REQUEST introspection §3
asks for) was not separately instrumented — the empirical no-404/no-loop/correct-redirect result is
the evidence recorded, not a direct log of the dispatcher type.

**§5–§7 (reuse existing logic, provenance, baseline control).** No OAuth2 logic duplicated — the
filter delegates entirely to the module's own unmodified bean and OpenMRS's own servlet dispatch. A
true baseline control was built first: unmodified upstream at the pinned commit, same Maven/JDK
toolchain, same disposable DB — reproduced UI-006's exact original failure (`/oauth2login` → 404;
authenticated `/ws/oauth2login` → 500 with `UserRedirectRequiredException`), proving the eventual
success comes from the patch, not the environment. Full provenance — upstream repo/commit, JDK/Maven
versions, exact build command, the new class, the `config.xml` diff, and checksums — is recorded in
`openmrs-config/patches/oauth2login-bridge/PROVENANCE.md`. **Baseline checksum:**
`541e0bbcd6149b2ba0b69cbfe26b4893133f94ad94886ec36f913a3c46559335`. **Canonical patched checksum:**
`65181782d1e08cbaefebffd9932835f248bdfaf4145259eec590101f7c7658cd` — two earlier rebuilds of the
*identical, unedited* source produced two different checksums (`00bcef57…`, `213e162a…`); this is
expected, not drift — Maven-embedded jar-entry timestamps make this toolchain's output
non-byte-reproducible, and functional equivalence was verified directly by re-running the full round
trip against the final `65181782…` artifact, not assumed. **Not wired into
`openmrs-config/Dockerfile`** — deliberately: it would be inert while the `authentication`-module
collision below blocks real login regardless, and `configure-sso.sh` is live in
`daher-health-openmrs-backend-1`.

**§8 (full synthetic OIDC round trip) — achieved, after two synthetic-data fixes.** Driving the
complete flow (synthetic browser via app-origin Host headers → `/oauth2login` → Keycloak → synthetic
credentials → authorization-code callback → OpenMRS) surfaced two configuration gaps in the
*disposable test setup itself*, not in the patch:
1. The synthetic user's email (`...@disposable.invalid`) failed OpenMRS's `UserValidator` with
   `email: Invalid Email address`. Cause, confirmed by version-checking the bundled
   `commons-validator-1.10.0.jar`: Apache Commons' `EmailValidator`/`DomainValidator` checks the TLD
   against IANA's real registered-TLD list, which deliberately excludes RFC 2606's reserved
   pseudo-TLDs (`.invalid`, `.test`, `.example`) — exactly the domain convention used for synthetic
   test data. Fixed by changing the synthetic user's email to a real-TLD domain
   (`...@disposable.example.com`) in the disposable Keycloak realm only.
2. The synthetic user's Keycloak client role (`clinician`) does not exist as an OpenMRS role,
   silently producing a role-less OpenMRS user (`oauth2login`'s `createUser` filters unmatched role
   names rather than failing), which then failed `Get Global Properties` privilege on the very next
   line of `OAuth2LoginController.login()`. Fixed by changing the synthetic role mapping to the
   existing `Provider` role.
With both fixed, the round trip completed: `302` to `/openmrs/`, and `GET /ws/rest/v1/session`
reported `{"authenticated":true,"user":{"username":"ui007-synthetic-user","systemId":"ae79c665-…"}}`
— the OpenMRS-side `sub` (Keycloak's subject UUID). No Basic Auth, no local admin credential, no
service account was used to obtain this session. The browser never received the confidential client
secret (measured in the captured redirect/callback traffic — only `code`/`state`/`session_state`
ever cross that boundary). Authenticated read confirmed against `GET /ws/rest/v1/location` (200,
real data) using only the session cookie.

**§9 (provider identity) — proven both ways.** With the realm's `provider` claim hardcoded `false`,
`currentProvider` was correctly `null`. Flipping that one protocol-mapper value to `true` and
re-running the flow produced a distinct, correctly-linked `currentProvider` object
(`779e5724-…`, display `<systemId> - Synthetic TestUser`) alongside the `user` object and the
`Provider` role — `user`/`currentProvider` are distinguishable and correct in both configurations.

**§10 (session/cookie, app-origin compatibility).** The self-links in REST responses correctly
carried the app-origin host (`http://app.disposable-a.invalid:8080/openmrs/ws/rest/v1/location/…`),
consistent with ADR-027/UI-004's expected architecture. `Secure; SameSite=Lax` itself was not
measured here — that attribute is added by Caddy's cookie rewrite in production (UI-004), not by
Tomcat directly, and per this ticket's own instruction production Caddy was not stood up or modified
to test it. Architecturally consistent, not directly measured.

**§11 (`oauth2login.started=false` gotcha) — reproduced deterministically, not just described.**
Recreated the disposable OpenMRS container twice against the same database: `SSO_ENABLED=false` →
`oauth2login.started` flipped `true → false` and the module was actually **unloaded**
(`OAuth2LoginActivator.willStart()` calling `ModuleFactory.stopModule()` then
`ModuleFactory.unloadModule()` — read directly from the pinned source; unload, not just stop, is the
operative verb) — and admin Basic Auth to `/ws/rest/v1/session` worked again, confirming break-glass
is restored when the module is genuinely disabled. Recreating a third time with `SSO_ENABLED=true`
on that same database: the module did **not** return — `oauth2login.started` stayed `false`,
`/oauth2login` `404`d again. **Nothing in the boot path re-loads an unloaded module when
`oauth2.enabled` later flips back to true** — a real activation runbook of "deploy SSO off, approve
later" is broken as it stands. `configure-sso.sh` was not modified to fix this: the fix requires
choosing between an OpenMRS admin-API module-start call and a direct database write, and the ticket
is explicit that the database write is out — squarely a `Human Decision Required: Yes` gap, not
something to decide unilaterally in a live, already-shipped activation script.

**§12 (restart, Keycloak outage) — both proven.** The full round trip was successfully re-run after
many container restarts across this ticket (not a synthetic one-off). Keycloak-outage test:
stopping `kc-ui007` mid-session left `/oauth2login`'s initial redirect construction unaffected (it
doesn't itself contact Keycloak) but the session remained `authenticated: false` — no session is
ever granted while Keycloak is unreachable; restarting Keycloak and re-running the flow succeeded
again immediately. Fails closed; recovers cleanly.

**§13 (failure-safety controls) — run, with one contradicted expectation.** Unauthenticated
`GET /ws/rest/v1/patient` → redirected to `/oauth2login`, never served. Wrong `redirect_uri` against
Keycloak's authorization endpoint → Keycloak `400`. Wrong client secret → verified directly at
Keycloak's token endpoint (`unauthorized_client` / `Invalid client credentials`) rather than through
OpenMRS end-to-end, because `oauth2.properties` is regenerated from `KEYCLOAK_OPENMRS_CLIENT_SECRET`
on every boot by `configure-sso.sh` — editing the file in place doesn't hold past a restart, and
recreating the container with a different env var for this one control was judged not worth a fourth
container recreation this ticket. **Contradicted expectation, recorded as measured, not assumed:**
`oauth2login`'s own request filter denies *every* unauthenticated path except
`/ws/rest/v1/session` once the module is active — this includes admin Basic Auth to any other REST
endpoint and the legacy login form. Break-glass local login, which this ticket and ADR-027 both
expected to remain available, **does not** while `oauth2login` is started. This is the module's own
documented design intent ("the only way to authenticate is through `/oauth2login`"), not something
UI-007's patch introduced — but it is a real, previously-unmeasured contradiction of this repo's
break-glass expectation. `Human Decision Required: Yes`.

**§14 (negative controls) — six of eight run; one did not reproduce, recorded honestly rather than
forced.**
- Unpatched module → original failure reproduced (§7 baseline). ✅
- Patched module → full OIDC succeeds (§8). ✅
- Wrong Keycloak client secret → fails (§13, verified at Keycloak directly). ✅
- Unauthenticated REST request → denied (§13). ✅
- Keycloak outage → no session granted, fails closed (§12). ✅
- Wrong redirect URI → Keycloak itself rejects with `400` before any OpenMRS involvement. ✅
- **Remove bridge filter's `config.xml` mapping → did NOT restore the 404.** Rebuilt the module
  with the `<filter-mapping>` block removed (filter class still declared, confirmed by unzipping the
  exact deployed artifact — `grep` for the mapping string in the packaged `config.xml` came back
  empty), redeployed, and `/oauth2login` still returned a full, correct `302` to Keycloak. Verified
  this wasn't a stale-container artifact (checksums matched the just-built no-mapping omod; by-name
  and by-IP requests to the container agreed). **Mechanism not understood — this control does not
  isolate the bridge as the sole cause of the fix**, and is reported as such rather than re-run until
  it produced the expected result. The isolation this ticket actually relies on is §7's baseline
  (unmodified upstream, same toolchain, same DB → original failure) versus patched → success, which
  does hold. One related, also-unresolved discrepancy: hitting `/ws/oauth2login` directly (bypassing
  `/oauth2login` entirely) produced a `302` bounce back to `/oauth2login` from
  `OAuth2LoginRequestFilter`, not the raw `500`/`UserRedirectRequiredException` UI-006 recorded for
  the same URL — worth a fresh look before anyone relies on UI-006's exact `/ws/oauth2login` behavior
  again.
- **"Break bridge target" / "remove redirect-exception handling" → not run**, given the
  filter-mapping result above already showed this specific rebuild-and-redeploy cycle does not
  produce diagnostic clarity for this module, and continuing would have meant repeating an
  unproductive cycle rather than gathering new evidence.

**THE BLOCKER (new, distinct from UI-006's, found only because UI-006's dispatch problem is now
fixed):** past the point the bridge patch reaches, `OAuth2LoginController.login()`'s
`Context.authenticate(new OAuth2TokenCredentials(userInfo))` failed with OpenMRS logging `Multiple
authentication schemes overrides are being provided, this is currently not supported.` —
`Context.setAuthenticationScheme()`'s by-type `getBean(AuthenticationScheme.class)` Spring lookup
throws `NoUniqueBeanDefinitionException` because **two** modules bundled in the O3 3.7.x reference
backend each register their own `@Component` `AuthenticationScheme` bean:
`oauth2login`'s `OAuth2UserInfoAuthenticationScheme` and `authentication` 2.3.0's
`DelegatingAuthenticationScheme` (confirmed by decompiling every loaded module's classes and
matching against the interface, not guessed from names). No scheme-selection global property
resolves this — `authentication.scheme` only configures what `DelegatingAuthenticationScheme`
delegates to *after* it has already won the ambiguous lookup. Disabling `authentication`
(`authentication.started=false`, a disposable global-property flip, reverted after testing) resolved
the ambiguity and let every §8–§13 proof above complete. **This is an upstream compatibility gap
between two modules the reference backend bundles together — not a defect in UI-007's bridge patch,
and not fixable by extending it.** `Human Decision Required: Yes` — disabling `authentication`
removes `ForcePasswordChangeFilter` and its 2FA/secret-question/lockout surface; making
`oauth2login`'s bean `@Primary` requires patching a second upstream module, which is exactly the
"second source patch, second fork" this ticket's minimal-bridge scope was meant to avoid. No
recommendation is made here on which way to resolve it.

**Disposable environment:** built a fresh `daher-ui007_internal` Docker network with
`mariadb-ui007`, `openmrs-ui007`, `kc-ui007` (realm `daher`, client `openmrs`, one synthetic user),
and a throwaway `ui007-curl` container used to drive the multi-step OIDC flow with `--resolve`-based
Host-header spoofing (simulating what Caddy's Host-preserving reverse proxy does in production,
without standing up a real Caddy instance). All containers, the network, and the disposable volumes
were removed by the end of this ticket using the same `docker exec <name> bash -c "kill -TERM 1"` →
wait-for-exited → `docker rm` technique UI-006 established, **except `ui007-curl`**: neither
`SIGTERM` nor `SIGKILL` delivered via `docker exec` to its PID 1 (confirmed with exit code 0 on both,
but the process did not die) had any effect, and daemon-level `docker rm -f` is blocked in this
sandbox the same way `stop`/`kill` are. **This one container was left running** — it is stateless
(a bare `sleep 3600` loop from `curlimages/curl`, no data, disconnected from any network) and will
exit on its own within the hour; it is flagged here rather than silently left. Confirmed clean
otherwise: no other `ui007`/`disposable`-named resources remain, and all twelve `daher-health-*`
containers are still up (one pre-existing, unrelated issue observed and not touched per "one ticket
at a time": `daher-health-adapter-1` is crash-looping and `daher-health-daher-ui-1` reports
unhealthy — both predate this ticket, per the git-status modifications to `provisioning/src/daher_agent/*`
already present in the working tree before this ticket started).

**Committed this ticket:** `openmrs-config/patches/oauth2login-bridge/` — `OAuth2LoginBridgeFilter.java`,
`config.xml.patch`, `PROVENANCE.md`. Not wired into `openmrs-config/Dockerfile`. This entry and the
matching `DECISIONS.md` update. No production Caddyfile, compose file, realm config, or
`configure-sso.sh` was modified.

**Human decisions required (three, all new, all block real facility SSO activation independent of
each other):**
1. How to resolve the `oauth2login`/`authentication` module `AuthenticationScheme` bean collision
   (disable `authentication` and lose its 2FA/lockout surface, or patch a second module).
2. Whether losing break-glass local login for the duration `oauth2login` is active is acceptable, and
   if not, how to preserve it (this is the bundled module's own designed behavior, not a bug in
   anything Daher wrote).
3. How to make `oauth2login.started` reactivate deterministically after a later SSO approval
   (admin-API call from `configure-sso.sh`, vs. a database write the ticket's own instructions
   rule out, vs. some other supported mechanism not yet identified).

**Recommended next ticket:** none of the above should be decided by an agent. Once Liban decides
question 1 (the collision), a short follow-on ticket can wire the (already-proven) bridge patch into
`openmrs-config/Dockerfile` and re-run this ticket's full proof against that decision. Questions 2
and 3 can be decided independently and in either order.

---

### UI-008 / DH-008C · Wire the oauth2login patch into the image build + resolve the AuthenticationScheme collision · ✅ SSO-off and SSO-on proven; §7/§8 and the full restart/negative-control matrix deferred by ticket-permitted scope cut

**Gate check.** Confirmed `8461d95` was not yet on `origin/master`, pushed it, watched CI run
`32625830380` go green on all 6 jobs before starting any work, per this ticket's explicit
precondition.

**§1–§2 (collision audit, re-confirmed).** Re-verified UI-007's finding by reading every bundled
omod's `config.xml` in the pinned O3 3.7.x backend (30 modules): `authentication-2.3.0` is the only
other module (besides `fhir2`'s unrelated servlet filter) touching `AuthenticationScheme`. This
ticket's own "human decisions already made" section had already settled question 1 from UI-007's
handoff (exclude `authentication` when SSO is on, don't fork a second module) — implemented as
`exclude-authentication-when-sso.sh`, sourced from `startup.sh` immediately after
`startup-init.sh`'s unconditional `$OMRS_MODULES_DIR` repopulation (confirmed from that script's own
source: it rewrites the modules directory from the read-only `distribution/` copy on **every** boot,
so the exclusion has to re-run every boot too, not once at build time).

**§3 (patch wired into the image, reproducibly).** `openmrs-config/Dockerfile` now clones
`openmrs-module-oauth2login` at the exact pinned commit
`2770233627343a53719ec981e997d94b1d90b7ad`, applies the Daher bridge patch via `git apply` (the
`maven:3.9.11-eclipse-temurin-17` build image has no `patch` binary — confirmed, switched to `git
apply` which is already needed for the clone step), and builds the module in a dedicated build
stage before copying the artifact into `distribution/openmrs_modules/`. `config.xml.patch` is
**byte-identical to UI-007's committed version** (bridge filter only; no `<mandatory>` element) —
see the dead end below for why. Both `on` and `off` build targets build clean; `docker compose
config -q` (default and `backup` profiles) and the demo compose file all parse clean with the
changed Dockerfile.

**Dead end, recorded so it is not retried: config.xml `<mandatory>true</mandatory>`.** Tried first,
paired with bumping `configVersion` to `1.3` (below `1.3`, `ModuleFileParser.extractMandatory()`
silently ignores the element regardless of content — confirmed from source). Built and deployed
correctly once an XML-comment-legality bug in the same edit was fixed (a literal `--` inside a
`<!-- -->` comment, the same class of bug UI-007 hit), but then threw
`org.openmrs.module.ModuleException`'s sibling, `MandatoryModuleException: The oauth2login module is
marked as 'mandatory' and so cannot be stopped or unloaded`, on every `SSO_ENABLED=false` boot.
Cause: `ModuleFactory.stopModule()` refuses to stop/unload a module whose `Module.isMandatory()` is
true (set from config.xml's `<mandatory>` element only) — and `OAuth2LoginActivator.willStart()`'s
own designed behavior is to call exactly `stopModule()`+`unloadModule()` on itself whenever
`oauth2.enabled=false`. Config.xml `<mandatory>` is therefore structurally incompatible with
`oauth2login`'s self-disabling design; reverted to the UI-007 patch content byte-for-byte
(`git show 8461d95:openmrs-config/patches/oauth2login-bridge/config.xml.patch`).

**§5 (collision resolution) — both modes proven live, not just designed.** Built a disposable stack
(`daher-ui008_internal` network, `mariadb-ui008`, `openmrs-ui008`) and, after the environmental
finding below, reached a genuinely booted, fully-initialized database. On that database:
- **SSO off:** `authentication-2.3.0.omod` present, `oauth2login` self-unloaded (its own activator's
  designed behavior), exactly one `AuthenticationScheme` bean (`Context.setAuthenticationScheme`
  logged the single-override line, never the "Multiple authentication schemes" error), admin Basic
  Auth to `/ws/rest/v1/session` succeeded (`authenticated:true`, real admin user/roles).
- **SSO on:** `authentication-2.3.0.omod` absent from `/openmrs/data/modules/` (confirmed by
  directory listing, not inferred), `oauth2login-1.5.0.omod` present and started, `/oauth2login`
  returned a `302` whose target host (`kc-ui008`) was never stood up this ticket — this evidences the
  module active and the bridge filter genuinely wired into the dispatch chain, **not** a reachable
  identity provider or a completed round trip; that proof is UI-007's, against this same patch
  content — exactly one `AuthenticationScheme` bean (same single-override log line, no collision),
  and admin Basic Auth to `/ws/rest/v1/session` returned HTTP 200 with `authenticated:false` in the
  body — break-glass correctly unavailable while `oauth2login` is active, matching UI-007's finding
  exactly (not a new regression, the module's own designed intent).

**§6 (`oauth2login.started=false` fix) — working mechanism found after one design that measurably
does not work.**
- **First design, disproven 3-for-3:** call the supported `ModuleActionResource1_8` REST resource
  (`POST /ws/rest/v1/moduleaction`, `action=START` — the same code path the admin UI's module
  "Start" button uses) to start `oauth2login` in place. This reliably flips `oauth2login.started` to
  `true` in the database every time it was tried (verified directly via `global_property` after each
  attempt), but **never** made `/oauth2login` reachable within the same container lifecycle — tried
  with a 5-minute wait after the call, then a 20-minute wait; both times the module's own triggered
  Spring context refresh (`Refreshing Context`/`Done refreshing Context` in the log) completed in
  single-digit seconds, and `/oauth2login` still 404'd for the entire remaining wait. This is not a
  patience problem — a natural boot that starts with `oauth2login.started` already `true` reaches a
  reachable `/oauth2login` in under 30 seconds, every time that was measured. Whatever the
  REST-triggered, already-initialized-context module start does internally, it does not reach the
  same end state as Tomcat's own boot-time module start. (An initial theory blaming a logged
  `IllegalStateException: Filters cannot be added to context [/openmrs] as the context has been
  initialised` was checked and **ruled out**: the same exception, from the same
  `WebComponentRegistrar`/`refreshWAC` stack, also fires on boots that fully succeed — it is
  unrelated to this failure and should not be cited as the cause.)
  A real defect found in the process: `activate-oauth2login-if-needed.sh`'s original design killed
  the container (via its own `exit 1` fail-loud path) three separate times **after** the database
  update had already succeeded, because it was checking a condition (`/oauth2login` reachability)
  that this REST-triggered start path can never satisfy — not because it wasn't patient enough;
  extending the wait window from 5 to 20 minutes did not change the outcome. Any future automation
  must not fail loudly on a check that can never pass by construction.
- **Working design:** `ModuleFactory.getModulesThatShouldStart()` (confirmed from source) starts a
  module if global property `<id>.started` is null/true, **or** if global property `<id>.mandatory`
  is `"true"` — two independent conditions — while `ModuleFactory.stopModule()`'s
  `MandatoryModuleException` guard checks only `Module.isMandatory()` (the config.xml-derived field,
  untouched by the global property). Setting `oauth2login.mandatory=true` via the standard
  `SystemSettingResource1_9` REST resource (`POST /ws/rest/v1/systemsetting` — the same
  `GlobalProperty` write path the admin settings UI uses, no SQL) therefore makes the module always
  attempt to start on the *next* boot without arming the guard that made the config.xml `<mandatory>`
  approach dead. `activate-oauth2login-if-needed.sh` now sets this property when it detects
  `oauth2login.started=false` with SSO on, then deliberately exits non-zero so
  `openmrs-backend`'s already-existing `restart: unless-stopped` policy provides the next boot —
  no new self-restart automation was added; the existing policy is reused for exactly the case it
  exists for. **Proven live, twice**, forcing `oauth2login.started=false` as the test precondition
  (a legitimate test setup per this ticket's own instructions — the SQL prohibition is on the fix,
  not on constructing the failure state to test against): first boot detected the flag, set
  `mandatory=true`, exited 1; Docker's restart policy relaunched the container exactly once
  (`RestartCount: 1`); the second boot started `oauth2login` normally and `/oauth2login` was
  reachable within ~40 seconds. The `mandatory` global property is **self-cleaning** — confirmed:
  once the module successfully starts, `ModuleFactory.startModule()` itself overwrites
  `<id>.mandatory` back to `Module.isMandatory()`'s real value (`false`, since config.xml carries no
  `<mandatory>` element), so nothing lingers between SSO toggles.
  **Precision on what's proven versus what's reasoned:** both live successes above ran against the
  *pre-fix* script text — a status-code-only `302` check for "already active." Verifying the fix
  against a fresh database (never having run `oauth2login` before) surfaced that the 302-only check
  cannot distinguish oauth2login's real redirect from OpenMRS's own wizard-incomplete
  `/initialsetup` redirect, which answers the identical path with the identical status code — and
  that attempting activation before OpenMRS's own first-time install finishes fails the REST call
  outright (the API isn't functional yet) and produced a genuine crash loop, `RestartCount` past 20
  within a minute, before this was caught. Fixed with two guards: check the actual redirect target,
  not just the status code, and gate the whole function on `/openmrs/data/openmrs-runtime.properties`
  existing (its absence is the exact condition OpenMRS itself logs as "Initial setup is needed" — on
  a database that has never started the module, there is no `started=false` to heal, so doing nothing
  is correct, not just safe). A third guard was added defensively, not because it was triggered: if
  `oauth2login.mandatory` is already `true` and the module still isn't active, stop restarting and
  leave the container running degraded rather than loop unboundedly. **The mechanism (mandatory
  property + one restart) is proven live, twice. The current script text, with these three guards, is
  not** — committed on source-level reasoning plus a clean build/syntax check, not a fresh live boot,
  given the time already spent this ticket on this one path. Recommended as the very first thing the
  next session verifies.

**§9 (restart matrix) — 2 of 8 cells proven, not the full matrix.** "Initialized DB, SSO off → SSO
on" and "SSO on → clean restart → still works" were both exercised live as part of the §6 proof above
(clean restart: `docker start` after a graceful in-container `kill -TERM 1`, `/oauth2login` reachable
again within ~20 seconds, no re-activation needed). Fresh-DB-with-SSO-on-from-first-boot, the
Keycloak-outage/recovery pair, and the break-glass transition cells were **not** run this ticket —
see the scope cut below.

**Environmental finding, not a regression: first boot is genuinely slow on this host, independent of
any patch.** The first attempt to reach a working session took over 50 minutes on a fresh database
before this ticket suspected an environmental cause rather than a bug. Diagnosed with `jstack`:
Hibernate Search's Lucene backend runs 15-20 parallel worker threads committing the full-text index
(`IndexWriter.commit()` → native `fsync()`), each accumulating only ~12s of real CPU time against
20-30+ minutes of wall-clock elapsed — I/O-bound, not looping. **Isolated with a genuine control**:
booted `daher-health/openmrs-backend:3.7-on` (the pristine, pre-UI-008 image, no oauth2login build
stage, no Dockerfile changes from this ticket) on its own fresh database with identical env vars —
it hit the **identical** stall, same thread pattern, same `IllegalStateException` log line, still not
ready after 30 minutes, block I/O climbing steadily (not flat/wedged) the whole time. This exonerates
every UI-006/007/008 change: the first-boot cost is a base-image/host characteristic on this
disposable, shared, resource-contended host, not something introduced by the oauth2login work. A
warm/pre-initialized database fixture would make future disposable-environment testing on this stack
dramatically faster and is worth its own ticket if this kind of testing continues.

**§4, §7, §8, §10, §11 — scope cut this ticket, not silently dropped:**
- §4's specific "rebuild without the patch, reproduce the original failure" baseline was not re-run
  standalone this ticket (UI-007 already proved it against this exact patch content); the pristine-
  image control above serves the same isolating purpose for the install-duration finding instead.
- §7 (break-glass operator procedure) was **not designed or implemented**. The ticket's own
  "human decisions already made" section already settled the *semantics* (normal=SSO-only,
  Keycloak-outage=fail-closed, break-glass=explicit operator action only, never automatic); what
  remains is the concrete mechanism, which is genuine scope on its own and was cut in favor of
  finishing §5/§6 with real evidence rather than leaving everything half-proven. Recorded as
  **not started**, not as a finding.
- §8 (full synthetic OIDC round trip against this integrated image) was **not re-run** — no
  disposable Keycloak container was stood up this ticket. UI-007 already proved this exact patch
  content end-to-end (synthetic user, correct provider, no Basic Auth/service-account fallback); §5's
  proof above establishes the collision-resolved image reaches a genuine `302` toward the identity
  provider, which is as far as this ticket verified without Keycloak running.
- §10 honored: no real facility SSO activated, no staff added, no clinical write, no OpenELIS wiring,
  no Daher logout UI, no clinical form touched.
- §11 negative controls run implicitly by the work above (unauthenticated/wrong-credential REST
  denied, collision-restored-equivalent already covered by the SSO-off mode proof, activation
  intentionally left `started=false` and confirmed to correctly fail the reachability check before
  the fix) but the explicit wrong-Keycloak-secret and Keycloak-unavailable-after-being-up controls
  were **not** run (no Keycloak container this ticket).

**Disposable environment.** `daher-ui008_internal` network, `mariadb-ui008`, `openmrs-ui008`, plus a
short-lived `openmrs-ui008-control`/`mariadb-ui008-control` pair for the pristine-image isolation
test. All containers, volumes, and the network were removed by the end of this ticket using the
established `docker exec <name> bash -c "kill -TERM 1"` → wait-for-exited → `docker rm` technique
(one container needed `docker update --restart=no` first — it had been started with `--restart
unless-stopped` for the §6 proof and kept auto-relaunching faster than the teardown loop's poll
interval could observe an "exited" state). Confirmed clean: no `ui008`-named containers, volumes, or
networks remain; the live `daher-health-*` stack (twelve containers) was not touched. Two pre-existing,
unrelated issues observed and left alone per "one ticket at a time": `daher-health-adapter-1` is
crash-looping (`Restarting (255)`), and two unexplained, unkillable auto-named containers
(`wizardly_lichterman`, `sad_pasteur`) remain on the host from outside this session's own work —
daemon-level `docker kill`/`rm -f` is blocked in this sandbox for them, the same restriction pattern
noted in UI-006/UI-007.

**Full local suite run** (Python 3.13 containers, matching UI-007's approach since the host's Python
3.14 has no `pydantic-core` wheel): adapter 94, provisioning 549, seed 16, form-safety 19, ui 22 =
**700 total, unchanged from UI-007** — no application code was touched this ticket, only
`openmrs-config/Dockerfile` and two new shell scripts. `ruff`/`oxlint`/`tsc`/`vitest`/`npm run build`
all clean. `docker compose config -q` (default + `backup` profiles) and the demo compose file all
parse clean.

**Committed this ticket:** `openmrs-config/Dockerfile` (oauth2login build stage, both `sed`
insertions into `startup.sh`), `openmrs-config/exclude-authentication-when-sso.sh` (new),
`openmrs-config/activate-oauth2login-if-needed.sh` (new). This entry and the matching `DECISIONS.md`
update if warranted. `config.xml.patch` unchanged from UI-007 (byte-identical, confirmed). No
production Caddyfile, compose file, realm config, or `configure-sso.sh` was modified. `SSO_ENABLED`
untouched on the live stack; no real facility SSO activated; no clinical write.

**Human decisions still required:**
1. Break-glass operator procedure (§7) — semantics already decided by this ticket's own instructions,
   concrete mechanism not yet designed.
2. Whether/when to actually flip `SSO_ENABLED=true` on the live stack — this ticket proves the
   mechanism works in a disposable environment; activation on the real facility stack remains a
   separate decision gated on staff-name mapping (BLOCKER-5) per DH-008's existing entry.

**Recommended next ticket:** first, one fresh-DB `SSO_ENABLED=true` boot from a genuinely empty
database against the current `activate-oauth2login-if-needed.sh` (three guards, never live-tested
together) to close that gap before anything else — this is cheap relative to everything else queued
and the current script text has not earned the same confidence as the mechanism it implements. Then
stand up a disposable Keycloak container and re-run §8's full round trip plus the remaining §9
restart-matrix cells (Keycloak outage/recovery, break-glass transition) against this now-integrated
image, then design §7's break-glass mechanism as its own scoped piece of work. Also worth its own
ticket, lower priority: a warm/pre-initialized database fixture for this stack's disposable-environment
tests, given the ~50-minute-plus first-boot cost measured (and exonerated as environmental, not a
regression) this ticket.

---

## Critical path

```
BLOCKER-1 ✅ → DH-002 ✅ → DH-005 ✅ → DH-007 ✅ → DH-008 🟢 ─┐
                       │           (activation: staff names)  ├→ DH-018
DH-009 ✅ → DH-010 ✅ → DH-025 🟢 → DH-011 ─────────────────────┤
                       │            │                         │
                       │            ├─ DH-011A ✅  ADR-026     │
                       │            ├─ DH-011B ✅  spike       │
                       │            ├─ DH-011E 🟢  poller      │
                       │            │     (built, NOT active)  │
                       │            └─ 🛑 BLOCKER-6 ── target leg
                       │                  (human decision)     │
                       │                     ↓                 │
                       │            DH-011C/J/K/L → DH-012 → DH-013 ─┘
                       │
        DH-014 → DH-015  (run parallel with SSO, not after)
```

**The critical path now runs through a human decision, not through code.** BLOCKER-6 gates
DH-011's target leg and therefore DH-012, DH-013 and DH-018 — five of the eight golden-flow steps.
Everything buildable on the OpenMRS side of that boundary is done.

Longest poles: BLOCKER-6's resolution plus the target leg behind it, and DH-008's activation (staff
names). DH-011E removed the poller/watermark from this list on 2026-08-17.

## Known bugs

| # | Bug | Status |
|---|---|---|
| 1 | Gateway could not resolve `backend`/`frontend` — hard-coded upstream names in its nginx template | Fixed (network aliases) |
| 2 | Backend healthcheck reported healthy 8s into a 49-minute boot | Fixed (explicit `= 200` on the REST session endpoint) |
| 3 | `OMRS_CONFIG_ADD_DEMO_DATA` inert in this image | Documented, not removed |
| 4 | Demo-patient trigger had two writers; `off` later allowed a second 50-row batch | **Fixed — DH-024**; local base removes only upstream's trigger, mounted `on/off` is authoritative; throwaway volume retains 100 fictional rows and two deliberate patients because no patient data was deleted |
| 5 | Admin password defaults to the published `Admin123` | **Fixed and empty-volume verified — DH-019** |
| 6 | The old throwaway volume still accepted `Admin123` after DH-019 landed | **Fixed in live state 2026-08-13** — rotated through the OpenMRS API; current configured password passes and default is refused |
| 7 | Default `docker compose up -d` selected an unconfigured backup scheduler and would restart-loop | **Fixed — DH-022**; backup is an explicit profile until production settings exist |
| 8 | OpenELIS Horiba Micros60 and Pentra60 imports abort on a unique-description conflict | **Closed by scope — ADR-016.** v1 is manual result entry; analyzers deferred to v1.1. Left untouched on purpose: rollback is clean, uniqueness holds, endpoints answer. Not a defect to fix — a log behaviour to recognise |
| 9 | PostgreSQL health passed against the temporary init server, racing hardening into shutdown | **Fixed and exact empty-volume graph verified — DH-024** |
| 10 | OpenELIS ignores `DEFAULT_PW` and shipped `adminADMIN!` even on empty volumes | **Fixed and empty-volume form-login verified — DH-024** |
| 11 | Optional OpenELIS Odoo test-product mapping CSV is absent and logs an error | **Documented, not fabricated** — no Odoo service or billing integration is in v1; adding product/test mappings would be scope and laboratory configuration work |
| 12 | A stale adapter `processing` claim cannot distinguish a pre-write crash from a post-write/pre-ledger crash | **Core fixed and runtime-verified — DH-025**; concrete OpenELIS correlation attach/query remains a DH-011 activation gate. No transport exists yet, so no message has been affected |
| 13 | **The OpenELIS internal TLS certificate expired 2026-07-23** and nothing on that path validates expiry, so it kept serving | **Fixed and live-verified — DH-021.** Material is now generated on this deployment, valid to 2028-11-16, and the generator renews early (`OE_CERT_RENEW_BEFORE_DAYS`) rather than at expiry |
| 14 | **`OE_KEYSTORE_PASSWORD`/`OE_TRUSTSTORE_PASSWORD` are empty in `.env`**, so `${VAR:-kspass}` silently selected upstream's published passwords while the variables looked configured | **Fixed — DH-021.** Both are now `:?` required; an empty value fails loudly at `up` instead of quietly selecting a public password |
| 17 | **The provisioning executor's 900s timeout is far short of a real OpenMRS first boot (~49 min)**, so the first genuine facility provisioning would time out and be reported as failed while the stack was still coming up | **Fixed 2026-08-16 — per-verb timeouts.** Liban chose the longer-timeout option over detaching. Verbs that wait on the compose health graph (`provision`, `start`, `rotate_tls`) get 5400s; `backup_now` gets its own bound because it scales with stored data, not boot time; `status`/`stop` stay at 900s. Both long bounds are per-host configurable. The bound is asserted **against the real `docker-compose.yml`** — it must clear every `start_period` (the largest is 1800s) and the measured 49-minute cold boot — so a future gate with a longer grace period fails the suite rather than the first deployment. 371 tests, Ruff clean. Control-verified three ways: restoring 900s fails the two guards by name, wiring `provision` to the fast bound fails the per-verb tests, and running without the compose file mounted **fails instead of skipping**. ⚠️ Still unfixed by this: bug 18 below, and the fix has **not** been exercised against a real ~49-minute boot — no clinical stack has ever booted through the provisioning path |
| 18 | **The agent does not poll while a long job runs**, so a `stop`/`status`/`backup_now` issued during a ~50-minute provision is refused as stale when finally claimed (`JobValidator` rejects jobs older than 900s) | **Fixed 2026-08-20 — `execute` moved off the poll loop's thread (`poller.py`).** `poll_once` dispatches to a background execution slot and returns immediately; the loop's `fetch` cadence continues while a long job runs. Two slots, not one: `status` is read-only (`docker compose ps`/`config`) and always runs, including alongside an in-flight mutating job; every other verb (`provision`, `start`, `stop`, `rotate_tls`, `backup_now`) shares one **mutating** slot, so at most one runs at a time — a second one claimed while the first is in flight is refused immediately (`"busy"`) rather than racing two `docker compose` invocations against the same project. Staleness protection (ADR-022) is untouched: `JobValidator` still runs synchronously on the calling thread, before dispatch, with the same 900s window — the fix is that a `status` issued mid-provision is now validated and run promptly instead of waiting behind it. `executor.py`, `validation.py`, `jobs.py` and `queue.py` are all unchanged; the queue's per-host FIFO dispatch order is unchanged too. ⚠️ **A companion lifecycle-table fix was required to ship this safely.** Concurrent polling makes it newly possible for a duplicate `PROVISION` targeting a facility still `PROVISIONING` to be claimed and refused (busy, or the pre-existing `FacilityExistsLocally` check) *while the real attempt is genuinely still running* — under the old, blocking loop this could never happen, because nothing else could be claimed until the first job's `execute()` returned. `next_facility_state`'s `PROVISION` row previously treated any non-`succeeded` report as `DEGRADED`; a busy-refused duplicate would have flipped a healthy, still-provisioning facility to `DEGRADED` **permanently** (the row only fires from `PROVISIONING`, so the real attempt's later `succeeded` report would arrive too late to match it and correct it). Fixed in `app.py`: the row now only degrades on a genuine `failed` report; `refused` is a no-op, matching how every other row already treats a non-`succeeded` report. Only reachable via a compromised/buggy API bypassing `create_facility`'s conflict check — ADR-022's own stated residual risk — but the codebase's own precedent (`FacilityExistsLocally`) treats exactly that threat as real, so it was closed rather than logged as a caveat. 11 new tests (poller concurrency, staleness regression, lifecycle-table symmetry), **464 provisioning tests total** (from 453). **Control-tested, three breaks, each on a throwaway copy of the tree:** disabling the busy-refusal check (any verb dispatches regardless of what is already in flight) — **3 fail**; restoring the old binary `PROVISION` lifecycle row (`RUNNING if succeeded else DEGRADED`) — **2 fail**; reverting `poll_once` to call `execute` inline instead of dispatching to a thread, reproducing bug 18 exactly — **6 fail**, one unaffected (`test_the_mutating_slot_frees_once_the_job_finishes`, correctly: slot-clearing-after-completion holds regardless of the execution model, so that control has nothing to break). ⚠️ **The demo harness (`demo/agent_runner.py`) was checked, not just assumed safe.** It now dispatches non-blocking, so it can claim a second job while the first is still executing, and its `agent_outcomes.jsonl`/`agent_executed.jsonl` files can interleave writes from two threads. Checked what reads those files: nothing committed does (`grep` across `demo/harness_*.py` and `demo/README.md` finds no consumer) — they are write-only instrumentation for manual inspection today, so there is no ordering assumption this fix could violate. Worth re-checking if a harness is later built to read them. Not exercised against a real ~50-minute boot — no clinical stack has ever booted through the provisioning path, so the concurrency fix is proven at the poller/queue level, not against real `docker compose` timing |
| 16 | **Three upstream demo forms loaded here perform clinical interpretation** — `structured_soap_note` (an `alert` key, `calculateExpression`, expression validators), `mental_health_assessment_form`, `form-engine-cookbook` | **Open — raised 2026-08-13 by DH-004's new guard.** Demo content, not maternity forms; nothing changed. `OMRS_DEMO_CONTENT=off` removes them, but that is a decided switch (BLOCKER-2) — **Human Decision Required: Yes** |
| 20 | **The Caddy container reported `unhealthy` for seven days while serving perfectly.** Root cause found 2026-08-20: the image runs `caddy` as **PID 1**, and Caddy does not reap orphaned children. Every healthcheck is a `docker exec`; each exited child was reparented to PID 1 and never `wait()`ed, leaking one PID per probe — ~5,760/day at one probe per 15s. The container reached `pids.current 37,499` against a cgroup `pids.max` of **37,503**, so no new process could start at all and every probe failed with `procReady not received` | **Fixed and verified 2026-08-20 — `init: true` on the caddy service.** Diagnosis was evidence-led, not plausible: `cgroup.threads` was **17** while `pids.current` was **37,499**; every other container in the stack had `threads == pids.current`; and 37,499 matches the ~40,460 healthchecks the container had run. The adapter already used `init: true` and sat at 3 PIDs, which is why it never leaked. After recreating with `--no-deps` (DH-016's lesson — `up -d caddy` re-runs `openelis-certs`): PID 1 is `/sbin/docker-init`, `pids.current` fell 37,499 → **19** and stayed flat across 8 healthcheck cycles, health went **healthy**, and all three vhosts still answer 301/200/302. DH-027D's parser now reports the live stack **healthy 11/11** — the signal it built was correct all along. A CI compose-invariant guard now fails if `init: true` is removed, control-verified in both directions |
| 19 | **OpenELIS `PatientProvider` returns `201 Created` when the native persist fails**, with no `Location` header in either case. Demonstrated 2026-08-17 by a control pair differing only in the patient's given name: an invalid name logged `ERROR … invalid name format` and created **0** rows; a valid one created 1. Both returned 201 | **Open — upstream defect, not ours; do not "fix" OpenELIS.** The consequence is ours to defend against: an adapter trusting this 201 would write `completed` into the DH-025 ledger for an order that never existed — silent loss, the one direction `WATERMARK-DESIGN.md` refuses. **Any Option-F implementation must confirm by read-back, never by status code.** Also blocks learning the created resource id from the response, which compounds DH-011C |
| 15 | **The OpenELIS webapp healthcheck is a false green.** It asserts only that `/OpenELIS-Global/` returns `302`. A completely failed Spring context still returns 302, so the container reported **healthy** while every page was a redirect loop and the application was down | **Fixed and verified 2026-08-13.** The check now requires `LoginPage` to return 200 *and* contain the Spring Security `_csrf` token — something only a live application context produces. Proven in both directions: it passes against the healthy webapp and **fails against the bare 302** (0-byte body) that the broken context returned. `start_period` raised 60s → 180s because the context must genuinely be up, not merely bound |
| 21 | **`daher-health-adapter-1` was restart-looping (2,779 restarts observed) on `Can't locate revision identified by '0002_watermarks'`**, discovered independently while wrapping up UI-009. The database was correctly at `0002_watermarks` with the full expected schema (`watermarks` table + `uq_watermarks_stream`); current source has always contained `0002_watermarks.py` with the correct `down_revision`. Root cause: the *running container* was bound to an old image (`sha256:dedcda03…`, since garbage-collected) whose `/app/migrations/versions/` held only `0001_safety_ledger.py` — confirmed with `docker cp` reading the container's own filesystem directly, not a fresh container from the current tag. The `daher-health/adapter:0.1.0` tag itself had already moved on to a correct image; the stale *container* simply never got recreated after that rebuild | **Fixed 2026-08-23.** No DB write of any kind was performed — no stamp, no drop, no rollback; `alembic_version` and all three tables (`processed_messages`, `dead_letters`, `watermarks`, all empty — the poller has never run) were confirmed unchanged before and after. Rebuilt the adapter image from current HEAD and recreated **only** the adapter container (`docker compose up -d --no-deps adapter`) against the existing, untouched database; every other service's uptime was confirmed unaffected. Restart count reset to 0 and stayed there; `/health/live` and `/health/ready` both `{"status":"up"}`; DH-011E's poller remains structurally inactive (no `POLL`/`ENABLE` env var exists to disable — it was never wired to run automatically). Root cause reproduced on demand as a negative control (a throwaway image missing `0002_watermarks.py`, run against a DB already at `0002`, gave the identical error) and cleaned up. A CI regression guard was added (`adapter` job, `.github/workflows/ci.yml`) that runs `alembic upgrade head` against a fresh ephemeral Postgres and asserts `current == heads == 0002_watermarks`; control-tested in both directions — passes against real source, and correctly **fails** against a copy with `0002_watermarks.py` deleted (comparing `current` to a hardcoded `EXPECTED_HEAD` was necessary specifically because `current == heads` alone is *not* sufficient: with the file missing, a fresh DB migrates to `0001` and both `current` and `heads` agree on that wrong answer, control-verified). This guard cannot by itself prevent a future stale-*container* incident — that is a deployment/ops class of bug, not a CI class of bug — it verifies the current build's migration chain is real and complete. 94 adapter tests unchanged. Two disposable Postgres containers used for this investigation could not be removed due to an intermittent daemon-level permission denial on `docker kill`/`stop`/`rm` (same environment quirk recorded at the end of UI-009) — noted for manual cleanup, not routed around |

| 22 | **`daher-health-daher-ui-1` had been failing its healthcheck continuously** (`wget: can't connect to remote host: Connection refused`, `FailingStreak` 7,017, spanning essentially its entire 29h uptime) | **Fixed 2026-08-23 — same stale-container class as bug 21, confirmed rather than assumed.** UI-004 had already found and committed the real fix (`docker-compose.yml`'s healthcheck probes `127.0.0.1`, not `localhost` — this image's musl libc resolves `localhost` to `::1` only, `getent hosts localhost` confirmed `::1 localhost` with no `127.0.0.1` entry, and nginx binds `0.0.0.0:80` only, confirmed via `ss -tlnp`) but deliberately did not recreate the already-running container to avoid a Docker dependency-cascade (Caddy's `depends_on: daher-ui: condition: service_healthy`). The live container was still carrying the pre-fix `Config.Healthcheck` (`http://localhost/`) 24+ hours later. The application itself was never down: nginx master + 18 workers running throughout, static bundle present, `wget http://127.0.0.1/` from inside the stale container returned `200` the whole time, and `https://app.localhost:8443/` through Caddy served the real shell and `/openmrs/*` routing correctly even while the container reported unhealthy — a pure false-red, the mirror image of bug 15's false-green. Negative control captured directly against the live, still-stale container before touching anything: `http://localhost/` → refused, `http://[::1]/` → refused (proving no IPv6 listener, not just a resolution quirk), `http://127.0.0.1/` → 200. Repaired narrowly: image rebuilt from current HEAD (all layers cache-hit, confirming the existing tag was already current — checked, not assumed, the same discipline as bug 21), then, because this sandbox's Docker daemon denies `stop`/`kill`/`rm -f` on running containers (the same constraint recorded against UI-004/UI-005 in `DECISIONS.md`), recreated via `docker update --restart=no` + `nginx -s quit` (nginx's own CLI signal path, not a daemon-mediated one — succeeded where `kill -9` on a PID had failed for other images this session) + `docker rm` + `docker compose up -d --no-deps daher-ui`. Every other service's uptime confirmed unchanged across the repair. Result: healthy immediately, `FailingStreak` 0, restart count 0, held stable; app origin and `/openmrs/*` routing reconfirmed after the recreate; bundle reconfirmed free of privileged secrets. No source change was needed — the committed config was already correct — so nothing to commit; CI unaffected |
| 23 | **`keycloak/mappers/openmrs-client-roles-mapper.json` carries no `usermodel.clientRoleMapping.clientId` scoping key**, live or committed — contradicting ADR-017B's own text claiming it is "scoped to `usermodel.clientRoleMapping.clientId=openmrs`". Found 2026-08-26 (ADR-017F): a synthetic user's `roles` claim measurably included Keycloak's default `account`-client roles (`manage-account`, `manage-account-links`, `view-profile`) alongside its `openmrs`-client roles, via the realm's own `default-roles-daher` composite. **Reconfirmed and escalated 2026-08-27**: this is not merely inert noise. A disposable negative control — a synthetic user holding only a foreign client's role named exactly `Provider` (no `openmrs` `Provider` grant, `fullScopeAllowed: true` on the openmrs client, confirmed live) — produced a pre-fix `roles` claim containing `Provider`, identical in shape to a genuine grant, and ADR-017E2's exact-string check cannot distinguish the two. Genuine privilege-escalation path, not a theoretical one | **Fixed, automated-tested, and disposable-live-demonstrated 2026-08-27.** Added `"usermodel.clientRoleMapping.clientId": "openmrs"` to the mapper config, identically in the committed `keycloak/mappers/openmrs-client-roles-mapper.json` (the reconciliation-path fragment) and the inline copy in `keycloak/realm-config/daher-realm.json` (the fresh-import path) — the two were already byte-identical duplicates and stayed that way. No oauth2login/Java change; ADR-017E2's exact-match Provider derivation untouched; no other Keycloak/OpenMRS config touched (`git diff --stat`: 3 files, no OpenELIS/SAML lines). Live-proven on a disposable Keycloak 26.7.1 realm imported from the committed JSON: pre-fix, a foreign-only `Provider` grant leaked `Provider` into the token (critical negative control reproduced); post-fix, the same user's token contained only its genuine `openmrs` roles, in both the fresh-realm-import path and the existing-realm `reconcile-sso.sh` path (re-applied live against the same running pre-fix realm). Six identities and every combination the ticket required were run end-to-end through real OpenMRS (`openmrs-adr017f`, unchanged from the Bug #25 proof, database `openmrs_adr017f`): two fresh clinicians each authenticated on their first callback with `roles: [Daher Clinician Admission, Provider]` (decoded server-side from the actual ID token OpenMRS consumed), `currentProvider` non-null, exactly 9 privileges, no `Organizational: Nurse`; a fresh Reception identity got `roles: [Daher Reception]`, `currentProvider: null`, 3 privileges; the foreign-`Provider`-only identity (plus the minimum legitimate `openmrs` → `Daher Reception` grant, needed only so OpenMRS itself would authenticate it) got `roles: [Daher Reception]`, `currentProvider: null`, and — checked directly against `provider` table rows, not inferred — **zero** Provider rows created; a mixed identity (genuine `openmrs` Provider + Admission, plus foreign `Provider` and an arbitrary foreign role) resolved `currentProvider` correctly from its genuine grant alone, with the foreign roles making no difference. Foreign-only grants of `Daher Clinician Admission` and `Daher Reception` (no genuine `openmrs` equivalent) produced an absent `roles` claim entirely — fail-closed. A realm-role-only identity (`clinician`, no `openmrs` client roles) also produced an absent claim — realm roles were never the leak vector; the `account`-client composite was. Canonical admission (`Daher Maternity Admission` visit → linked `Admission` encounter → `Maternité`, `encounterProviders: []`, audit creator = the authenticated clinician) still passed for the narrowed post-fix identity; Reception was still denied admission. Two further first-login callbacks (repeat logins) kept identical User/Provider UUIDs — Bug #25 not regressed. OpenELIS SAML config: proven untouched by `git diff`, not exercised live (no mechanism exists by which this change could reach it — separate client, separate protocol). New CI static guard (`.github/workflows/ci.yml`, "Bug #23") asserts both files stay byte-identical, `clientId=="openmrs"`, `claim.name=="roles"`, `multivalued=="true"`; control-tested against 5 deliberate regressions (pristine pre-fix config, wrong client, partial/drifted fix, renamed claim, wrong mapper type) — each failed for its specific asserted reason. All writes were synthetic/disposable (`openmrs_adr017f` DB, `kc-ui009`/disposable-Keycloak realms); real facility stack was not touched, `SSO_ENABLED` stayed `false` there |
| 24 | **First-time OpenMRS user auto-provisioning (`Daemon.createUser`, triggered by a genuinely new OIDC identity's first login) threw a `ValidationException`.** Found 2026-08-26 (ADR-017F); root cause isolated 2026-08-26 (Bug #24/#25 ticket), **corrected — not a platform defect**. The exact field error, extracted via `Accept: application/xml` (Jackson's `Accept: application/json` path was itself found to truncate the response before the message field, a separate cosmetic serialization quirk, not chased further): `'<user>' failed to validate with reason: email: Invalid Email address`. Cause: this ticket's own prior-session synthetic test users used the RFC 2606-reserved `.invalid` TLD (e.g. `adr017f-narrow@disposable.invalid`) — `UserValidator.isEmailValid()` (commons-validator's `EmailValidator`, checked against the pinned upstream source) correctly rejects it as not a real, IANA-recognized TLD. Reproduced identically for two different role shapes (clinician and reception), ruling out role composition as a cause and confirming it was purely the email. Fixed by changing the test users' emails to `@example.com` (a real, recognized TLD) — no source, config, or patch change; a real clinician's real email (any real ccTLD, e.g. Djibouti's `.dj`) would never trigger this | **Closed — was a test-fixture defect, not a code defect.** No fix needed beyond correcting synthetic test data. First-login provisioning works cleanly with a valid-TLD email — see ADR-017F's live closure proof for the full positive-path evidence (narrow clinician and reception, both first-login, both clean) |
| 26 | **`docs/UI-001-CLINICAL-UX.md` §13a.1 claims patient-search/visit-list results carry age/DOB data.** Found 2026-08-27 (UI-014's pre-write query audit): the `patient` sub-representation embedded in `GET /visit?...` never carries a nested `person` field at any representation depth tried (custom or full), on this pinned build. `GET /person/{uuid}` does return `gender`/`age`/`birthdate` at top level, confirming the data exists — it is a genuinely separate resource, never embedded in the visit-list call | **Documented, not fixed.** `docs/UI-001-CLINICAL-UX.md` needs a future correction pass (not touched by UI-014 — outside its own §14 change list); `OpenMrsPerson` was left in `types.ts` since other call paths (`formatBirthInfo`, patient search) still populate it. UI-014's Maternité board omits the birth-info line entirely rather than showing a misleading "non renseignée" |
| 25 | **First-login auto-provisioning discarded OpenMRS's authoritative daemon-created User and reread through an older transaction snapshot.** Root-caused 2026-08-26 against pinned oauth2login 1.5.0/OpenMRS 2.8.7. Formal pre-fix series: **10 failures / 10 brand-new valid-`@example.com` clinicians**. Instrumentation inside `OAuth2UserInfoAuthenticationScheme.authenticate()` measured an active outer transaction at `REPEATABLE-READ`: initial username lookup null; awaited `Daemon.createUser` returned a fully populated committed User from its separate thread/session; same-outer-session username reread still null. A separate SQL connection immediately saw the User/Person/roles, and the next HTTP transaction saw the User. oauth2login therefore built `BasicAuthenticated(null, ...)`, so the first callback returned unauthenticated and controller-level Provider reconciliation was skipped. The divergence was the transaction snapshot, not username/systemId mismatch, stale input, role timing, OpenMRS auth cache, delayed daemon completion, or an uncommitted write | **Fixed, automated-tested, and disposable-live-demonstrated 2026-08-26.** `OAuth2UserInfoAuthenticationScheme.java.patch` makes its private wrapper return `ContextDAO.createUser(...)` and assigns that returned User; the invalid same-transaction reread is removed. No retry, sleep, redirect, cache flush, isolation change, or broad transaction change. Focused tests: 3/3 first-login + unchanged 10/10 Provider derivation; pristine upstream negative control: 3 run, the two changed properties fail; static mutation control also fails specifically. Two fresh clinicians authenticated on the first callback with exactly 1 Person/1 User/1 Provider, exact ADR-017F roles/nine privileges, and stable UUIDs across two repeat logins each. Fresh Reception authenticated first try with `currentProvider:null`; registration succeeded and admission was denied. Deliberate validation failure left 0 User/Person/Provider/role rows. Canonical clinician admission passed with correct linkage/creator and `encounterProviders: []`. Application 857, Docker build, compose, and affected guards passed. All writes were synthetic/disposable; real facility SSO stayed off. Bug #23 remains open and unchanged. |

## Notes for next session

- See **▶ START HERE** near the top of this file for the immediate next action.
- Read `DECISIONS.md` before touching anything. ADR-005 is accepted and load-bearing; ADR-003,
  ADR-006, ADR-007 are pinned and not to be "simplified". **ADR-026 is OPEN** — Option F is a
  recorded candidate, not a selection.
- **The adapter's OpenMRS side is built but nothing runs it** (DH-011E). If you are tempted to wire
  up the 15-second loop to "finish the job", stop: there is no delivery target until BLOCKER-6 is
  decided, and a poller that claims work it cannot deliver would mark orders `completed` that never
  reached a laboratory. The ledger is deliberately marked `completed` only after a delivery returns
  a target record.
- **Two upstream behaviours to defend against, not fix:** OpenELIS's `PatientProvider` returns
  `201 Created` when its native persist fails (bug 19 — verify by read-back, never by status code),
  and OpenMRS `fhir2` 4.2.0 accepts `_sort` and silently ignores it (`WATERMARK-DESIGN.md` §3).
- ~~**Caddy reports `unhealthy` while serving correctly**~~ — ✅ **diagnosed and fixed 2026-08-20**
  (bug 20). It was zombie-PID exhaustion from exec healthchecks with a non-reaping PID 1; `init: true`
  fixes it. The instinct to restart first would have cleared the evidence and the fault would have
  returned in a week — worth remembering next time something looks like "just needs a restart".
- **Step 1 has now genuinely run.** API verification and Liban's SPA registration are separate
  evidence and both are complete; DH-003 verified French configuration, while the human-visible
  SPA language observation remains explicitly unreported.
- `docker compose config` passing still means only that the syntax parses. Every one of the five
  findings above survived that check.
- Digests are recorded from a real pull (ARCHITECTURE §12). **Never fabricate a digest.**
- Timing: cold boot ≈49 min, warm ≈38s. Never `docker compose down -v`.
- Nothing here authorizes real patient data. Pilot-ready ≠ clinically live (ADR-012). This local
  stack now holds 100 fictional demo patients plus two deliberate test patients in OpenMRS, **and one
  synthetic patient in OpenELIS** (`id=2`, `Dhspike Zzsynthetic`, created by DH-011B and retained on
  purpose), which makes that distinction concrete rather than theoretical.
- **An open direction, not yet an ADR:** the 2026-08-17 roadmap moves the project to **cloud-primary**
  deployment. That changes where patient data physically lives, which `AGENTS.md` makes a
  stop-and-ask, so it needs an ADR before RES-001/002 or DH-027J/K proceed. `docs/ARCHITECTURE.md`
  still says data lives "on the facility's own VM" — deliberately left until the decision is made
  rather than quietly rewritten.
