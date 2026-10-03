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

**Which screen each one is for — decided 2026-09-24 (ADR-060, closing DH-USABILITY-001).**

```text
DHealth Patient                              mobile-first
Front Desk · Clinician · Lab Staff · Admin   desktop + tablet, both supported
~390px handset                               NOT a supported primary staff-work surface
```

Tablet is a supported staff surface and must not be weakened: a workflow that works at 1280 and
breaks at 768 is a defect, not a deferral. Staff pages must still render and remain navigable on a
handset, but full handset optimisation and card-layout conversion are **not** required for MVP 1.0,
and the dense workflows — clinical documentation, result entry, result release, billing,
administration — need no dedicated 390px layout before pilot. Later tickets may add handset-friendly
staff workflows where **observed** usage justifies them.

This says nothing about patients. §7's assisted-digital strategy and law 4 below are statements
about the people receiving care, and are unaffected.

### DHealth Exchange

The shared network backbone. Responsible for transaction-scoped routing, organization boundaries,
orders, assignments, result exchange, audit, and workflow state. **It is not a longitudinal
national EMR** — ADR-042's data-minimization boundary is unchanged by this document: no free-text
clinical notes, no medication list, no diagnosis history, no general encounter data.

`CURRENT`: this is the one surface with real, tested, CI-qualified backend code today — DH-EX-001
through DH-EX-014B **[re-audited 2026-09-14]** (was "through DH-EX-007", then "through DH-EX-012A",
then "through DH-EX-012C", then "through DH-EX-013", then "through DH-EX-014A"). See §8. The result data model is
`LabOrderItem → LabResult (container) → LabObservation[]` (0..N observations per item) as of
DH-EX-012C/ADR-047, implementing the requirement real field evidence established — see
`DECISIONS.md`'s ADR-046/ADR-047 and `docs/FIELD-DIAGNOSTIC-WORKFLOW.md`. **Pre-release correction
of an entered observation is implemented** (DH-EX-013, ADR-045 Questions 1–3): an immutable
supersession chain, never a destructive overwrite. **An immutable release-version manifest and
versioned `DoctorReview` foundation is implemented** (DH-EX-014A, ADR-048 Model 3): every
explicit release binds to an exact, reconstructable observation manifest, and provider reads derive
content from that manifest rather than live state. **Post-release amendment episodes and version
releases are now implemented** (ADR-045 Questions 4–5, ADR-048 Model 3, DH-EX-014B): a lab tech prepares
corrections in a PREPARING amendment episode, and an authorized releaser publishes a new release-version
manifest with a fresh clinician review obligation. See §8 and the dated update note below.

### DHealth Clinic

DHealth-native provider software for clinics/providers that do not have an adequate existing
clinical system. MVP purpose: patient lookup / minimum registration needed for the lab workflow;
create diagnostic lab orders; see order status; receive completed results; clinician review/
follow-up disposition. **It is not initially a complete hospital information system.**

~~`NOT BUILT`: no dedicated DHealth Clinic frontend or branded application exists.~~ **CORRECTED
[DH-STATE-001, 2026-09-17]:** a native DHealth Clinic frontend now exists and is built and
component/UI-tested (`ui/src/features/clinic/`; DH-CLINIC-002/003A/003B/003C) — patient search
against existing `PatientReference` bindings, diagnostic order creation against the Exchange
catalog, durable order-intent recovery across reload/restart, order status, and results/release-
version history. It is **not runtime-deployable today**: it has no wired authentication (mounted
with no auth token; see `ui/src/App.tsx`), the Exchange Keycloak realm defines only a confidential
backend client with no browser-safe public client, and the reverse-proxy config does not route
`/exchange/v1/*` from the Clinic app's own origin. See root `STATUS.md` for the full current build/
blocked breakdown; do not duplicate that table here. The Exchange backend already exposes provider-
facing order-creation/read routes the Clinic frontend now calls (§8); a provider-scoped patient-
reference *binding* API exists (DH-EX-009, exact source-identity get-or-create) **[re-audited
2026-09-13 — was "patient lookup/registration does not exist even at the API level"]**, but full
patient lookup/search/registration by name or demographic still does not (§8, §10).

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

**2026-09-18 update (DH-LAB-001):** a dedicated DHealth Lab frontend now exists
(`ui/src/features/lab/`), `WRITTEN, TESTED, CI-QUALIFIED` — but only its first slice: authentication,
lab-organization context, the assigned-order work queue, safe order detail, and accept/reject. It is
**not** yet real-Keycloak/browser-runtime-qualified (same evidence-level boundary as DHealth
Clinic), and result entry, result validation/release, and specimen/sample-collection recording are
still `NOT BUILT` on the frontend — deliberately deferred to DH-LAB-002 through DH-LAB-004, since the
existing result-entry APIs described below are too free-form to expose directly without a
controlled result-definition/template layer first.

**2026-09-18 update (DH-LAB-002):** the frontend's specimen/sample-collection-recording gap noted
just above is now closed — `Enregistrer la collecte` (gated to `ACCEPTED` with no existing
milestone) and `Commencer l'analyse` (gated to `ACCEPTED` with a milestone present) are wired into
the order-detail panel, each behind an explicit confirmation, against the pre-existing
`record_collection`/`start_processing` backend (DH-EX-011) unchanged. Result entry and result
validation/release remain `NOT BUILT` on the frontend, still deliberately deferred to DH-LAB-003/004
for the same controlled-result-definition reason.

**2026-09-18 update (DH-LAB-003):** the controlled result-definition/template layer this file's own
DH-LAB-001 note above said was a prerequisite now exists — `LabResultTemplate`/
`LabResultTemplateObservation`, scoped to `LabOffering` (ADR-053), `WRITTEN, TESTED,
CI-QUALIFIED, MIGRATION-APPLIED-AGAINST-REAL-POSTGRESQL-IN-CI`. Native Lab admins can create
immutable, versioned templates and activate exactly one per offering; `LabOrdersPage` shows a
read-only preview of the active template's observation definitions for `IN_PROGRESS` orders. Actual
Patient result *entry* against those definitions is still `NOT BUILT` on the frontend — this ticket
deliberately stops at the controlled-definition layer plus a read-only plan, per its own scope;
DH-LAB-004 is the ticket that adds the value-entry mutation, pinned to an exact template version.

**2026-09-18 update (DH-LAB-004):** that value-entry mutation now exists, `WRITTEN, TESTED,
CI-QUALIFIED, MIGRATION-APPLIED-AGAINST-REAL-POSTGRESQL-IN-CI, CONCURRENCY-QUALIFIED-AGAINST-A-
NATIVELY-INSTALLED-REAL-POSTGRESQL-INSTANCE`. `LabOrdersPage`'s read-only preview is now a
controlled Native entry form (`NativeResultEntryForm`): each item's first write pins its
container to the offering's exact active template at that locked instant, permanently — the pin
survives any later template-version activation. The browser submits only a definition identity
and a value; the server derives every structural field from the pinned template. Completion
additionally requires every required definition to have a value. Release now requires the exact
`result_fingerprint` from a just-performed inspection — a SHA-256 digest over immutable observation
identities that changes the instant any effective observation is added or corrected — closing the
race where a releaser inspects a result set, a technician corrects a value, and a stale release
click would otherwise release a set the releaser never saw. A new "Libération" surface gives
releaser-only users (no `LAB_TECH`) the real least-privilege release queue and release-confirmation
UI DH-LAB-001 only had a truthful placeholder for. Real correction/amendment UX for a
Native-template-bound result does not exist yet — the raw free-structure API now fails closed
against a template-bound container rather than allow an unsafe workaround — deferred to DH-LAB-005.

