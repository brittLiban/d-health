# STATUS — Daher Health

**This is the canonical, present-tense status document for this repository.** It is short by
design and does not replace the detailed historical records it summarizes. Where this file and any
other document disagree, see §J (Documentation precedence) before assuming either is wrong.

## A. As-of

**2026-09-18, DH-DEMO-001, directly on `master`** (this ticket's own explicit instruction: no
branch, no PR). Starting point was `master` at `0916afd` (DH-LAB-005's own final commit). All
facts below were re-verified directly against the current repository and fresh local test runs on
this exact commit. **DH-CLINIC-004B's own claims (§C/§I) are unchanged by this ticket** — this
ticket's `git diff` touched no authentication/Keycloak/Caddy production code at all; the only
pre-existing files modified are `.github/workflows/ci.yml` (additive) and one UI test file, every
other file is new. **Real-PostgreSQL qualification of this ticket's own reset-safety boundary and
baseline/reset idempotency was actually run this session** (2 tests, 4 executions, zero flakes,
including a genuine positive control and a genuine permission bug found and fixed) against the
same native PostgreSQL 18 instance prior tickets located inside the sandbox. **This sandbox's
Docker still cannot pull real image blobs from the CDN** (documented since DH-LAB-002), so this
ticket's own central claim — a native-only Compose stack reaching healthy without ever waiting on
OpenMRS/OpenELIS — has **not** been observed booting anywhere yet; the CI job this ticket's single
push triggers is its first real attempt, not a confirmation of something already proven locally.
This ticket adds no new migration — Alembic head stays `0025_native_result_binding`.

**2026-09-18, DH-LAB-005, directly on `master`.** Starting point was `master` at `eefc1f2`
(DH-LAB-004's own final documentation commit). Real-PostgreSQL qualification of this ticket's own
four new concurrency races (concurrent Native corrections of the same observation, Native
correction vs. original release, amendment Native correction vs. amendment release, two releasers
racing the same amendment) was run against the identical native PostgreSQL 18 instance DH-LAB-004
first located inside the sandbox, migrated to head via real `alembic upgrade head`, all 4 tests run
clean 4x in a row. See the DH-LAB-005 entry in `PROJECT_STATE.md` for exact commands and evidence.
This ticket added no new migration — Alembic head stayed `0025_native_result_binding`, confirmed
against CI's own `EXPECTED_HEAD`.

## B. Product direction

DHealth is an interoperable diagnostic network: providers, laboratories, and patients, either on
native DHealth apps or through an existing EMR/LIS connector. The current product contract is
`DECISIONS.md`'s **ADR-043** and **`docs/MVP-1.0.md`**. The pre-pivot OpenMRS/OpenELIS
maternity/birth-centre system is preserved as the **first reference connector track**, real and
still binding wherever it is touched, not the definition of DHealth's overall scope. Four product
laws govern the Exchange track: no forced marketplace, no double entry, faster than what it
replaces, no digital-literacy requirement for care (`docs/MVP-1.0.md` §6). Nothing in this ticket
changes, weakens, or reinterprets any of the four.

## C. PROVEN / QUALIFIED

*Qualified at the specific evidence level stated per item below — CI/unit/integration and, where
noted, real-PostgreSQL. This is not a claim of production deployment, and where a ticket's own
formal closure is a separate reserved human decision, that is stated explicitly in its bullet.*

- **DHealth Exchange backend, DH-EX-001 through DH-EX-014B**: organization onboarding; patient/order
  creation; provider- and patient-driven lab assignment; accept/reject/processing; sample
  collection; multi-observation result entry/completion (`LabOrderItem → LabResult → LabObservation[]`,
  ADR-047); an explicit, separately authorized release step (ADR-044); immutable pre-release
  observation correction (ADR-045 Qs 1-3); an immutable release-version manifest and versioned
  `DoctorReview` (ADR-048 Model 3, DH-EX-014A); post-release amendment preparation/release under the
  same model (DH-EX-014B); a provider-scoped native Patient directory, separated from
  `PatientReference` source-identity provenance (ADR-051, DH-IDENTITY-001); explicit,
  platform-admin-configured provider-lab routing relationships, distinct from marketplace listing
  (ADR-052, DH-CLINIC-006); a provider-scoped, one-obligation-per-review result-review queue
  (`GET .../result-reviews`, DH-CLINIC-007) exposing that same ADR-048 machinery operationally; a
  lab-safe minimal patient-identity projection on the lab work-queue response, resolved from
  `Patient` (never `PatientReference`'s own snapshot fields) via a new N+1-safe batched
  multi-provider resolution path (DH-LAB-001); the lab work-queue's own remaining `LabOrder.items`
  lazy-load N+1 (disclosed but left unfixed by DH-EX-011/DH-LAB-001) is now closed via
  `selectinload(LabOrder.items)` — one bounded query per queue read regardless of order count, not
  one per row (DH-LAB-002); a controlled, immutable, `LabOffering`-scoped Native Lab result-structure
  template layer (`LabResultTemplate`/`LabResultTemplateObservation`, ADR-053) plus a batched,
  read-only order-scoped result-entry-plan resolving each `IN_PROGRESS` item's active template
  (DH-LAB-003) — additive to, and never constraining, the existing source-flexible
  `LabObservation`/`record_observation` model Connect will eventually use; DH-LAB-004 closes
  the native loop with first-write exact template-version pinning (survives later activation
  changes), a Native controlled batch value endpoint, required-definition-gated completion, a
  least-privilege release queue, and a release-inspection fingerprint that closes the
  inspect-then-stale-release race; **DH-LAB-005** closes the two gaps DH-LAB-004 deliberately left
  open rather than build unsafely — a Native controlled *correction* endpoint (browser submits only
  the new value; every structural field, including the pinned template context, is derived
  server-side from the predecessor's own template definition, both pre-release and inside an
  amendment) plus a complete post-release amendment lifecycle (discovery that survives a page
  refresh, controlled amendment correction, a fingerprint gate on amendment release mirroring
  DH-LAB-004's original-release gate exactly, a least-privilege amendment release queue that never
  lists an amendment with zero revisions, and a lab-facing exact-per-version content read). The raw
  free-structure observation/amendment-correction APIs both now refuse a template-bound
  predecessor outright (fail closed, proven with genuine red→green discrimination on both), and no
  new `LabOrderStatus`/amendment status was added — an order stays `COMPLETED` after an amendment
  release; PREPARING/RELEASED remain the only two amendment states, with no "cancel" state of any
  kind. DH-LAB-002/003/004 are CI-qualified and CI-PostgreSQL-qualified where CI runs it (SQLite +
  disposable `postgres:16-alpine`); **DH-LAB-005 is fully locally qualified** (adapter + UI suites,
  real-PostgreSQL concurrency races) **but not yet CI-qualified** — this ticket's own
  GitHub-Actions-budget-mode instruction targets one push for the whole ticket, so the push-and-CI
  step happens after this documentation, not before; see `PROJECT_STATE.md`'s DH-LAB-005 entry and
  the handoff for the actual CI run result. Migration head unchanged at
  `0025_native_result_binding`, current == heads == CI's `EXPECTED_HEAD` (this ticket needed no
  schema change). DH-LAB-004's and DH-LAB-005's own new concurrency logic is **additionally**
  qualified against a real, natively-installed PostgreSQL instance found in the sandbox — see §A.
  **Not** real-Keycloak-qualified, not runtime/browser-demonstrated, not production-deployed. Full
  ticket-by-ticket detail: `docs/MVP-1.0.md` §8, `PROJECT_STATE.md`.
- **DH-IDENTITY-001 — provider-scoped native Patient directory (ADR-051)**: `Patient`
  (`exchange_patients`) now exists as the provider-scoped human-workflow identity record, separated
  from `PatientReference`'s unchanged exact source-identity/provenance meaning. Native, idempotent
  registration (`POST .../organizations/{provider_id}/patients`), provider-scoped directory
  read/list/search, §19 registration recovery by idempotency key, and the legacy exact-identity
  bind endpoint's own compatibility behavior (new identity → new 1:1 Patient; existing identity →
  unchanged) are all implemented. Migration `0022` backfills every pre-existing `PatientReference`
  1:1, never merged. **No fuzzy/demographic matching anywhere** — proven by test that identical
  demographics under a different idempotency key or a different source identity always produce a
  second, distinct Patient. Database-level tenant safety: `PatientReference`'s composite foreign key
  to `Patient(id, provider_organization_id)` rejects a cross-provider link, proven against real
  PostgreSQL (`tests/integration/test_dhidentity001_postgres_qualification.py`, CI-wired) — SQLite
  does not enforce foreign keys by default, so this claim rests on the Postgres run, not the
  SQLite-based unit suite. Real-PostgreSQL-qualified: registration concurrency (PG1-PG8), the FK
  rejection above, and migration backfill/downgrade-guard qualification
  (`scripts/qualify_dh_identity_001_migration.py`, CI-wired). Adapter: **897 passed, 101 skipped**
  (855 pre-existing + 35 new patient-directory tests + 7 new legacy-binding-compatibility tests
  including a post-completion-review regression fix, 3 new Postgres-only tests correctly skipped
  locally; count corrected here from an earlier stale 896 — the fix landed after this line was first
  written). Native Clinic frontend (`ClinicPatientsPage.tsx`) migrated to the new directory API;
  `pendingPatientStorage.ts` rewritten so persistent browser recovery state carries an idempotency
  key only, never any demographic field. **Not** real-Keycloak/browser-runtime-qualified — same
  CI/vitest evidence level as the rest of DHealth Clinic, no new claim beyond that. See
  `DECISIONS.md` ADR-051 and `PROJECT_STATE.md`'s DH-IDENTITY-001 entry.
- **DH-CLINIC-005 — wrong-patient-safe human identity UX**: Native Clinic's Orders/Results screens
  no longer identify patients by truncated UUID fragments. Order/result read APIs
  (`LabOrderResponse`, `LabOrderSummaryResponse`, `ProviderOrderResultsResponse`,
  `ProviderReleaseVersionContentResponse`) now carry a server-resolved `patient` identity projection
  (name/DOB/contact only), resolved through `patient_reference_id -> PatientReference.patient_id ->
  Patient`, batched per list (proven not N+1 by counting actual SQL statements) — never a
  client-supplied id, never the legacy `PatientReference.display_name` snapshot. A reference whose
  linked Patient cannot be resolved fails closed as a server integrity error, never silently renders
  `patient=None`. No schema migration — additive read-side API only. Frontend: a shared
  `PatientIdentity` component (name prominent, truthful "Nom non renseigné"/"Identité limitée"
  fallbacks, timezone-safe DOB formatting, full technical id never truncated) now drives
  Orders/Results/Patients; `ClinicPatientsPage.tsx`'s native workflow operates on
  `PatientDirectoryEntry` directly (no more `ClinicPatientSummary` compatibility bridge); a real,
  previously-untested async race in `ClinicResultsPage`'s result-detail loading (a stale earlier
  selection's response could overwrite the current one) is fixed and covered by 7 mandatory
  concurrency tests (RR1-RR7). Adapter: **911 passed, 101 skipped** (897 baseline + 14 new). UI
  clinic suite: **171 passed** across 10 files. **Not** real-Keycloak/browser-runtime-qualified —
  same CI/vitest evidence level as the rest of DHealth Clinic. See `DECISIONS.md` ADR-051's
  2026-09-17 clarification and `PROJECT_STATE.md`'s DH-CLINIC-005 entry.
- **DH-CLINIC-006 — provider-assigned laboratory workflow (ADR-052)**: `ProviderLabRoute`
  (`exchange_provider_lab_routes`, migration `0023_provider_lab_routes`) is the missing
  routing-authority concept behind DH-EX-010's `assign_by_provider` — a platform-admin-configured,
  explicit "may this provider route to this lab" fact, independent of `LabDirectoryProfile.listed`
  and never joined against `LabPrice` anywhere in the new code path. New APIs: route management
  (`PUT .../lab-routes/{lab_id}`, platform-admin only, idempotent, audited), the provider's own
  route directory (`GET .../lab-routes`, CLINICIAN), and the order-specific whole-order eligibility
  read (`GET .../orders/{order_id}/eligible-labs`) that the routing UI actually consumes — zero
  eligible labs is a real, valid `[]` response, never an error or a fabricated fallback.
  `assign_by_provider` now requires an active route *inside its own locked mutation*
  (`ProviderLabRouteNotActive` → 409), checked before every other re-check, so a future caller
  bypassing HTTP cannot skip the routing boundary; deactivating a route never touches an
  already-made `LabSelection`. `LabOrderResponse`/`LabOrderSummaryResponse` gained a batched,
  N+1-safe `lab_assignment` projection resolved from the canonical `LabSelection`, present whenever
  one exists regardless of the order's current status (a SELECTED-then-REJECTED order still shows
  the lab that rejected it) and failing closed (`LabAssignmentIntegrityError`) only when a status
  that structurally requires one has none. Real-PostgreSQL-qualified (§20 PG1/PG2/PG5: concurrent
  same-lab assignment converges to one `LabSelection`; concurrent different-lab assignment has
  exactly one winner and one clean conflict; a route-deactivation race resolves to one of two
  truthful outcomes only, never a torn state). Frontend: a new shared `LabRoutingStep` component
  (patient + ordered tests + eligible labs + explicit confirmation, no price/ranking of any kind,
  structurally unable to call the marketplace/price/patient-selection endpoints) drives a new
  `'routing'` step in the order wizard (strictly after durable commit, strictly before
  confirmation) and a new "Choisir un laboratoire" action on both the Orders page and the Patient
  workspace's order history, for any order still `AWAITING_LAB_SELECTION`. Adapter: **960 passed**
  (adapter suite grew by ~49 tests; one unrelated pre-existing test-isolation flake confirmed by
  individual re-run, not a regression). UI: full suite passes (WSL2/vitest worker-pool flakiness
  non-deterministically drops a handful of unrelated files per run, confirmed individually green;
  clinic suite alone: **150 passed** across 10 files, up from 146). **Not**
  real-Keycloak/browser-runtime-qualified — same evidence level as the rest of DHealth Clinic.
  **Does not solve "no double entry"** (ADR-043 Law 2) — this proves DHealth-side routing to a
  DHealth lab *organization* only, not that any real lab staff system received the order; that is
  DH-PILOT-TOPOLOGY-001's own future choice. **DH-CLINIC-004B remains separately open, unchanged.**
  See `DECISIONS.md` ADR-052 and `PROJECT_STATE.md`'s DH-CLINIC-006 entry.
