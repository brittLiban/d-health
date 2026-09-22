# STATUS — Daher Health

**This is the canonical, present-tense status document for this repository.** It is short by
design and does not replace the detailed historical records it summarizes. Where this file and any
other document disagree, see §J (Documentation precedence) before assuming either is wrong.

## A. As-of

**2026-09-22, DH-MVP-ONBOARD-001 closing change, directly on `master`. The ticket is CLOSED.** The
last onboarding hole: `first_administrator` is optional at enrollment — correctly, since a partner
is often signed up before the intended administrator has ever logged in — so a facility could exist
that **nobody could administer, with no screen to fix it**. Attaching somebody afterwards meant the
API and that person's identity UUID. Every facility detail now carries an **Équipe et accès**
section listing who belongs to it, what each may do, and whether that access is live, with
*Ajouter une personne* → search → select → roles → membership. **Frontend only**: it calls the
`GET`/`POST /organizations/{id}/memberships` pair that already existed, which a platform
administrator reaches through `require_membership`'s `via_platform_admin` bypass — no new endpoint,
no new model, no migration. The search/select/roles panel was **extracted** from the enrollment
wizard rather than copied, so both routes to a membership stay one implementation. A successful
attach re-reads readiness, so the `administrator_present` blocker clears because the server says so.
**Nothing on the screen removes access**: `POST .../memberships` is ensure/add and this API revokes
nothing, so held roles render ticked and disabled, inactive memberships are listed but not editable,
and there is no control whose label implies a removal the server would refuse. Revocation,
invitations and Keycloak provisioning remain out of scope and unbuilt.

**2026-09-21, DH-MVP-ONBOARD-001 correction, directly on `master`.** Two ticket-owned gaps closed,
from `1621ca6`. **The wizard's first-administrator step now performs the action it offered**: the
selected person's membership and role assignments are created in the *same transaction* as the
organization and its enrollment, so there is no state where a facility exists and nobody can
administer it. An illegal role for the facility type, or a person who has never signed in, creates
nothing at all. Role legality stays in `OrganizationRoleAssignmentRepository` — not restated in the
endpoint and not in React. **And a Native laboratory may now only be sent work it can finish**:
DHealth's human result entry runs against a controlled active template, so an active offering
without one is refused *per service* — not eligible, and direct assignment refused by test name —
while the lab's other services route normally. Zero template coverage blocks the facility outright,
because a lab that can complete nothing it advertises is not a destination. CONNECT is untouched by
that rule, by construction rather than by convention.

