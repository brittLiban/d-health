# STATUS — Daher Health

**This is the canonical, present-tense status document for this repository.** It is short by
design and does not replace the detailed historical records it summarizes. Where this file and any
other document disagree, see §J (Documentation precedence) before assuming either is wrong.

## A. As-of

**2026-09-17, DH-CLINIC-004A, branch `dh-clinic-004a-browser-auth`.** Starting point was `master`
at `357bd3578ec86024e6ff787bbd39b2327b47245e` (DH-STATE-001, merged). All facts below were
re-verified directly against the current repository and fresh local test runs on this branch — none
are carried forward from an earlier session's report without re-checking.

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
  same model (DH-EX-014B). All CI-qualified and real-PostgreSQL-qualified (SQLite + disposable
  `postgres:16-alpine`), migration head `0021_order_intent_ownership` current == heads == CI's
  `EXPECTED_HEAD`. **Not** real-Keycloak-qualified, not runtime/browser-demonstrated, not
  production-deployed. Full ticket-by-ticket detail: `docs/MVP-1.0.md` §8, `PROJECT_STATE.md`.
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
  gates Clinic's data layer so `/me` cannot fire before real authentication exists. **CONFIG-QUALIFIED,
  not runtime-qualified**: no live Keycloak/Caddy stack was booted, so no real redirect/login/token-
  acceptance round trip has been observed. See `DECISIONS.md` ADR-050 and `PROJECT_STATE.md`'s
  DH-CLINIC-004A entry.

## D. BUILT BUT NOT RUNTIME-PROVEN

- **DHealth Clinic native frontend** (`ui/src/features/clinic/`): patient search against existing
  `PatientReference` bindings, diagnostic order creation against the Exchange catalog, durable
  order-intent recovery across reload/restart, order status, results/release-version history, and
  (as of DH-CLINIC-004A, §C) a complete, config-qualified authentication path. Built and
  component/UI-tested. **Still cannot be called deployed** — every piece needed to authenticate now
  exists and is wired together (public Keycloak client, same-origin routing, in-memory token
  lifecycle, an auth gate that blocks anonymous `/me`), but nobody has booted the real stack: no
  Keycloak realm import of the two-client file has been observed, no browser has actually completed
  a redirect/login/PKCE round trip, and no live Caddy routing has been exercised end to end. That
  gap is DH-CLINIC-004B's explicit job (§I).
- Everything in §C's Exchange-backend list beyond CI/PostgreSQL qualification: no real-Keycloak
  qualification, no manual live/browser demonstration, no production deployment, for any Exchange
  ticket.

## E. BLOCKED / OPEN

- **WhatsApp/SMS/email result delivery**: `ResultDelivery` is created `PENDING` at order creation
  and no code path advances it past `PENDING`. Stays `BLOCKED`, not `DONE`, until a real transport
  runs the flow against real credentials — a mock is never done, per this repo's pinned P0 rule.
- **Real Keycloak qualification**: not run for any Exchange ticket; every authorization boundary is
  proven only against a locally generated RSA keypair (`StaticKeySource`), never a live identity
  provider.
- **Real-browser qualification of native Clinic authentication**: the config-level pieces exist
  (§C), but a real OIDC round trip against a live Keycloak/Caddy stack has never been observed. See
  §D. This is DH-CLINIC-004B's job (§I).
- **Full patient lookup/search/registration by name or demographic**: does not exist at the API or
  frontend level. What exists is narrower — provider-scoped `PatientReference` *binding* to an
  already-known exact source identity (DH-EX-009). This is a real, current gap, not a stale claim —
  re-confirmed by inspection this ticket.

## F. NOT BUILT

- **DHealth Lab** (native laboratory frontend): no code of its own exists. No directory under
  `ui/src/features/` corresponds to it (confirmed: only `admission/`, `auth/`, `clinic/`,
  `maternity/`, `patients/` exist).
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

1. Real Keycloak qualification (currently: not run, §E).
2. DHealth Clinic native browser authentication + same-origin Exchange routing — config-qualified
   as of DH-CLINIC-004A (§C), but real Keycloak/browser E2E qualification is not run (§D — this is
   DH-CLINIC-004B, §I).
3. Manual live/browser demonstration of the golden path (currently: not run).
4. A performed backup **restore drill**, not merely a script that exists (pinned P0; see
   `PROJECT_STATE.md` for current backup status).
5. WhatsApp/SMS result delivery running against real credentials, not a mock (pinned P0, §E).
6. Clinical lead sign-off on the order set and partograph fields (`docs/CLINICAL-SIGNOFF.md`) —
   unrelated to the Exchange track's own gates above, but binding wherever the maternity track is
   touched. Pilot-ready is never clinically live without it.

No real-patient pilot exists on either track as of this status.

## I. Next ticket

**DH-CLINIC-004B — Real Keycloak + browser E2E qualification.**

DH-CLINIC-004A (this entry's own ticket) built and config-qualified native browser authentication;
it did not, and was not scoped to, boot a real stack or observe a real browser round trip. That is
DH-CLINIC-004B's job, not started here.

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