- **DH-CLINIC-003C — durable provider lab order intent (ADR-049)**: re-verified this ticket, fresh
  local run on this exact commit, not carried forward — **adapter: 854 passed, 98 skipped**;
  **UI: 26 test files, 370 tests, all passing**. Bidirectional `LabOrder.intent_id`/
  `LabOrderIntent.committed_lab_order_id` ownership, cross-provider/cross-clinician anti-enumeration
  (404, not 403), server-derived practitioner identity (request body cannot supply one), historical
  (pre-003C, `intent_id IS NULL`) order replay preserved through the legacy endpoint, and
  concurrency PG2-PG6 (real PostgreSQL) all re-confirmed present and green. **Scope-qualified, not
  closed**: qualified for same-browser-profile reload/recovery only — no cross-device or
  cross-browser discovery workflow exists or is claimed; no real-Keycloak or browser-runtime
  qualification exists for this ticket either. Formal closure (`DH-CLINIC-003C CLOSED: Y/N`) remains
  Liban's own decision, per his explicit instruction — this status re-verifies the technical claims,
  it does not self-declare closure. See `PROJECT_STATE.md`'s DH-CLINIC-003C entries and
  `DECISIONS.md`'s ADR-049.
- **OpenMRS/OpenELIS maternity/birth-centre stack**: real, running, unchanged by this ticket. See
  `CURRENT_STATE.md`'s "Assessed 2026-08-17" inventory for the detailed, still-accurate breakdown.
