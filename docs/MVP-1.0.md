# DHealth MVP 1.0 — Product Contract

**Status:** Product direction ACCEPTED, architecture ACCEPTED — see `DECISIONS.md`'s ADR-043 (which
extends, and does not retract, ADR-042's 2026-09-08 pivot decision). **Not yet implemented** beyond
what DH-EX-001 through DH-EX-007 already built (§8).
**Date:** 2026-09-12. **Audited against `origin/master` at `4e12dcfb4cc9b447496783fff1eb24eaf21711a5`.**

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
through DH-EX-007. See §8.

### DHealth Clinic

DHealth-native provider software for clinics/providers that do not have an adequate existing
clinical system. MVP purpose: patient lookup / minimum registration needed for the lab workflow;
create diagnostic lab orders; see order status; receive completed results; clinician review/
follow-up disposition. **It is not initially a complete hospital information system.**

`NOT BUILT`: no dedicated DHealth Clinic frontend or branded application exists. The Exchange
backend already exposes provider-facing order-creation/read routes a future DHealth Clinic would
call (§8); patient lookup/registration does not exist even at the API level (§8, §10).

### DHealth Lab

DHealth-native laboratory software for laboratories that do not have an adequate LIS. MVP purpose:
incoming work queue; accept/reject assigned orders; specimen/sample workflow sufficient for the
pilot; processing state; structured result entry; result validation/release workflow; completed
work/history. **It is not initially intended to replace every advanced LIS capability.**

`NOT BUILT`: no dedicated DHealth Lab frontend exists. The Exchange backend implements the work
queue, accept/reject/start-processing, and structured result entry (§8); specimen/sample workflow
and result validation/release are explicit `NOT BUILT` gaps (§10).

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
the full patient-matching policy for Djibouti is not invented here.

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