**2026-09-18 update (DH-LAB-005):** that correction/amendment UX now exists, `WRITTEN, TESTED,
LOCALLY-QUALIFIED, CONCURRENCY-QUALIFIED-AGAINST-A-NATIVELY-INSTALLED-REAL-POSTGRESQL-INSTANCE`
(full local adapter + UI suites, real-PostgreSQL races; the actual push-triggered CI run for this
ticket is reported separately, per this ticket's own GitHub-Actions-budget-mode instruction not to
spend a second push merely to record CI status in-repo). A
Native controlled correction endpoint (browser submits only the new value; every structural field,
including the pinned template context, is derived server-side from the predecessor's own template
definition) closes the gap both pre-release and inside a post-release amendment. The full
amendment lifecycle — start, controlled correction, fingerprint-gated release, a least-privilege
amendment release queue, lab-facing exact per-version content — reuses DH-EX-014B's existing
`LabResultAmendment`/`LabResultReleaseVersion` machinery rather than inventing a parallel one; the
amendment release fingerprint gate mirrors DH-LAB-004's own original-release gate exactly, closing
the identical inspect-then-stale-release race on the amendment side. No new `LabOrderStatus` or
amendment status exists — an order stays `COMPLETED` after an amendment release, and PREPARING/
RELEASED remain the only two amendment states, with no "cancel" state of any kind. This is
explicitly **not** an "Edit Result" feature: every historical observation, release version, and
review remains independently reconstructable, no value is ever mutated in place, and no result set
is ever released without the releaser having actually inspected its exact fingerprint.
LIS/Connect integration and demo seed data remain deferred to DH-DEMO-001+.

**2026-09-30 update (DHEALTH-LAB-RECEPTION-001):** the *first* item in this section's own MVP purpose
list — "incoming work queue; accept/reject assigned orders" — is now
`WRITTEN, TESTED, REAL-POSTGRESQL-QUALIFIED, BROWSER-QUALIFIED` rather than merely written. The
correction this ticket owes the DH-LAB-001 note above is that its work was never re-qualified after
ADR-067 turned the gate into a capability: the authority, privacy and tenant claims for intake had
never been asserted at the HTTP edge, and the concurrency claims were **green for the wrong reason** —
DH-EX-006's three races all run on SQLite, where SQLAlchemy omits `FOR UPDATE` entirely, so the row
lock they exist to exercise was never taken. 23 new adapter tests at the edge, 8 against a disposable
PostgreSQL 16.15 container, 10 browser phases against real Keycloak, 12 negative controls. With the
lock removed the SQLite races **still passed** while PostgreSQL caught 7 of 25 concurrent accepts
transitioning instead of 1.

**No new capability, no new order state, no migration, and no ADR.** `lab.queue.work` already fit and
`SELECTED` already means "routed here, not yet answered", which is exactly what an incoming queue is;
inventing a `RECEIVED` or `AT_RECEPTION` state to make the interface convenient would have added a
second truth about the same fact. One behavioural change: the incoming queue sorted newest-first with
every other view, which buried the order that had been waiting longest — it now sorts by the routing
instant, oldest first, and the review views are unchanged. **No waiting duration is computed or
displayed**, the same refusal DHEALTH-FRONT-DESK-001 made on the clinic side.

Two things this did **not** resolve, stated because the ticket's own language assumed otherwise: the
laboratory decline still carries **no reason** (`reject()` is a pure state transition — recorded as an
open question alongside **D-11**, not filled), and **specimens remain entirely `D-3`** **[corrected
2026-10-01, DHEALTH-SPECIMEN-001/ADR-070: no longer entirely — a durable specimen record exists and
`D-3` is split. Its participant half (specimen types, accession numbers, labels, specimen-level
rejection, void semantics, recollection linkage) still needs a laboratory participant, and none has
been consulted]**. Human usability
is **UNQUALIFIED**: no laboratory worker has used this surface.