- **Native browser authentication + same-origin Exchange routing (ADR-050, DH-CLINIC-004A)**: a
  public, secretless `dhealth-native-ui` Keycloak client (Authorization Code + PKCE S256, standard
  flow only) now exists in the `dhealth-exchange` realm, carrying the same `dhealth-exchange-api`
  audience the backend already validates unchanged — proven by a new adapter unit test that mints a
  token under the new client's `azp` and confirms `TokenValidator` accepts it. `app.${BASE_DOMAIN}`'s
  Caddy vhost now proxies `/exchange/v1/*` to the adapter, same path scoping as the existing
  `exchange.${BASE_DOMAIN}` vhost. A shared, Clinic-independent auth layer
  (`ui/src/features/auth/exchange/`) drives `keycloak-js` with `check-sso` (never a redirect as an
  init side effect), holds tokens in memory only, single-flights and fails closed on refresh, and
  gates Clinic's data layer so `/me` cannot fire before real authentication exists.
  **CONFIG-QUALIFIED for the auth layer itself; the realm-reconciliation gap this ADR originally
  flagged as unbuilt is now closed and real-Keycloak-runtime-qualified (DH-CLINIC-004B,
  `keycloak/reconcile-exchange.sh`, run `35210377720`, commit `7752bf9` — real pinned Keycloak
  26.7.1, `Exited (0)`, reconciler's own success log observed).** That same real run also found and
  fixed a genuine defect this ADR's config-level review had missed twice: two realm-JSON description
  fields exceeded Keycloak's `VARCHAR(255)` column limit and crashed the entire realm import against
  a real database. **Still not observed: a real browser completing the redirect/login/PKCE round
  trip.** The qualification run's full-stack boot stalled on the pre-existing, unmodified
  `openmrs-backend`/`openelis-webapp` services never reaching healthy within CI's wait window, which
  blocks Caddy and therefore every `app.${BASE_DOMAIN}` vhost the browser suite needs — consistent
  with this repo's own documented ~50-minute-plus full-stack cold boot (`.github/workflows/ci.yml`
  header), not a defect in this ADR's own code. See `DECISIONS.md` ADR-050's 2026-09-17 overlay and
  `PROJECT_STATE.md`'s DH-CLINIC-004B entry.