| Golden-journey step | Status | Evidence |
|---|---|---|
| Provider identifies/registers patient | **NOT BUILT** (registration) / partial (reference binding) | `PatientReferenceRepository.get_or_create` exists (`identity.py`), but no HTTP route exposes it — `create_lab_order` (api.py:1513) only calls `patients.get_for_organization` and 404s if the reference doesn't already exist. There is no "look up or register a patient" API today. |
| Clinician creates lab order | WRITTEN, TESTED, CI-QUALIFIED | DH-EX-004, `POST /organizations/{provider_id}/orders` (api.py:1509). CI run `34441434606` (per PROJECT_STATE.md's DH-EX-004 FINAL CLOSURE). |
| Lab is assigned/selected | WRITTEN, TESTED, CI-QUALIFIED — **patient-selection path only** | DH-EX-005, `POST /patient/order/selection` (api.py:1807), gated by an opaque bearer grant, exact price consent (`expected_price_ids`), and full DH-EX-003 eligibility re-evaluation. **No provider/facility-assigned-lab route exists** — confirmed by the full route inventory in `api.py`; this is the launch-option (a) referenced in ADR-043's DH-EX-005 conflict note, and it is `NOT BUILT`, not merely undocumented. |
| Lab receives order | WRITTEN, TESTED, CI-QUALIFIED | `GET /organizations/{lab_id}/lab-orders` (api.py:1868), DH-EX-006. |
| Lab accepts order | WRITTEN, TESTED, CI-QUALIFIED | `POST .../accept` (DH-EX-006), `SELECTED`→`ACCEPTED`. |
| Sample/specimen workflow | PARTIAL | DH-EX-011: order-level sample/specimen collection milestone is implemented for the lab workflow (`LabSampleCollection`, `POST .../record-collection`). This is a single fact-of-recording milestone, not specimen management — no specimen type, container, volume, site, handling, storage, or adequacy modeling exists in the Exchange schema. |
| Processing | WRITTEN, TESTED, CI-QUALIFIED | `POST .../start-processing` (DH-EX-006), `ACCEPTED`→`IN_PROGRESS`. |
| Result entered | WRITTEN, TESTED-per-commit-message, CI-QUALIFIED | DH-EX-007, `POST .../items/{item_id}/result` (api.py:1997). CI run `34649375844` (DH-EX-007D2). Not independently re-run by any documentation session (no Python interpreter available in this environment — see PROJECT_STATE.md's DH-EX-007 entry). |
| Result validated/released | **NOT BUILT** | DH-EX-007 only implements automatic `COMPLETED` once every item has a result (application/PostgreSQL-proven counting) — there is no separate authorization step distinguishing "entered" from "validated/released." Recorded as a product gap in ADR-043 and §10 below, not silently assumed to exist. |
| Ordering provider receives result | WRITTEN, TESTED-per-commit-message, CI-QUALIFIED | `GET .../orders/{order_id}/results` (api.py:2077), provider-only, only once `COMPLETED`. |
| Clinician reviews result | WRITTEN, TESTED-per-commit-message, CI-QUALIFIED | `POST .../review/no-follow-up-required` / `.../follow-up-required` (api.py:2187/2200). |
| Patient receives status/follow-up communication | **NOT BUILT** | `ResultDelivery` is created `PENDING` at order creation (DH-EX-001) and no code path anywhere in the repository advances it past `PENDING` — confirmed by grep. No WhatsApp/SMS/email transport exists for the Exchange. `ResultDeliveryRepository.mark_delivered`/`mark_failed` exist (`results.py`) but are never called. |

**RUNTIME-QUALIFIED, for any of the above: none.** Every qualification on record for DH-EX-001
through DH-EX-007 is CI/unit/integration-test-level against SQLite and/or a disposable PostgreSQL
instance. **Real Keycloak qualification: NOT RUN for any Exchange ticket.** **Manual live/browser
demonstration: NOT RUN.** No production deployment exists. No pilot has begun.

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
item, not resolved by this document:

1. **No patient lookup/registration API.** Order creation requires a pre-existing
   `patient_reference_id`; there is no route to create or find one. Blocks DHealth Clinic's most
   basic MVP purpose.
2. **No provider/facility-assigned-lab path.** Only patient self-selection (with price consent)
   exists. ADR-043's launch-option (a) is not implemented.
3. **DH-EX-005's price-consent contract vs. Law 1.** Recorded in ADR-043 as an explicit,
   unresolved conflict — not reinterpreted here.
4. **No specimen/sample management.** DH-EX-011 added an order-level collection *milestone* only
   (`LabSampleCollection`) — no specimen type, container, volume, site, handling, storage, or
   adequacy entity/state exists in the Exchange schema.
5. **No result validation/release step**, distinct from result entry. See ADR-043.
6. **`ResultDelivery.status` semantics undecided.** `DELIVERED` could mean available-to-provider,
   sent-to-connector, received-by-connector, presented-to-user, or acknowledged-by-user. Not
   chosen. See ADR-043.
7. **No patient communication transport.** No SMS/WhatsApp/email code exists for the Exchange;
   ADR-003's real-credential WhatsApp gate (maternity-era) is unrelated infrastructure that has
   never been wired to the Exchange.
8. **Sensitive-content policy per channel** (SMS/WhatsApp/lock-screen/voice/shared family phones)
   is undecided.
9. **Proxy/family authorization** is undecided; no code or semantics exist.
10. **Full patient-matching/national-identifier policy for Djibouti** is undecided beyond the
    existing exact-source-identity rule.
11. **`LabResult.numeric_value`/`reference_range_low`/`reference_range_high` have no API-level
    `max_digits`/`decimal_places` constraint** matching their `Numeric(18, 4)` database columns —
    confirmed by reading `api.py`'s `LabResultSubmitRequest` (line 647) against `models.py`'s
    `LabResult` (line 474), the same class of gap DH-EX-003R already fixed for `LabPrice.amount`
    (api.py line 366). A value with more than 4 decimal places or too many integer digits reaches
    the database rather than a clean 422. This is DH-EX-007A in the follow-up backlog (§15).
12. **No exact-SHA CI-independent review (DH-EX-007R) exists** for DH-EX-007 itself — only the
    implementing commit's own self-review, per PROJECT_STATE.md's DH-EX-007 entry.
13. **DHealth Exchange has no connector wiring to OpenMRS/OpenELIS.** The two systems exist as
    separate, working stacks; nothing routes an Exchange order through them or vice versa.
14. **No real Keycloak qualification anywhere in the Exchange.** Every authorization boundary
    (`require_clinician`, `require_lab_tech`, etc.) is proven against a locally generated RSA
    keypair (`StaticKeySource`), never a real identity provider.

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
current in code: `PatientOrderResponse` (api.py:557, `extra="forbid"`) defines no result field.

**Central data boundary — `DECIDED` at ADR-042, unchanged here.** The Exchange core may persist
organization identity, patient/practitioner *references* (source-system + source-identity only),
requested-test catalog entries, sender/lab organization identity, a frozen price snapshot, order
status, the lab result value, result-delivery state, and the doctor's follow-up disposition. It may
not hold free-text clinical notes, a medication list, diagnosis history, or general encounter data.

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

1. **DH-EX-007A** — result numeric/API precision parity with PostgreSQL (§10, item 11).
2. Exchange deployment/auth wiring (no environment currently runs the Exchange API against real
   infrastructure end to end).
3. Real Keycloak qualification for the Exchange's authorization boundaries.
4. Patient reviewed/result-ready disposition (a patient-facing signal distinct from the doctor's
   own review disposition — not designed here).
5. Result/PHI read-access audit (does every read of a `LabResult` value get an audit row today, not
   just mutations? — not verified by this document).
6. Identity-first-seen audit consistency (whether `PatientReference`/`PractitionerReference`
   creation is itself audited as consistently as order/result mutations).
7. `ResultDelivery` semantics decision (§10, item 6).
8. The current pytest collection warning present across every DH-EX-* CI run on record (never
   identified or fixed; carried forward unresolved).
9. Exchange API modularization (`api.py` has grown to over 2,200 lines across seven tickets without
   a structural split).
10. Full deployed synthetic golden journey (§4) demonstrated end to end against real infrastructure.
11. Patient-selection/price-contract decision for a price-free launch UX (ADR-043's DH-EX-005
    conflict).
12. Lab result validation/release workflow (§10, item 5).
13. Assisted-digital/print/QR patient workflow (§7).
14. Provider UX speed/order-set requirements (Law 3 targets, §6).
15. DHealth Connect no-double-entry integration contract (the concrete mechanism by which a
    connector satisfies Law 2 — not designed here).

---

*This document is maintained under the same append-only discipline as `PROJECT_STATE.md` and
`CURRENT_STATE.md`: corrections are appended dated entries, not silent rewrites of a prior claim.*