**2026-09-21, DH-MVP-ONBOARD-001, directly on `master`** (this ticket's own explicit instruction: no
branch, no PR). Starting point was `master` at `85f2cba` (DH-MVP-POLISH-001's final commit). This
ticket builds the **facility enrollment control plane**: a platform operator can enrol a clinic or
laboratory, attach its first administrator, record whether it will use DHealth directly or keep its
existing software, capture what that software is, and read a server-derived answer to "can clinics
send orders here, and if not exactly why". **Migration `0026_facility_enrollment`** adds three tables
and backfills every existing organization as `NATIVE`/`ORGANIZATION_READY` — a statement of fact,
since Connect does not exist and every organization operating today *is* Native.

The distinction the ticket exists to protect: **enrolled ≠ configured ≠ operationally ready ≠
production-routable**. Production routing to a Native facility now requires real operational
authority — a provider needs an active clinician, a laboratory needs both an active technician and
an active release authority, because entry and release are separate authorities (ADR-044) and a lab
without a releaser can accept work it can never finish. One person may hold both lab roles.
Losing that staffing blocks new routing **without** rewriting the configured `ProviderLabRoute`;
restoring it makes the same route usable again. A **CONNECT facility cannot reach production
readiness at all**, however complete its discovery, because no connector exists — enforced in the
repository, not at one endpoint.

**Evidence level: local qualification only, because GitHub Actions executes nothing.** Every job of
every run ends in 2–3 seconds with zero steps and no logs; the previously-green run `35527353831` on
SHA `5983da9` was re-run as a control and **failed identically**. GitHub finally named the cause
itself, in an annotation on run `35621071589`: *"The job was not started because recent account
payments have failed or your spending limit needs to be increased."* An account-billing condition,
not a workflow or repository defect, and nothing in this repository can clear it. **CI conclusion
for this ticket is unavailable: repository code was never evaluated, so it is neither failed nor
green.**

Measured locally: adapter **1168 passed, 118 skipped** (from 1122), ruff check and format clean
across 156 files; **811 UI tests across all 54 test files**, run in seven batches because a
whole-suite run loses a nondeterministic subset of files to worker-start failures; `tsc` and
`tsc -p e2e/tsconfig.json` clean; oxlint 0 errors / 45 warnings; build clean with no secrets across
28 files; migration re-qualified against **real PostgreSQL 18** (upgrade, backfill with zero orphans,
CHECK constraints refusing bad values, duplicate enrollment refused, downgrade and re-upgrade,
`current == heads == 0026_facility_enrollment`); and seven platform scenes captured in **headless
Chromium at 1280, 768 and 390** with no console error, failed request, overflow, off-screen control
or unlabelled input. **Five negative controls executed and reverted** (activation gate, assignment
gate, eligibility filter, refusal-telemetry failure, and the frontend's offer guard).
**Eleven defects found and fixed**, including one class worth naming: tests of mine that passed for
the wrong reason because a fixture used a different token issuer than the API resolved.

Production routing is also **switched on from the interface**, on a care facility's detail screen: a
two-gesture confirmation that restates what activation permits, no activation control at all for a
Connect laboratory or an unready one, deactivation never gated, and a server refusal rendered reason
by reason with no success reported. A laboratory that loses its release authority reads *Relation
configurée · Routage indisponible* — both halves, because the relationship survives and the routing
does not.

**Not proven:** no DHealth Connect transport exists and none was built; no real browser has been used
by a person; no E2E ran; no CI conclusion; and the first-administrator bootstrap still requires the
person to sign in once (no invitation system, by scope). *Corrected 2026-09-21: the wizard now
creates that membership and its roles atomically — see the correction entry above.*

**2026-09-20, DH-MVP-POLISH-001, directly on `master`** (this ticket's own explicit instruction: no
branch, no PR). Starting point was `master` at `50a7eac` (DH-PATIENT-001's own final commit), verified
against `origin/master` by a fresh `git fetch` rather than trusted from the prompt. This ticket made
the native surfaces **one authenticated staff application** (`ui/src/features/staff/`): the
Clinic/Lab application switch is gone, `/me` is loaded once for the whole application instead of once
per product, and which workflows a person sees derives from their active organization and roles in it.
It also introduced the central design-token layer, self-hosted typefaces (no font CDN at runtime), a
workflow relay derived from real order state, a two-gesture review confirmation, an honest
unsaved-value indicator in result entry, and the patient surface restyled to the same design language
at its own density. **No backend route, no schema change, no migration** — Alembic head stays
`0025_native_result_binding`. The adapter was not edited at all; its suites were run as a regression
check and match the previous baseline exactly (**1122 passed, 118 skipped**). Evidence level: **unit
tests, static analysis, and the CI-equivalent checks runnable locally** — the remote CI run this
ticket's single push triggers is reported in the session handoff, not claimed here, since these words
were written before that push existed. **[Updated after the push: CI run `35526256367` on SHA
`5057b6d` completed with all ten jobs green, `conclusion=success`, queried per job rather than read
from a watch command's exit code. A second push follows it carrying one review fix — the masthead
role labels — so that run's own result is not yet known at the time of writing either.]** **794
frontend tests across all 52 test files**, measured by
reconciling a whole-suite run (which reported only 24 files and left 27 workers unstarted, a known
flake of this 9p-mounted checkout) against the 27 remaining files run by name in three batches —
24 + 27 = 51 on disk, 421 + 366 = 787, plus 7 tests in one new file added after review. **13 negative controls actually executed** (each claimed
property deliberately broken, the specific test observed going red for the specific reason, then
reverted); one of them did not fire on first attempt and was rewritten until it did. **Fourteen bugs were
found and fixed during this ticket** — including a pre-existing one (21 CSS custom properties
referenced and defined nowhere, so "muted" labels had been rendering at full strength), a
working-looking `Se reconnecter` button that could not have reconnected anything, an axe suite that
had been auditing the wrong surface, and this ticket's own review tests passing green over a submit
path that threw before it ran. See `PROJECT_STATE.md`'s DH-MVP-POLISH-001 entry for all fourteen. The twelfth came from review, not from me: the masthead was rendering the authorization enum (`LAB_TECH · LAB_RESULT_RELEASER`) in the one band that is always on screen.
**Not proven:** no real browser rendered any of this, at any width, on any device; no font was
observed loading; no focus ring or contrast ratio was measured; no E2E ran (no live stack, and
`typecheck:e2e` — which does pass locally — is not part of CI); and the organization `<select>` on
which this ticket's whole premise rests has never been used by a person who belongs to two
facilities. jsdom cannot measure layout or resolve a cascade of custom properties, which is exactly
why the token guards are source scans.

**2026-09-20, DH-PATIENT-001, directly on `master`** (this ticket's own explicit instruction: no
branch, no PR). Starting point was `master` at `a5992bd` (DH-DEMO-001's own final commit), verified
against `origin/master` by a fresh `git fetch` rather than trusted from the prompt. This ticket
added the **first DHealth Patient frontend** (`ui/src/features/patient/`) plus a clinician-side
patient handoff (QR + printable sheet + revoke/rotate), a provider-authorized patient-access
metadata read, and a server-owned patient follow-up projection. **No migration** — Alembic head
stays `0025_native_result_binding`, confirmed against CI's own `EXPECTED_HEAD`. Evidence level:
**unit tests plus the CI-equivalent checks runnable locally** — the remote CI run this ticket's
single push triggers is reported in the session handoff, not claimed here, since these words were
written before that push existed. 18 new adapter tests (1104 → **1122 passed**, 118 skipped) and 92
new frontend tests (**684 passed across all 46 files**, measured in eight small batches — a single
whole-suite `vitest` run is not a reliable count on this machine, and the `PROJECT_STATE.md` entry
explains why, along with the pre-existing CI weakness that measurement exposed), plus **18 negative
controls actually executed** (each claimed property
deliberately broken, the specific test observed going red for the specific reason, then reverted).
**Three real bugs were found and fixed during this ticket** — see §C. **Not proven:** no real
browser rendered this patient screen, no real phone scanned one of these QR codes, no printer
produced one of these sheets, and no real-PostgreSQL qualification was needed or run (this ticket
adds no new concurrency surface — the one new backend read is a single-row, lock-free,
non-mutating GET). Responsive behaviour was written mobile-first and reasoned about at 320/375/430px
but **not measured in a real browser** — jsdom cannot measure layout.

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
- **Patient access metadata read and patient result-status projection (DH-PATIENT-001)**: a
  provider-authorized `GET .../orders/{order_id}/patient-access` behind the identical
  `CLINICIAN`-for-the-owning-provider boundary as issuance/revocation, returning `{active,
  expires_at, access_mode}` and never the raw token, its SHA-256 digest, or the grant UUID; an
  expired grant reads as inactive with a `null` expiry, and reading it never mutates `revoked_at`.
  Plus `PatientOrderResponse.result_status`, resolved from the order's *latest released version* and
  that version's own `DoctorReview`, so a version-2 amendment release correctly reverts the
  patient's view to `AWAITING_CLINICIAN_REVIEW` instead of presenting a disposition recorded about
  superseded content. A grandfathered `COMPLETED`-without-release order uses its one honest unbound
  review row; no release version is ever fabricated for it. **Unit-qualified** (18 adapter tests,
  incl. the mandated amendment regression and a no-result-value assertion against the serialized
  JSON string rather than only a field list) with **7 backend negative controls executed**; the
  remote CI run for this work is reported in the DH-PATIENT-001 session handoff, not asserted here. No
  migration; Alembic head unchanged at `0025_native_result_binding`. One real bug found and fixed:
  `GrantRecord` returned naive datetimes on SQLite read-back, which would have serialized an
  offset-less `expires_at` a browser parses as local time. See `PROJECT_STATE.md`'s DH-PATIENT-001
  entry.

## D. BUILT BUT NOT RUNTIME-PROVEN

- **DHealth Clinic native frontend** (`ui/src/features/clinic/`) **[no longer a separate
  application — updated 2026-09-20, DH-MVP-POLISH-001: these pages are now sections of the one staff
  application (bullet below, ADR-054). `ClinicApp`/`ClinicShell` are deleted and `ClinicProvider` no
  longer fetches `/me`; everything this bullet claims about behaviour still holds, reached through
  `Patients`/`Ordres`/`Résultats` rather than through a product the user opens.]**: patient registration/search
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
- **DHealth Lab native frontend** (`ui/src/features/lab/`) **[no longer a separate application —
  updated 2026-09-20, DH-MVP-POLISH-001: same as Clinic above. `LabApp`/`LabShell` are deleted and the
  `canWorkOrders`/`canReleaseResults` capability model described below is now derived once, from the
  active membership, by `WorkspaceProvider` — the release/entry authority separation it encodes is
  unchanged and still covered by a negative control.]** (DH-LAB-001, extended by DH-LAB-002,
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
- **DHealth Patient native frontend** (`ui/src/features/patient/`, DH-PATIENT-001) — the first
  patient-facing surface in the product, and the only one with no authenticated user: a
  single-screen, mobile-first, French-first status view authorized solely by DH-EX-005's
  pre-existing order-scoped opaque grant, reached through a QR code or link whose credential lives
  in the URL **fragment** and is scrubbed from the address bar on ingestion. It renders order
  status, the assigned laboratory's public contact detail, the requested tests by human name, and
  one server-owned follow-up projection — **never** a result value, reference range,
  interpretation, price, internal identifier, or clinician identity. Mounted before any
  `ExchangeAuthProvider` exists, so a patient is structurally incapable of being redirected to
  Keycloak. Paired with a Clinic-side `PatientAccessHandoff` that issues/revokes/rotates access and
  produces a locally-generated QR plus a printable sheet for patients with no smartphone. Built and
  component/UI-tested only — **same "built but not runtime-proven" boundary as DHealth Clinic and
  DHealth Lab above**: no real browser has rendered this screen, no real phone has scanned one of
  these QR codes, and no printer has produced one of these sheets. Responsive layout was written
  mobile-first and reasoned about at 320/375/430px but **not measured** — jsdom cannot measure
  layout. No SMS/WhatsApp/email/voice transport was added; `ResultDelivery` is untouched and
  viewing a status page deliberately does **not** mark it `DELIVERED`. See `docs/ARCHITECTURE.md`
  §19 and `PROJECT_STATE.md`'s DH-PATIENT-001 entry.
- **Facility enrollment and integration readiness** (`adapter/.../facility_enrollment.py`,
  `ui/src/features/platform/`, DH-MVP-ONBOARD-001) — the control plane for joining the network.
  A platform operator enrols an organization and its enrollment in one transaction, attaches a first
  administrator found through a bounded platform-only identity lookup — or attaches people later
  from the facility's **Équipe et accès** section, which grants access and never removes it —
  records `NATIVE` or `CONNECT` explicitly, captures a Connect facility's software/vendor/version/hosting and the interfaces it
  reports, and reads a server-derived answer to whether production orders may be routed there.
  **Four facts stay separate**: enrolled, configured, operationally ready, production-routable.
  Native routing requires an active CLINICIAN (provider) or an active LAB_TECH *and*
  LAB_RESULT_RELEASER (laboratory), one person may hold both lab roles, and losing that staffing
  blocks new routing without rewriting the configured route. A **CONNECT facility cannot reach
  production readiness at all** while no connector exists — enforced in the repository, and
  unaffected by staffing, offerings, templates or a verified interface. Platform scope is distinct
  from organization scope and **no organization is fabricated** for a platform administrator with no
  membership. Built and component/UI-tested plus real-PostgreSQL migration qualification — **no CI
  conclusion exists for it** (see §A), no E2E has run, and no person has used it in a browser. See
  `DECISIONS.md` ADR-055, `docs/ARCHITECTURE.md` §21, `docs/FACILITY-ONBOARDING.md`, and
  `PROJECT_STATE.md`'s DH-MVP-ONBOARD-001 entry.
- **One authenticated DHealth staff application** (`ui/src/features/staff/`, DH-MVP-POLISH-001) —
  supersedes the separate `ClinicApp`/`LabApp` shells and the application-switch buttons that reached
  them. `StaffApp` loads `/me` **once** for the whole application; the active organization plus the
  user's roles in it decide which of `Aujourd'hui / Patients / Ordres / Résultats` is available and
  what each contains, so a person with memberships in a clinic and a laboratory switches
  *establishment*, not product. `ClinicProvider`/`LabProvider` survive as projections of that one
  session and fetch nothing, which is why the existing Clinic and Lab page tests remained meaningful
  rather than being rewritten to match new code. Frontend capability derivation is **usability, not
  security** — each of the five capabilities is documented against the backend gate that actually
  enforces it, and the server refuses regardless of what the browser rendered. An organization switch
  discards the org-scoped subtree by `key`, so a late response from one organization cannot render
  under another. There is deliberately **no `Réglages` item**: see §I. Built and component/UI-tested
  only — **same "built but not runtime-proven" boundary as every native surface above**: no real
  browser has rendered this shell, no real person has used the establishment selector, no E2E has run,
  and no font has been observed loading. See `DECISIONS.md` ADR-054, `docs/ARCHITECTURE.md` §20,
  `docs/UI-002-DESIGN-LANGUAGE.md`, and `PROJECT_STATE.md`'s DH-MVP-POLISH-001 entry.
- **Native demo environment** (DH-DEMO-001, `docker-compose.native-demo.yml`,
  `scripts/demo/*.sh`, `adapter/src/daher_adapter/exchange/demo_seed.py`): a dedicated Compose
  project running the real Keycloak/adapter/DHealth-UI/Caddy code paths, deliberately never
  waiting on OpenMRS/OpenELIS (which stays completely untouched), with a P0 reset-safety boundary
  (three independent facts, real-PostgreSQL-qualified — §C) and deterministic synthetic-only
  baseline data. Real Keycloak-backed authentication throughout — grepped, zero demo-conditional
  branch exists in any authentication/request-handling code. **[Corrected 2026-09-20,
  DH-PATIENT-001 — this bullet previously said "the demo has never actually booted anywhere" and
  called the CI job "its first attempt, not a confirmation"; that attempt has since succeeded.]**
  **Real native Compose boot: qualified in CI** (run `35390037354` at `a5992bd`, job `native demo —
  real boot (DH-DEMO-001)` green with all 10 jobs — verified against the GitHub API, not carried
  forward from a report). **Real Keycloak interactive login: not yet qualified. Real browser golden
  journey: not yet qualified.** This sandbox's Docker still cannot pull real image blobs from the
  CDN, so that boot is CI's evidence, not a local observation. See `PROJECT_STATE.md`'s
  DH-DEMO-001 entry for the full evidence matrix.
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
- **DHealth Connect** (EMR/LIS integration layer, beyond the OpenMRS/OpenELIS reference track
  itself): no code of its own exists yet. **[Updated 2026-09-20, DH-PATIENT-001 — this bullet
  previously also named "DHealth Patient (assisted-digital patient layer)" as having no code of its
  own; a first DHealth Patient surface now exists, see §D.]** What remains not built on the patient
  side specifically: **push notification of any kind** (WhatsApp, SMS, email, voice — DH-PATIENT-001
  deliberately built a safe access path first and wired no transport to it), detailed result
  delivery to patients (excluded by policy, not pending — patients see status only), patient
  accounts, family/proxy authorization, and offline/PWA capability.
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

**NEXT: DH-CONNECT-CONTRACT-001 — External EMR/LIS Integration Contract + Qualification Model.**
DH-MVP-ONBOARD-001 made it possible to enrol a facility, record that it keeps its own EMR or LIS,
and capture what that software is and which interfaces it reports. What it deliberately could not
do is connect any of them: no DHealth connector exists for any external system, so a Connect
facility can be fully enrolled, fully discovered, hold a verified interface, and still be unable to
receive one production order. The onboarding UI says exactly that and refuses to say more.

The next ticket defines the one canonical contract every external connector must satisfy before it
may carry production work: connector identity and facility binding, authentication, patient and
practitioner correlation, order delivery and acknowledgement, collection and processing events,
result observations, source timestamps kept distinct from DHealth received and recorded times,
professional release, rectificatifs, external report identifiers, idempotency, duplicate
suppression, retry, outage recovery, dead-letter behaviour, connector health, credential rotation,
mapping versioning, suspension, and qualification. Only once that contract is accepted should
`DH-CONNECT-AGENT-001` and specific adapters be built. The readiness gate this ticket installed is
what will keep an unqualified connector out of production in the meantime.

**[Superseded 2026-09-21, DH-MVP-ONBOARD-001 — the paragraph below proposed a facility-setup ticket
and is preserved as the brief it was written as; that work is now done, under a different name.]**

~~**NEXT: DH-MVP-ADMIN-001 — Facility Setup + Administration.**~~ DH-MVP-POLISH-001 made the native
surfaces one authenticated staff application whose visible workflow derives from the active
organization and the user's roles in it (ADR-054, `docs/ARCHITECTURE.md` §20). That exposes the next
gap plainly: **nothing in the product can create or administer any of the facts it now depends on.**
An organization, a membership, a role grant, a provider-lab route, a lab offering, a result template
— every one is created today by a platform administrator through the API or a seeding script, and the
staff application has no `Réglages` navigation item at all, deliberately, because there is no screen
to put behind it. The next ticket should build that: organization profile, member and role
administration within a facility, provider-lab route visibility, and whatever offering/template
administration the laboratory side needs, each gated on the authority the backend already enforces
(`ORG_ADMIN` is a real role in the enum and currently has **no** frontend at all). It should not
re-open the workflow architecture, and it should not invent a role, a permission, or an
administrative concept the backend does not already model — if a screen needs one, that is a backend
decision to raise, not a frontend assumption to make.

**What the MVP still must not be claimed to do**, regardless of the above: no SMS/WhatsApp
notification, no detailed patient result delivery, no national MPI, no full EMR, no full LIS, no
Connect integration, no clinical decision support. And "functionally complete" is not
"runtime-proven": see §A and §D for what has and has not been observed running. DH-MVP-POLISH-001
adds a further honest limit of its own — **no part of the new design has been seen in a real
browser**, so every claim about it is about intent and about behaviour under jsdom.

**[Superseded 2026-09-20, DH-MVP-POLISH-001 — the paragraph that stood here proposed
DH-MVP-POLISH-001 itself and is preserved below as the brief it was written as, since the ticket it
describes is now done and its scope is worth comparing against what actually landed.]**

~~**NEXT: DH-MVP-POLISH-001 — Unified Product Polish + Operational UX.**~~ With DH-PATIENT-001 the
native MVP's functional layer is complete end to end: a clinic identifies/registers a patient,
orders diagnostics, assigns a configured laboratory; the lab receives, accepts, records collection
and processing, enters controlled results, and releases them under separate authorization;
corrections and amendments are versioned immutably; the clinician reviews an exact version and
records a follow-up disposition; and the patient can now reach a simple status/follow-up view
through an order-scoped QR/link or a staff-assisted printed handoff. The next ticket should stop
adding major clinical workflow architecture and instead make DHealth Clinic, DHealth Lab, and
DHealth Patient feel like one coherent product — navigation consistency, visual hierarchy, home
dashboards, empty/loading/error states, responsive layout, French wording consistency, status
presentation, forms, confirmation patterns, branding, accessibility, workflow speed, first-use
clarity, and support/error recovery. Administrative onboarding UX, security hardening,
operations/monitoring, backup/restore qualification, DHealth Connect, and any marketing site are
each separate later evaluations, explicitly **not** part of that ticket. **[Delivered, with two
deliberate deviations recorded in ADR-054: "home dashboards" became one `Aujourd'hui` section rather
than a dashboard per product, and "DHealth Clinic / DHealth Lab / DHealth Patient feel like one
product" was achieved by making Clinic and Lab stop being separate applications at all rather than by
styling two applications alike.]**

**DH-CLINIC-004B remains open — incomplete, not closed.** Realm/client reconciliation is now
real-Keycloak-runtime-qualified (§C), and a genuine deployment-breaking defect (VARCHAR(255) realm
import crash) was found and fixed along the way. The browser E2E suite (12 scenarios + 4 negative
controls) is written, typechecked, and reviewed, but has never executed against a live browser: the
one qualification run attempted stalled because `openmrs-backend`/`openelis-webapp` never reached
healthy within CI's wait window, blocking Caddy. Needed to close this ticket: a CI runner with more
capacity (self-hosted or a larger GitHub-hosted runner) — not a different test design — then a
successful Case A run through the full Playwright suite, then Case B (upgrade path), then the
idempotent-repeat check. See `PROJECT_STATE.md`'s DH-CLINIC-004B entry for full evidence.

**[Added 2026-09-20, DH-MVP-POLISH-001 — part of that suite is now written against controls that no
longer exist, and this is stated rather than quietly left to fail.]** E2E-11 navigated directly to a
per-product native app and clicked between `DHealth Clinique (Natif)` and `DHealth Laboratoire
(Natif)`. Those two buttons are gone: there is one staff application and one `Ouvrir l'application
DHealth` control (ADR-054). The spec was rewritten to `page.goto('/?app=maternity')` followed by that
one click, which still exercises the invariant it was written for — the connector's `check-sso` must
stay silent — by a simpler path. **That rewrite is written, not verified**: `npm run typecheck:e2e`
passes locally (and is not part of CI), and the click target is reachable with no OpenMRS session
because the connector shell has no auth gate, but no Playwright run has executed it. Any other step
in that suite assuming two separately navigable native applications should be re-derived against
ADR-054's URL table before it is trusted. **None of this closes DH-CLINIC-004B**, which still needs
exactly what the paragraph above says it needs.

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

**DH-DEMO-001 — Resettable Visible Native DHealth Demo Environment — is implemented and its native
Compose boot is now CI-qualified** (§C/§D): a dedicated Compose project running the real Keycloak/
adapter/DHealth-UI/Caddy code paths without ever waiting on OpenMRS/OpenELIS (untouched,
preserved), a P0 reset-safety boundary independently real-PostgreSQL-qualified (three facts:
database name, a process-own env var, a marker table only the demo's own db-init script creates),
and deterministic synthetic-only baseline data — real Keycloak-backed authentication throughout, no
bypass anywhere (grepped).

**[Corrected 2026-09-20, DH-PATIENT-001 — the paragraph this replaces said "the demo has never
actually booted anywhere" and described the `native-demo-boot` CI job as "its first real attempt".
That attempt has since run and succeeded, so that text is now stale and is corrected here rather
than left standing. This is a documentation correction only; DH-PATIENT-001 changed nothing about
the demo environment.]** Precisely what is and is not qualified:

- **Real native Compose boot: qualified in CI.** Run `35390037354` at
  `a5992bd37c40f1296955f15692a84560f7c7b3f0`, job `native demo — real boot (DH-DEMO-001)`, green
  along with all 10 jobs in that run — verified directly against the GitHub API during
  DH-PATIENT-001, not carried forward from a report.
- **Real Keycloak interactive login: not yet qualified.** The stack boots with real Keycloak; no
  human has completed an interactive browser login against it.
- **Real browser golden journey: not yet qualified.** No manual or Playwright walkthrough exists.

This sandbox's Docker still cannot pull real image blobs from the CDN, so the boot above is CI's
evidence, not a local observation. This does not touch, does not runtime-qualify, and does not
change DH-CLINIC-004B's own open item. See `PROJECT_STATE.md`'s DH-DEMO-001 entry for the full
evidence matrix.

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