- **Native demo reset-safety boundary and baseline/reset idempotency (DH-DEMO-001)**:
  `demo_seed.py`'s three-fact refusal (wrong database name; a process-own env var; an unreadable/
  missing marker table) and its `TRUNCATE ... CASCADE` reset (clears every workflow table,
  preserves every baseline row, idempotent on replay) are **real-PostgreSQL-qualified** — 2 tests,
  4 clean executions, including a genuine positive control (a differently-named scratch database
  was correctly refused) and a genuine permission bug found and fixed (the marker table's missing
  `GRANT SELECT`, which without the fix would have made a non-demo database raise a raw DB error
  instead of a clean refusal). See `PROJECT_STATE.md`'s DH-DEMO-001 entry.

## D. BUILT BUT NOT RUNTIME-PROVEN

- **DHealth Clinic native frontend** (`ui/src/features/clinic/`): patient registration/search
  against the native Patient directory (DH-IDENTITY-001, §C), diagnostic order creation against the
  Exchange catalog using the directory entry's `primary_patient_reference_id` compatibility bridge,
  durable
  order-intent recovery across reload/restart, order status, results/release-version history, and
  (as of DH-CLINIC-004A, §C) a complete, config-qualified authentication path. Built and
  component/UI-tested. **Still cannot be called deployed.** DH-CLINIC-004B has since observed a real
  Keycloak import of the two-client file and real reconciliation against it (§C) — but no browser has
  actually completed a redirect/login/PKCE round trip, and no live Caddy routing has been exercised
  end to end, because the full stack (Caddy included) never finished booting in the one qualification
  run attempted. That gap is DH-CLINIC-004B's own open item (§I), not closed by this entry.
