# DHealth MVP 1.0 — Product Contract

**Status:** Product direction ACCEPTED, architecture ACCEPTED — see `DECISIONS.md`'s ADR-043 (which
extends, and does not retract, ADR-042's 2026-09-08 pivot decision). **Not yet implemented** beyond
what DH-EX-001 through DH-EX-012A already built (§8).
**Date:** 2026-09-12, **re-audited 2026-09-13 (DH-FIELD-001).** Audited against `origin/master` at
`cbdc3dc8b9d2defedebadfb52cc87b6bc4374ec7`. **What changed in the re-audit, disclosed rather than
silently corrected:** the 2026-09-12 snapshot below predated DH-EX-009 through DH-EX-012A and
recorded several since-resolved items as `NOT BUILT`/`OPEN DECISION` — every such correction below
is marked inline as **[re-audited 2026-09-13]** rather than silently rewritten, so a reader comparing
against an earlier printout of this document can still see what changed and when. This document's
own append-only discipline (bottom of file) governs this pass exactly as it has every prior one.

> **What this document is not.** It invents no clinical content. Lab panel contents, ANC/
> partograph fields, danger thresholds, and staff-role assignments remain reserved to the clinical
> lead and Liban (`AGENTS.md`, `docs/CLINICAL-SIGNOFF.md`). Where a claim below would require
> clinical judgment this document does not have, it says so and leaves the answer open rather than
> guessing.

**Evidence vocabulary used throughout this document, precisely:**

| Term | Meaning |
|---|---|
| `CURRENT` | Exists in code today, confirmed by reading it |
| `DECIDED` | A product/architecture decision is made (ADR), even if not yet built |
| `TARGET` | A measurable goal to validate with real users — not a claim it is already met |
| `PROPOSED PILOT` | A shape for the first pilot — a hypothesis, not a secured commitment |
| `NOT BUILT` | No code exists for this yet |
| `OPEN DECISION` | A real, unresolved choice with named alternatives — not decided by this document |

---

## 1. Product statement

**DHealth is an interoperable diagnostic network.** It connects doctors and clinics, independent
laboratories, and patients so that a lab order and its result move through one shared, auditable
transaction — whether the organizations on each end use DHealth's own software or their existing
EMR/LIS. DHealth is not defined by any single application; it is defined by the transaction it
makes reliable across organizational boundaries.

## 2. Problem DHealth MVP solves

Today, when a clinic without its own laboratory needs a diagnostic test, the order, the sample
routing, and the result typically move by phone call, paper slip, or manual message between
systems that were never designed to talk to each other. Each handoff is a place a result can be
delayed, lost, or entered incorrectly, and reconciling "did the lab get this?" or "is the result
back yet?" falls on clinical staff's memory and follow-up calls rather than a system of record.
DHealth's MVP replaces that chain with one auditable order that both sides can see the true state
of, without requiring either side to first replace the software they already run.

## 3. Product family

DHealth MVP 1.0 consists of five product surfaces.

### DHealth Exchange

The shared network backbone. Responsible for transaction-scoped routing, organization boundaries,
orders, assignments, result exchange, audit, and workflow state. **It is not a longitudinal
national EMR** — ADR-042's data-minimization boundary is unchanged by this document: no free-text
clinical notes, no medication list, no diagnosis history, no general encounter data.

`CURRENT`: this is the one surface with real, tested, CI-qualified backend code today — DH-EX-001
through DH-EX-013 **[re-audited 2026-09-13]** (was "through DH-EX-007", then "through DH-EX-012A",
then "through DH-EX-012C"). See §8. The result data model is `LabOrderItem → LabResult (container)
→ LabObservation[]` (0..N observations per item) as of DH-EX-012C/ADR-047, implementing the
requirement real field evidence established — see `DECISIONS.md`'s ADR-046/ADR-047 and
`docs/FIELD-DIAGNOSTIC-WORKFLOW.md`. **Pre-release correction of an entered observation is now
implemented** (DH-EX-013, ADR-045 Questions 1–3): an immutable supersession chain, never a
destructive overwrite. Post-release amendment (ADR-045 Questions 4–5) remains OPEN and
unimplemented, deferred to DH-EX-014.

### DHealth Clinic

DHealth-native provider software for clinics/providers that do not have an adequate existing
clinical system. MVP purpose: patient lookup / minimum registration needed for the lab workflow;
create diagnostic lab orders; see order status; receive completed results; clinician review/
follow-up disposition. **It is not initially a complete hospital information system.**