The Exchange backend implements the work
queue, accept/reject/start-processing, structured result entry, an explicit order-level
sample/specimen *collection milestone* (DH-EX-011), and an explicit, separately-authorized result
*release* step (DH-EX-012/ADR-044) **[re-audited 2026-09-13 — was "specimen/sample workflow and
result validation/release are explicit `NOT BUILT` gaps"]** (§8). Specimen/sample workflow remains
`PARTIAL` — DH-EX-011 is a single fact-of-recording milestone, not specimen management (no
container/volume/site/adequacy modeling exists). Result *pre-release correction* is now
`WRITTEN, TESTED, CI-QUALIFIED, REAL-POSTGRESQL-QUALIFIED` (DH-EX-013, ADR-045 Questions 1–3 —
an immutable supersession chain, LAB_TECH-only, no reason field, refuses outright once the order is
released). **The release-version manifest and versioned-review foundation ADR-048 requires is now
implemented**, `WRITTEN, TESTED, CI-QUALIFIED, REAL-POSTGRESQL-QUALIFIED` (DH-EX-014A, Model 3, CI
run `34802002919` on commit `0bb5ec9`): an explicit release binds to an exact, immutable manifest of
observation ids, and `DoctorReview` is version-capable. **Post-release amendment episodes and
version releases are now implemented** (DH-EX-014B, ADR-045 Questions 4–5, ADR-048 Model 3):
`WRITTEN, TESTED, REAL-POSTGRESQL-QUALIFIED`, resolving the pilot blocker on post-release amendments.
**[Re-audited
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

**Dated update, 2026-09-22 (DH-CONNECT-CONTRACT-001).** The *contract* a connector must satisfy now
exists — `docs/CONNECT-CONTRACT.md`, ADR-056 — together with a durable qualification model that
records whether a given build satisfied it. The *transport* does not: no FHIR client, HL7 parser,
MLLP, SFTP runtime, webhook receiver, polling loop, on-premise agent or delivery worker exists, and
nothing in this repository has contacted an external EMR or LIS. A Connect facility can now hold a
registered connector and remain **unroutable**, because `connector_operational` derives from a
runtime that does not exist. That is DH-CONNECT-AGENT-001's subject.

**Corrected the same day.** Capability evidence carries an explicit strength — `DECLARED` or
`EXERCISED` — and only exercised evidence satisfies a required production capability. Since
`ORDER_RECEIPT` cannot be driven without a transport, **no connector in this build reaches
`connector_qualified = true` at all**; the synthetic suite passes every scenario it can run and
production qualification stays incomplete. Two claims, stated separately.

**Dated update, 2026-09-23 (DH-CONNECT-AGENT-001).** The *runtime* now exists: a durable delivery
engine, a service-identity boundary, an on-premises agent, retries, dead letters and health derived
from what an agent reports. `ORDER_RECEIPT` is exercised by delivering a real order through it, so
**the synthetic laboratory suite reaches `connector_qualified = true` for the first time** — and a
Connect facility with a qualified connector *and* a healthy agent is **still unroutable**, because
routing additionally requires a qualified vendor adapter and none exists. The two paragraphs above
describe what was true before this; what changed is the runtime, not the product's availability.
DH-CONNECT-ADAPTER-001 is the ticket that would change that.

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
where appropriate; and, in a later validated phase, audio/voice.

**Status update, 2026-09-20 (DH-PATIENT-001) — supersedes this section's previous "`NOT BUILT`:
none of these channels exist yet for the Exchange (DH-EX-005's patient API has no frontend at
all)".** Four of those patterns now exist and are `TESTED`: **simple mobile web** (a single-screen,
mobile-first, French-first status view at `ui/src/features/patient/`), **large obvious actions /
minimal text** (one headline status plus at most one supporting sentence), **QR** (generated
in-process, client-side only), and **print** (a clinician-produced handoff sheet a patient with no
smartphone can carry back to the clinic or laboratory). ~~**Still `NOT BUILT`:** SMS, WhatsApp,
email, audio/voice, and any offline/PWA capability — deliberately excluded from DH-PATIENT-001 so
that a safe access path existed before any transport was wired to it. A transport added later can
carry this same access path rather than needing a second one.~~ **CORRECTED [DH-NOTIFY-001,
2026-09-24, ADR-058]:** that later transport now exists for **SMS and WhatsApp**, and it does carry
this same access path rather than a second one, exactly as predicted. It is
`WRITTEN, TESTED, REAL-POSTGRESQL-QUALIFIED` and **not `REAL-PROVIDER-QUALIFIED`** — no real
provider has been contacted, no credential exists, no message has reached a handset, and ADR-003's
gate stays `BLOCKED`. **Email, audio/voice and offline/PWA remain `NOT BUILT`** (`§28` non-goals).
One consequence is load-bearing and recorded in ADR-058: the raw access credential is returned once
and is unrecoverable, so the **release/amendment message carries no link at all** — it tells the
patient an update exists and points at the link they already hold or at their facility. Only the
message sent at issuance, while the secret is still in memory, carries a URL. See
`docs/PATIENT-NOTIFICATION.md`.

What a patient sees is **status only** — never a result value, a reference range, or any
interpretation — and the clinician's follow-up disposition is projected server-side
(`PatientOrderResponse.result_status`) so no frontend infers it. An amendment release resets that
projection to "awaiting clinician review": a disposition recorded about superseded content is never
presented as applying to the current result. See §13 for the boundary this respects and
`PROJECT_STATE.md`'s DH-PATIENT-001 entry for evidence.

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
| Provider identifies/registers patient | **PARTIAL** (was **NOT BUILT**/partial; **2026-09-17 DH-IDENTITY-001 dated update**: the native registration gap this row named is now closed) | DH-EX-009's `POST .../organizations/{provider_id}/patient-references` (exact source-identity bind) is unchanged and still exists for connector/integration use. **New (DH-IDENTITY-001/ADR-051):** `POST .../organizations/{provider_id}/patients` — a real, idempotent, provider-scoped native patient-directory registration API (display name, optional exact DOB, optional contact; server-owned identity, no demographic matching). CI-qualified (896 adapter tests) and real-PostgreSQL-runtime-qualified (concurrency, migration backfill, composite-FK tenant rejection — `tests/integration/test_dhidentity001_postgres_qualification.py`, CI-wired). Still **not** a "look up or register a patient by name/demographic across providers" API — directory scope is strictly one provider organization, by design (ADR-051 Decision 1), not a remaining gap. No real-browser demonstration of the Clinic UI's own create/list/search flow exists yet — UI-level qualification is CI/vitest only, same evidence level as the rest of DHealth Clinic. |
| Clinician creates lab order | WRITTEN, TESTED, CI-QUALIFIED | DH-EX-004, `POST /organizations/{provider_id}/orders` (`api.py:1806`). CI run `34441434606` (per PROJECT_STATE.md's DH-EX-004 FINAL CLOSURE). |
| Lab is assigned/selected | WRITTEN, TESTED, CI-QUALIFIED — **both paths now exist** (was "patient-selection path only"); **2026-09-18 DH-CLINIC-006 dated update: the provider-assignment path now requires an explicit configured route, and the Clinic UI itself now performs it** | DH-EX-005's patient-selection path, `POST /patient/order/selection` (`api.py:2176`), gated by an opaque bearer grant, exact price consent (`expected_price_ids`), and full DH-EX-003 eligibility re-evaluation — **plus DH-EX-010's provider/facility-assigned path**, `POST .../orders/{order_id}/assign-lab` (`api.py:1934`), which shares the identical `LabSelection` row but never touches price consent. ADR-043's launch-option (a) conflict is closed by DH-EX-010; DH-EX-005's own price-consent contract is otherwise unchanged. **New (DH-CLINIC-006/ADR-052):** the assign-lab path now requires an active, platform-admin-configured `ProviderLabRoute` between the ordering provider and the target lab, enforced inside `assign_by_provider`'s own locked mutation (`ProviderLabRouteNotActive` → 409) — independent of marketplace `listed` status and never requiring a price. A new order-scoped `GET .../orders/{order_id}/eligible-labs` read and a new shared `LabRoutingStep` frontend component let a clinician actually perform this assignment from the Clinic UI itself (new-order wizard + Orders/Patients history), which did not exist before this ticket — real-PostgreSQL-qualified concurrency (concurrent same-lab convergence, concurrent different-lab single-winner, route-deactivation-race resolves truthfully). |
| Lab receives order | WRITTEN, TESTED, CI-QUALIFIED | `GET /organizations/{lab_id}/lab-orders` (`api.py:2246`), DH-EX-006. |
| Lab accepts order | WRITTEN, TESTED, CI-QUALIFIED | `POST .../accept` (`api.py:2272`, DH-EX-006), `SELECTED`→`ACCEPTED`. |
| Sample/specimen workflow | PARTIAL | DH-EX-011: order-level sample/specimen collection milestone (`LabSampleCollection`, `POST .../record-collection`), required before `start-processing` may run. This is a single fact-of-recording milestone, not specimen management — no specimen type, container, volume, site, handling, storage, or adequacy modeling exists in the Exchange schema. **[2026-09-29 DHEALTH-LAB-BLOCKED-001 / ADR-068 dated update]** The *cannot-proceed* half is now built at order level: `BLOCKED` + durable `LabOrderBlock` episodes (reason, optional note, actor, instant, server-owned resume state), `POST .../block`, `POST .../blocks/{id}/resolve`, `GET .../blocks`. `WRITTEN, TESTED, REAL-POSTGRESQL-QUALIFIED, BROWSER-QUALIFIED` (34 adapter + 13 PostgreSQL + 19 UI tests, 13 browser phases, 8 negative controls; migration `0037_lab_order_blocks`). **This row stays PARTIAL** and the reason is worth stating precisely: blocking models *no specimen at all*. `SPECIMEN_UNUSABLE` says the laboratory stopped; it does not identify a tube, create an accession, or link a recollection. The specimen entity remains `D-3`, entirely open. The reason catalog is **provisional** pending a laboratory participant (`D-11`). **[2026-10-01 DHEALTH-SPECIMEN-001 / ADR-070 dated update]** A **durable specimen record now exists**: `LabSpecimen` (`exchange_lab_specimens`, migration `0039_lab_specimens`), any number per order — `lab_order_id` is deliberately **not** unique — immutable, carrying `recorded_at` and the authenticated `recorded_by_identity_id`, plus `GET .../lab-orders/{id}/specimens`. What that bought is the thing one collection row made impossible: **a recollection that preserves the earlier sample**. `WRITTEN, TESTED, REAL-POSTGRESQL-QUALIFIED, BROWSER-QUALIFIED`. **This row still stays PARTIAL**, and the reason is again worth stating precisely: the specimen carries **no type, container, tube, volume, site, accession number, storage location, quality grade or status** — `TestCatalogEntry` has no specimen-type field, so the type cannot even be *derived*, which is a real product gap and not a deferral. There is no explicit **recollection linkage**: the collection action carries no statement of cause, and inferring one from two timestamps would be fabricated provenance. `D-3` is now **SPLIT** — its architectural half is resolved, its participant half (types, accession, labels, specimen-level rejection, void semantics, recollection linkage) **still needs a laboratory participant and none has been consulted**. `D-11` is untouched. |
| Processing | WRITTEN, TESTED, CI-QUALIFIED | `POST .../start-processing` (`api.py:2341`, DH-EX-006), `ACCEPTED`→`IN_PROGRESS`. |
| Result entered | WRITTEN, TESTED, CI-QUALIFIED | DH-EX-007/DH-EX-012, `POST .../items/{item_id}/result` (`submit_lab_result`, `api.py:2419`). **Behavior changed by DH-EX-012, not merely re-cited**: even an order's last missing result no longer transitions the order — it only reports `ready_for_release`. See the next row. |
| Result validated/released | **WRITTEN, TESTED, CI-QUALIFIED, REAL-POSTGRESQL-QUALIFIED** (was **NOT BUILT**) | DH-EX-012/ADR-044: `POST .../release-results` (`release_lab_order_results`, `api.py:2988`), gated on a new `LAB_RESULT_RELEASER` role (additive alongside `LAB_TECH`), required before `LabOrder.status` becomes `COMPLETED`. This is the separate authorization step ADR-043 originally recorded as missing — it now exists, distinctly from result entry. **DH-EX-012C/ADR-047, 2026-09-13:** the underlying result domain is no longer one scalar per item — `POST .../items/{item_id}/observations` (`record_lab_observation`, `api.py:2719`) records any number of observations under an item's container, and `POST .../items/{item_id}/complete-result-entry` (`complete_lab_result_entry`, `api.py:2776`) is the one explicit, LAB_TECH-only signal that an item's entry is finished (never inferred from observation count); `POST .../items/{item_id}/result` (`submit_lab_result`, `api.py:2604`) remains as an atomic single-observation convenience call for the simple case. Provider/lab-inspection reads now return nested observations per item. Real-PostgreSQL qualified, including concurrency (observation-vs-observation, observation-vs-completion, completion-vs-release, concurrent completion) and migration upgrade/downgrade-guard evidence — see `PROJECT_STATE.md`'s DH-EX-012C entry. **DH-EX-013/ADR-045 Questions 1–3, 2026-09-13:** correcting an already-entered observation **before** release is `WRITTEN, TESTED, CI-QUALIFIED, REAL-POSTGRESQL-QUALIFIED` — `POST .../observations/{observation_id}/correct` (`correct_lab_observation`, `api.py:2836`) supersedes the currently-effective observation with a new immutable one (`supersedes_observation_id`/`superseded_at`, never a destructive overwrite), LAB_TECH-only, no reason field, refuses outright (409) once the order is released; `GET .../observations/history` returns the full chain to LAB_TECH/LAB_RESULT_RELEASER only. **DH-EX-014A/ADR-048, 2026-09-14:** a successful release now atomically creates an immutable release-version manifest (exact effective observation ids, never live state) and binds the order's `DoctorReview` to that version; provider reads derive content from the manifest, not live state; a new `GET .../release-versions` (`get_lab_order_release_version_history`, `api.py:3062`) is the foundation read for a future multi-version history UI (at most one version exists today). `WRITTEN, TESTED, CI-QUALIFIED, REAL-POSTGRESQL-QUALIFIED` — commit `0bb5ec97759665953d70eb03b0c3c4db7f7d88c0`, CI run `34802002919`, 9/9 checks independently confirmed green via the GitHub check-runs API. (Line numbers above are current as of the DH-EX-014A commit; if read after a later ticket touches `api.py` again without updating these citations, treat them as approximate, matching this document's "stale but kept for history" convention at §10 item 11.) **DH-EX-014B/ADR-048 Model 3, 2026-09-14:** post-release amendment episodes and releases are `WRITTEN, TESTED, REAL-POSTGRESQL-QUALIFIED` — `POST .../amendments` (`start_lab_order_amendment`) initiates a single active `PREPARING` episode (no reason field, per ADR-048 Decision 5); `POST .../amendments/{id}/observations/{obs_id}/correct` (`correct_lab_order_amendment_observation`) supersedes observations immutably; `POST .../amendments/{id}/release` (`release_lab_order_amendment`, `LAB_RESULT_RELEASER`-only) releases a new immutable manifest and fresh version-bound `DoctorReview`. **What is still not built:** no "not performed"/"no result obtainable" representation exists (deliberately, per ADR-047's accepted zero-observation resolution). |
| Ordering provider receives result | WRITTEN, TESTED, CI-QUALIFIED | `GET .../orders/{order_id}/results` (`get_lab_order_results`, `api.py:2594`), provider-only, only once `COMPLETED` — the gate that now depends on explicit release, not automatic completion. |
| Clinician reviews result | WRITTEN, TESTED, CI-QUALIFIED, REAL-POSTGRESQL-QUALIFIED — **2026-09-18 DH-CLINIC-007 dated update: the review workflow is now operational in the Clinic UI itself, not merely a backend contract** | The version-specific and legacy review routes (`POST .../release-versions/{v}/review/...` / `POST .../review/...`) predate this ticket, from DH-EX-014A/014B. **New (DH-CLINIC-007/ADR-048):** a provider-scoped review queue, `GET .../organizations/{provider_id}/result-reviews` — one entry per outstanding review obligation, never one per order — plus a rewritten `ClinicResultsPage.tsx` that loads a version's exact immutable content before offering any disposition control, always targets the exact displayed version (never "latest"), discloses truthfully that `FOLLOW_UP_REQUIRED` records only a workflow decision (no scheduling/contact/task engine), and re-reads authoritative state after any ambiguous or conflicting mutation rather than blindly retrying. Real-PostgreSQL-qualified for the identical-disposition race scenario (PG-R2) the pre-existing DH-EX-014A/014B suites did not isolate, alongside reconfirmation of the opposite-disposition and version-isolation races (PG-R1/PG-R3) those suites already covered. No new `DoctorReview` status, clinical interpretation, or urgency ranking was added. |
| Printable released result report | **WRITTEN, TESTED, REAL-POSTGRESQL-QUALIFIED, RUNTIME/BROWSER-QUALIFIED** (was **NOT BUILT**) | DHEALTH-LAB-REPORT-001/ADR-072, 2026-10-02. `GET .../lab-orders/{order_id}/release-versions/{id}/report` (laboratory, `lab.queue.work` or `lab.result.release`) and `GET .../orders/{order_id}/release-versions/{id}/report` (ordering clinic, `order.manage`) return **one** shared projection, built by one server-side function from `get_manifest_for_version(release_version_id)` and nothing else. **No migration** (head stays `0039_lab_specimens`), no new capability, no new role, no report table and no accession number. A printable document renders from that payload through a portal outside `#root`, so printing removes the application rather than hiding an enumerated list of chrome; the laboratory reaches it from its version history (one button per version) and the clinician from the version their panel actually fetched, never "latest". Released clinical content is exact-version immutable; the header's laboratory, clinic and patient names are **current reference data read live**, which is recorded as a limitation and tested rather than claimed away. Deliberately absent: `DoctorReview` state, visit context, notes, money, every specimen field (D-3's participant half is open), the releasing identity, patient contact, and every interpretation-shaped field — reference ranges are printed when the laboratory released them and **nothing compares a value to one**. No PDF download, no signature, no certification, no accreditation. **D-8 unchanged:** a report view is a clinical read and is **not** success-audited, exactly as the exact-version content reads it sits beside are not. Evidence: 35 HTTP-edge tests, 6 real-PostgreSQL tests (including a report read while a Version 2 release is blocked mid-flight on the order's row lock), 40 UI unit tests, 19 real-browser phases including print media and two generated PDFs. **Human usability is not claimed** — no laboratory technician and no clinician has looked at this document. |
| Patient receives status/follow-up communication | **PARTIAL** — was **NOT BUILT**; **2026-09-20 DH-PATIENT-001 dated update: patient *access* now exists; patient *notification* still does not** | **New (DH-PATIENT-001):** the first DHealth Patient frontend (`ui/src/features/patient/`) — a single-screen mobile-web status view authorized solely by DH-EX-005's pre-existing order-scoped opaque grant, reached through a QR code or link whose credential travels in the **URL fragment** and is scrubbed from the address bar on ingestion, never persisted in any browser storage, and never sent to Keycloak (the patient route returns before any `ExchangeAuthProvider` exists). A clinician-side `PatientAccessHandoff` issues/revokes/rotates that access and produces a printable sheet, so a patient with no smartphone still has a physical artifact to present — Law 4 (§6), not a convenience. Backend additions: a provider-authorized metadata read (`GET .../orders/{order_id}/patient-access`, `{active, expires_at, access_mode}`, never the raw token or its digest) and a server-owned `PatientOrderResponse.result_status` projection (`NOT_READY` / `AWAITING_CLINICIAN_REVIEW` / `NO_FOLLOW_UP_REQUIRED` / `FOLLOW_UP_REQUIRED`) resolved from the order's *latest released version* and that version's own review obligation — so an amendment release correctly reverts the patient's view to awaiting-review rather than showing a disposition about superseded content. `WRITTEN, TESTED` (18 adapter + 92 frontend tests, 18 negative controls executed); no migration (head stays `0025_native_result_binding`). **Why this is PARTIAL, not done:** this is *pull* access — the patient must be handed a link or QR. ~~**Still NOT BUILT:** any *push* notification (WhatsApp/SMS/email/voice transport), and detailed result delivery to patients (deliberately excluded — patients see status only, never result values).~~ **[2026-09-24 DH-NOTIFY-001/ADR-058 dated update]** *Push* notification for **SMS and WhatsApp** is now `WRITTEN, TESTED, REAL-POSTGRESQL-QUALIFIED`: a release or amendment creates a durable obligation **inside the release transaction**, a dispatcher drains it, and a signed provider receipt is the only writer of `DELIVERED`. **It is not `REAL-PROVIDER-QUALIFIED`, and this row therefore stays PARTIAL** — no real provider has been contacted, no credential exists, no message has ever reached a handset, and ADR-003's gate remains `BLOCKED` (pilot gate 5 uncleared). **Email and voice remain `NOT BUILT`** (`§28` non-goals). Detailed result delivery to patients remains excluded **by policy, not by absence**: the notification carries no clinical content at all, enforced by templates that have no substitution slot for any. One consequence recorded in ADR-058: because the raw access credential is unrecoverable after issuance, the release/amendment message carries **no link** — it points at the link the patient already holds, or at their facility. `ResultDelivery` remains untouched and still never advances past `PENDING`: re-confirmed by grep as of 2026-09-20, and DH-PATIENT-001 deliberately did **not** make viewing a status page mean `DELIVERED` — that semantic remains a separate undecided product question (§10). `ResultDeliveryRepository.mark_delivered`/`mark_failed` still exist and are still never called. |

> **[Read this first — 2026-09-26, DHEALTH-STATE-001.]** Three of the four claims in the paragraph
> immediately below are **no longer true as present state**. They were true when written and are
> kept rather than rewritten. The corrections are at the end of this section, under the 2026-09-26
> dated update.

~~**RUNTIME-QUALIFIED, for any of the above: none.**~~ Every qualification on record for DH-EX-001
through DH-EX-014B is CI/unit/integration-test-level against SQLite and/or a disposable PostgreSQL
instance (re-confirmed **[DH-STATE-001, 2026-09-17]** — was "through DH-EX-012A", before that
"through DH-EX-007"; this line was previously out of step with §8's own citation range above, which
had already been updated). ~~**Real Keycloak qualification: NOT RUN
for any Exchange ticket**~~ (DH-EX-008 wired the deployment but could not qualify live, per §10 item
14 below). **Manual live/browser demonstration: NOT RUN** — *still true, see gate 3*. **No
production deployment exists** — *still true*. **No pilot has begun** — *still true*.

**2026-09-18 dated update (DH-DEMO-001):** none of the above changes. What is new is a way to
*observe* this golden journey end to end without a real hospital's EMR/LIS: a dedicated Compose
project (`docker-compose.native-demo.yml`) runs the real Keycloak/adapter/DHealth-UI/Caddy code
paths — never a parallel mock stack — without ever waiting on OpenMRS/OpenELIS, seeded with
deterministic synthetic-only data via `adapter/src/daher_adapter/exchange/demo_seed.py` and a P0
reset-safety boundary that is real-PostgreSQL-qualified (three independent facts refused/allowed
correctly, 4 clean runs). **This does not upgrade any row above to RUNTIME-QUALIFIED**: this
sandbox's Docker cannot pull real image blobs from the CDN, so the demo stack has never actually
booted anywhere yet. See `PROJECT_STATE.md`'s DH-DEMO-001 entry and `STATUS.md` §D for the full
evidence matrix.

**2026-09-25 dated update (DHEALTH-NATIVE-DEPLOY-001):** two sentences of the note above are now
false and are corrected rather than left standing. (1) "this sandbox's Docker cannot pull real image
blobs from the CDN, so the demo stack has never actually booted anywhere yet" — it has: in CI
(DH-DEMO-001's own boot job) and, from DHEALTH-NATIVE-MVP-1.0 onward, locally, with images fetched
out-of-band and loaded because the blob CDN is still unreachable. (2) The Native topology is no
longer defined by a file called *demo*. `docker-compose.native.yml` is the canonical Native
deployment — PostgreSQL, Keycloak, the adapter, the UI, Caddy, and nothing else, requiring no
OpenMRS, OpenELIS or MariaDB service, image or hostname — and the demo is now a thin overlay on it
that adds demo users, a separate database name and the seed authority.

**What that changes about the rows above: nothing.** This is deployment shape, not product
capability. No row moves to a higher qualification level because of it. What *did* move, in
DHEALTH-NATIVE-MVP-1.0 and this ticket, is that the golden journey of §4 has now been walked in a
real Chromium against real Keycloak on this topology (12 tests: 7 journey phases plus 5
authorization refusals), including the *entered-is-not-released* boundary, and survived a stack
restart with its order, release and review intact. That is still **synthetic data, one operator, no
clinician**: DH-USABILITY-001 remains `INTERNAL WORKFLOW REVIEW — NOT USER-QUALIFIED`, and nothing
here is production-deployed, pilot-ready, clinically live, or disaster-recovery qualified.
`docs/ARCHITECTURE.md` §25 describes the two deployment tracks.

**2026-09-25 dated update (DHEALTH-NATIVE-RECOVERY-001):** **pilot gate 4 — a *performed* restore
drill rather than a script that exists — is now cleared for the Native/Exchange track.** It is the
first of `STATUS.md` §H's gates to clear, and the oldest unmet pinned P0 on this side of the
repository.

What was actually done: a populated synthetic Native deployment was backed up to one encrypted
archive, **every durable volume was then deleted** (`down -v` — PostgreSQL holding both databases,
the adapter's file-backed audit trail, Caddy's data), a clean deployment was rebuilt from source
control and proved *not* to contain the source state, the archive was restored into it, PostgreSQL
tenant isolation was re-proved over TCP with real passwords, the **same staff identity from before
the backup** signed in through real Keycloak in a real Chromium, the **same order, release version
and clinician review** were found through the real product, and a **new complete clinical workflow**
was then carried out on the restored deployment. Seven negative controls were executed and reverted.

**What this changes about the rows above: nothing.** Recovery is not a clinical capability, and no
row moves. One row's *deployment* reality did change on the way through, though: this file's
DH-NOTIFY-001 notification work was **unreachable in the canonical Native deployment** until today —
`docker-compose.native.yml` passed the adapter no notification environment at all, so the Meta
webhook route the previous ticket published at the edge could only ever answer 403. That block now
exists, off by default. The feature's qualification level is unchanged and ADR-003's gate is
untouched: **WhatsApp stays `BLOCKED`, pilot gate 5 is not cleared**, and the durable notification
state this drill recovered was created on the `fake` provider, which has no network code at all.

**Still not cleared, and not claimed:** off-site recovery (no archive has left the host, and Native
has no uploader — if gate 4 is read as requiring off-site too, it is not cleared), scheduled backup
for Native, restore at realistic data volume, real Keycloak token-validation qualification (gate 1),
manual live demonstration (gate 3), real-provider messaging (gate 5), clinical sign-off (gate 6),
human usability (DH-USABILITY-001 remains `INTERNAL WORKFLOW REVIEW — NOT USER-QUALIFIED`), and any
production deployment. The maternity/reference-connector track keeps its own recovery status: its
2026-08-13 drill stays partially verified and still owes a re-run. Detail: `docs/NATIVE-RECOVERY.md`.

**2026-09-26 dated update (DHEALTH-STATE-001):** a documentation reconciliation, correcting this
section's own headline rather than adding capability. **Three sentences above the table are now
false as present state and one is not.** The headline block still reads *"**RUNTIME-QUALIFIED, for
any of the above: none**"*, *"**Real Keycloak qualification: NOT RUN for any Exchange ticket**"* and
*"**Manual live/browser demonstration: NOT RUN**"*. Corrected, in order:

- **"RUNTIME-QUALIFIED: none" — no longer true.** This section defines `RUNTIME-QUALIFIED` as
  *demonstrated against a real running system, e.g. real PostgreSQL or a real browser*. Several
  rows now meet that definition on both counts.
- **"Real Keycloak qualification: NOT RUN for any Exchange ticket" — no longer true.** See §10
  item 14, now struck and resolved.
- **"Manual live/browser demonstration: NOT RUN" — still true**, and the distinction is
  load-bearing: the golden journey ran in **automated** Chromium. Pilot gate 3 says *manual* and is
  **not cleared** (root `STATUS.md` §H).
- **"No production deployment exists. No pilot has begun." — still true**, both of them.

Also superseded by this: the 2026-09-25 RECOVERY-001 paragraph immediately above lists *"real
Keycloak token-validation qualification (gate 1)"* among what is not cleared. **Gate 1 cleared on
2026-09-26**; gates 3, 5 and 6 in that same sentence remain uncleared exactly as written, and so
does everything else it names. Gate 2 (Native browser authentication + same-origin routing) also
cleared on 2026-09-26.

Which golden-journey steps meet `RUNTIME-QUALIFIED` under this section's own definition — patient
identification (read path only), order creation, lab assignment/routing, lab receipt, acceptance,
the collection milestone, processing, result entry, release, provider receipt of the result, and
clinician review — is set out surface by surface in root `STATUS.md` §C's 2026-09-26 banner, which
also names the surfaces that remain jsdom-only: native patient **registration**, the
correction/amendment lifecycle, the DHealth Patient status screen, and platform administration.

**`HUMAN DECISION REQUIRED` — not decided here.** Whether the per-row `Status` cells in the table
above should now be re-stamped `RUNTIME-QUALIFIED` is left open. The evidence supports it under this
section's own rubric, and the 2026-09-25 DEPLOY-001 note deliberately took the more conservative
position that *"no row moves to a higher qualification level"*. Rather than silently overrule a
previous ticket's judgment inside a reconciliation pass, both readings are recorded and the choice
is Liban's. **The rows themselves are unchanged by this ticket.**

## 9. Explicit out-of-scope list

DHealth MVP 1.0 does **not** require:

full inpatient hospital information system · complete longitudinal national EHR · pharmacy ·
radiology/PACS · insurance claims · patient payments · consumer price marketplace · AI diagnosis ·
AI clinical interpretation · clinical decision support · bed management as an Exchange MVP
requirement · maternity workflows as an Exchange MVP requirement · appointment scheduling · full
accounting/billing · every analyzer integration · nationwide rollout.

**[2026-10-03 DHEALTH-APPOINTMENTS-001 / ADR-073 dated update — a listed item above has now been
built, and the list is deliberately left standing.]** **`appointment scheduling`** in the list above
now exists in the product: a clinic can record that a named patient is expected at a named facility
at a date and time, move it, cancel it, and turn an arrival into the existing clinic Visit. See
ADR-073 and §C of `STATUS.md`.

**The entry is not removed, and that is the point.** It says MVP 1.0 does not *require* appointment
scheduling, which remains true — this ticket did not establish that it does. What was built is the
narrow, connected-workflow half named in ADR-073 §2: an expectation and an arrival handoff. There is
no reminder transport, no no-show state, no room or resource model, no capacity or double-booking
rule, no recurrence, no waitlist, no patient self-booking, and no Connect/FHIR projection — so the
marketplace-shaped "appointment scheduling" this list excludes is still excluded, and §9's sentence
below about existing work not being deleted for being off the critical path is the sentence that
applies.

**Whether this moves onto the MVP critical path is Liban's call and has not been made.** It is
recorded as a Human Decision in `PROJECT_STATE.md` under DHEALTH-APPOINTMENTS-001 rather than
settled here: a ticket that builds a thing does not get to promote it into the MVP definition on its
own authority, and an agent reading this list later must not infer from the code's existence that
the scope decision was taken.


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
4. **No specimen/sample management.** Unchanged as a *gap*; **split by severity 2026-09-27
   (DHEALTH-NATIVE-OPERATING-PRODUCT-001, `docs/NATIVE-OPERATING-PRODUCT.md` §16).** Two different
   things were bundled here. (a) ~~**An accepted order that cannot proceed has no honest state** — a
   haemolysed tube leaves the order in a queue state claiming progress that is not happening, and
   rejecting the whole order is the wrong tool for one bad tube. That is small, order-level, and a
   **P0 switch blocker**.~~ **RESOLVED 2026-09-29, DHEALTH-LAB-BLOCKED-001 / ADR-068:** `BLOCKED`
   is a real order state carrying a structured reason, the worker who set it, the instant, and the
   state it resumes to, held in durable per-occurrence episodes that are never overwritten or
   deleted. A blocked order cannot advance or be released; the ordering clinician is told it
   stopped and given the reason key, and nothing more. **The four-reason catalog is provisional**
   and its confirmation with a laboratory participant is open (`D-11`); notably **no
   equipment/analyser reason exists yet**. (b) **A first-class specimen entity** — multi-specimen orders, accession
   identifiers, per-specimen received/rejected timestamps, recollection linkage — is **P1**, and
   whether Launch-1 needs it is `D-3`, a laboratory-workflow question for a real laboratory
   **[2026-10-01, DHEALTH-SPECIMEN-001/ADR-070: the *architectural* half of that question was
   answered by the operator directing the ticket and is now built. The *laboratory-workflow*
   question this line names — which types, which identifiers, what a label carries, when a specimen
   is invalid as a specimen — is exactly the half that remains, and remains for a participant]**
   participant. Container types, volumes, storage rules, aliquots, stability windows,
   chain-of-custody and analyser interfacing remain out of scope. Original text follows.
   DH-EX-011 added an order-level collection
   *milestone* only (`LabSampleCollection`) — no specimen type, container, volume, site, handling,
   storage, or adequacy entity/state exists in the Exchange schema.
5. ~~No result validation/release step, distinct from result entry.~~ **RESOLVED [re-audited
   2026-09-13].** DH-EX-012/ADR-044 added exactly this: an explicit `LAB_RESULT_RELEASER`-gated
   release step, structurally separate from result entry, required before `COMPLETED`. **What
   remains open as a direct consequence, recorded as new items 15–16 below:** no correction/
   amendment path exists either before or after this new release step (ADR-045), and the underlying
   result-per-item data model this release step operates on is itself confirmed not final for real
   panel-based workflows (ADR-046).
6. ~~**`ResultDelivery.status` semantics undecided.**~~ **RESOLVED 2026-09-27 by ADR-064
   (DHEALTH-RESULT-DELIVERY-001) — open since 2026-09-12, and ADR-043's own deadline ("a future
   ticket must decide before any delivery mechanism is implemented") had already been missed, because
   Connect's delivery runtime shipped first.** The answer: **`ResultDelivery` is DEPRECATED /
   COMPATIBILITY-ONLY and owns nothing.** No new code writes or reads it; a Connect adapter must never
   touch it; the table, enum and every historical row are preserved; **no migration was required.**
   Canonical owners — released result → `LabResultReleaseVersion`; Native availability → **derived,
   no stored state**; clinician review → `DoctorReview` (version-scoped); external provider-system
   delivery → `ConnectorDeliveryObligation`/`ConnectorDelivery`; patient messaging →
   `PatientNotification`. The decisive argument was **structural**: `ResultDelivery` is unique per
   *order*, but since ADR-048 the fact is per *release version* (v1 delivered while v2 is owed), so it
   is the wrong shape for its own stated meaning rather than merely a duplicate. Original text and the
   2026-09-27 sharpening both follow, unedited.

   Still undecided, and **sharpened 2026-09-27
   (DHEALTH-NATIVE-OPERATING-PRODUCT-001, `docs/NATIVE-OPERATING-PRODUCT.md` §23)** with a finding
   this entry did not have: **two different things in this repository are now called "delivery", and
   the Exchange one is dead.** `connect/delivery.py`'s `DeliveryRepository` is live (called from
   `connect/inbound.py` and `connect/runtime_api.py`); the Exchange's `ResultDeliveryRepository` has
   **no production caller**, its `result_delivered` audit event type is never emitted, and for a
   Native order the row is created `PENDING` and **stays `PENDING` forever** — so a reader concludes
   results are undelivered. Of the seven facts that could be meant, six already have a home; only
   "viewed by a human" is unmodelled, and this is not that. **Two options:** retire it from the
   Exchange schema, or rename it `ProviderSystemDelivery` and create it only for Connect-bound orders.
   **`D-7`, `HUMAN DECISION REQUIRED`, and it must be settled before `DH-CONNECT-ADAPTER-001` builds
   against it** — otherwise the first real adapter resolves the ambiguity by accident. Original text
   follows. `DELIVERED` could mean
   available-to-provider, sent-to-connector, received-by-connector, presented-to-user, or
   acknowledged-by-user. Not chosen. See ADR-043.
7. ~~**No patient communication transport.** Unchanged. No SMS/WhatsApp/email code exists for the
   Exchange; ADR-003's real-credential WhatsApp gate (maternity-era) is unrelated infrastructure
   that has never been wired to the Exchange.~~ **PARTIALLY RESOLVED [DH-NOTIFY-001, 2026-09-24,
   ADR-058].** SMS and WhatsApp code now exists for the Exchange — domain, content policy, durable
   queue, dispatcher, provider adapter and a signed delivery-receipt webhook — and ADR-003's gate is
   now wired to it rather than unrelated to it. **The gate itself is unchanged and still
   `BLOCKED`:** no real provider has been contacted and no message has ever reached a handset, so
   this is resolved as *code* and unresolved as *capability*. Email and voice remain `NOT BUILT`
   (`§28` non-goals).
8. **Sensitive-content policy per channel** (SMS/WhatsApp/lock-screen/voice/shared family phones)
   is undecided. **Sharpened, not resolved [DH-NOTIFY-001, 2026-09-24]:** ADR-058 makes every
   channel carry the *same nothing* — no clinical content of any kind, enforced by templates with
   no substitution slot — which **sidesteps** the per-channel question rather than answering it. If
   a future channel or message type should carry more on one channel than another, that is still an
   open decision with nothing decided for it here.
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
14. ~~**No real Keycloak qualification anywhere in the Exchange.** Unchanged in substance — DH-EX-008
    wired the deployment configuration (env contract, realm import, routing) but could not qualify
    against a live Keycloak in its own sandbox (registry image unreachable); every authorization
    boundary (`require_clinician`, `require_lab_tech`, etc.) remains proven only against a locally
    generated RSA keypair (`StaticKeySource`), never a real identity provider.~~
    **RESOLVED [re-audited 2026-09-26, DHEALTH-STATE-001].** The canonical Native deployment
    (`docker-compose.native.yml`, ADR-061) points the adapter at the real `dhealth-exchange` realm's
    issuer and JWKS endpoint, so `JWKSKeySource` — the production path, not `StaticKeySource` —
    validated tokens a real Chromium obtained from a real Keycloak Authorization Code + PKCE login,
    and the authorization boundaries then made real decisions on those identities, refusing five
    hostile cases (`ui/e2e/native/02-authorization.spec.ts`). Negative controls NC-NATIVE-1 and
    NC-NATIVE-3 confirm the suite can see those boundaries break. Root `STATUS.md` §H gate 1 clears
    on this. **Scope, kept narrow:** synthetic data on one workstation at `BASE_DOMAIN=localhost`
    with Caddy's internal CA; the *unit* suite still uses `StaticKeySource`, appropriately; and
    Exchange surfaces the journey never exercised (post-release amendment, platform administration)
    have no real-Keycloak evidence of their own.
15. ~~**New [DH-FIELD-001, 2026-09-13]. No result correction (pre-release) or amendment
    (post-release) path exists.** `LabResult` is insert-only, package-wide, with no entering-actor
    column and no revision metadata (confirmed by DH-EX-012A's audit). ADR-045 records the decision
    packet distinguishing the two problems; **neither is decided or implemented.** See ADR-045.~~
    **PARTIALLY RESOLVED [re-audited 2026-09-13, DH-EX-013].** ADR-045 Questions 1–3 are accepted
    and implemented: a `LabObservation` may be corrected before release via an immutable
    supersession chain (`supersedes_observation_id`/`superseded_at`), LAB_TECH-only, no reason
    field, structurally refused once the order is released. Every new observation (entry or
    correction) now carries `recorded_by_identity_id`; pre-existing rows stay `NULL`. ~~**Post-release
    amendment (ADR-045 Questions 4–5) remains fully OPEN and unimplemented** — deferred to
    DH-EX-014, unaffected by this change.~~ **RESOLVED [re-audited 2026-09-17, DH-EX-014B]:**
    post-release amendment (ADR-045 Questions 4–5) is accepted and implemented under ADR-048
    Model 3 — see §3 and §8 above, `DECISIONS.md`'s DH-EX-014B section, and `PROJECT_STATE.md`'s
    DH-EX-014B entry. This item's OPEN framing was already stale before this pass — §3 and §8 had
    been updated at DH-EX-014B time but this line was missed; see root `STATUS.md` §5 for the
    contradiction audit that caught it. See `DECISIONS.md`'s ADR-045 addendum and
    `PROJECT_STATE.md`'s DH-EX-013 entry for the pre-release-correction half of this item.
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
7. ~~`ResultDelivery` semantics decision (§10, item 6). Unchanged.~~ **RESOLVED 2026-09-27, ADR-064:
   deprecated / compatibility-only, owns nothing, no migration. See §10 item 6.**
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
15. ~~DHealth Connect no-double-entry integration contract (the concrete mechanism by which a
    connector satisfies Law 2 — not designed here). Unchanged; sharpened by field evidence that at
    least one real participating laboratory already runs software of some kind (§3 DHealth Connect,
    `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Finding 7) — still no specific LIS, protocol, or interface
    claimed.~~ **RESOLVED as a contract [DH-CONNECT-CONTRACT-001, 2026-09-22]** — `docs/CONNECT-
    CONTRACT.md` and ADR-056 define the canonical message set, capability model, identity scoping,
    failure semantics and fail-closed mapping rule a connector must satisfy, and
    `docs/CONNECT-QUALIFICATION.md` defines the evidence that proves one did. Law 2 is enforced
    *as a qualification requirement*, not a slogan: the `NDE1-no-double-entry` scenario fails a
    connector whose workflow requires a human to enter the same result in two systems, whatever
    else it passes. **Still no specific LIS, protocol, or interface claimed, and no connector
    exists** — the transport remains unbuilt (DH-CONNECT-AGENT-001), and the harness runs against a
    synthetic subject that contacts nothing.
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

**DH-EX-014B update (2026-09-14):** Post-release amendments are now implemented under ADR-048
Model 3. The lab can prepare immutable amendment corrections and an authorized releaser can publish
a new release-version manifest with a fresh clinician review obligation; provider history and
exact-version reads are manifest-backed. SQLite unit/API tests (136 passed) and real-PostgreSQL
qualification (33 passed across 6 test suites, migration 0018->0019 upgrade/downgrade clean, and
both downgrade guards verified) pass. Real Keycloak qualification, manual live/browser demonstration,
and production deployment remain unrun, and the Exchange is not pilot-ready by this note.

**DH-MVP-POLISH-001 update (2026-09-20): "DHealth Clinic" and "DHealth Lab" are product *names*, not
separate applications.** §3's product family still describes them as distinct software, and until
this ticket the frontend agreed: `ClinicApp` and `LabApp` were separate React applications, reached
through two application-switching buttons, each loading `/me` for itself. They are now one
authenticated staff application (`ui/src/features/staff/`). Which workflows a person sees is derived
from their active organization and their roles in it, so a clinician sees ordering and review, a
technician sees the work queue, someone with both memberships switches establishment rather than
switching product, and nobody is asked to know in advance which product their role belongs to. The
names remain accurate as descriptions of the two halves of the workflow and of who each serves; they
no longer describe two things to install, open, or log into. `?app=clinic` and `?app=lab` survive as
URL aliases onto the one application. Full shape: `docs/ARCHITECTURE.md` §20; decisions and their
consequences: ADR-054.

Three things this note does **not** claim. The maternity/reference-connector track is untouched and
still reached at `?app=maternity` (ADR-042/043), deliberately outside the staff product's navigation.
~~No facility-setup or administration surface exists yet — there is no `Réglages` navigation item,
because DH-MVP-ADMIN-001 owns that and a navigation entry leading to "Bientôt" is worse than its
absence.~~ **[Corrected 2026-09-26, DHEALTH-NATIVE-ACCESS-001: one narrow facility-owned
administration surface now exists — `Équipe et accès`, gated on `ORG_ADMIN`. There is still no
`Réglages` item and no facility-setup surface; that remains DH-MVP-ADMIN-001's, and the
"navigation-entry-leading-to-Bientôt" rule is why the new section arrived only once it did something.
See §10's admin gap entry for what a facility still cannot do.]**
**[Further corrected 2026-09-27, DHEALTH-NATIVE-OPERATING-PRODUCT-001: the full switch-ready
administration scope is now specified — organization profile, facility-owned services and prices,
result templates, offerings, routing visibility — in `docs/NATIVE-OPERATING-PRODUCT.md` §19, together
with what must never reach facility staff (database hosts, OIDC internals, connector queues, compose
settings). Specified, not built.]** And nothing here changes §6's laws
or §13's boundaries: colour in this product carries
workflow responsibility (a step is complete, something is owed, this stopped) and never clinical
interpretation, no result value is coloured by its magnitude, and no clinical value — typed, drafted,
released, or amended — is persisted to `localStorage`, `sessionStorage`, `IndexedDB`, a cookie, or a
URL on any surface. The consequence of that last rule is stated in the UI rather than hidden: a typed
result value is lost by a page refresh and by a *failed* token refresh, and the field says so.

This ticket added no backend route, no schema change, and no migration (head remains
`0025_native_result_binding`). It is a frontend and documentation ticket. Real Keycloak qualification,
a live browser walkthrough, and any real-patient pilot remain unrun on this track, and nothing in
this note makes the Exchange pilot-ready.

**DH-MVP-ONBOARD-001 update (2026-09-21): how a facility joins the network, and the four facts that
are not the same fact.** §3's product family describes what DHealth Clinic and DHealth Lab do; it
never said how a real clinic or laboratory becomes a participant. That control plane now exists
(`docs/FACILITY-ONBOARDING.md`, ADR-055, `docs/ARCHITECTURE.md` §21): a platform operator enrols a
facility, attaches its first administrator, records whether it will use DHealth directly or keep its
existing software, and — for the latter — records what that software is and what DHealth actually
knows about its interfaces.

The distinction this introduces, and which §8's build table should be read against from now on:
**enrolled ≠ configured ≠ operationally ready ≠ production-routable.** An organization existing says
nothing about whether orders can be sent there. Production routing to a Native facility requires real
operational authority — a provider needs an active clinician, a laboratory needs both an active
technician and an active release authority, because entry and release are separate authorities
(ADR-044) and a lab without a releaser can accept work it can never finish. Losing that staffing
blocks new routing without deleting the configured relationship, and restoring it makes the same
route usable again.

**DHealth Connect is enrollable, not integrated.** A Connect facility can be fully enrolled, fully
discovered, have a verified interface, and still be unable to receive a single production order,
because no connector to any external EMR or LIS exists and none has been qualified. That is enforced
rather than merely stated: `READY_FOR_ROUTING` is unreachable for a CONNECT facility, and no API
request or UI action overrides it. The three levels — a partner's *reported* interface, DHealth's
*verified* interface, and a *qualified* connector — are kept apart in the data and in the wording.
Transport, mapping, credential storage and qualification are DH-CONNECT-CONTRACT-001's subject and
none of them is built here.

This ticket adds migration `0026_facility_enrollment` and no backend route that moves clinical data.
Law 2 (no double entry) remains binding: a missing connector is never to be solved by asking staff to
enter results twice. No real patient data is required at any point in onboarding — discovery records
whether a source system *has* a patient identifier namespace or a release event, never any patient's
data.

---

## 16. The Native clinic gained a visit (DHEALTH-CLINIC-VISIT-001, 2026-09-27)

Added rather than rewriting §4 or §8: the golden journey this document defines is the **exchange**
journey (a provider orders, a laboratory completes, a patient reads), and that journey is unchanged.
What changed is that the Native provider side now has a clinical thread to hang an order on.

**What exists now.** A clinician can record that a patient arrived, why they came in the patient's own
words, that they were taken into consultation, a free-text note that may be corrected (each correction
an append-only revision, earlier ones retained and visible), which analyses were ordered *during that
consultation*, and that the encounter is finished — and can see that history the next time the patient
comes in. `Visites` is a staff section; `exchange_visits`/`exchange_visit_notes` are real tables; the
lifecycle is `CHECKED_IN` → `IN_PROGRESS` → `COMPLETED`, plus `CANCELLED` from `CHECKED_IN` only.
ADR-065 and `docs/NATIVE-OPERATING-PRODUCT.md` §38 carry the detail.

**Where it touches this document's laws.**

| Law | Effect |
|---|---|
| 1 — no forced marketplace | Unaffected. A visit routes nothing and prices nothing. |
| 2 — no double entry | **Strengthened in one direction, and a new obligation in another.** A clinician ordering during a visit does not re-enter the patient: the ordering flow is handed the patient and the visit. But a facility that keeps consultations in an existing EMR must not be asked to write them into DHealth as well — so the Native visit is for facilities using DHealth *natively*, and a connector track facility's consultations stay where they are. Nothing in this ticket asks anyone to type a consultation twice. |
| 3 — faster than what it replaces | **Still a validated target, not a current claim.** No clinician has used this surface and nothing has been timed against the paper register or the EMR it would replace. §32 of the operating-product spec is the plan; it has not been executed. |
| 4 — no digital-literacy requirement for care | Unaffected. A visit is staff-facing; no patient interaction changed, and the assisted-digital paths of §7 are untouched. |

**§10 gaps: none closed, one narrowed.** Item 6 (`ResultDelivery` semantics) was closed by ADR-064,
not this ticket. This ticket closes nothing here and opens nothing new, but it does narrow the
practical reach of the "no clinical thread" observation in §8: the gap was never listed as a numbered
item, because §8 described the implementation as an exchange core, which it was.

**What this does not make true.** A place to write a clinical note is not clinical sign-off. Every
structured clinical field — vitals, examination templates, diagnosis coding (**D-5**) — still requires
the clinical lead, and the order set still does. **Pilot-ready remains distinct from clinically live**,
and nothing here is authorization to use DHealth on a real patient. No real patient data was used at
any point; every fixture, seed and qualification record is synthetic.

**DHEALTH-ACCESS-CAPABILITIES-001 update (2026-09-28): the two sentences above that say "roles" are
now wrong, and this is the correction.** DH-MVP-POLISH-001's note says which workflows a person sees
is "derived from their active organization and their roles in it", and DHEALTH-NATIVE-ACCESS-001's
correction says `Équipe et accès` is "gated on `ORG_ADMIN`". Both described the truth when written.
Neither is true now. Staff authority on the Native track is **capabilities**; a role is a *template*
that grants a set of them, and a facility administrator can grant or withdraw an allowed capability
from one individual without inventing a combination role. Navigation is derived from the same
server-resolved capability set the backend enforces, not from role names, and `Équipe et accès` is
gated on `staff.access.manage`. `ORG_ADMIN` still holds it, so nobody's access changed — that is
measured, not assumed: a 10-persona × 16-boundary authority matrix recording exact status codes is
identical either side of the change. ADR-067 and `docs/NATIVE-OPERATING-PRODUCT.md` §40 carry the
detail; `docs/ARCHITECTURE.md` §31 carries the shape.

**Where it touches this document's laws.** None of the four move. This is an authorization refactor
with a facility-facing administration surface; it routes nothing, prices nothing, and no patient
interaction changed. Law 3 is untouched and still a validated target here too — no facility
administrator has used the capability sheet and nothing has been timed.

**§10 gaps: none closed.** The admin gap entry stands. This is also **not** an answer to the Front
Desk authority question (**D-1**) **[Corrected 2026-09-30, DHEALTH-FRONT-DESK-001 / ADR-069: the Front Desk half is now **resolved**, and the answer was not a role. Reception authority is three independently grantable capabilities -- `patient.directory.read`, `patient.register` and the new `visit.check_in`. No `FRONT_DESK` template was invented then and none was invented now; nothing gates on one, and `ORG_ADMIN` plus a direct `visit.check_in` grant runs reception with no role change. The prediction in this paragraph -- that answering it later would be a choice of which capabilities to grant rather than a change to authorization code -- held, with one exception worth naming: `require_clinician` was narrowed from six capabilities to four, because its any-of included the two patient capabilities a receptionist legitimately holds.]**: no `FRONT_DESK` template was invented, because choosing what such
a role may do is a product decision, and the ticket that created the seam explicitly forbade
guessing it. What changed is that answering it later is a choice of which capabilities go in a
template, not a change to Billing, Visits or patient-directory authorization code. **D-8** (auditing
successful sensitive reads) is also still open: capability *mutations* are audited, reading a
capability sheet is not.

**What this does not make true.** A capability model is not an IAM product: the catalog is twelve
code-defined capabilities, there is no attribute-based policy engine, no expression language and no
per-user screen designer, and system templates are not editable this release. Nothing here is
Exchange-track work and nothing here makes the Exchange pilot-ready. **Pilot-ready remains distinct
from clinically live.** No staff member's real access was changed; every organization, membership
and person used in this work is synthetic, and no real patient data was used.