- **DHealth Lab native frontend** (`ui/src/features/lab/`, DH-LAB-001, extended by DH-LAB-002,
  DH-LAB-003, DH-LAB-004, and DH-LAB-005): authentication (reusing the same shared
  `ExchangeAuthProvider`/`ExchangeAuthGate` Clinic uses — no second OAuth client), a capability
  model (`canWorkOrders`/`canReleaseResults`) that now drives real navigation — a dual-role user
  sees Accueil/Ordres/Libération, a releaser-only user sees Accueil/Libération with no route to the
  `LAB_TECH` queue at all — a lab selector for multi-lab users with immediate (render-phase) state
  clearing on switch, an assigned-order work queue, a safe order-detail panel driven by the
  lab-safe minimal patient projection (§C), accept/reject with explicit reject confirmation and
  three-way lost-response recovery, an explicit collection-milestone confirmation and a
  start-processing confirmation, each with its own lost-response recovery and
  generation/tenant-ref race protection. DH-LAB-004 turned DH-LAB-003's read-only
  result-structure preview into a controlled Native entry form (`NativeResultEntryForm`); a new
  "Libération" surface (`LabReleasePage`) gave releaser-only users a least-privilege release queue,
  full result inspection, and a fingerprint-gated release confirmation. **DH-LAB-005 closes the
  correction/amendment gap this surface deliberately left open**: an already-entered Native value
  now shows "Valeur actuelle" plus a Corriger control (browser submits only the new value; the
  exact confirmation wording is required), with correction history exposed via the existing
  correction-history read (current vs. superseded always labeled unambiguously, never as equally
  current); a COMPLETED order's panel now discovers, starts, or resumes a post-release amendment
  (server-driven, never dependent on browser memory — "Commencer un amendement"/"Reprendre
  l'amendement" are mutually exclusive, and no "Annuler l'amendement" control exists anywhere,
  since PREPARING/RELEASED are the only two amendment states that exist); a new
  `AmendmentWorkspace` component gives the durable-revision warning before an amendment's first
  correction; `LabReleasePage`'s "Libération" tab gained an "Amendements" section (queue,
  inspection, fingerprint-gated release, with the exact distinct stale-fingerprint wording an
  amendment race requires) rather than a disconnected second page; a new `ReleaseVersionHistory`
  component exposes every release version (status always textual, never color-only) and one
  version's exact, immutable content on demand. Built and component/UI-tested only — **same "built
  but not runtime-proven" boundary as DHealth Clinic above**: no real-Keycloak or real-browser
  qualification exists for it either. LIS/Connect integration remains explicitly **not** part of
  this surface. See `DECISIONS.md` ADR-043's 2026-09-18 clarification, ADR-053 and its
  DH-LAB-004/DH-LAB-005 addenda, ADR-044's DH-LAB-004 addendum, ADR-045's DH-LAB-005 addendum,
  ADR-048's DH-LAB-005 addendum, and `PROJECT_STATE.md`'s DH-LAB-001/DH-LAB-002/DH-LAB-003/
  DH-LAB-004/DH-LAB-005 entries.
- **Native demo environment** (DH-DEMO-001, `docker-compose.native-demo.yml`,
  `scripts/demo/*.sh`, `adapter/src/daher_adapter/exchange/demo_seed.py`): a dedicated Compose
  project running the real Keycloak/adapter/DHealth-UI/Caddy code paths, deliberately never
  waiting on OpenMRS/OpenELIS (which stays completely untouched), with a P0 reset-safety boundary
  (three independent facts, real-PostgreSQL-qualified — §C) and deterministic synthetic-only
  baseline data. Real Keycloak-backed authentication throughout — grepped, zero demo-conditional
  branch exists in any authentication/request-handling code. **Built and locally qualified only —
  the demo has never actually booted anywhere.** This sandbox's Docker cannot pull real image
  blobs from the CDN, so the one thing this ticket exists to prove — native-only boot reaching
  healthy — has not yet been observed; the CI job this ticket's push triggers is its first
  attempt, not a confirmation. No real Keycloak login and no real browser walkthrough of the
  golden journey exist yet either. See `PROJECT_STATE.md`'s DH-DEMO-001 entry for the full
  evidence matrix.
- Everything in §C's Exchange-backend list beyond CI/PostgreSQL qualification: no real-Keycloak
  qualification, no manual live/browser demonstration, no production deployment, for any Exchange
  ticket.

## E. BLOCKED / OPEN

- **WhatsApp/SMS/email result delivery**: `ResultDelivery` is created `PENDING` at order creation
  and no code path advances it past `PENDING`. Stays `BLOCKED`, not `DONE`, until a real transport
  runs the flow against real credentials — a mock is never done, per this repo's pinned P0 rule.
- **Real Keycloak qualification of authorization boundaries**: still not run for token
  validation/audience/authorization decisions — every such boundary is proven only against a locally
  generated RSA keypair (`StaticKeySource`), never a live identity provider. (Narrower than before:
  DH-CLINIC-004B did runtime-qualify realm/client *reconciliation* against a real Keycloak — §C — but
  that is a deployment-provisioning proof, not a token-validation one.)
- **Real-browser qualification of native Clinic authentication**: the config-level pieces exist
  (§C), and realm reconciliation is now real-Keycloak-qualified (§C), but a real OIDC round trip
  against a live browser/Caddy stack has never been observed — the one qualification run attempted
  stalled before Caddy ever started (§D). This is DH-CLINIC-004B's open item (§I).
- **Full-stack (Caddy-included) boot on a shared GitHub-hosted CI runner**: `openmrs-backend` and
  `openelis-webapp` did not reach Docker `healthy` within a 3600s wait window in the one attempt made
  (DH-CLINIC-004B, run `35210377720`). Consistent with this repo's own documented ~50-minute-plus
  cold boot being a manual/fresh-VM procedure, not a shared-runner one — see `.github/workflows/ci.yml`'s
  header. Not a code defect found in this attempt; next step is a runner with more capacity
  (self-hosted or a larger GitHub runner), not a different test design.