`NOT BUILT`: no dedicated DHealth Clinic frontend or branded application exists. The Exchange
backend already exposes provider-facing order-creation/read routes a future DHealth Clinic would
call (§8); a provider-scoped patient-reference *binding* API exists (DH-EX-009, exact source-identity
get-or-create) **[re-audited 2026-09-13 — was "patient lookup/registration does not exist even at
the API level"]**, but full patient lookup/search/registration by name or demographic still does not
(§8, §10).

**Field-validated requirement (DH-FIELD-001, 2026-09-13):** real Djibouti clinic-order artifacts
confirm ordering is operationally simple — patient, requested diagnostic service(s), date, ordering-
clinician provenance. DHealth Clinic ordering must stay service-level and fast: a clinician selects
diagnostic *services*, never individual underlying observations, and must never be forced through an
analyte-by-analyte ordering flow. See `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Findings 1–2.

### DHealth Lab

DHealth-native laboratory software for laboratories that do not have an adequate LIS. MVP purpose:
incoming work queue; accept/reject assigned orders; specimen/sample workflow sufficient for the
pilot; processing state; structured result entry; result validation/release workflow; completed
work/history. **It is not initially intended to replace every advanced LIS capability.**

`NOT BUILT`: no dedicated DHealth Lab frontend exists. The Exchange backend implements the work
queue, accept/reject/start-processing, structured result entry, an explicit order-level
sample/specimen *collection milestone* (DH-EX-011), and an explicit, separately-authorized result
*release* step (DH-EX-012/ADR-044) **[re-audited 2026-09-13 — was "specimen/sample workflow and
result validation/release are explicit `NOT BUILT` gaps"]** (§8). Specimen/sample workflow remains
`PARTIAL` — DH-EX-011 is a single fact-of-recording milestone, not specimen management (no
container/volume/site/adequacy modeling exists). Result *pre-release correction* is now
`WRITTEN, TESTED, CI-QUALIFIED, REAL-POSTGRESQL-QUALIFIED` (DH-EX-013, ADR-045 Questions 1–3 —
an immutable supersession chain, LAB_TECH-only, no reason field, refuses outright once the order is
released). *Post-release amendment* remains `NOT BUILT`/`OPEN DECISION` (ADR-045 Questions 4–5,
§10), deferred to DH-EX-014 — unaffected by the result-domain change below. **[Re-audited
2026-09-13, DH-EX-012C — the sentence that followed here is no longer current,
kept only as history: "the underlying result-domain shape itself is confirmed not final for real
panel-based workflows (ADR-046), native DHealth Lab result entry should not be built against
today's schema until DH-EX-012B resolves that."]** The multi-observation result domain ADR-046
required is now implemented (ADR-047, DH-EX-012C): a diagnostic service's result entry is a
per-item container (`LabResult`) holding zero, one, or many child observations (`LabObservation`),
entered one at a time or via one atomic single-observation convenience call, with one explicit
completion action per item — never inferred from how many observations exist. See §8's "Result
validated/released" row for the exact API shape.

**Field-validated requirement (DH-FIELD-001, 2026-09-13), now implemented (DH-EX-012C, same day):**
DHealth Lab's native result workflow supports one ordered diagnostic service producing multiple
returned observations (ADR-046/ADR-047). No panel composition, analyte vocabulary, or clinical
content of any kind was invented to build this — observation identity is caller-supplied
(`source_code`/`display_label`) or, for the simple single-observation case, reused from the ordered
item's own catalog snapshot; no DHealth-owned observation catalog exists yet. Reference-range
representation (numeric bounds only, for now) still lives per observation, unchanged in shape from
before — richer representations (inequality-style values, textual reference information) remain a
future, not-yet-built extension, per Finding 8. See `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Findings 2
and 8, and `DECISIONS.md`'s ADR-047 for the full accepted architecture.

### DHealth Patient

The patient interaction layer. It must support digitally comfortable patients while **never making
digital literacy a condition of receiving care** (Law 4, §6). **It is not initially a full patient
portal or longitudinal personal-health record.**

`NOT BUILT`: DH-EX-005 implements a patient-facing API (opaque bearer-grant order access and lab
selection) with no frontend of any kind. See §7 for the assisted-digital strategy this surface must
follow once built.

### DHealth Connect

The integration layer for organizations that already have EMR/LIS software. The intended invariant:

> Use DHealth-native software **or** integrate the existing system — never maintain the same
> transaction manually in both.

OpenMRS/OpenELIS (ADR-042) are the first reference connector track under this model, not an
instance of DHealth Clinic/Lab. `CURRENT`: the OpenMRS/OpenELIS clinical stack and its adapter exist
and run (see `CURRENT_STATE.md`); its connection to the *Exchange* core specifically (as opposed to
the connector's own internal order/result routing) is `NOT BUILT` — no file under
`openmrs-config/` or `openelis-config/` has ever referenced the Exchange, confirmed by grep as of
this document's audit.

**Field-validated requirement (DH-FIELD-001, 2026-09-13):** a real Djibouti laboratory's own
structured, computer-generated result packet is evidence that at least one real participating
laboratory already runs software of some kind — reinforcing, not merely restating, Law 2's "no
double entry" invariant above: the intended steady state for such an organization is existing LIS +
DHealth Connect, never existing LIS + manual duplicate DHealth entry. A future connector integration
must be able to preserve, without inventing or normalizing away, (a) the source system's own
transaction identifiers alongside DHealth's own, (b) the distinction between a source-system event
time (collection, result creation, professional release) and DHealth's own received/recorded time,
and (c) a source's own result/reference-range representation (which may not always be a clean
numeric low/high pair) — without ever deriving a clinical judgment from it. **No specific LIS
product, interface protocol (API/FHIR/HL7/CSV or otherwise), or analyzer-integration capability is
claimed to exist for this or any facility** — none was demonstrated by the reviewed evidence. See
`docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Findings 4, 5, 7, 8, and 15.

## 4. MVP golden journey

The first usable DHealth product must prove this journey reliably, across organizations:

```text
Provider identifies/registers patient
        ↓
Clinician creates lab order
        ↓
Lab is assigned/selected
        ↓
Lab receives order
        ↓
Lab accepts order
        ↓
Sample/specimen workflow
        ↓
Processing
        ↓
Result entered
        ↓
Result validated/released
        ↓
Ordering provider receives result
        ↓
Clinician reviews result
        ↓
Patient receives an appropriate status/follow-up communication
```

**The MVP is not defined by feature count.** It succeeds when this transaction works reliably
across organizations that did not previously share a system. §8 maps each step against what exists
today.

## 5. Native vs. integrated facility model

Two deployment models, corresponding directly to Law 2 (§6):

**Native model.** The facility does not have an adequate system. DHealth Clinic and/or DHealth Lab
is the authoritative workflow system for that facility's participation in the network.

**Integrated model.** The facility has an existing EMR/LIS. DHealth Connect keeps staff in their
existing system as much as practicable; DHealth only carries what the connector cannot.

**Transitional pilot exception.** If a connector is not yet available for a given system, DHealth
may temporarily handle a clearly isolated workflow the incumbent system does not already own. This
must never be documented as the intended production architecture — it is a named, time-boxed
exception, not a third permanent model.

## 6. The four non-negotiable product laws

These bind every future DHealth implementation ticket the same way `AGENTS.md`'s clinical-content
rules already bind clinical work. A ticket that would violate one of these needs a superseding ADR,
not a quiet exception.

> ### LAW 1 — No forced marketplace
> Day-one DHealth is a connected diagnostic workflow, not a price-shopping product. Price
> comparison and cheapest-price lab ranking are not required patient-facing features for launch.
> Doctor/facility-assigned lab workflows are valid; patient lab choice may still exist where
> appropriate. Broader marketplace/discovery may be introduced later. **The existing Exchange
> pricing implementation (DH-EX-003) is not deleted** — it is implemented infrastructure whose
> patient-facing use is not required for launch and may be useful again in a later product phase.
>
> ### LAW 2 — No double entry
> A DHealth production workflow must not require staff to manually maintain the same clinical
> transaction in DHealth and another production clinical system. Forbidden steady state: a doctor
> enters an order into an existing EMR, then manually re-enters it into DHealth; a lab tech records
> a result in an existing LIS, then manually re-records it into DHealth. See §5 for the two
> permitted deployment models and the one named transitional exception.
>
> ### LAW 3 — Faster than what it replaces
> DHealth cannot win by merely being digital. High-frequency tasks must be extremely low-friction.
> `TARGET`, not a current claim: a common provider lab order in approximately under 20 seconds; a
> common lab order acceptance in approximately under 10 seconds; minimal navigation for routine
> actions; sensible remembered defaults for repeated choices; order sets/favorites supported by the
> native product direction; no unnecessary duplicate demographic requests; no interoperability
> jargon in normal clinical UX. These are targets to validate with real users.
>
> ### LAW 4 — No digital-literacy requirement for care
> A patient must never need a smartphone, app installation, WhatsApp navigation skill, strong
> literacy, or familiarity with digital interfaces to complete a DHealth-supported care journey.
> DHealth Patient is assisted-digital by design (§7).

## 7. Assisted-digital patient strategy

**DHealth Patient must be usable without requiring digital comfort.** Supported interaction
patterns may include: simple mobile web; large, obvious actions; minimal text; print; QR/order
reference; clinic/reception assistance; lab/reception assistance; SMS where appropriate; WhatsApp
where appropriate; and, in a later validated phase, audio/voice. `NOT BUILT`: none of these
channels exist yet for the Exchange (DH-EX-005's patient API has no frontend at all); this section
records the design constraint any future build must satisfy, not a status.

**One order, many doors.** A DHealth order has one authoritative identity. A patient interacting
through mobile web, a printed slip, a QR code, an order number, receptionist assistance, lab-staff
assistance, or a future voice/audio channel must still resolve to the same underlying order. The
access channel must never create a parallel clinical workflow.

**Field-reaffirmed (DH-FIELD-001, 2026-09-13):** real Djibouti workflow evidence confirms paper
remains operationally valid today, supporting the transitional pattern already implied above —
digital order → optional printed slip → patient carries it → lab scans QR/enters a reference → the
same canonical order opens — reaffirming "one order, many doors" as a real-world requirement, not a
speculative one. **Identifying an order by QR/reference must never itself be authorization**: it is
a lookup key, structurally independent of the Exchange's own authenticated organization/user
authorization and of DH-EX-005's separate patient-bearer-grant mechanism — neither is weakened or
bypassed by knowing an order's reference. See `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Findings 12–13.
Still `NOT BUILT`; this remains a design constraint, not a status.

**Human-assisted workflow is a feature, not a failure.** DHealth should not attempt to remove
people from every interaction. For users who need assistance, clinic and laboratory staff are valid
interaction points — a receptionist printing the order, a lab tech scanning it, staff helping
verify the correct patient. `OPEN DECISION`, not decided here: any family/helper proxy-authorization
workflow requires its own future security/privacy decision; no proxy semantics are invented by this
document.

**Patient identity safety.** Ease of use must not weaken identity safety. Search may tolerate
spelling/transliteration differences as a *discovery* aid; it must never silently merge or
automatically identify patients. Staff must confirm identity according to an approved workflow.
`CURRENT`, from DH-EX-004/ADR-042: patient references are bound by source-system + source-identity
only — never a national identifier, never fuzzy-matched into an automatic merge. `OPEN DECISION`:
the full patient-matching policy for Djibouti is not invented here. **A narrower, field-raised
question, also `OPEN DECISION` (DH-FIELD-001, 2026-09-13), not answered here:** what minimum
patient attributes does a receiving laboratory need to safely verify the person presenting for an
order? Real lab workflow may use demographic detail (e.g. date of birth) for this; this document
does not conclude the Exchange should automatically transmit or store it — that would be a real
privacy/scope decision requiring Liban's approval and further field validation, not an agent's
inference from one artifact. See `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Finding 11.

**Language/localization.** DHealth is being designed for Djibouti. `DECIDED`: professional
clinical/administrative interfaces remain French-first unless a later validated facility
requirement changes a specific workflow (consistent with `AGENTS.md`'s existing French-first rule).
`TARGET`, `NOT BUILT`: patient-facing architecture must be *capable* of supporting Somali, Afar,
Arabic, and French — no language beyond what already exists in the OpenMRS/OpenELIS connector track
is currently implemented for any DHealth surface, and no medical translations are invented by this
document. Audio/voice is roadmap direction, not current MVP implementation.

## 8. Current implementation vs. intended MVP

Per-step status of the golden journey (§4), stated precisely — `WRITTEN` (exists in code),
`TESTED` (has automated test coverage, per the implementing commit's own report unless otherwise
noted), `CI-QUALIFIED` (an exact-SHA GitHub Actions run has been independently observed green),
`RUNTIME-QUALIFIED` (demonstrated against a real running system, e.g. real PostgreSQL or a real
browser), or `NOT BUILT`.

**[Re-audited 2026-09-13, DH-FIELD-001 — every row below re-verified against `origin/master` at
`cbdc3dc8b9d2defedebadfb52cc87b6bc4374ec7`, not carried forward from the 2026-09-12 pass. Several
line-number citations below had already drifted from that pass's own numbers; all are re-checked
here, not copied.]**

| Golden-journey step | Status | Evidence |
|---|---|---|
| Provider identifies/registers patient | **PARTIAL** (was **NOT BUILT**/partial) | DH-EX-009 added `POST .../organizations/{provider_id}/patient-references` (`bind_patient_reference`, `api.py:1759`) — an exact source-identity get-or-create bind. `create_lab_order` (`api.py:1809`) still only calls `patients.get_for_organization` and 404s if no reference exists — so an order-creation caller must bind first, in a separate call. There is still no "look up or register a patient by name/demographic" API. |
| Clinician creates lab order | WRITTEN, TESTED, CI-QUALIFIED | DH-EX-004, `POST /organizations/{provider_id}/orders` (`api.py:1806`). CI run `34441434606` (per PROJECT_STATE.md's DH-EX-004 FINAL CLOSURE). |
| Lab is assigned/selected | WRITTEN, TESTED, CI-QUALIFIED — **both paths now exist** (was "patient-selection path only") | DH-EX-005's patient-selection path, `POST /patient/order/selection` (`api.py:2176`), gated by an opaque bearer grant, exact price consent (`expected_price_ids`), and full DH-EX-003 eligibility re-evaluation — **plus DH-EX-010's provider/facility-assigned path**, `POST .../orders/{order_id}/assign-lab` (`api.py:1934`), which shares the identical `LabSelection` row but never touches price consent. ADR-043's launch-option (a) conflict is closed by DH-EX-010; DH-EX-005's own price-consent contract is otherwise unchanged. |
| Lab receives order | WRITTEN, TESTED, CI-QUALIFIED | `GET /organizations/{lab_id}/lab-orders` (`api.py:2246`), DH-EX-006. |
| Lab accepts order | WRITTEN, TESTED, CI-QUALIFIED | `POST .../accept` (`api.py:2272`, DH-EX-006), `SELECTED`→`ACCEPTED`. |
| Sample/specimen workflow | PARTIAL (unchanged this pass) | DH-EX-011: order-level sample/specimen collection milestone (`LabSampleCollection`, `POST .../record-collection`, `api.py:2374`), required before `start-processing` may run. This is a single fact-of-recording milestone, not specimen management — no specimen type, container, volume, site, handling, storage, or adequacy modeling exists in the Exchange schema. |
| Processing | WRITTEN, TESTED, CI-QUALIFIED | `POST .../start-processing` (`api.py:2341`, DH-EX-006), `ACCEPTED`→`IN_PROGRESS`. |
| Result entered | WRITTEN, TESTED, CI-QUALIFIED | DH-EX-007/DH-EX-012, `POST .../items/{item_id}/result` (`submit_lab_result`, `api.py:2419`). **Behavior changed by DH-EX-012, not merely re-cited**: even an order's last missing result no longer transitions the order — it only reports `ready_for_release`. See the next row. |
| Result validated/released | **WRITTEN, TESTED, CI-QUALIFIED, REAL-POSTGRESQL-QUALIFIED** (was **NOT BUILT**) | DH-EX-012/ADR-044: `POST .../release-results` (`release_lab_order_results`, `api.py:2955`), gated on a new `LAB_RESULT_RELEASER` role (additive alongside `LAB_TECH`), required before `LabOrder.status` becomes `COMPLETED`. This is the separate authorization step ADR-043 originally recorded as missing — it now exists, distinctly from result entry. **DH-EX-012C/ADR-047, 2026-09-13:** the underlying result domain is no longer one scalar per item — `POST .../items/{item_id}/observations` (`record_lab_observation`, `api.py:2686`) records any number of observations under an item's container, and `POST .../items/{item_id}/complete-result-entry` (`complete_lab_result_entry`, `api.py:2743`) is the one explicit, LAB_TECH-only signal that an item's entry is finished (never inferred from observation count); `POST .../items/{item_id}/result` (`submit_lab_result`, `api.py:2571`) remains as an atomic single-observation convenience call for the simple case. Provider/lab-inspection reads now return nested observations per item. Real-PostgreSQL qualified, including concurrency (observation-vs-observation, observation-vs-completion, completion-vs-release, concurrent completion) and migration upgrade/downgrade-guard evidence — see `PROJECT_STATE.md`'s DH-EX-012C entry. **DH-EX-013/ADR-045 Questions 1–3, 2026-09-13:** correcting an already-entered observation **before** release is `WRITTEN, TESTED, CI-QUALIFIED, REAL-POSTGRESQL-QUALIFIED` — `POST .../observations/{observation_id}/correct` (`correct_lab_observation`, `api.py:2803`) supersedes the currently-effective observation with a new immutable one (`supersedes_observation_id`/`superseded_at`, never a destructive overwrite), LAB_TECH-only, no reason field, refuses outright (409) once the order is released; `GET .../observations/history` returns the full chain to LAB_TECH/LAB_RESULT_RELEASER only. CI-qualified on commit `05ebe22` (all 9 check runs verified `success` via GitHub's check-runs API). (Line numbers above are current as of the DH-EX-013 commit; if read after a later ticket touches `api.py` again without updating these citations, treat them as approximate, matching this document's "stale but kept for history" convention at §10 item 11.) **What is still not built:** amending an already-*released* result has no supported path at all (ADR-045 Questions 4–5, §10, deferred to DH-EX-014); no "not performed"/"no result obtainable" representation exists (deliberately, per ADR-047's accepted zero-observation resolution). |
| Ordering provider receives result | WRITTEN, TESTED, CI-QUALIFIED | `GET .../orders/{order_id}/results` (`get_lab_order_results`, `api.py:2594`), provider-only, only once `COMPLETED` — the gate that now depends on explicit release, not automatic completion. |
| Clinician reviews result | WRITTEN, TESTED, CI-QUALIFIED | `POST .../review/no-follow-up-required` / `.../follow-up-required` (`api.py:2701`/`api.py:2714`). |
| Patient receives status/follow-up communication | **NOT BUILT** (unchanged this pass) | `ResultDelivery` is created `PENDING` at order creation (DH-EX-001) and no code path anywhere in the repository advances it past `PENDING` — re-confirmed by grep as of this pass. No WhatsApp/SMS/email transport exists for the Exchange. `ResultDeliveryRepository.mark_delivered`/`mark_failed` exist (`results.py`) but are never called. |

**RUNTIME-QUALIFIED, for any of the above: none.** Every qualification on record for DH-EX-001
through DH-EX-012A is CI/unit/integration-test-level against SQLite and/or a disposable PostgreSQL
instance (re-confirmed this pass — was "through DH-EX-007"). **Real Keycloak qualification: NOT RUN
for any Exchange ticket** (DH-EX-008 wired the deployment but could not qualify live, per §10 item
14 below). **Manual live/browser demonstration: NOT RUN.** No production deployment exists. No pilot
has begun.

## 9. Explicit out-of-scope list

DHealth MVP 1.0 does **not** require:

full inpatient hospital information system · complete longitudinal national EHR · pharmacy ·
radiology/PACS · insurance claims · patient payments · consumer price marketplace · AI diagnosis ·
AI clinical interpretation · clinical decision support · bed management as an Exchange MVP
requirement · maternity workflows as an Exchange MVP requirement · appointment scheduling · full
accounting/billing · every analyzer integration · nationwide rollout.

**Existing work in any of these areas is not deleted merely because it is off the new MVP critical
path.** The maternity/OpenMRS/OpenELIS stack, bed management (ADR-007), the partograph (ADR-006),
and the provisioning/multi-facility system all remain in the repository as preserved, reusable
work — see §14.

## 10. Known gaps

Gaps found by direct code inspection during this audit, each an `OPEN DECISION` or `NOT BUILT`
item, not resolved by this document. **Re-audited 2026-09-13 (DH-FIELD-001) — items resolved since
the 2026-09-12 pass are marked `RESOLVED` in place, not deleted, so the original gap remains
legible; item numbers are kept stable since §15 and other documents point at them by number.**

1. ~~No patient lookup/registration API.~~ **PARTIAL, not fully resolved [re-audited
   2026-09-13].** DH-EX-009 added `POST .../organizations/{provider_id}/patient-references`, an
   exact-source-identity get-or-create bind — order creation no longer strictly requires a
   *pre-existing* reference if the caller binds first. Full lookup/search/registration by name or
   demographic still does not exist. Still blocks DHealth Clinic's most basic MVP purpose, just less
   completely than before.
2. ~~No provider/facility-assigned-lab path.~~ **RESOLVED [re-audited 2026-09-13].** DH-EX-010
   added `POST .../orders/{order_id}/assign-lab` — ADR-043's launch-option (a) is now implemented,
   coexisting with patient self-selection on the same `LabSelection` row.
3. **DH-EX-005's price-consent contract vs. Law 1.** Unchanged, still an explicit, unresolved
   conflict recorded in ADR-043 — DH-EX-010's provider-assignment path is one of the two acceptable
   launch options ADR-043 itself named for this conflict, not a resolution of the conflict itself.
4. **No specimen/sample management.** Unchanged. DH-EX-011 added an order-level collection
   *milestone* only (`LabSampleCollection`) — no specimen type, container, volume, site, handling,
   storage, or adequacy entity/state exists in the Exchange schema.
5. ~~No result validation/release step, distinct from result entry.~~ **RESOLVED [re-audited
   2026-09-13].** DH-EX-012/ADR-044 added exactly this: an explicit `LAB_RESULT_RELEASER`-gated
   release step, structurally separate from result entry, required before `COMPLETED`. **What
   remains open as a direct consequence, recorded as new items 15–16 below:** no correction/
   amendment path exists either before or after this new release step (ADR-045), and the underlying
   result-per-item data model this release step operates on is itself confirmed not final for real
   panel-based workflows (ADR-046).
6. **`ResultDelivery.status` semantics undecided.** Unchanged. `DELIVERED` could mean
   available-to-provider, sent-to-connector, received-by-connector, presented-to-user, or
   acknowledged-by-user. Not chosen. See ADR-043.
7. **No patient communication transport.** Unchanged. No SMS/WhatsApp/email code exists for the
   Exchange; ADR-003's real-credential WhatsApp gate (maternity-era) is unrelated infrastructure
   that has never been wired to the Exchange.
8. **Sensitive-content policy per channel** (SMS/WhatsApp/lock-screen/voice/shared family phones)
   is undecided. Unchanged.
9. **Proxy/family authorization** is undecided; no code or semantics exist. Unchanged.
10. **Full patient-matching/national-identifier policy for Djibouti** is undecided beyond the
    existing exact-source-identity rule. Unchanged. **Sharpened by field evidence, not resolved
    [re-audited 2026-09-13]:** what minimum patient attributes a receiving laboratory needs to
    safely verify the presenting patient is now an explicitly named open question, not merely
    implied — §7, `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Finding 11.
11. ~~`LabResult.numeric_value`/`reference_range_low`/`reference_range_high` have no API-level
    `max_digits`/`decimal_places` constraint matching their `Numeric(18, 4)` database columns —
    confirmed by reading `api.py`'s `LabResultSubmitRequest` (line 647) against `models.py`'s
    `LabResult` (line 474), the same class of gap DH-EX-003R already fixed for `LabPrice.amount`
    (api.py line 366). A value with more than 4 decimal places or too many integer digits reaches
    the database rather than a clean 422. This is DH-EX-007A in the follow-up backlog (§15).~~
    **RESOLVED [re-audited 2026-09-13].** Fixed by DH-EX-007A (`PROJECT_STATE.md`'s DH-EX-007A
    entry) — the API boundary now rejects an over-scale or over-precision Decimal value with a
    clean 422 before it can reach the database. (Original line citations above are stale — kept
    verbatim for history, not re-verified against current line numbers since the gap they describe
    no longer exists.)
12. **No exact-SHA CI-independent review (DH-EX-007R) exists** for DH-EX-007 itself — only the
    implementing commit's own self-review, per PROJECT_STATE.md's DH-EX-007 entry. Not re-verified
    by this pass; carried forward unchanged.
13. **DHealth Exchange has no connector wiring to OpenMRS/OpenELIS.** Unchanged. The two systems
    exist as separate, working stacks; nothing routes an Exchange order through them or vice versa.
14. **No real Keycloak qualification anywhere in the Exchange.** Unchanged in substance — DH-EX-008
    wired the deployment configuration (env contract, realm import, routing) but could not qualify
    against a live Keycloak in its own sandbox (registry image unreachable); every authorization
    boundary (`require_clinician`, `require_lab_tech`, etc.) remains proven only against a locally
    generated RSA keypair (`StaticKeySource`), never a real identity provider.
15. ~~**New [DH-FIELD-001, 2026-09-13]. No result correction (pre-release) or amendment
    (post-release) path exists.** `LabResult` is insert-only, package-wide, with no entering-actor
    column and no revision metadata (confirmed by DH-EX-012A's audit). ADR-045 records the decision
    packet distinguishing the two problems; **neither is decided or implemented.** See ADR-045.~~
    **PARTIALLY RESOLVED [re-audited 2026-09-13, DH-EX-013].** ADR-045 Questions 1–3 are accepted
    and implemented: a `LabObservation` may be corrected before release via an immutable
    supersession chain (`supersedes_observation_id`/`superseded_at`), LAB_TECH-only, no reason
    field, structurally refused once the order is released. Every new observation (entry or
    correction) now carries `recorded_by_identity_id`; pre-existing rows stay `NULL`. **Post-release
    amendment (ADR-045 Questions 4–5) remains fully OPEN and unimplemented** — deferred to
    DH-EX-014, unaffected by this change. See `DECISIONS.md`'s ADR-045 addendum and
    `PROJECT_STATE.md`'s DH-EX-013 entry.
16. ~~**New [DH-FIELD-001, 2026-09-13]. The current `LabOrderItem → one LabResult` model is
    confirmed not final for real panel-based laboratory workflows.** Real field evidence shows one
    ordered diagnostic service may need to return multiple observations — today's schema cannot
    represent this. ADR-046 accepts this as a binding architectural requirement without choosing a
    schema. This is now the reason item 15's implementation is paused, not merely still open — see
    `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` and ADR-046. DH-EX-012B is the ticket that will resolve
    this.~~ **RESOLVED [re-audited 2026-09-13, DH-EX-012C].** ADR-047 (recommended by DH-EX-012B,
    accepted by Liban) is implemented: `LabOrderItem → LabResult` (container) `→ LabObservation[]`
    (0..N per container), real-PostgreSQL qualified including concurrency and migration
    upgrade/downgrade evidence. **Item 15's pause is now lifted for the schema-shape reason above —
    ADR-045's own five questions remain independently unresolved and its implementation remains
    paused for those reasons, not this one.** See `PROJECT_STATE.md`'s DH-EX-012C entry and
    `DECISIONS.md`'s ADR-047 accepted-resolution append.

## 11. Pilot shape (`PROPOSED PILOT`)

A hypothesis for the first pilot's scale, not a secured commitment. **No facility has agreed to
participate; this is not evidence of one.**

- One anchor laboratory.
- Approximately three participating clinics.
- Approximately 5–10 clinicians.
- Synthetic qualification first, before any real patient data.
- An appropriate regulatory/data-governance path established before real patient use — not yet
  defined by this document.
- A narrow, common diagnostic workflow, not the full catalog.
- Direct operational support during the pilot window.
- Measurable workflow outcomes (§12), collected deliberately rather than assumed.

## 12. Success metrics (`PROPOSED`, to validate — none are current facts)

Routine order-creation speed · lab-acceptance speed · duplicate-entry rate · failed patient
searches · percentage of transactions requiring a manual workaround · order-to-result completion
rate · result turnaround time · result successfully returned to the ordering provider · doctor
review completion rate · unnecessary phone/WhatsApp chasing · staff support requests · weekly
clinician retention/use · serious privacy/cross-tenant incidents.

The strongest qualitative signal, if the pilot works: **participating users do not want to return
to the old workflow.**

No numerical achievement is claimed anywhere in this document.

## 13. Safety and privacy boundaries

**Clinical safety boundary — unchanged and binding.** DHealth records, transports, presents, and
audits clinical information. It does not independently diagnose, interpret, or recommend treatment.
`AGENTS.md`'s existing restrictions on inventing clinical content are not weakened by this document
or by the product pivot.

**Patient result-release policy — `DECIDED` at ADR-042, unchanged here.** The ordering doctor
receives the full lab result. The patient does not automatically receive detailed numeric/clinical
values — only order status, which lab was selected, that a result is ready, that the doctor has
reviewed it, and the doctor's `NO_FOLLOW_UP_REQUIRED`/`FOLLOW_UP_REQUIRED` disposition. Confirmed
current in code: `PatientOrderResponse` (`api.py:652`, `extra="forbid"` at `api.py:657`) defines no
result field — **re-verified 2026-09-13 (DH-FIELD-001): this document's own line citation had
drifted to `api.py:557`, which no longer points at this class; corrected here.**

**Central data boundary — `DECIDED` at ADR-042, unchanged here.** The Exchange core may persist
organization identity, patient/practitioner *references* (source-system + source-identity only),
requested-test catalog entries, sender/lab organization identity, a frozen price snapshot, order
status, the lab result value, result-delivery state, and the doctor's follow-up disposition. It may
not hold free-text clinical notes, a medication list, diagnosis history, or general encounter data.
An ordering-practitioner reference used on the lab side of a transaction must derive from this same
existing source-system + source-identity provenance mechanism (DH-EX-004/DH-EX-009), never from
unrelated authentication/account metadata — reaffirmed by field evidence, not a new rule
(`docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Finding 9).

**Never a shared multi-tenant clinical *store*** for a connector-backed facility (ADR-020/ADR-021,
unaffected by this document) — that rule is scoped to per-facility clinical stacks, not to the
Exchange's own routing/audit-scoped core, per ADR-042.

**No third-party AI/ML service ever receives patient data**, per `AGENTS.md`, unchanged.

## 14. Roadmap relationship to later DHealth Hospital / broader network

**The maternity-first OpenMRS/OpenELIS stack is not discarded.** It becomes: a preserved
clinical-stack implementation; reference connector infrastructure under DHealth Connect's model;
evidence about real OpenMRS/OpenELIS integration (identity/SSO, FHIR round-trips, order
correlation); and reusable deployment/security/backup/interoperability work (Caddy, Keycloak,
backup/restore, provisioning). It is no longer the definition of the DHealth MVP. Historical
documentation describing it as the product (most of `docs/ARCHITECTURE.md` §1–§16, `README.md`'s
pre-2026-09-08 sections, `CURRENT_STATE.md`'s maternity-era assessment) remains historical
evidence — it is not rewritten to look as though the current network vision always existed.

A future "DHealth Hospital" or broader multi-facility network tier — larger facilities running
DHealth-adjacent or fully-integrated systems at scale, potentially building on the existing
`provisioning/` multi-facility control plane (ADR-020/ADR-021/ADR-022) — is a plausible later
direction referenced here for continuity. **It is not scoped, decided, or started by this
document.**

## 15. Follow-up backlog

Recorded here and in `PROJECT_STATE.md`'s DH-MVP-001 entry. No implementation detail beyond what is
already evidenced above is assigned to any item — each is a ticket to open, not a design already
made.

**Re-audited 2026-09-13 (DH-FIELD-001) — item numbers kept stable; resolved items marked in place.**

1. ~~**DH-EX-007A** — result numeric/API precision parity with PostgreSQL (§10, item 11).~~
   **RESOLVED [re-audited 2026-09-13]** — see §10 item 11.
2. Exchange deployment/auth wiring. **PARTIAL [re-audited 2026-09-13]** — DH-EX-008 wired the real
   Docker/Keycloak/Caddy deployment configuration; live qualification against a running Keycloak is
   still blocked (registry image unreachable in the qualifying sandbox) and remains open as
   DH-EX-008R.
3. Real Keycloak qualification for the Exchange's authorization boundaries. Unchanged — see §10
   item 14.
4. Patient reviewed/result-ready disposition (a patient-facing signal distinct from the doctor's
   own review disposition — not designed here). Unchanged.
5. Result/PHI read-access audit (does every read of a `LabResult` value get an audit row today, not
   just mutations? — not verified by this document). Unchanged.
6. Identity-first-seen audit consistency (whether `PatientReference`/`PractitionerReference`
   creation is itself audited as consistently as order/result mutations). Unchanged.
7. `ResultDelivery` semantics decision (§10, item 6). Unchanged.
8. The current pytest collection warning present across every DH-EX-* CI run on record (never
   identified or fixed; carried forward unresolved). Not re-verified by this pass.
9. Exchange API modularization (`api.py` has grown to over 2,800 lines across twelve-plus tickets
   without a structural split — **re-measured 2026-09-13, was "over 2,200 lines across seven
   tickets"**).
10. Full deployed synthetic golden journey (§4) demonstrated end to end against real infrastructure.
    Unchanged.
11. Patient-selection/price-contract decision for a price-free launch UX (ADR-043's DH-EX-005
    conflict). Unchanged — DH-EX-010's provider-assignment path is a coexisting option, not a
    resolution of this specific conflict (§10 item 3).
12. ~~Lab result validation/release workflow (§10, item 5).~~ **RESOLVED [re-audited
    2026-09-13]** — see §10 item 5. **Superseded by two new, narrower items below, raised by
    resolving this one:** result correction/amendment (item 16) and result-domain cardinality
    (item 17).
13. Assisted-digital/print/QR patient workflow (§7). Unchanged; reaffirmed by field evidence
    (§7, `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Findings 12–13).
14. Provider UX speed/order-set requirements (Law 3 targets, §6). Unchanged.
15. DHealth Connect no-double-entry integration contract (the concrete mechanism by which a
    connector satisfies Law 2 — not designed here). Unchanged; sharpened by field evidence that at
    least one real participating laboratory already runs software of some kind (§3 DHealth Connect,
    `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Finding 7) — still no specific LIS, protocol, or interface
    claimed.
16. ~~**New [DH-FIELD-001, 2026-09-13].** Result correction (pre-release) and amendment
    (post-release) implementation — ADR-045's decision packet, not yet decided by Liban. **No
    longer paused behind item 17 [re-audited 2026-09-13, DH-EX-012C] — item 17 is resolved, but
    ADR-045's own five questions remain independently unresolved on their own terms** (§10 item
    15).~~ **PARTIALLY RESOLVED [re-audited 2026-09-13, DH-EX-013]** — pre-release correction
    (ADR-045 Questions 1–3) is implemented; see §10 item 15. **Narrowed to post-release amendment
    only (ADR-045 Questions 4–5) — the natural next ticket, DH-EX-014, not yet started.**
17. ~~**New [DH-FIELD-001, 2026-09-13].** Result-domain schema resolution — the ticket referred to
    throughout this document as DH-EX-012B: must decide how one ordered diagnostic service can
    produce zero, one, or multiple returned observations (ADR-046), without inventing panel
    composition, analyte vocabulary, or any fixed observation set.~~ **RESOLVED [re-audited
    2026-09-13, DH-EX-012C]** — see §10 item 16.

---

*This document is maintained under the same append-only discipline as `PROJECT_STATE.md` and
`CURRENT_STATE.md`: corrections are appended dated entries, not silent rewrites of a prior claim.*