- **Cross-provider or national patient lookup/matching by demographic**: does not exist and is
  explicitly out of Launch-1 scope (ADR-051 Decision 11) — this is a boundary, not a gap. **2026-09-17
  dated update (DH-IDENTITY-001):** provider-scoped native patient *registration* by name (display
  name, optional exact DOB, optional contact) and provider-scoped substring search now exist
  (§C) — the narrower claim this bullet originally made ("what exists is only exact-identity
  binding, DH-EX-009") is superseded for the provider-scoped case; DH-EX-009's exact-identity bind
  endpoint itself is unchanged and still exists for connector/integration use.

## F. NOT BUILT

- **DHealth Lab LIS/Connect integration.** DH-LAB-001 through DH-LAB-004 built the full native
  loop (authentication/queue/accept/reject, collection-milestone recording and start-processing,
  the controlled result-structure template layer, controlled entry, required-gated completion, a
  least-privilege release queue, and fingerprint-gated release); §D's DH-LAB-005 entry closed the
  remaining correction/amendment gap DH-LAB-004 deliberately left closed rather than building
  unsafely. Demo seed data now exists (§D, DH-DEMO-001), but LIS/Connect integration still does
  not.
- **DHealth Patient** (assisted-digital patient layer) and **DHealth Connect** (EMR/LIS integration
  layer, beyond the OpenMRS/OpenELIS reference track itself): no code of their own exists yet.
- **Any patient-facing marketplace or pricing surface.** Not built, not scheduled for Launch 1 —
  preserved as a product-law boundary (§B), not merely an unstarted feature.
- **Any decision-support, alerting, or clinical-interpretation logic anywhere in the repository.**
  Excluded by decision, not by omission — the partograph in particular stays data-entry only, no
  alert lines, no action lines, no prolonged-labour flagging (pinned P0).

## G. Launch-1 scope

Per `docs/MVP-1.0.md` §9's explicit out-of-scope list: no full inpatient HIS, no complete
longitudinal national EHR, no pharmacy/radiology/PACS, no insurance claims or patient payments, no
consumer price marketplace, no AI diagnosis/interpretation/decision support, no nationwide rollout.
Launch 1 is the Exchange backend plus whichever native frontends are actually wired end-to-end and
deployed — today that is **none**: the Exchange backend is unqualified against a live identity
provider, and the one native frontend that exists (DHealth Clinic), while now config-complete for
authentication (§C), has never been runtime/browser-qualified (§D).

## H. Pilot gates

Gates that must all clear before any real-patient pilot on the Exchange track, none of which have
cleared yet:

1. Real Keycloak qualification of token validation/authorization (currently: not run, §E). Realm/
   client reconciliation is now real-Keycloak-qualified (§C) — narrower than this gate.
2. DHealth Clinic native browser authentication + same-origin Exchange routing — config-qualified
   as of DH-CLINIC-004A, reconciliation real-Keycloak-qualified as of DH-CLINIC-004B (§C), but a real
   browser round trip is still not observed (§D/§E — DH-CLINIC-004B, blocked on full-stack boot
   capacity, §I).
3. Manual live/browser demonstration of the golden path (currently: not run).
4. A performed backup **restore drill**, not merely a script that exists (pinned P0; see
   `PROJECT_STATE.md` for current backup status).
5. WhatsApp/SMS result delivery running against real credentials, not a mock (pinned P0, §E).
6. Clinical lead sign-off on the order set and partograph fields (`docs/CLINICAL-SIGNOFF.md`) —
   unrelated to the Exchange track's own gates above, but binding wherever the maternity track is
   touched. Pilot-ready is never clinically live without it.

No real-patient pilot exists on either track as of this status.

## I. Next ticket

**DH-CLINIC-004B remains open — incomplete, not closed.** Realm/client reconciliation is now
real-Keycloak-runtime-qualified (§C), and a genuine deployment-breaking defect (VARCHAR(255) realm
import crash) was found and fixed along the way. The browser E2E suite (12 scenarios + 4 negative
controls) is written, typechecked, and reviewed, but has never executed against a live browser: the
one qualification run attempted stalled because `openmrs-backend`/`openelis-webapp` never reached
healthy within CI's wait window, blocking Caddy. Needed to close this ticket: a CI runner with more
capacity (self-hosted or a larger GitHub-hosted runner) — not a different test design — then a
successful Case A run through the full Playwright suite, then Case B (upgrade path), then the
idempotent-repeat check. See `PROJECT_STATE.md`'s DH-CLINIC-004B entry for full evidence.

**DH-IDENTITY-001 — native patient identity/directory contract — is implemented** (§C), and did not
touch, did not runtime-qualify, and does not change DH-CLINIC-004B's own open item above in any way.
See `DECISIONS.md` ADR-051 and `PROJECT_STATE.md`'s DH-IDENTITY-001 entry for full evidence.

**DH-CLINIC-005 — wrong-patient-safe human identity UX — is now implemented** (§C), consuming that
directory to eliminate UUID fragments as the primary way humans identify patients in Orders/Results.
Same evidence-level boundary as above: identity-UX work does not touch, does not runtime-qualify,
and does not change DH-CLINIC-004B's own open item. See `DECISIONS.md` ADR-051's 2026-09-17
clarification and `PROJECT_STATE.md`'s DH-CLINIC-005 entry for full evidence.

**DH-CLINIC-006 — provider-assigned laboratory workflow — is now implemented** (§C): explicit,
platform-admin-configured provider-lab routing, closing the "which labs is a provider actually
configured to work with" gap behind DH-EX-010's own `assign_by_provider`. Same evidence-level
boundary as above: routing work does not touch, does not runtime-qualify, and does not change
DH-CLINIC-004B's own open item; it also does not claim ADR-043 Law 2 ("no double entry") is solved
— that remains DH-PILOT-TOPOLOGY-001's own future decision. See `DECISIONS.md` ADR-052 and
`PROJECT_STATE.md`'s DH-CLINIC-006 entry for full evidence.

**DH-CLINIC-007 — released-result review + follow-up UX — is now implemented** (§C): a
provider-scoped review queue (one entry per outstanding `DoctorReview` obligation, never one per
order — an order with two independently-pending amendment versions surfaces as two entries),
exact-version content loaded and validated before any review disposition, version-specific review
mutations preferred over the legacy order-only route whenever a version exists, and a truthful
follow-up-scope disclosure (`FOLLOW_UP_REQUIRED` records only that a clinician judged follow-up
necessary — no scheduling, contact, or task engine). No clinical interpretation, urgency ranking, or
new review status was added; the three existing `DoctorReview` statuses remain authoritative. No
migration was needed — the version-specific review contract already existed from DH-EX-014A/014B;
the only new backend surface is the read-only queue endpoint. Real-PostgreSQL-qualified for the one
concurrency scenario not already covered by DH-EX-014A/014B's own PG suites (identical-disposition
race provenance, PG-R2) plus reconfirmation of PG-R1/PG-R3 on the current schema. Same evidence-level
boundary as above: this work does not touch, does not runtime-qualify, and does not change
DH-CLINIC-004B's own open item; it also does not resolve `ResultDelivery`'s own separate, unrelated
state machine, which remains untouched. See `DECISIONS.md` ADR-048's 2026-09-18 clarification and
`PROJECT_STATE.md`'s DH-CLINIC-007 entry for full evidence.

**DH-LAB-001 — native DHealth Lab shell, assigned-order queue, accept/reject — is now implemented**
(§C/§D): the first frontend DHealth Lab has ever had (`ui/src/features/lab/`), built against the
existing Exchange lab-workflow backend rather than any new backend. Same evidence-level boundary as
above: this work does not touch, does not runtime-qualify, and does not change DH-CLINIC-004B's own
open item. **Corrects a documentation drift, not a product decision:** this ticket's own text
caught forward-looking sections of this file and `PROJECT_STATE.md` framing native Lab and DHealth
Connect as a fork to choose between, which ADR-043 never actually intended — see `DECISIONS.md`
ADR-043's 2026-09-18 dated clarification. Result entry, result release, LIS/Connect integration,
and demo seed data remain not built (§F). See `PROJECT_STATE.md`'s DH-LAB-001 entry for full
evidence.

**DH-LAB-002 — collection + processing workflow for native DHealth Lab — is now implemented** (§C/
§D): the second Native Lab increment, built entirely against the existing DH-EX-011 backend
(`record_collection`/`start_processing` on `LabWorkflowRepository`, both already CI/real-PostgreSQL
qualified before this ticket — no new backend workflow endpoint, table, or `LabOrderStatus` value
was added). What this ticket actually added: `ExchangeClient.recordLabOrderCollection`/
`startProcessingLabOrder`, the `LabCollectionResponse` type, the panel UI (explicit confirmation for
both actions, truthful null-collection wording, no specimen/barcode/free-text/manual-timestamp
field), each action's own distinct lost-response recovery (collection: 3-way; processing: 4-way,
including a "prerequisite not met" outcome collection/accept-reject never has), the matching
race/stale-response/lab-switch/unmount test suites (mirroring, not sharing, LAB-001's own), the
`LabOrder.items` queue N+1 fix (§C) with its own query-count proof, `LabHomePage`'s hardening to the
same tenant/generation discipline with a dedicated stale-count test, and two backend role-negative
tests (`LAB_RESULT_RELEASER`-alone denied on both actions) that DH-EX-011 could not have written
because that role did not exist until DH-EX-012. **Docker was reachable this session (via `sudo
docker` in WSL2) but pulling `postgres:16-alpine` was blocked by this sandbox's network egress to
Docker Hub's CDN** (two attempts, both timing out mid-transfer on different blob layers), so the
concurrency/race backend guarantees this ticket depends on were not re-run against real PostgreSQL
here — they are cited as DH-EX-011's own prior, still-valid evidence (the backend transition/lock
logic is unchanged; only a read-only `selectinload` was added to the queue query).
Same evidence-level boundary as every other DHealth Lab entry above: not real-Keycloak-qualified,
not real-browser-demonstrated. Result entry, release, LIS/Connect integration, and demo seed data
remain not built (§F). See `PROJECT_STATE.md`'s DH-LAB-002 entry for full evidence.

**DH-LAB-003 — controlled, LabOffering-scoped result templates + read-only entry plan — is now
implemented** (§C/§D, ADR-053): the third Native Lab increment, and the first to add new schema
since DH-LAB-001 (migration `0024_result_templates`, purely additive — new
`exchange_lab_result_templates`/`exchange_lab_result_template_observations` tables plus the usual
security-audit CHECK-constraint widening). Templates are scoped to `LabOffering`, not
`TestCatalogEntry` — two labs offering the same catalog test may report a different structure
without that being a second ordered-service identity (ADR-053 Decision 1). Immutable per version,
at most one active per offering via a `FOR UPDATE`-locked compare-and-swap mirroring
`CatalogRepository.set_price`'s own discover-and-swap discipline, org-admin-authorized like
offerings/prices, no reference-range or clinical-interpretation field anywhere in the model. The
existing low-level `LabObservation`/`record_observation` API is completely untouched — templates
are additive Native Lab configuration, never a new requirement Connect's own source-flexible
observations must satisfy (ADR-053 Decision 8). A new batched, read-only
`GET .../lab-orders/{order_id}/result-entry-plan` resolves each `IN_PROGRESS` item's active
template for the frontend's own read-only preview; a currently-assigned order's item with no
`LabOffering` row at all is treated as a structural-integrity failure, never silently rendered
(`assign_by_provider`/`select` both require one at assignment time, and offering rows are never
deleted). **The migration itself applied cleanly against a real, disposable PostgreSQL instance in
push-triggered CI** (`alembic upgrade head` succeeded 0022→0023→0024) — genuine real-Postgres
evidence for the schema. The two new PG-A1/PG-A2 concurrent-activation tests were **not executed
this session**, for the identical Docker-reachable-but-CDN-blocked reason DH-LAB-002 already
disclosed. No result-entry, no result value, no LIS/Connect code anywhere in this ticket. Same
evidence-level boundary as every other DHealth Lab entry above: not real-Keycloak-qualified, not
real-browser-demonstrated. Result entry and release remain not built (§F). See
`PROJECT_STATE.md`'s DH-LAB-003 entry for full evidence.

**DH-LAB-004 — Native Result Entry + Completion + Release — is now implemented** (§C/§D): the
fourth Native Lab increment, closing the native loop end to end with first-write exact
template-version pinning, a Native controlled batch value endpoint, required-definition-gated
completion, a least-privilege release queue, and a release-inspection fingerprint. Same
evidence-level boundary as above: does not touch, does not runtime-qualify, and does not change
DH-CLINIC-004B's own open item. See `PROJECT_STATE.md`'s DH-LAB-004 entry for full evidence.

**DH-LAB-005 — Correction + Amendment UX — is now implemented** (§C/§D): the fifth Native Lab
increment, closing the two gaps DH-LAB-004 deliberately left closed rather than build unsafely — a
Native controlled correction endpoint (pre-release and inside an amendment, browser submits only
the new value) and a complete post-release amendment lifecycle (discovery, controlled correction,
fingerprint-gated release, a least-privilege amendment release queue, lab-facing exact
per-version content). This ticket is explicitly **not** an "Edit Result" feature — every
historical observation, release version, and review remains independently reconstructable; no
value is ever mutated in place, no release version is ever collapsed, and no value set is ever
released without the releaser having actually inspected the exact fingerprint of what they are
releasing. No new migration, no new `LabOrderStatus`/amendment status. Same evidence-level
boundary as above: does not touch, does not runtime-qualify, and does not change DH-CLINIC-004B's
own open item; DH-CLINIC-004B remains separately open, unchanged. See `DECISIONS.md` ADR-045's and
ADR-048's 2026-09-18 addenda and `PROJECT_STATE.md`'s DH-LAB-005 entry for full evidence.

**DH-DEMO-001 — Resettable Visible Native DHealth Demo Environment — is now implemented, but
NOT yet runtime-proven** (§C/§D): a dedicated Compose project running the real Keycloak/adapter/
DHealth-UI/Caddy code paths without ever waiting on OpenMRS/OpenELIS (untouched, preserved), a
P0 reset-safety boundary independently real-PostgreSQL-qualified (three facts: database name,
a process-own env var, a marker table only the demo's own db-init script creates), and
deterministic synthetic-only baseline data — real Keycloak-backed authentication throughout, no
bypass anywhere (grepped). **What remains open, stated plainly:** this sandbox's Docker cannot
pull real image blobs from the CDN, so the demo has never actually booted anywhere. The CI job
this ticket's own push triggers (`native-demo-boot`) is its first real attempt at the one thing
this ticket exists to prove, not a rerun of something already observed. No real Keycloak login
and no real browser walkthrough of the golden journey exist yet. This does not touch, does not
runtime-qualify, and does not change DH-CLINIC-004B's own open item. See `PROJECT_STATE.md`'s
DH-DEMO-001 entry for the full evidence matrix.

**Forward context only, not started:** DH-DEMO-001's own forward sequence begins with native
runtime/browser golden-journey qualification (the immediate next step — see above), then
DH-SEC-001, DH-OPS-001, DH-RECOVERY-001, DH-DEMO-E2E-001, DH-CONNECT-CONTRACT-001, and finally
DH-PILOT-TOPOLOGY-001 — last, not first, since it answers only "which deployment model fits a
specific real facility" once one exists, never "which product path DHealth permanently supports"
(ADR-043's 2026-09-18 clarification). Full order: `PROJECT_STATE.md`'s DH-LAB-003 entry.

## J. Documentation precedence

This file is the **summary of record for current status** — read it first. It does not replace or
duplicate the documents below, and it is not itself an ADR:

- **`DECISIONS.md`** — the record of accepted/rejected decisions (ADRs). Append-only; corrections
  are dated inline overlays, never rewrites of history. Authoritative for *why* a decision was made.
- **`docs/MVP-1.0.md`** — the full current-vs-MVP gap table (§8) and product-family scope (§3).
  Authoritative for granular per-capability evidence level.
- **`PROJECT_STATE.md`** — the detailed, ticket-by-ticket implementation and evidence log.
  Authoritative for exactly what changed, when, and under what evidence, per ticket.
- **`CURRENT_STATE.md`** — the historical maternity/reference-connector-track inventory
  ("Assessed 2026-08-17"), plus a running Exchange-era summary note. Authoritative for the
  connector-track's own detailed history.
- **`docs/ARCHITECTURE.md`** — authoritative for *how* the system is built, not for current status.
- **`AGENTS.md`** — governs how work in this repository is done; read before any ticket.

If any of the above appears to contradict this file, treat it as a signal this file is stale and
needs its own dated correction — do not silently trust either side without checking the current
code.
