# DHealth Native — the switch-ready operating product

**DHEALTH-NATIVE-OPERATING-PRODUCT-001, 2026-09-27.** A product specification, not an
implementation. It answers one question: *what would an outpatient clinic or a diagnostic laboratory
need in order to stop relying on its current daily operating software and use DHealth instead?*

**Nothing in this document is built by it.** Where it names a module, a screen or a field, that is a
specification for a later ticket to build, and §31's ticket sequence is how. Appearing in this
document is not authorization to implement — the same rule DH-USABILITY-001 §43 and the queued brief
this supersedes both stated.

**On the name.** The specification work was queued on 2026-09-24 under `DHEALTH-NATIVE-MVP-1.0`, an
identifier that a *different*, completed piece of work — the real-browser golden-journey qualification
— also took on 2026-09-25. `STATUS.md` §I carried the collision to Liban as `HUMAN DECISION REQUIRED`.
**It is resolved here:** the completed qualification keeps `DHEALTH-NATIVE-MVP-1.0`, and this
specification is `DHEALTH-NATIVE-OPERATING-PRODUCT-001`. No history is rewritten.

---

## 0. Summary — read this if you read nothing else

**Switch-ready means a facility can run a complete working day inside DHealth without reaching for
the software it used before.** Not "has many features". Not "deployed". Not "clinically approved".
§2 defines it precisely and §3 lists what it explicitly does not mean.

**DHealth Native today already does the hard part.** The diagnostic loop — patient → order → routing
→ lab acceptance → collection → processing → structured result entry → authorized release → clinician
review — is built, real-browser-qualified, and stronger than most of what it would replace: controlled
result templates, immutable corrections, entry and release as separate authorities, immutable release
versions, version-scoped review. §5 inventories it honestly.

**What is missing is not diagnostics. It is the working day around them.** A clinic using DHealth
today can order a test and read a result, but cannot record *why the patient came*, cannot see *who is
waiting*, and cannot *take payment*. Those are not features a mature EMR happens to have; they are the
acts a clinic performs dozens of times a day.

### The five P0 switch blockers

Each is tied to a concrete operating failure, not to what comparable products contain (§28).

| # | Blocker | The concrete failure without it |
|---|---|---|
| **P0-1** | **Visits / Encounters** (§11) | A returning patient's record is a list of lab orders with no clinical thread. Nothing records why they came, what was found, or what to do next. A clinician cannot answer "what happened last time?" |
| ~~**P0-2**~~ **CLOSED 2026-09-30, ADR-069** | **Arrival and a waiting list** (§9, §14) | Nobody can tell who is in the waiting room. The clinician has no queue and must be told verbally who is next. The clinic keeps a paper register alongside DHealth — double entry, and Law 2 broken. |
| **P0-3** | **Minimal billing: invoice, payment, receipt** (§18) | A private clinic or laboratory that cannot issue a receipt cannot take money. This is not a convenience; it is the transaction the facility exists on. DHealth has **no** invoice, payment or receipt concept at all. |
| **P0-4** | **An honest "cannot proceed" state on an accepted order** (§16) | A haemolysed tube today has nowhere to be recorded. The order stays `ACCEPTED`/`IN_PROGRESS` while nothing happens, the queue entry lies, and nobody monitors it. Rejecting the whole order is the wrong tool for one bad tube. |
| **P0-5** | **Patient-directory migration** (§20) | A clinic with four thousand existing patients cannot begin. Re-registering each one at the desk on arrival is the only path, which means months of degraded operation. |

**Deliberately *not* P0:** appointments/calendar (this market is walk-in-first — P1), barcode labels
(P1, handwriting is current practice), first-class specimen entities (P1 — see §16 for why the small
part is P0 and the LIMS-shaped part is not), reports (P2), offline/PWA (P2), historical clinical
migration (P3 — keep the legacy system read-only).

### The single hardest constraint, and it is not a feature

**Three of the four human roles in the working hypothesis have no backend authority, and cannot be
UI personas.** Every provider-side non-admin capability in DHealth is gated on `CLINICIAN` —
including reading the patient directory — and every lab-side one on `LAB_TECH`. So:

```
    Front Desk        needs patient read + arrival write, and NOT clinical authority
                      -> today the only role that can read patients is CLINICIAN
                      -> giving Front Desk CLINICIAN grants order creation and result review

    Lab Reception     same shape on the laboratory side against LAB_TECH

    Cashier           needs patient identity + amounts, and NOT clinical content
```

This is **one** `BACKEND AUTHORITY DECISION REQUIRED` (§28, D-1), not three, and it shapes the whole
sequence: the first ticket is deliberately **clinician-only** so that it does not have to answer it,
and the Front Desk ticket carries the decision. Anywhere this document shows a Front Desk column, it
is describing what that role *would* need, never a mapping that exists.

### What to build first

**`DHEALTH-CLINIC-VISIT-001` — Visits / Encounters, clinician-only.** Full brief in §32. It is first
because everything else in the clinic depends on it: a waiting list is a list of visits, a bill is
for a visit, a chart is a history of visits. It is scoped so that a Front Desk role can be added later
without a schema change.

---

## 1. Purpose and audience

This document is the product contract for DHealth Native's operating product. It is written to be read
by a product owner, an engineer, a designer, a future implementation agent, and a clinic or laboratory
stakeholder — **without any of them reverse-engineering the repository**. Where it states what exists,
that was verified by reading code during this ticket, and §5 says at what evidence level.

It is deliberately *not*: a UI design, a database schema, a project plan with dates, or user research.

---

## 2. What "switch-ready" means

> **Switch-ready:** every routine operational act that an outpatient clinic or a diagnostic
> laboratory performs in a normal working day has a supported path inside DHealth, so the facility can
> stop depending on its previous operating software for daily work.

Four tests a candidate feature must pass to be called necessary for switch-readiness:

1. **Daily.** It happens in a normal working day, not in an edge case. A quarterly regulatory export
   is not a switch blocker.
2. **Unavoidable.** The facility cannot choose not to do it. Taking payment is unavoidable; running a
   turnaround-time report is not.
3. **No honest workaround.** Either there is no workaround, or the workaround is double entry — which
   Law 2 forbids as a *normal* workflow (it remains acceptable for disaster continuity, §24).
4. **Ownership exists.** Some named role performs it and some named role receives the result of it
   (§27). A status nobody monitors is not an operational workflow.

### What switch-ready is NOT

These are four different claims and this document keeps them apart:

| Claim | Meaning | Status |
|---|---|---|
| **Switch-ready** | A facility could operate its day in DHealth | **This document defines it. It is not yet met.** |
| **Production deployed** | Running for a real facility on real infrastructure | No, on either track |
| **Clinically approved** | A clinical lead has signed off on clinical content | No — and `AGENTS.md` requires that sign-off separately |
| **Human usability qualified** | Real front-desk, clinician and lab participants have used it | No — DH-USABILITY-001 remains `INTERNAL WORKFLOW REVIEW — NOT USER-QUALIFIED` |
| **Regulatory certified** | Any external certification | No, and none is claimed anywhere |

Reaching switch-ready does **not** clear a pilot gate. Three of six remain uncleared (`STATUS.md` §H).

---

## 3. Product boundaries

### The hybrid position is preserved

DHealth Native and DHealth Connect are complementary, and this specification must not define Native
in a way that forces a facility to switch.

```
   OPTION A   facility already has good software
                  -> DHealth Connect, keep their system

   OPTION B   facility has weak or no software
                  -> DHealth Native, replace it

   OPTION C   facility wants to replace one side only
                  -> DHealth Clinic + their existing LIS via Connect
                  -> their existing EMR + DHealth Lab via Connect
```

> **Keep what works. Replace what does not.**

### Target of this specification

**In:** outpatient clinics; diagnostic laboratories. Small to medium, Djibouti-first.

**Out of switch-ready scope, explicitly** (§29 is the full list): inpatient admission, bed
management, nursing medication administration, inpatient medication charts, operating theatre, ICU,
full pharmacy, PACS, full radiology workflow, insurance claims, hospital ERP, national longitudinal
EHR, national MPI, clinical AI of any kind, and any diagnosis or treatment recommendation.

**A 300-bed hospital replacing its whole HIS is not the target of this document.**

### The maternity / reference-connector track is untouched

`ui/src/features/maternity/` and `ui/src/features/admission/` are the OpenMRS/OpenELIS
reference-connector track (ADR-042/043), reached at `?app=maternity`. They are **out of switch-ready
scope and are not discarded** — `AGENTS.md`'s rules still bind anything that touches them. This
document does not propose changing them, and inpatient admission appearing there is not evidence that
inpatient workflow is in Native's scope.

---

## 4. Product laws

The four existing laws (ADR-043, `docs/MVP-1.0.md` §6) bind unchanged:

1. **No forced marketplace.** Day-one DHealth is a connected workflow, not price-shopping.
2. **No double entry.** Integrate the existing system or use DHealth natively — never both by hand.
3. **Faster than what it replaces.** Routine actions must be low-friction, against validated targets.
4. **No digital-literacy requirement for care.** Assisted-digital, non-smartphone paths always exist.

This specification adds eight operating-product constraints:

5. **Role-scoped by default.** A screen shows what this role needs, not everything.
6. **Organization-scoped by default.** Every read and write is scoped to one facility, server-side.
7. **Ask only for information DHealth actually needs.** The native patient form asks for a name and
   nothing else. That is the standard new screens match, not regress from.
8. **Derive facts the system already knows.** If DHealth can compute it from data it holds, it must
   not ask. Turnaround time is derivable; do not add a field for it.
9. **Administrative authority does not imply clinical authority.** `ORG_ADMIN` is not a clinician;
   `PLATFORM_ADMIN` is not a universal clinical user; a releaser is not an administrator.
10. **Patient safety outranks UI convenience.** Where the two conflict, the safer flow wins, and the
    reason is written down.
11. **Physical workflow matters as much as software state.** A tube, a queue and a printed slip are
    part of the product.
12. **Assisted-digital operation must remain possible.** Someone must be able to do the digital part
    *for* a patient who cannot.

**Do not create a screen because conventional EMRs have one.** Every screen in §30 names the
operational question it answers.

---

## 5. Existing capability inventory

Verified by reading code during this ticket. Evidence levels:

- **BUILT + RUNTIME-QUALIFIED** — exercised in a real browser against the real stack
- **BUILT, NOT HUMAN-QUALIFIED** — works, but no real participant has used it
- **PARTIAL** — real but materially incomplete
- **BACKEND ONLY** — API/domain exists, no screen
- **NOT BUILT** — neither
- **OUT OF SCOPE** — deliberately excluded

### DHealth Clinic (provider side)

| Capability | Status | Notes |
|---|---|---|
| Staff sign-in, real Keycloak, Authorization Code + PKCE | BUILT + RUNTIME-QUALIFIED | `JWKSKeySource` against the real realm |
| One staff application, navigation derived from real authority | BUILT + RUNTIME-QUALIFIED | ADR-054; `staffSections.ts` |
| `Aujourd'hui` work relay | BUILT, NOT HUMAN-QUALIFIED | `ClinicHomePage` + `WorkflowRelay` — **exists**; it is not a dashboard |
| Patient directory: search, open, register | BUILT + RUNTIME-QUALIFIED (register: read path yes, native registration form **not** browser-exercised) | `Patient`, ADR-051, provider-scoped |
| Diagnostic order wizard: catalog select, review, durable commit | BUILT + RUNTIME-QUALIFIED | `LabOrderIntent` → `LabOrder`, idempotency-key discipline |
| Lab routing by the clinician (order-scoped eligible labs) | BUILT + RUNTIME-QUALIFIED | DH-EX-010/ADR-052; requires an active `ProviderLabRoute` |
| Released-result read, version-scoped | BUILT + RUNTIME-QUALIFIED | Immutable `LabResultReleaseVersion` |
| Clinician review with follow-up disposition | BUILT + RUNTIME-QUALIFIED | `DoctorReview`, version-scoped |
| Patient-access handoff: issue, QR, printable sheet, revoke | BUILT + RUNTIME-QUALIFIED (print not paper-tested) | Order-scoped opaque grant |
| **Visits / encounters** | **NOT BUILT** | No `Visit`, `Encounter` or `Appointment` model exists — verified |
| **Arrival / check-in / waiting list** | ~~**NOT BUILT**~~ **BUILT + RUNTIME-QUALIFIED 2026-09-30** (ADR-069, migration `0038_visit_check_in`) | `visit.check_in` + the `visits/waiting` projection. `CHECKED_IN` is the waiting fact; no new state. **Waiting *duration* is deliberately not built** — §38 |
| **Appointments / calendar** | **NOT BUILT** | — |
| **Billing: invoice, payment, receipt** | **NOT BUILT** | No `Invoice`, `Payment`, `Receipt`, `Charge` or `Service` model exists — verified |

### DHealth Lab

| Capability | Status | Notes |
|---|---|---|
| Assigned-order work queue | BUILT + RUNTIME-QUALIFIED | `require_lab_tech` |
| Accept / reject an assigned order | BUILT + RUNTIME-QUALIFIED | Order-level |
| Order-level collection milestone | BUILT + RUNTIME-QUALIFIED | `LabSampleCollection` — one row per order, `recorded_at` only |
| Start processing | BUILT + RUNTIME-QUALIFIED | Requires the collection fact; never inferred |
| Template-driven structured result entry | BUILT + RUNTIME-QUALIFIED | `LabResultTemplate`, version-pinned, ADR-053 |
| Gated entry completion per ordered item | BUILT + RUNTIME-QUALIFIED | — |
| Pre-release correction (immutable supersession) | BUILT, NOT HUMAN-QUALIFIED | ADR-045; not browser-exercised |
| Explicit release by a separate authority | BUILT + RUNTIME-QUALIFIED | `LAB_RESULT_RELEASER`, ADR-044, entry ≠ release |
| Post-release amendment + re-release | BUILT, NOT HUMAN-QUALIFIED | ADR-048; not browser-exercised |
| **Lab reception / patient presents at the lab** | **NOT BUILT** | — |
| **Specimen adequacy, rejection, recollection** | **PARTIAL** | Order-level `BLOCKED` is built (P0-4, ADR-068) and a **durable specimen record** now exists (ADR-070): recollection is representable — a later collection creates a new `LabSpecimen` and never overwrites the earlier one. Still absent: rejection of an *individual* specimen as a specimen-level fact, and any explicit recollection *linkage* (the workflow input cannot say why a second sample was taken) — **D-3's participant half** |
| **Multi-specimen orders** | **PARTIAL** | The schema no longer lies: `exchange_lab_specimens.lab_order_id` is **not** unique, so an order may hold any number of specimens (ADR-070). No multi-container *workflow* and no per-specimen test mapping, so "blood taken, urine pending" is still not expressible — that needs specimen types, which are **D-3's participant half** |
| **Specimen labels / barcodes** | **NOT BUILT** | The specimen has a durable internal id suitable for a later label ticket to reference. It is **not** an accession number and nothing renders it as one (ADR-070 §5) |
| **Lab billing** | **NOT BUILT** | — |
| **Walk-in / self-pay laboratory patient** | **NOT BUILT** | Every order today originates from a provider organization |

### DHealth Patient

| Capability | Status | Notes |
|---|---|---|
| Order-scoped status view, no account | BUILT, **screen not browser-exercised** | Grant replayed at the API only |
| Credential in URL fragment, scrubbed, never stored | BUILT + RUNTIME-QUALIFIED | SEC-13 |
| QR + printable handoff | BUILT, print not paper-tested | Law 4's assisted path |
| WhatsApp / SMS notification | BUILT, **NOT REAL-PROVIDER-QUALIFIED** | ADR-058/059; ADR-003's gate is still `BLOCKED` |

### Administration

| Capability | Status | Notes |
|---|---|---|
| `Équipe et accès` — list, deactivate, reactivate, add/remove role | BUILT + RUNTIME-QUALIFIED | DHEALTH-NATIVE-ACCESS-001 |
| Last-active-administrator invariant | BUILT + RUNTIME + PG-QUALIFIED | ADR-063 |
| Platform facility enrollment, readiness, routing, connectors | BUILT, **NOT browser-exercised** | Platform scope, not facility-owned |
| Lab offerings | **BUILT + RUNTIME-QUALIFIED** | ADR-071. `Analyses réalisées` on `Services et tarifs`, gated on `facility.configure`. A laboratory can see which analyses it accepts and start or stop accepting each one; disabling stops NEW orders and changes nothing historical |
| Lab result templates | BACKEND ONLY | APIs exist; no facility-facing screen. **Unchanged by ADR-071**, which deliberately did not fold result structure into availability |
| Released result report | **BUILT + RUNTIME-QUALIFIED** | ADR-072. A printable report for one **exact** released version, reachable from the laboratory's version history (one button per version) and from the clinician's result panel (the version on screen, never "latest"). Prints as a document, not as the application page. No PDF download, no signature, no accession number, no specimen fact |
| Lab pricing | BACKEND ONLY | `LabPrice` exists; no screen; see §18 |
| **Organization profile, contact details, hours** | **NOT BUILT** | — |
| **Facility-owned service/price administration** | **NOT BUILT** | — |

### Exchange and Connect

| Capability | Status |
|---|---|
| Organization / membership / role model, live per-request authorization | BUILT + RUNTIME-QUALIFIED |
| Transactional security audit for mutations | BUILT + RUNTIME-QUALIFIED |
| Anti-enumeration refusal shapes (404 vs 403) | BUILT + RUNTIME-QUALIFIED |
| Canonical Native deployment, destructive restore drill | BUILT + RUNTIME-QUALIFIED (pilot gate 4 cleared) |
| Connector contract, qualification evidence, runtime agent | BUILT, **no real vendor adapter exists** |
| `ResultDelivery` | **DEPRECATED / COMPATIBILITY-ONLY** (ADR-064) — owns nothing; see §23 |

---

## 6. Staff personas and the role model

### The hypothesis, and its status

DH-USABILITY-001 §B.6 recorded four human roles as an **untested hypothesis**, not a conclusion:

```
   Front Desk        Clinician        Lab Staff        Admin
```

This specification keeps the hypothesis and adds what the queued brief could not: **what it costs in
backend authority.**

### What exists today

| Backend role | Where it is checked | What it grants |
|---|---|---|
| `CLINICIAN` | `require_clinician` | Patient directory read/write, order creation, released-result read, review. **The only provider-side non-admin role.** |
| `LAB_TECH` | `require_lab_tech` | Lab queue, accept/reject, collection, result entry. **The only lab-side working role.** |
| `LAB_RESULT_RELEASER` | `require_lab_result_releaser` | Release, and pre-release inspection. Never implied by `LAB_TECH` (ADR-044). |
| `ORG_ADMIN` | `require_org_admin` | `Équipe et accès`. Never clinical (ADR-044, law 9). |
| `dhealth-platform-admin` | Keycloak realm role | Network administration. Never clinical. Bound by ADR-063's last-admin invariant. |

### The mapping, honestly

| Human persona | Backend today | Verdict |
|---|---|---|
| **Clinician** | `CLINICIAN` | Exists. A real mapping. |
| **Lab Staff (analysis)** | `LAB_TECH` | Exists. A real mapping. |
| **Lab Staff (release)** | `LAB_RESULT_RELEASER` | Exists, and stays separate — ADR-044 is a safety boundary, not role fragmentation. |
| **Admin** | `ORG_ADMIN` | Exists. A real mapping. |
| **Front Desk** | **none** | **No backend authority. Not a UI persona.** See below. |
| **Cashier / billing** | **none** | **No backend authority.** May or may not be distinct from Front Desk — part of D-1. |
| **Lab Reception** | **none** | Same problem on the lab side. |

### Why Front Desk cannot be a UI persona

`require_clinician` guards the patient directory itself. So a front-desk worker who must find a
patient by name has exactly two options today:

```
   give them CLINICIAN   -> also grants order creation, released-result read, clinician review
                            (over-grants clinical authority to a non-clinical job — breaks law 9)

   give them nothing     -> they cannot find the patient, so they cannot do their job
```

Neither is acceptable. **A new backend authority is genuinely required**, and introducing it means
changing which authority guards patient-directory reads — a change to a shipped security surface.

> **BACKEND AUTHORITY DECISION REQUIRED — D-1 (§28).** Do not add a role to satisfy this document.

**Consequence for sequencing:** `DHEALTH-CLINIC-VISIT-001` is deliberately **clinician-only** so it
does not depend on D-1, and is designed so a Front Desk role can be added afterwards without a schema
change (§32).

### Naming on screen

French-primary, using the vocabulary that already exists (`roleLabels.ts`): `Clinicien`, `Analyse`,
`Libération`, `Administration`. A new front-desk role would need a label; proposing one is part of D-1,
not of this document.

---

## 7. Role visibility matrix

For every module, what each role sees. **`FD` (Front Desk) and billing columns describe a role that
does not exist yet** — they are requirements for D-1, not current behaviour.

Legend: **FULL** · **LTD** limited, minimum data only · **NONE** · **CAP** capability-dependent

| Module | Front Desk *(D-1)* | Clinician | Lab Staff | Releaser | Org Admin |
|---|---|---|---|---|---|
| **Aujourd'hui** | LTD — arrivals, waiting, who is not yet seen | FULL — own queue, reviews owed | FULL — lab work owed | LTD — release queue | LTD — access exceptions only, no clinical |
| **Rendez-vous** (calendar) | FULL — book, cancel, no-show | LTD — own schedule | NONE | NONE | NONE |
| **Patients** | LTD — name, DOB, contact, *no clinical* | FULL | NONE | NONE | NONE |
| **Visites** | LTD — exists, status, reason *(see below)* | FULL | NONE | NONE | NONE |
| **Ordres** | NONE | FULL | CAP — only orders assigned to this lab | LTD — orders pending release | NONE |
| **Résultats** | NONE | FULL — released versions for own patients | CAP — own lab's entry work | FULL — pre-release inspection + release | NONE |
| **Réception** (lab) | n/a | NONE | FULL | LTD | NONE |
| **Prélèvements** (samples) | n/a | LTD — status only, on own orders | FULL | LTD | NONE |
| **Libération** | NONE | NONE | NONE | FULL | NONE |
| **Facturation** | FULL — amounts, payment state, receipt | LTD — whether settled, never amounts-as-gate | LTD — lab's own charges | NONE | LTD — totals, not clinical |
| **Équipe et accès** | NONE | NONE | NONE | NONE | FULL |
| **Paramètres établissement** | NONE | NONE | NONE | NONE | FULL |
| **Rapports** | LTD — operational counts | LTD — own clinical workload | LTD — lab operational | NONE | LTD — facility totals |

### The minimum-data rules behind the matrix

These are the point of the matrix; the grid alone is not a specification.

- **Front Desk sees no released-result content.** Checking a patient in does not require knowing
  their haemoglobin. Front Desk may see *that* a result is available (so they can tell the patient to
  wait for the clinician), never the values.
- **Front Desk sees the visit's *reason* but not its *note*.** The reason is what the patient told
  the desk; the clinician's note is not. If a facility disagrees, that is a policy decision, not a
  default.
- **Billing sees amounts, not narratives.** A cashier needs patient identity, what service was
  provided, how much, and whether it is paid. Never the clinician's note, never result values, never
  a diagnosis. §19.
- **Lab staff never see the provider's longitudinal chart.** They see the order assigned to them, the
  patient-identity attributes needed to confirm the right person, and nothing about unrelated visits.
- **A releaser does not gain administration**, and an `ORG_ADMIN` does not gain release. Both
  directions are already enforced and must stay that way (ADR-044, law 9).
- **`ORG_ADMIN` sees no clinical content anywhere.** `Équipe et accès` loads no patient, order or
  result today, and that is a property to preserve.
- **Clinician sees released versions, not drafts.** Entry ≠ released is a safety invariant, not a
  convenience (ADR-044).

---

## 8. DHealth Clinic — target operating model

### Information architecture

Evaluated, not assumed. Current shipped navigation is `Aujourd'hui / Patients / Ordres / Résultats`
plus `Équipe et accès` for an administrator.

| Section | Verdict | Why |
|---|---|---|
| **Aujourd'hui** | **Keep** | Already exists and answers "what needs me now". |
| **Patients** | **Keep** | Exists. |
| **Visites** | **Add — P0** | The missing clinical thread. §11. |
| **Ordres** | **Keep** | Exists. Becomes visit-linked. |
| **Résultats** | **Keep** | Exists. |
| **Facturation** | **Add — P0** | §18. |
| **Rendez-vous** | **Add — P1, not P0** | This market is walk-in-first; a clinic can operate on walk-ins and a paper diary for bookings without double-entering clinical data. §10. |
| **Rapports** | **Add — P2** | Operational counts only. §25. |
| **Paramètres établissement** | **Add — P1** | Facility profile and price list. §19. |
| ~~`Réglages`~~ | **Do not add as a container** | A settings *application* is not a requirement. Named screens, each doing something. |

**The rule that governs this list:** a section that does nothing is a promise the product has not
kept. Nothing is listed-and-disabled, and nothing is marked *Bientôt*.

### The questions a Clinic specification must answer

**What does staff open first in the morning?** `Aujourd'hui`, showing today's expected arrivals,
who has arrived and is waiting, visits in progress, and results awaiting this clinician's review.
Different roles see different Today views (§13).

**How does a walk-in arrive?** Front Desk finds or registers the patient, then creates a visit
directly in `CHECKED_IN` — there was never an appointment, and forcing one would be a fiction. Until
D-1 exists, the clinician does this.

**How does a scheduled patient arrive?** Their appointment already exists; Front Desk marks arrival,
which creates or advances the visit to `CHECKED_IN` and links it to the appointment.

**How is an existing patient found safely?** By name prefix and date of birth, provider-scoped, with
identity shown prominently. **No fuzzy matching** — ADR-051's boundary holds, and identical
demographics must never silently mean the same person (§20).

**How is a new patient registered?** The existing native form, which asks for a name and nothing
else. That is the standard (law 7).

**How is a patient checked in?** One action that creates the visit or moves it to `CHECKED_IN`,
recording who did it and when.

**How does a clinician know who is waiting?** `Aujourd'hui` lists visits in `CHECKED_IN` for this
organization, oldest first. This is P0-2 and it is the reason a waiting list is not merely a nicety.

**What creates a visit?** Arrival, in practice. A visit may also be created directly by a clinician
for a patient already in front of them. §11.

**What does a clinician document?** Reason for visit, a free-text clinical note, orders, and a
follow-up disposition. **Nothing structured beyond that** without clinical-lead sign-off (§12).

**How are diagnostic orders tied to that visit?** By an explicit, nullable `visit_id` on the order.
It cannot be inferred: a `Patient` may hold several `PatientReference` rows and orders attach to the
reference, so "the patient's orders" is not "this visit's orders". §32.

**How do previous visits appear?** The patient chart is a reverse-chronological list of visits, each
expandable to its note, orders and released results. §14.

**How do released results return into the patient's workflow?** They already do — the review queue
exists. Once visits exist, a released result also appears against the visit that ordered it, so a
clinician opening last week's visit sees what came back.

**How does follow-up appear?** `DoctorReview`'s disposition already carries
`FOLLOW_UP_REQUIRED` / `NO_FOLLOW_UP_REQUIRED`, version-scoped. Follow-up surfaces on
`Aujourd'hui` and on the patient chart. No new domain needed — law 8.

**How does the visit become complete?** The clinician completes it explicitly. It does not complete
itself when a result arrives: results can arrive days later, and a visit that reopens itself is a
status nobody owns.

**How is payment represented?** An invoice for the visit, its lines, payments against it, and a
receipt. §18.

**What gets printed and given to the patient?** Receipt; lab handoff slip with the QR; optionally a
visit summary. §21.

**What if the patient cannot use DHealth Patient?** The printed sheet with the QR *is* the assisted
path, and it already exists. Law 4 and law 12.

---

## 9. Front Desk

A workflow, not a screen — and the one that most needs D-1 resolved.

```
   patient arrives
        |
   find (name + DOB)  --or--  register (name only)
        |
   scheduled?  ---yes--->  mark arrival, link appointment
        |
        no
        |
   create visit directly (walk-in)
        |
   CHECKED_IN  ------------------->  appears on clinician's Aujourd'hui
        |
   [optional] take payment / open invoice
        |
   clinician takes over
```

**What Front Desk may see:** patient name, date of birth, contact, the visit's existence and status,
the reason as the patient stated it, whether a result is available (not its content), and billing
state.

**What Front Desk may not see:** the clinician's note, result values, any diagnosis, and any other
facility's patients.

**Why DOB is included:** wrong-patient risk. Two patients with the same common name is the ordinary
case, and a desk worker confirming identity needs a second attribute. This follows law 10.

---

## 10. Rendez-vous (calendar) — P1, and small

> **[BUILT 2026-10-03, DHEALTH-APPOINTMENTS-001/ADR-073 — and the state list below is superseded.]**
> `exchange_appointments` exists with **two** states, `SCHEDULED` and `CANCELLED`, not the four
> proposed here. `ARRIVED` is not a state: arrival is `visit_id IS NOT NULL` plus the linked visit's
> own status, so attendance has one authoritative home. `NO_SHOW` is not a state either — it is an
> operational judgement nobody has defined (who records it, how late is late, whether a telephone
> call changes it), and ADR-073 records it as an open Human Decision rather than inventing the
> policy. The field list is also narrower than proposed: **no clinician and no note**, both recorded
> as open questions. Everything in "Defer to later, explicitly" below remains deferred and unbuilt.

**Not P0.** A walk-in-first clinic can operate without it: bookings can live in a paper diary while
*clinical* data lives in DHealth, which does not violate Law 2 because no clinical information is
entered twice. That makes it materially incomplete, not blocking.

When built, the smallest useful version:

- appointment: patient, date/time, clinician (optional), status, note (short, optional)
- states: `BOOKED` → `ARRIVED` (creates/links the visit) · `CANCELLED` · `NO_SHOW`
- walk-ins coexist and never require a retroactive appointment

**Defer to later, explicitly:** recurring appointments, automated reminders, rooms and resources,
scheduling optimisation, wait-time prediction, patient self-booking. None is needed to operate a day.

---

## 11. Visits / Encounters — P0-1

The first major product gap and the first implementation ticket.

### The minimum domain

Scrutinised field by field against law 7.

| Field | Keep? | Why |
|---|---|---|
| `patient_id` | **Yes** | The `Patient` directory record (ADR-051). |
| `provider_organization_id` | **Yes** | Tenant scope, law 6. |
| `clinician_identity_id` | **Yes** | Who owns this visit. Nullable until a clinician takes it. |
| `status` | **Yes** | §12. |
| `reason_for_visit` | **Yes** | Short free text. The one thing that makes a visit list legible. |
| `clinical_note` | **Yes** | Free text, clinician-entered, append-only revisions. §12. |
| `started_at` / `completed_at` | **Yes** | Server clock. Derives duration and TAT — law 8. |
| `visit_type` (walk-in / scheduled) | **Derive** | From whether an appointment is linked. Law 8. |
| `appointment_id` | ~~**Yes, nullable**~~ **INVERTED 2026-10-03 (ADR-073 §3)** | The link lives on the *appointment* (`Appointment.visit_id`, nullable + UNIQUE), not here. A walk-in is the common arrival and needs no column at all, so `exchange_visits` keeps no field that is NULL for most of its rows. `exchange_visits` gained only an additive `UNIQUE(id, provider_organization_id)`, so the booking's composite foreign key can make the database refuse a cross-clinic link. |
| `follow_up` | **No — derive** | `DoctorReview` already carries the disposition. Law 8. |
| occupation, religion, ethnicity, marital status, next of kin, intake questionnaire, structured history | **No** | No workflow in this specification needs them. Law 7. |
| vitals / measurements | **No — not in the first ticket** | Real clinical content requiring clinical-lead sign-off. §12. |
| diagnosis code | **No — not in the first ticket** | §12 and D-5. |

### What a visit is not

Not an EMR note system. Not a billing document (the invoice references it). Not a container for
everything that ever happened to a patient.

---

## 12. Visit state model, and clinical documentation

### States

```
            (booked appointment)
                    |
   PLANNED ---------+
      |             |
      |        [arrival]
      v             v
   CANCELLED    CHECKED_IN  ------> NO_SHOW   (expected, never arrived)
                    |
             [clinician takes it]
                    v
              IN_PROGRESS
                    |
              [clinician completes]
                    v
               COMPLETED
```

Five states plus `CANCELLED`. Each earns its place:

| State | Why it is necessary | Who may enter it |
|---|---|---|
| `PLANNED` | A booked appointment that has not arrived. Only exists with a calendar; omit until §10 ships. | Front Desk *(D-1)* |
| `CHECKED_IN` | **The waiting list is this state.** P0-2 exists because of it, and was closed by it (ADR-069). | `visit.check_in`; clinician |
| `IN_PROGRESS` | Distinguishes "waiting" from "being seen" — the difference the waiting room cares about. | Clinician |
| `COMPLETED` | Terminal. The clinician says so; nothing auto-completes. | Clinician |
| `NO_SHOW` | Operationally real and needed for `PLANNED` to be honest. | Front Desk *(D-1)* |
| `CANCELLED` | From `PLANNED` or `CHECKED_IN`. Never from `IN_PROGRESS` — that is a completed-with-note. | Front Desk *(D-1)*; clinician |

**Rejected as decoration:** `ARRIVED` separate from `CHECKED_IN`; `READY_FOR_CLINICIAN`; `TRIAGED`
(no triage in scope, §29); `BILLED` (billing is its own state, §18); `CLOSED` distinct from
`COMPLETED`.

> **[SUPERSEDED IN PART 2026-10-03, DHEALTH-APPOINTMENTS-001/ADR-073.** The table above lists five
> states plus `CANCELLED`; the shipped lifecycle is **four** — `CHECKED_IN`, `IN_PROGRESS`,
> `COMPLETED`, `CANCELLED` — and that is now permanent rather than a first instalment.
>
> `PLANNED` is retired because a booked appointment is **not an encounter**. It lives in
> `exchange_appointments`, and representing it as a visit status would give every patient who booked
> and never attended a visit row in their clinical history — after which "a visit happened" stops
> meaning "somebody was in the building" for the waiting list, the one-active-visit invariant, the
> patient chart and billing alike. `NO_SHOW` is retired with it, since this table's own justification
> for it was "for `PLANNED` to be honest", and because no no-show policy exists to record.
>
> `CANCELLED` is therefore reachable from `CHECKED_IN` only. Cancelling a *booking* is a different
> act on a different row and creates, cancels and touches no visit. See ADR-073 §1.]**

**Until D-1 resolves, the clinician performs every transition.** The model does not change when a
Front Desk role arrives — only who may call each transition.

### Clinical documentation boundary

DHealth **may record**: reason/symptoms as stated, history entered by a professional, measurements
entered by a professional, clinician notes, orders, a clinician-entered diagnosis, a
clinician-entered plan, clinician-entered follow-up instructions.

DHealth **must never**: diagnose, recommend treatment, interpret a result, generate a plan, or triage.
This is `AGENTS.md`'s global rule and ADR-043 §13, and it is absolute.

**For the first ticket: free-text note only.** Any structured clinical field — vitals with units and
ranges, HPI, review of systems, examination templates — is clinical content and must be marked
*needs sign-off from clinical lead* before it is specified, never invented by an agent or a designer.

**Diagnosis.** A clinician-recorded free-text diagnosis is inside the boundary. A *code system*
(ICD-10 or other) is a clinical and operational choice with real consequences — that is **D-5** in
§28, not a decision for this document. A system-generated diagnosis is permanently out of scope.

---

## 13. Aujourd'hui / worklist

It exists (`ClinicHomePage`, `LabHomePage`, `WorkflowRelay`). It answers **"what work requires me
now?"** and must not become a metrics dashboard.

| Role | What Today shows |
|---|---|
| Front Desk *(D-1)* | Expected arrivals today; who has arrived and is waiting; who has not arrived |
| Clinician | Patients waiting (`CHECKED_IN`); own visits `IN_PROGRESS`; released results awaiting own review; follow-ups owed |
| Lab Staff | Orders assigned and not yet accepted; awaiting collection; in processing; result entry incomplete |
| Releaser | Orders whose entry is complete and release is owed; amendments awaiting release |
| Org Admin | Access exceptions only — e.g. a member with no role. **No clinical content.** |

**Excluded:** totals-for-their-own-sake, month-to-date revenue, staff leaderboards, any number that
does not name a next action.

---

## 14. Patient chart

One workspace tying together identity, visits, orders, released results, reviews, follow-up and a
billing summary — **sectioned by role**, not a dump.

```
   PATIENT  (name, DOB, contact, provider-scoped identity)
      |
      +-- Visites          reverse chronological; each -> reason, note, orders, results
      +-- Ordres           all diagnostic orders, with status
      +-- Résultats        released versions only, with review disposition
      +-- Facturation      invoices, payments, balance                     [billing roles]
      +-- Accès patient    active grant / QR state                         [clinician]
```

| Section | Front Desk *(D-1)* | Clinician | Lab Staff |
|---|---|---|---|
| Identity | LTD (name, DOB, contact) | FULL | **Only via an order assigned to them** |
| Visites | LTD (exists, status, reason) | FULL | NONE |
| Ordres | NONE | FULL | Own assigned orders only |
| Résultats | NONE (may see *availability*) | FULL | Own lab's work only |
| Facturation | FULL | LTD | Own lab's charges |

**Lab staff never get the longitudinal chart.** They reach patient identity through an order assigned
to their laboratory, and see the attributes needed to confirm the right person — no more.

---

## 15. Orders and results integration

Already built and **not redesigned here**. Preserved exactly:

- controlled result templates, pinned by version at entry
- immutable corrections — a correction supersedes, never overwrites
- **entry authority ≠ release authority** (ADR-044)
- immutable release versions
- post-release amendments as a new version, never an edit
- version-scoped clinician review
- **entered ≠ released**, enforced structurally

**What changes when visits exist:** an order gains an optional `visit_id`, and the clinic surfaces
orders and results against the visit that produced them. Nothing about the result or release model
changes.

---

## 16. DHealth Lab — target operating model, reception, and the specimen decision

### Information architecture

| Section | Verdict |
|---|---|
| **Aujourd'hui** | Keep — exists |
| **Ordres** | Keep — exists |
| **Résultats** | Keep — exists (entry + inspection) |
| **Libération** | Keep — exists, separate authority |
| **Réception** | **Not added** — `Ordres` opening on `Nouveaux` *is* this surface (§42, 2026-09-30). A second navigation entry over the same projection would be two doors into one room. Still open for a walk-in laboratory (§17), which is a different question |
| **Prélèvements** | **Add — P1** for the entity; the blocking part is P0-4 below |
| **Facturation** | **Add — P0** (§18) |
| **Paramètres** | **Not added as a section** — `Services et tarifs` already *is* the facility-configuration surface, and ADR-071 put laboratory offerings on it rather than opening a second door over the same subject (the reasoning `Réception` records above). Result templates and the facility profile remain backend-only and are the remaining content a `Paramètres` section would hold |

### The questions a Lab specification must answer

**How does an order arrive?** Today: a provider organization creates it and routes it over an active
`ProviderLabRoute`. That is the Native path and it works.

**How does a *patient* arrive?** Unspecified today — see §17.

**How is identity confirmed at the bench?** The lab sees the patient identity carried by the order.
**Which attributes a receiving laboratory needs in order to safely confirm the presenting patient is
an open question** — `docs/MVP-1.0.md` gap 10 and `docs/FIELD-DIAGNOSTIC-WORKFLOW.md` Finding 11.
**D-4** in §28. This document does not invent the list.

**Connect orders?** Identical workflow. Connect is the boundary; the lab's screens do not change
(§22).

**Accept / reject?** Built, order-level.

**Collection?** Built, order-level milestone (`LabSampleCollection`) — `recorded_at` only, explicitly
not a specimen entity.

**What if the sample is unsuitable?** **Nothing represents this.** P0-4.

**Processing, entry, correction, release, amendment?** All built (§15).

**How does the provider receive the released result?** They read it in DHealth (Native) or it is
delivered through Connect, whose delivery obligation is the authoritative record of that (ADR-064).
The `ResultDelivery` ambiguity is resolved — §23.

**Lab billing?** Not built. §18.

### The specimen decision — split deliberately

The temptation is a full LIMS specimen graph. The honest answer is that **one small piece is
blocking and the rest is not.**

**P0-4 — an accepted order that cannot proceed needs an honest state.** Order-level, small:

```
   ACCEPTED / IN_PROGRESS
        |
   [lab: cannot proceed]
        v
   BLOCKED  + reason + who + when        -> appears on Aujourd'hui as work owed
        |                                -> visible to the ordering clinician
   [resolved: recollected / clarified]
        v
   back to the prior state
```

Why this is P0 and not P1: without it, a haemolysed tube leaves the order sitting in a queue state
that **claims work is progressing when it is not**, nobody is told, and nobody owns it. That fails
§27's ownership test and law 11. The workaround — telephone the clinic — leaves the software lying.
The reason list is operational, not clinical (*sample unusable, insufficient quantity, patient did not
attend, clarification needed*) and must be confirmed with a laboratory participant before it is fixed.

> **BUILT 2026-09-29 — `DHEALTH-LAB-BLOCKED-001`, ADR-068.** `BLOCKED` is a real `LabOrderStatus`,
> reachable only from `ACCEPTED`/`IN_PROGRESS` and only together with a `LabOrderBlock` **episode**
> written in the same transaction — so a blocked order can always say why, who, when, and what it
> will resume to. Episodes are never overwritten or deleted: an order blocked twice has two of
> them, which is what makes *"has this order been trouble before?"* answerable at all. At most one
> may be unresolved at a time (a partial unique index behind the `LabOrder` row lock). Resolution
> restores `blocked_from_status` and nothing else — the destination is never the client's to pick.
> The ordering clinician sees the status and the reason key; the laboratory's free-text note and
> the technician's identity do not cross the organization boundary; the patient sees neither.
>
> **The reason list above is implemented exactly as written here and remains PROVISIONAL.** The
> confirmation this paragraph requires has *not* happened — it is **D-11** in §28 and an open
> `Human Decision Required` item in `PROJECT_STATE.md`. **Neither the catalog nor its French labels
> has been validated by anyone who works in a laboratory**, and they are to be validated **during
> human usability / workflow qualification**, not inferred.
>
> Four ordinary laboratory situations have **no category at all** today and go in the free-text
> note: **analyser downtime, power outage, reagent shortage or unavailability, and recollection
> needed.** Four more map to a reason only on our own unverified assumption: **insufficient sample,
> damaged sample, incorrect/wrong sample, missing information.** All eight are the scenarios to put
> to the participant — asking how *they* would file each one in their own words, not which of our
> four they would pick.
>
> **No reason may be added on an agent's or an author's own reasoning**, `TECHNICAL_ISSUE` included.
> The free-text note is the honest home for whatever does not fit, and stays available for it.
>
> **[Updated 2026-10-01, DHEALTH-SPECIMEN-001/ADR-070.** The specimen entity is now built, and
> the worked example above is what the browser qualification actually drives: collect, block for
> `SPECIMEN_UNUSABLE`, resolve, collect again — and **both specimens remain**. `BLOCKED` is
> unchanged by that ticket; it is still the order-level cannot-continue fact, and it is still where
> the operational reason lives. Nothing in the specimen record says *why* a later sample was
> taken.]**
>
> Still **not** built, and deliberately: recollection *linkage* (an explicit "this replaced that" —
> the collection action carries no such statement, and inferring it from two timestamps would be
> fabricated provenance), specimen types, labels, and any announcement of a block to an external
> system through Connect (§22 — Connect refuses to apply a released result to a blocked order,
> which is correct, but nothing *tells* an external system). **Connect also cannot report a
> recollection**: its collection event is keyless, so a second event for the same order is a
> replay rather than a second specimen (ADR-070 §4).

**P1 — a first-class specimen entity. [BUILT in its architectural half, 2026-10-01, ADR-070.]**
`LabSpecimen` exists: any number per order, immutable, carrying when it was recorded and the
authenticated actor who recorded it. What that bought is the thing a single collection row made
impossible — **a recollection that preserves the earlier sample.**

What it did *not* buy, and what the rest of this paragraph's original list still names as open:
one order spanning several specimen **types** (a CBC and a urine culture cannot share a tube, and
nothing yet says which analysis needs which sample — `TestCatalogEntry` carries no specimen-type
field, so the type cannot even be derived); one specimen satisfying several ordered items;
**accession identifiers**; per-specimen received/rejected timestamps; recollection as an explicitly
**linked** event. All of those are **D-3's participant half** and need a laboratory participant.

**Not required for Launch-1:** container types, volumes, storage and handling rules, aliquots,
stability windows, chain-of-custody, analyser interfacing.

> **[Corrected 2026-10-01, DHEALTH-SPECIMEN-001.** D-3 is now **split**, and the distinction matters
> because this sentence is the one a later reader will quote. The *architectural* question — does a
> specimen entity exist, is its cardinality truthful, is collection provenance recorded, is a prior
> specimen preserved — was answered by the operator directing DHEALTH-SPECIMEN-001, and ADR-070
> implements it. The *participant* question is untouched: which specimen types a laboratory works
> with and where their governed source is, whether staff need a human-facing accession number,
> what a label must carry, when a specimen is invalid as a specimen rather than the order becoming
> `BLOCKED`, whether a mistaken collection record may be voided, and whether an explicit
> recollection link is needed. **No laboratory participant has been consulted.** Building the entity
> did not answer any of that and must not be read as having done so.]**

### Barcode / specimen labels — P1

Handwriting a tube is current practice in this market and is not unsafe when identity is confirmed at
collection. When labels are built, the **application contract** (not a printer driver):

- identifier: the order's existing identifier, or the specimen's once §16's P1 exists — never a new
  parallel numbering scheme
- human-readable: patient name, date of birth, order identifier, collection date
- machine-readable: Code128 or QR of the identifier alone — never clinical content on a tube
- association: which ordered items this specimen serves
- reprint: allowed, audited, and visibly marked as a reprint
- **the manual fallback stays** — a handwritten tube must never be rejected by the software

---

## 17. The walk-in laboratory patient

Today **every order originates from a provider organization**. A patient walking into a laboratory
without a clinic order has no representation at all.

This matters commercially: independent laboratories in this market take direct walk-in business.

Two shapes, and the choice is a product decision (**D-6**, §28):

```
   (a) the laboratory acts as its own requester
       lab registers the patient, creates the order itself, routes it to itself
       -> reuses the whole existing pipeline
       -> needs a LAB organization to hold a patient directory, which today only PROVIDERs do

   (b) a distinct self-requested order type
       -> new concept, new authorization surface, more honest about "no ordering clinician"
```

Recommendation: **(a)**, because it reuses a qualified pipeline rather than adding a parallel one —
but it requires deciding whether a laboratory may hold patient records, which has privacy and
identity consequences. Hence D-6.

If a laboratory is the pilot facility, this becomes **P0** for that facility. For a clinic pilot it is
P2.

---

## 18. Facturation (billing) — P0-3, and deliberately narrow

### Purpose

> Can this facility charge for what it provided, and give the patient a truthful receipt and balance?

**Not** accounting. Not revenue-cycle management.

### Minimum domain

| Concept | Purpose |
|---|---|
| Billable service + price | What the facility charges for, and how much. §19. |
| Invoice | One per visit (clinic) or per order (laboratory). Facility-scoped. |
| Invoice line | Service, quantity, unit price, line total. **Price frozen at invoicing.** |
| Payment | Amount, method (cash / mobile money / card / transfer), timestamp, who took it. |
| Receipt | A printable artifact proving payment. §21. |
| Balance | **Derived** — invoice total minus payments. Never stored. Law 8. |
| Void / correction | An invoice is voided with a reason and superseded, never edited or deleted. §26. |

**Explicitly out of scope:** insurance claims, insurer eligibility, general ledger, payroll,
inventory, tax computation, multi-currency beyond the pilot's DJF, instalment plans, debt collection.

### What must not be reused

`LabOrderItem.selected_price_amount` is **not** an invoice line. It is DH-EX-005's *marketplace price
consent* fact, written only by the patient-selection path; `assign_by_provider` deliberately leaves it
`NULL`, meaning *"price was not part of this transaction"* — explicitly **not** "costs zero". A
billing price is a different fact captured at a different moment by a different actor. Conflating them
would make a consent record look like an invoice.

`LabPrice` **is** a reasonable source for a laboratory's own list price (it already supersedes rather
than mutates, preserving history). It is offering-scoped, so it does not cover consultations.

### Two decisions this document will not make

> **D-2a — who pays whom in a routed order?** The laboratory bills the patient; the laboratory bills
> the clinic; or the clinic bills the patient and settles with the laboratory. These are different
> domains with different privacy boundaries. A pilot facility's actual commercial arrangement decides
> it.
>
> **D-2b — what happens when payment is incomplete?** See §27's hard rule below.

### The hard rule

**Payment state must not block release of a medically necessary result.** If anyone proposes
`unpaid → result cannot be released`, that is a clinical-safety and business-policy decision requiring
explicit human sign-off — **never** encoded casually. Recorded as **D-2b**, `HUMAN DECISION REQUIRED /
CLINICAL POLICY`. Until decided, billing observes clinical state and never gates it.

### Billing privacy

A cashier needs: patient identity, what service, how much, paid or not. A cashier does **not** need
the clinician's note, result values, or any diagnosis. A billing line may name *"Numération formule
sanguine"* — the test that was performed — because that is the thing being charged for; it must not
carry the result.

---

## 19. Administration and the service catalogue

### Facility-owned administration

`Équipe et accès` is the first slice and is built. The switch-ready set adds:

| Screen | Status | Priority |
|---|---|---|
| `Équipe et accès` | **BUILT** | — |
| Organization profile — name, address, phone | NOT BUILT | P1 |
| Services and prices | **BUILT** — clinic services and prices (ADR-066); laboratory *offerings* added 2026-10-01 (ADR-071) on the same screen, gated independently | — |
| Lab result templates | BACKEND ONLY | P1 |
| Lab offerings | **BUILT** (2026-10-01, ADR-071) | — |
| Released result report | **BUILT** (2026-10-02, ADR-072) | — |
| Provider–lab routing (view; edit as policy allows) | BACKEND ONLY, platform-scoped | P1 |
| Opening hours | NOT BUILT | P2 — only if a workflow needs it |
| Notification configuration *status* | NOT BUILT | P2 — read-only status, never credentials |
| Enrollment / readiness (read-only) | BUILT, platform-scoped | P2 |

**Never exposed to facility staff:** database hosts, OIDC client internals, connector queue
internals, Docker or compose settings, migration state, container health. That is platform scope.

### Admin authority — unchanged

`ORG_ADMIN` ≠ clinician. `ORG_ADMIN` ≠ releaser. `PLATFORM_ADMIN` ≠ universal clinical user.

**ADR-063 holds:** a normal staff-access lifecycle operation may not leave an active organization with
zero active `ORG_ADMIN` memberships, and `PLATFORM_ADMIN` does not bypass that through the ordinary
endpoints. Emergency recovery is a separate, explicitly audited break-glass capability — named, not
designed, and not built.

### Service catalogue — keep the domains separate

The elegant-looking move is one generic `Service` that billing references. **Recommendation: do not.**

A laboratory test and a consultation have different operational semantics: a test has a catalogue
entry, an offering, a result template, a specimen and a release authority; a consultation has none of
those. Forcing one object would either strip what makes `LabOffering` safe or bolt laboratory concepts
onto a consultation that has no use for them.

**Instead: billing references a billable *source*, polymorphically.**

```
   invoice line  ->  source_type + source_id
                      |
                      +-- LAB_OFFERING      (existing domain, existing price history)
                      +-- CLINIC_SERVICE    (new, small: name + price + active)
```

Cost of this choice, stated: billing must handle two source types, and a report grouping revenue by
service must union them. That is a smaller cost than distorting a qualified clinical domain.

---

## 20. Migration and import

### Patient directory first — P0-5

Minimum capability: import a patient directory from **CSV or XLSX**.

Required behaviour, specified here and **not implemented**:

| Requirement | Why |
|---|---|
| Column mapping | Every facility's export differs. |
| Preview before commit | An operator sees what will be created. |
| Dry run | Full validation, zero writes. |
| Row-level validation with per-row errors | One bad row must not fail 4,000 good ones. |
| Error report, downloadable | The operator fixes and re-imports only failures. |
| Idempotency | Re-running the same file must not duplicate. Keyed on an operator-chosen source identifier, not on demographics. |
| Provenance | Every imported patient records which import created it, and when. |
| Import report, durable | What ran, by whom, how many created/skipped/failed. |
| Rollback | An import may be reversed **only while no clinical data references** its patients; after that, individual records are deactivated, never deleted (§26). |

### The rule that must not be broken

**No fuzzy patient matching.** ADR-051's boundary holds: identical demographics must never silently
merge two patients. Duplicates are surfaced to a human, who decides. Patient merge is separate
identity work and is not in this specification.

### Historical clinical data — P3, and the recommendation is *don't*

Recommendation: **import the active patient directory only; keep the legacy system read-only for
history.**

Why: importing old visits, results and invoices means asserting provenance DHealth cannot verify,
reconstructing release and review facts that never existed in the old system, and inventing clinical
structure — which `AGENTS.md` forbids. A released result in DHealth means a named authority released
it; a migrated row cannot honestly claim that.

Decision criteria for revisiting, if a facility pushes back:

1. Is the old system actually being switched off, or can it stay read-only?
2. Is a regulatory retention period involved?
3. Can the old data carry a named releaser and a release timestamp? If not, it cannot become a
   released result and would have to be visibly marked as unverified imported history.

---

## 21. Printing, paper, and assisted digital

The law is **not** "eliminate paper". It is: **paper must never be required to transport information
between systems.** A patient may still need something in their hand.

| Artifact | Facility | Purpose | Priority |
|---|---|---|---|
| Receipt | Both | Proof of payment | **P0 with billing** |
| Lab handoff slip + QR | Clinic | The patient carries it to the lab; also the assisted-digital path | **BUILT** (print not paper-tested) |
| Specimen label | Lab | Tube identification | P1 (§16) |
| Result report | Lab | For a patient or clinic without DHealth access | P1 |
| Visit summary | Clinic | What was done and what to do next | P2 |
| Appointment slip | Clinic | Reminder | P2, with §10 |
| Collection ticket | Lab | Queue position | P3 |

**Every critical digital workflow keeps an assisted-human fallback.** The QR sheet already exists and
is the pattern.

---

## 22. DHealth Patient, and DHealth Connect

### DHealth Patient stays narrow

Preserved: **no account**, order-scoped opaque grant, status-oriented, no diagnostic interpretation,
QR/printed handoff, credential in the URL fragment and never stored.

**Not a patient portal.** No login, no message threads, no document library, no self-booking.

When visits exist, the patient surface may additionally show: appointment date and where to go; order
status; whether a result is available; and the clinician's own follow-up instruction **if the clinician
chose to share it**. It must **not** automatically expose the clinical note or result values — that is
a deliberate boundary, not a limitation to fix.

Notification (WhatsApp/SMS) remains optional and configuration-dependent. **Switch-readiness must not
depend on Meta being configured** — ADR-003's gate is still `BLOCKED`, and the QR/printed path is
sufficient on its own.

### Connect interaction

Every workflow is labelled:

| Workflow | Mode |
|---|---|
| DHealth Clinic → DHealth Lab | **NATIVE ONLY** — end to end |
| DHealth Clinic → external LIS | **CONNECT REQUIRED** |
| External EMR → DHealth Lab | **CONNECT REQUIRED** |
| External EMR → external LIS | **CONNECT on both sides** |
| Visits, calendar, billing, front desk, reception | **NATIVE ONLY** — no Connect semantics |
| Patient access / notification | **NATIVE ONLY** (the grant is DHealth's) |

**Connect remains the only boundary to external clinical systems.** Visits and billing must not grow
their own integration semantics. A mixed facility (DHealth Clinic + external LIS) uses Native for
visits and billing and Connect for the diagnostic leg — the screens do not change.

---

## 23. ResultDelivery — RESOLVED 2026-09-27 by ADR-064

> **D-7 is closed.** `ResultDelivery` is **DEPRECATED / COMPATIBILITY-ONLY**. It owns nothing, no new
> code may write or read it, and a Connect adapter must never touch it. The table, its enum and every
> historical row are preserved; **no migration was required** and Alembic head stays
> `0033_staff_access_lifecycle`. Full reasoning, the ownership table and the adapter guardrail:
> **ADR-064** (`DECISIONS.md`), implemented by `DHEALTH-RESULT-DELIVERY-001`.
>
> **Canonical ownership, one authoritative owner per fact:**
>
> | Fact | Owner |
> |---|---|
> | A released clinical result exists | `LabResultReleaseVersion` |
> | Available to a Native provider | **derived** — no stored state, by decision |
> | A clinician reviewed one exact version | `DoctorReview` (version-scoped) |
> | The provider's external system received it | `ConnectorDeliveryObligation` → `ConnectorDelivery` |
> | The patient was messaged / message delivered | `PatientNotification` (+ signed provider receipt) |
> | A patient read or understood anything | deliberately not modelled |
>
> **The decisive argument was structural, not redundancy.** `ResultDelivery.lab_order_id` is unique —
> one status per *order* — but since ADR-048 a result is released as a *version* and an amendment
> produces another, so the fact is version-scoped: v1 delivered while v2 is owed.
> `ConnectorDeliveryObligation` is unique per `release_version_id`. `ResultDelivery` is therefore the
> wrong *shape* for its own stated meaning, not merely a duplicate of it.
>
> **`DH-CONNECT-ADAPTER-001` is no longer blocked by this ambiguity**, and now carries an explicit
> rule: its responsibility ends at the Connect delivery contract's acknowledgement; it never writes
> `ResultDelivery`, never writes `DoctorReview`, and never infers patient notification from provider
> delivery.
>
> **Terminology rule for all future work:** never write bare "delivered" without naming who received
> what — prefer `released`, `available to the provider`, `reviewed`, `provider-system delivery
> acknowledged`, `patient notification sent`, `patient notification delivered`.

The evaluation that produced that decision is kept below, unedited, because it is the evidence.

### 23.1 The original evaluation (2026-09-27, before the decision)

### What was found

Two different things in this repository are called "delivery":

| | Table / class | Callers |
|---|---|---|
| **Connect** | `connect/delivery.py` `DeliveryRepository` | **Live.** `mark_delivered`/`mark_failed` called from `connect/inbound.py` and `connect/runtime_api.py`. This is outbound connector message delivery. |
| **Exchange** | `exchange_result_deliveries`, `ResultDeliveryRepository` | **No production caller.** One row created `PENDING` per order at creation; `mark_delivered`/`mark_failed` are called only from one unit test. `audit.py`'s `result_delivered` event type is never emitted. The table is in `demo_seed`'s `_WORKFLOW_TABLES`, so reset clears it. |

So for a Native order, `ResultDelivery` is created `PENDING` and **stays `PENDING` forever** — which
is the misleading part. A reader reasonably concludes results are undelivered.

### The distinct facts that must not be conflated

```
   1  result RELEASED                        -> LabResultReleaseVersion        EXISTS
   2  result AVAILABLE TO PROVIDER           -> implied by 1 for Native        EXISTS (derived)
   3  result DURABLY DELIVERED to an
      external provider system               -> Connect's own delivery         EXISTS (Connect)
   4  result VIEWED by a human               -> nothing                        NOT MODELLED
   5  result CLINICIAN REVIEWED              -> DoctorReview                   EXISTS
   6  patient notification SENT              -> PatientNotification            EXISTS
   7  patient notification DELIVERED         -> provider receipt               EXISTS
```

Every fact except 4 already has a home, and `ResultDelivery` duplicates 3 while being reachable for
Native orders where 3 is meaningless — a Native provider reads results from DHealth itself, so there
is nothing to deliver.

### Recommendation

Two options, both honest:

- **(a) Retire it from the Exchange schema.** Connect's `DeliveryRepository` becomes the only
  "delivery". Removes a misleading always-`PENDING` row and a dead audit event type.
- **(b) Rename it `ProviderSystemDelivery`, Connect-only.** Never created for Native orders; created
  when an order is bound to a connector. Keeps a place for fact 3 at the Exchange level.

**Preferred: (b)** if a real vendor adapter will need an Exchange-level record of external delivery;
**(a)** if Connect's own table is sufficient. That depends on the first adapter's design, which does
not exist yet.

> **D-7 — `HUMAN DECISION REQUIRED`.** This must be resolved **before** `DH-CONNECT-ADAPTER-001`
> builds against it, or the first real adapter will fix the ambiguity by accident. Recorded as
> `docs/MVP-1.0.md` gap 6 since 2026-09-12; this ticket sharpens it with the two-concepts finding but
> does not decide it. **No code is changed here.**

---

## 24. Failure and degraded operation

Neither pretending the internet never fails, nor building an offline-first distributed EMR.

### Launch-1 expectation

**DHealth Native requires connectivity for normal operation.** Said plainly rather than implied.

| When DHealth is unreachable | Expectation |
|---|---|
| Reception | Paper register, re-entered on recovery. **Disaster continuity, not normal workflow.** |
| Laboratory | Label and hold specimens physically; record in DHealth on recovery. Physical identity must not depend on the software being up (law 11). |
| Result release | **Must not happen on paper.** Release is an authority act with an immutable record; a paper release cannot be reconstructed honestly. |
| Result entry | Wait. Entering values from memory later is a patient-safety risk. |
| Reconciliation | On recovery, staff enter what happened, with the real times where known. |
| Duplicate avoidance | Existing idempotency-key discipline already protects order creation; the same must apply to any offline-then-replayed write. |

### The Law 2 tension, named

"Write it on paper and re-enter later" **is** double entry. It is acceptable as *disaster continuity*
and unacceptable as *normal operation*. The distinction is frequency: if a facility's connectivity
makes paper the daily norm, DHealth Native is the wrong product for that facility until offline
support exists — and saying so is more honest than shipping a degraded experience.

**Offline/PWA is P2**, to be decided by pilot evidence: measure real connectivity at the pilot site
before designing for it.

---

## 25. Reporting — P2, minimal, operational only

Each metric must answer an operational question. **No BI platform, no vanity dashboards.**

| Clinic | Question it answers |
|---|---|
| Visits today, by status | Is the clinic keeping up? |
| Patients waiting, longest wait | Is someone being forgotten? |
| Results awaiting review | Is a result sitting unreviewed? |
| Payments collected today | Does the till reconcile? |
| Outstanding balances | Who owes what? |

| Laboratory | Question it answers |
|---|---|
| Orders received today | Workload |
| Awaiting collection | Is a patient expected? |
| In processing | Bench load |
| Entry incomplete | What is unfinished? |
| Release queue depth | Is a result held up at release? |
| **Turnaround time** | Are we fast enough? **Derived** from timestamps that already exist — order creation, collection `recorded_at`, processing start, release. Law 8: no new field. |

**Excluded:** revenue trend charts, staff productivity leaderboards, month-over-month comparisons,
anything requiring a warehouse.

---

## 26. Data, audit, authorization, and retention

### Authorization boundaries — preserved exactly

- Authority is evaluated **server-side on every request** from a live database read: organization
  scope + role/capability scope + resource relationship. Nothing is trusted from the token but who
  the caller is.
- **Revocation takes effect on the next request** with the same unexpired token. Every new module
  inherits this automatically by using the existing `require_*` gates.
- **404 vs 403** anti-enumeration shapes are preserved: 404 for no relationship at all, 403 for a
  member lacking a specific permission.
- The browser is **never** the boundary. Hiding a control is usability; the server refuses regardless.

### Audit

Every new authority-bearing or clinical mutation writes a transactional audit row in the same
transaction as the change, using the existing `ExchangeSecurityAuditEvent` vocabulary extended
narrowly. New modules will need additive event types (visit created/completed, invoice issued, payment
recorded, specimen rejected) — added per ticket with a migration and a test, never speculatively.

**Successful sensitive *reads* are still not audited**, and `SecurityEventType` still has no read
event. That remains `HUMAN DECISION REQUIRED` (**D-8**) and this specification does not settle it —
but note that Visits, Billing and Front Desk all *increase* the amount of sensitive reading, so the
question gets more consequential, not less.

### Retention — what may happen to what

| Entity | Permitted |
|---|---|
| Released result / release version | **Immutable.** Corrected by superseding, amended by a new version. Never edited, never deleted. |
| Clinician review | Immutable per version. |
| Visit | Completed visits: **corrected by appended revision**, never deleted. Cancelled/no-show remain as history. |
| Clinical note | Append-only revisions; earlier text retained. |
| Invoice | **Voided with a reason** and superseded. Never edited, never deleted. |
| Payment | Immutable; reversed by an explicit offsetting record. |
| Patient | **Deactivated**, never deleted — clinical history references it. |
| Membership | Deactivated, never deleted (already true). |
| Appointment | Cancelled or no-show. Deletable only before arrival, and even then prefer cancel. |
| Imported patient | Reversible only while nothing clinical references it (§20). |

**No delete button for clinical data.** Patient duplicate/merge remains separate identity work.

---

## 27. Workflow ownership

Every workflow names an actor, a trigger, inputs, derived facts, a state change, the next owner, an
exception path, and an audit requirement. **A status nobody monitors is not an operational workflow.**

Worked example — clinic arrival:

| | |
|---|---|
| **Actor** | Front Desk *(D-1)*; clinician until then |
| **Trigger** | Patient physically arrives |
| **Required input** | Which patient; which appointment, if any |
| **Derived** | Organization (from session), timestamp (server clock), visit type (appointment present or not) |
| **State change** | Visit created, or `PLANNED` → `CHECKED_IN` |
| **Next owner** | **Clinician** — the visit appears on their `Aujourd'hui` |
| **Exception** | Patient leaves before being seen → `CANCELLED` with reason; expected and never arrives → `NO_SHOW` |
| **Audit** | Visit state transition, with actor |

Worked example — laboratory cannot proceed (P0-4):

| | |
|---|---|
| **Actor** | Lab Staff (`LAB_TECH`) |
| **Trigger** | Specimen unusable, insufficient, or patient absent |
| **Required input** | A reason from the operational list |
| **Derived** | Organization, order, timestamp, actor |
| **State change** | → `BLOCKED` |
| **Next owner** | **The ordering clinician** — it appears on their `Aujourd'hui` as action owed |
| **Exception** | Resolved → returns to the prior state, with the block retained as history |
| **Audit** | Required — a blocked diagnostic order is a clinically consequential event |

The full set of maps is §30.

---

## 28. Open product decisions

Only where different answers materially change architecture or workflow.

| # | Decision | Kind | Why it blocks |
|---|---|---|---|
| **D-1** | ~~**Which new backend authority does Front Desk need**, and does Billing share it or need its own?~~ **RESOLVED IN FULL.** Billing portion 2026-09-28 (ADR-066): its own — `BILLING_CLERK`, asked for as a *capability* rather than a role name. **Front Desk portion RESOLVED 2026-09-30, ADR-069**: three independently grantable capabilities and **no role** — `patient.directory.read`, `patient.register`, and the new **`visit.check_in`**. There is no `FRONT_DESK` role and nothing gates on one; `ORG_ADMIN` + a direct `visit.check_in` grant runs reception with no role change, which is how the browser qualification proves it. `visit.check_in` carries **no clinical read**: a holder cannot open a visit, its note history or its stated reason, even one they created. The answer to "does introducing it change what guards patient-directory reads" is yes, and in the safe direction — `require_clinician` narrowed from six capabilities to four, and the patient-directory routes now ask for `patient.directory.read` directly. | **RESOLVED** (ADR-066 + ADR-069) | **No longer blocks anything.** |
| **D-2a** | **Who pays whom in a routed order** — lab bills patient, lab bills clinic, or clinic bills patient and settles with lab. **Interim answer 2026-09-28, ADR-066 §3**: each organization invoices only what it actually performed, enforced server-side at the invoice line. **Still open** for commercial arrangements over routed external laboratory work — settlement, commissions, referral fees, revenue sharing, pass-through billing, markup and inter-facility AP/AR are all deliberately unbuilt. | Product / commercial | No longer blocks the billing ticket. Blocks any inter-facility commercial model. |
| **D-2b** | **What happens when payment is incomplete.** If anyone proposes unpaid → no release, that is clinical policy. | **HUMAN DECISION REQUIRED / CLINICAL POLICY** | Must never be encoded casually. Default until decided: billing never gates clinical release. |
| **D-3** | ~~**Is a first-class specimen entity required for Launch-1?**~~ **SPLIT 2026-10-01 (ADR-070), and only one half is resolved.** **Architectural half — RESOLVED:** the entity exists (`LabSpecimen`, migration `0039`), its cardinality is truthful (`lab_order_id` is deliberately **not** unique), collection provenance is recorded (instant + authenticated actor), and a prior specimen is never overwritten when another is collected. Decided by the operator directing DHEALTH-SPECIMEN-001, which is an architecture decision and **not** a substitute for the conversation below. **Participant half — STILL OPEN, and no laboratory participant has been consulted:** which specimen **types** a laboratory works with and where their governed source is (`TestCatalogEntry` has no such field, so type cannot be derived); whether staff need a human-facing **accession number** distinct from the internal id; what a physical **label** must carry; when a specimen is invalid *as a specimen* rather than the order becoming `BLOCKED`; whether a mistaken collection record may be **voided** and with what audit semantics; whether an explicit **recollection link** is needed and what workflow input could truthfully supply it. **Nobody may read ADR-070 as "specimens are done."** | **Architectural: RESOLVED** (ADR-070) · **Participant: Product, needs a laboratory participant** | No longer blocks modelling a physical sample. Still blocks specimen types, labels, accessioning and any specimen-level rejection. |
| **D-11** | **Is the block-reason catalog right, and are its French labels a laboratory's own words?** Four reasons ship as **provisional** (ADR-068), exactly the four §16 names. §16 requires confirmation with a laboratory participant before the list is fixed; **that has not happened, and neither the catalog nor the wording has been validated by anyone who works in a laboratory.** To be validated **during human usability / workflow qualification**, by asking a participant how *they* would categorise, in their own words: **analyser downtime · power outage · reagent shortage or unavailability · recollection needed · insufficient sample · damaged sample · incorrect/wrong sample · missing information.** The first four have no category today and go in the free-text note; the last four map to a reason only on our unverified assumption. **No reason is to be added on our own reasoning** — the free-text note stays the honest home for whatever does not fit. | Product, needs a laboratory participant | Does not block — the workflow is complete and qualified. Blocks calling the vocabulary settled, and is itself remaining Lab work. |
| **D-4** | **Which patient attributes must a receiving laboratory see to confirm the presenting patient?** (`docs/MVP-1.0.md` gap 10.) | Product + clinical safety | Blocks Lab Reception. Must not be invented. |
| **D-5** | **Diagnosis coding** — free text only, or a code system, and which? Clinician-recorded either way; system-generated never. | Clinical lead | Blocks any structured diagnosis. Free-text note is unaffected. |
| **D-6** | **May a laboratory hold its own patient records** so a walk-in laboratory patient is representable? | Product + privacy | Blocks walk-in lab workflow. P0 only if the pilot is a laboratory. |
| ~~**D-7**~~ | ~~**What `ResultDelivery` means**~~ — **RESOLVED 2026-09-27, ADR-064.** Deprecated / compatibility-only; owns nothing; no migration needed. §23. | ~~HUMAN DECISION REQUIRED~~ **CLOSED** | ~~Blocks the first Connect adapter~~ — **no longer blocking**, and the adapter now has an explicit guardrail. |
| **D-8** | **Should successful sensitive reads be audited?** Pre-existing; Visits/Billing/Front Desk make it weightier. | **HUMAN DECISION REQUIRED** (unchanged) | Does not block, but changes retention and storage posture. |
| **D-9** | **Staff session and idle timeout.** Pre-existing; current values are Keycloak defaults, not a clinical decision. | **HUMAN DECISION REQUIRED** (unchanged) | Does not block. |
| **D-10** | **Do staff interfaces need Arabic, Somali or English at Launch-1?** | Product | See §29. |

### Language — a recommendation, and why

**Recommendation: French-primary staff interface only for Launch-1.** French is Djibouti's working
medical and administrative language and is already first-class. The *patient-facing* side is a
different question — `docs/MVP-1.0.md` §7 records future capability for Somali/Afar/Arabic on the
patient side as direction, not current implementation.

Adding staff localisation now would mean translating a product whose vocabulary is still changing.
Recorded as **D-10** because a pilot facility could overturn it.

---

## 29. Explicit non-goals

Not specified for Launch-1, and **not** to be inferred from any section above: inpatient HIS,
inpatient admission, bed management, nursing medication administration, inpatient medication charts,
operating theatre, ICU, full pharmacy, PACS, full radiology, insurance claims, insurer eligibility,
general ledger, payroll, inventory or ERP, CRM, marketing automation, telemedicine, consumer
marketplace, national patient matching, national MPI, national longitudinal EHR, patient
self-registration, patient self-booking, **clinical AI of any kind**, AI triage, AI result
interpretation, and any diagnosis or treatment recommendation.

**On AI specifically:** no feature in this specification interprets, suggests, prioritises or
diagnoses. DHealth records and routes. This is `AGENTS.md`'s global rule and is not negotiable by a
product document.

Also excluded from *this ticket's* scope though not from the product forever: patient merge, offline
PWA, staff localisation, historical clinical migration, analyser interfacing, and the break-glass
administrator recovery ADR-063 names.

---

## 30. Screen inventory

Status: **BUILT** · **PARTIAL** · **NOT BUILT**. Every row names the data it must **not** show.

### DHealth Clinic

| Screen | Roles | Primary task | Minimum data | Primary action | Not shown | Backend | Status |
|---|---|---|---|---|---|---|---|
| Aujourd'hui (clinic) | `visit.check_in` and/or `visit.document`; order tiles need `order.manage` | Answer "what needs me now" | Waiting arrivals, own in-progress, reviews owed | Open the next item | Other clinicians' notes; totals | Visits + review queue | **BUILT 2026-09-30** (ADR-069) — the arrival and waiting surface lives here |
| Liste d'attente | `visit.check_in` and/or `visit.document` | See who is waiting | Name, DOB, arrival time, coarse status | Take the visit | Clinical note; results; **the stated reason**; ~~waited-for~~ | Visits | **BUILT 2026-09-30** as part of `Aujourd'hui`, not a separate screen (ADR-069 §4). **Two deliberate narrowings of this row:** the *reason* is withheld though §7 permits it (law 9, minimum necessary), and **"waited-for" is not built at all** — see §38 and the `PROJECT_STATE.md` blocker |
| Patients (search) | `patient.directory.read` | Find the right patient | Name, DOB, last visit date | Open patient | Result values; notes | `Patient` search | **BUILT** |
| Enregistrer un patient | `patient.register` | Register a new patient | **Name only** (law 7) | Create | everything else | `Patient` create | **BUILT**; browser-exercised 2026-09-30 by ADR-069's FD-B5, which registers a patient and checks them in without a second search |
| Dossier patient | Clinician | See the patient's history | Identity, visits, orders, released results | Start a visit | Other facilities' data | Visits + orders + results | **PARTIAL** |
| Visite (open) | Clinician | Document this encounter | Reason, note, orders, results | Complete the visit | Billing internals | Visits | **NOT BUILT** |
| Nouvel ordre | Clinician | Order diagnostics | Catalog, patient, target lab | Commit the order | Prices (Native path) | Order intent → order | **BUILT** |
| Ordres | Clinician | Track ordered work | Order, status, lab, patient | Open order | Other providers' orders | Orders | **BUILT** — see O-1b below |
| Résultats / revue | Clinician | Review released results | Released version, values, disposition | Record review | Unreleased drafts | Release versions + review | **BUILT** |
| Rendez-vous | FD *(D-1)* | Book and manage arrivals | Date, patient, clinician, status | Book / mark arrival | Clinical content | Appointments | **NOT BUILT** |
| Facture | FD/Cashier *(D-1)* | Charge and take payment | Patient, lines, total, paid, balance | Record payment | Notes; result values | Billing | **NOT BUILT** |
| Reçu (print) | FD/Cashier *(D-1)* | Give proof of payment | Facility, patient, lines, amount paid, date | Print | Clinical content | Billing | **NOT BUILT** |

### DHealth Lab

| Screen | Roles | Primary task | Minimum data | Primary action | Not shown | Backend | Status |
|---|---|---|---|---|---|---|---|
| Aujourd'hui (lab) | Lab Staff; Releaser | See lab work owed | Unaccepted, awaiting collection, processing, entry incomplete | Open the next order | Provider visit notes | Lab workflow | **BUILT** |
| Réception | Reception *(D-1)* | Confirm who this is and why | Patient identity attributes (**D-4**), requested tests, order validity | Confirm arrival | Clinical notes; unrelated history | Orders + identity | **NOT BUILT** |
| Ordres (lab) | Lab Staff | Work assigned orders | Order, patient, tests, status | Accept / reject | Other labs' orders | Lab workflow | **BUILT** |
| Prélèvements | Lab Staff | Record collection; flag problems | Order, specimen history (instant + recorded-by), block reason | Record collection / record another specimen / block | Result values; any specimen type, container or label field | Specimens + `BLOCKED` | **PARTIAL** — the milestone, `BLOCKED`/resolve/history and now a **durable specimen history** are built (ADR-070) and live on `Ordres` as a `Prélèvements` panel; still no separate top-level `Prélèvements` section, no specimen types and no labels (**D-3's participant half**) |
| Saisie des résultats | Lab Staff | Enter structured values | Template fields, units, ranges | Save / complete entry | Release control | Templates + results | **BUILT** |
| Libération | Releaser | Release results | Entered values, completeness, fingerprint | Release | Administration | Release | **BUILT** |
| Rectificatifs | Lab Staff; Releaser | Amend after release | Prior version, revision | Release amendment | — | Amendments | **BUILT**, not browser-exercised |
| Étiquettes | Lab Staff | Label the tube | Patient, DOB, order id, date | Print / reprint | Clinical content | Labels | **NOT BUILT** |
| Facture (lab) | Cashier *(D-1)* | Charge for tests | Patient, tests, total, paid | Record payment | Result values | Billing | **NOT BUILT** |

### Administration and Patient

| Screen | Roles | Status |
|---|---|---|
| Équipe et accès | Org Admin | **BUILT** |
| Profil de l'établissement | Org Admin | **NOT BUILT** |
| Services et tarifs | Org Admin | **NOT BUILT** |
| Modèles de résultats | Org Admin | **BACKEND ONLY** |
| Rapports | Org Admin; others LTD | **NOT BUILT** |
| Patient status (no account) | Patient | **BUILT**, screen not browser-exercised |

### O-1b — the six-column orders table at 768px

DH-USABILITY-001 left this open and the queued brief handed it here: the clinic orders table's
trailing columns and its `Parcours` control fall outside the card at **768px, a supported staff
width** (ADR-060). ADR-060 removed the handset half of the problem; tablet remains and **must not
regress**.

**Settled here:** the orders list becomes **responsive rows, not a fixed six-column table** — the
same `repeat(auto-fit, minmax(min(100%, 22rem), 1fr))` rule DH-USABILITY-001 already used to fix
P1-2 elsewhere, which is a rule rather than a breakpoint. Patient identity and status stay visible at
every supported width; the `Parcours` control never leaves the card. Implemented by whichever ticket
first touches that screen — it is a layout correction, not a new feature, and is **not** a reason to
open a ticket of its own.

---

## 31. Workflow maps

### A — scheduled clinic visit

```
  PATIENT        FRONT DESK (D-1)        CLINICIAN             LAB
     |                 |                     |                  |
  books ---------> appointment BOOKED        |                  |
     |                 |                     |                  |
  arrives -------> mark arrival              |                  |
     |            visit -> CHECKED_IN -----> appears on         |
     |                 |                  Aujourd'hui           |
     |                 |                     |                  |
     |                 |              takes visit -> IN_PROGRESS|
     |                 |              documents reason + note   |
     |                 |              orders tests -------------> assigned
     |                 |              completes -> COMPLETED     |
     |                 |                     |                  |
     |            invoice + payment           |                  |
     |<----------- receipt printed            |                  |
```

### B — clinic walk-in

```
  PATIENT        FRONT DESK (D-1)        CLINICIAN
     |                 |                     |
  arrives -------> find patient
     |            not found -> register (name only)
     |                 |
     |            create visit directly -> CHECKED_IN --> Aujourd'hui
     |                 |                     |
     |                 |              takes visit, documents, orders
     |                 |                     |
     |            invoice + payment -> receipt
```

No appointment is invented. Walk-in is the ordinary case in this market.

### C — clinician diagnostic order

```
  CLINICIAN                                  EXCHANGE                 LAB
     |                                          |                      |
  open visit (IN_PROGRESS)                      |                      |
     |                                          |                      |
  select tests from catalog                     |                      |
     |                                          |                      |
  choose lab (active ProviderLabRoute only) --> order intent           |
     |                                          |                      |
  commit (idempotency key) -----------------> LabOrder + items -------> appears in lab queue
     |                                       linked to visit_id        |
     |                                          |                      |
  hand patient the QR sheet ------------------------------------------> identity at reception
```

### D — patient arrives at the laboratory

```
  PATIENT        LAB RECEPTION (D-1)          LAB STAFF
     |                 |                          |
  presents QR / name -> find the order            |
     |            confirm identity (D-4)          |
     |            confirm tests requested         |
     |            [payment if lab bills patient]  |
     |                 |                          |
     |            hand off -------------------> collection
```

If there is **no** order (a walk-in laboratory patient) there is **no path today** — §17, D-6.

### E — collection through release

```
  LAB STAFF                          RELEASER              CLINICIAN
     |                                  |                     |
  accept order                          |                     |
     |                                  |                     |
  record collection                     |                     |
     |                                  |                     |
  [specimen unusable?] --> BLOCKED + reason ------------------> appears as action owed
     |                                  |                     |
  start processing                      |                     |
     |                                  |                     |
  enter values (template-pinned)        |                     |
  complete entry per item               |                     |
     |                                  |                     |
     +-- entry complete --------------> release queue          |
                                        |                     |
                                   inspect + release          |
                                   (immutable version) ------> appears for review
```

`entered ≠ released` throughout. A technician without `LAB_RESULT_RELEASER` can never release.

### F — clinician result follow-up

```
  CLINICIAN
     |
  Aujourd'hui: "results awaiting review"
     |
  open released version (exact version, not "latest")
     |
  record review --> NO_FOLLOW_UP_REQUIRED  ---> closes the obligation
              \--> FOLLOW_UP_REQUIRED ------> appears on the visit + chart
                                              patient may be told (§22)
     |
  [amendment released later] --> a NEW review obligation on the new version
```

### G — staff access revocation (built)

```
  ORG ADMIN                              COLLEAGUE'S BROWSER
     |                                        |
  Équipe et accès                    (already signed in, valid token)
     |                                        |
  "Retirer l'accès" -> confirmation           |
  naming person + establishment               |
     |                                        |
  confirm --> membership INACTIVE             |
              audit row, same transaction     |
     |                                        |
     |                            next request --> REFUSED
     |
  [refused if this would leave zero active ORG_ADMIN — ADR-063,
   and PLATFORM_ADMIN does not bypass it]
```

### H — payment and receipt

```
  PATIENT        CASHIER / FD (D-1)          CLINICAL SIDE
     |                 |                          |
     |            open invoice for the visit      |
     |            (lines from services/offerings) |
     |                 |                          |
  pays ----------> record payment (method, amount)|
     |                 |                          |
     |            balance DERIVED, never stored   |
     |<----------- print receipt                  |
     |                 |                          |
     |            [unpaid?] --> D-2b. Default: does NOT gate release
```

### I — patient directory import

```
  ORG ADMIN
     |
  upload CSV / XLSX
     |
  map columns
     |
  DRY RUN --> per-row validation --> error report (download, fix, re-upload)
     |
  preview what will be created
     |
  commit --> patients created, each stamped with import provenance
     |
  durable import report (who, when, created/skipped/failed)
     |
  [duplicates surfaced to a human — NEVER auto-merged, ADR-051]
```

### J — mixed Native + Connect facility

```
   DHealth Clinic (Native)                       external LIS
      visits      NATIVE                             |
      billing     NATIVE                             |
      patients    NATIVE                             |
      orders -----------------> DHealth Exchange --> Connect --> their LIS
                                     |                            |
      results <------------------ Exchange <----- Connect <--- released there
      review      NATIVE
```

The clinic's screens are identical to an all-Native facility. Connect is the only boundary; visits and
billing gain no integration semantics.

---

## 32. Human usability qualification plan

DH-USABILITY-001 remains `INTERNAL WORKFLOW REVIEW — NOT USER-QUALIFIED`. **This document is not user
research**, and its gap severities (§33) are specification judgment from workflow reasoning and
repository evidence — **not** participant findings.

> **The participant product-gap list stays empty until sessions run.** DH-USABILITY-001 §B.5 requires
> that, and §33's P0–P3 list must never be presented as though participants produced it. Two separate
> lists, permanently.

### Participants

One front-desk worker, one clinician, one laboratory technician, optionally one organization
administrator. Real staff, not colleagues.

### Part 1 — testable now, against screens that exist

| # | Task, given verbally with no instructions | What is being observed |
|---|---|---|
| N-1 | "Find the patient named …" | Is search obvious? Enough to confirm identity? |
| N-2 | "Register a new patient who has never been here." | Does the name-only form read as incomplete? |
| N-3 | "Order a blood test for this patient." | Catalog vocabulary; is choosing a lab understood? |
| N-4 | "This patient is going to the laboratory. Make sure they can check their result." | Is the QR handoff discovered without prompting? |
| N-5 | *(lab)* "These orders arrived. Start work on this one." | Accept, then collection — is the order right? |
| N-6 | *(lab)* "Enter these values." | Template fields; units; what does "complete entry" mean? |
| N-7 | *(releaser)* "Release this result." | Is entry-vs-release understood, or does it feel redundant? |
| N-8 | *(admin)* "This person has left. Remove their access." | Is the confirmation clear? Does "access retiré" read correctly? |
| N-9 | "Show me what you'd do first thing in the morning." | Does `Aujourd'hui` match their real first act? |
| N-10 | Watch at **768px** on a real tablet. | O-1b; does anything fall outside the card? |

### Part 2 — testable only once built

| # | Task | What is being observed |
|---|---|---|
| F-1 | "A patient just walked in. Handle it." | Does walk-in → visit match their mental model? |
| F-2 | "Who is waiting right now?" | Is the waiting list where they look? |
| F-3 | "Write down why this patient came and what you found." | Is one free-text note enough, or is structure demanded? |
| F-4 | "Charge for this visit and give a receipt." | Is the invoice legible? Is the receipt acceptable to a real patient? |
| F-5 | *(lab)* "This sample is unusable." | Is `BLOCKED` + reason what they expected? Who do they think is told? |
| F-6 | "Import your existing patient list." | Column mapping; is the dry run trusted? |

### What to record per session

Hesitation of more than a few seconds; wrong first click; terminology the participant did not
recognise or renamed; any field they asked why they must fill; any workflow they expected and could not
find; any point they reached for paper; any place they would have to type the same thing twice; and any
moment they were unsure whether they were allowed to do something.

For each gap a participant raises, §34's questions: who asked, which workflow triggered it, how often
it appeared, whether the workflow is impossible without it, and whether a manual workaround exists.

### Accessibility and literacy principles

Plain language, never architecture vocabulary. One obvious next action per screen. Minimum required
fields. Critical state never conveyed by colour alone. **Patient identity prominent wherever
wrong-patient risk exists.** Destructive actions explicit and named — never an unlabelled icon.
Role-specific navigation so nobody scans past what they cannot use. French-primary.

**Low literacy is not solved with tooltips.** Fewer steps and fewer fields is the fix.

### Mobile and tablet — ADR-060 preserved

DHealth Patient is **mobile-first**. Staff surfaces require **1280 and 768**. A ~390px handset must not
break catastrophically but is **not** a primary staff work surface for MVP 1.0. Dense staff workflows
are not designed for a phone, and tablet support must not regress.

---

## 33. Gap classification

**P0 = a facility cannot reasonably operate without it**, each tied to a concrete operating failure —
never to what mature EMRs contain.

### P0 — switch blockers

| # | Gap | Operating failure | Facility |
|---|---|---|---|
| P0-1 | Visits / encounters | No record of why a patient came or what happened; no clinical thread | Clinic |
| ~~P0-2~~ **CLOSED 2026-09-30** | Arrival + waiting list | ~~Nobody knows who is waiting; a paper register runs alongside DHealth (Law 2)~~ Built and runtime-qualified (ADR-069). **Human usability still unqualified** — no receptionist has used it, so "no paper register" is not yet a measured claim | Clinic |
| P0-3 | Invoice, payment, receipt | Cannot take money or prove payment | Both |
| ~~P0-4~~ | ~~Honest `BLOCKED` state on an accepted order~~ **DONE 2026-09-29, ADR-068** | ~~A queue entry claims progress that is not happening; nobody owns it~~ | Lab |
| P0-5 | Patient-directory import | A facility with an existing patient list cannot begin | Both |
| P0-6 | Facility-owned services and prices | Billing cannot exist without something to charge for | Both |

*(P0-6 is inseparable from P0-3 and ships with it.)*

### P1 — high value; operable but materially incomplete or manual

Appointments/calendar · Lab Reception screen · ~~first-class specimen entity (**D-3**)~~ **[built
2026-10-01, ADR-070 — architectural half only; specimen types, accessioning and specimen-level
rejection remain D-3's participant half]** · specimen labels
· facility profile · facility-owned result templates · ~~offerings~~ **[built 2026-10-01,
ADR-071]** · printable result report ·
browser-qualifying the amendment/correction lifecycle · browser-qualifying the patient status screen ·
browser-qualifying native patient registration.

### P2 — pilot learning; validate through use

Reports · opening hours · notification configuration status · offline/PWA (measure real connectivity
first) · visit summary print · walk-in laboratory patient *(P0 if the pilot is a laboratory)* ·
appointment slip.

### P3 — later

Historical clinical migration · patient merge · analyser interfacing · staff localisation ·
break-glass administrator recovery · collection ticket · recurring appointments and reminders ·
patient self-booking.

### Not a gap — deliberately out of scope

Everything in §29.

---

## 34. Implementation dependency graph

```
                       Patient Directory  (BUILT)
                              |
            +-----------------+------------------+
            |                                    |
     Patient Import (P0-5)                  VISITS (P0-1)
                                                 |
                       +-------------------+-----+-------+------------------+
                       |                   |             |                  |
              Arrival + Waiting      Orders (BUILT)   Patient Chart    Services+Prices
                  (P0-2)                  |            (extend)          (P0-6)
                       |                  |                                 |
                Front Desk role       Results (BUILT)                   BILLING (P0-3)
                    (D-1) ----------------+                                 |
                       |                                                    |
                  Calendar (P1)                                      Reports (P2)


   LAB:   Lab Orders (BUILT) --> Collection (BUILT) --> Results (BUILT) --> Release (BUILT)
                 |                      |
           Reception (P1)         BLOCKED state (P0-4)   <-- independent, small, do early
                 |                      |
             (needs D-1)          Specimen entity (P1, D-3)
                                        |
                                  Labels (P1)
```

Rules the graph encodes:

- **Visits gate most of the clinic.** A waiting list is a list of visits; a bill is for a visit; a
  chart is a history of visits.
- **Billing depends on services and visits/orders, and must not control clinical release** (D-2b).
- **P0-4 depends on nothing.** It is small and independent — a strong early win.
- **D-1 gates three tickets** (Front Desk, Reception, Billing-with-a-cashier) and **not** the first.
- **Connect is orthogonal.** `DH-CONNECT-ADAPTER-001` is a parallel track, **not cancelled**;
  sequencing between the tracks is Liban's call.

---

## 35. Follow-up ticket sequence

Eight tickets. Each is independently useful and testable; none is a rewrite.

| # | Ticket | Depends on | Blocked by a decision? |
|---|---|---|---|
| 1 | ~~**`DHEALTH-CLINIC-VISIT-001`**~~ — Visits/Encounters, clinician-only | Patient directory (built) | **DONE** 2026-09-27, ADR-065 |
| 2 | ~~**`DHEALTH-LAB-BLOCKED-001`**~~ — honest cannot-proceed state | Lab workflow (built) | **DONE** 2026-09-29, ADR-068 |
| ~~3~~ **DONE 2026-09-30** | **`DHEALTH-FRONT-DESK-001`** — arrival, waiting list, ~~Front Desk role~~ **and deliberately no Front Desk role**: three independently grantable capabilities instead (ADR-069) | Ticket 1 | ~~**D-1** (Front Desk portion)~~ **resolved by this ticket** |
| 4 | ~~**`DHEALTH-BILLING-001`**~~ — services, invoice, payment, receipt | Ticket 1 | **DONE** 2026-09-28, ADR-066 |
| 5 | **`DHEALTH-MIGRATE-PATIENTS-001`** — CSV/XLSX directory import | Patient directory | No |
| 6 | **`DHEALTH-LAB-RECEPTION-001`** — lab reception; specimen entity if D-3 says so | Ticket 2 | **D-1**, **D-3**, **D-4** |
| 7 | **`DHEALTH-CLINIC-CALENDAR-001`** — minimal appointments | Tickets 1, 3 | No |
| 8 | ~~**`DHEALTH-ACCESS-CAPABILITIES-001`**~~ — organization roles, permission grants, capability-driven UI | Ticket 4 | **DONE** 2026-09-28, ADR-067 |

Ticket 4 shipped **without** ticket 3. The dependency on Front Desk existed only because Billing was
assumed to borrow Front Desk's authority; ADR-066 gave Billing its own instead, which removed the
dependency rather than working around it.

**8 · `DHEALTH-ACCESS-CAPABILITIES-001` — Organization Roles, Permission Grants and
Capability-Driven UI.** Purpose: make the capability seam ADR-066 §1 introduced into the real thing,
so a facility decides who may do what instead of inheriting it from a job title. Scope: role
templates; granular permission grants and revocations per organization; **protected capabilities**
that may never be delegated; effective-permission calculation for a member holding several roles and
overrides; navigation and action visibility driven by the computed result rather than by role names
in the frontend. Also migrates the clinical gates (`require_clinician`, `require_lab_tech`,
`require_lab_result_releaser`) onto capabilities — carefully, because each carries ADR history
(ADR-044 above all) that a careless migration would quietly discard. Backend: replace
`CAPABILITY_ROLE_GRANTS` with configurable per-organization grants; `exchange/billing.py` and every
billing endpoint should need **no** change, and if they do, the seam was wrong. Safety invariants:
the last-admin invariant (ADR-063) survives; no capability grant may confer result-release authority
by accident; `BILLING_CONFIGURE_PRICES` and `BILLING_OPERATE` stay separable. Out of scope: platform
administration, cross-organization roles, delegation between facilities. Qualification: unit +
real-browser, plus a negative control proving a revoked capability closes both the endpoint and the
navigation entry.

### Ticket briefs

**2 · `DHEALTH-LAB-BLOCKED-001`** — Purpose: an accepted order that cannot proceed gets an honest
state with a reason and a named next owner. Roles: `LAB_TECH` sets it; the ordering clinician receives
it. Backend: lab workflow state machine, new audit event type, migration. Frontend: lab
`Prélèvements`/order detail, clinician `Aujourd'hui`. Safety invariant: a blocked order must appear as
work owed to a human — never a silent state. Out of scope: specimen entity, recollection linkage,
labels. Qualification: unit + real-browser; a negative control proving the block is visible to the
clinician.

**3 · `DHEALTH-CLINIC-FRONTDESK-001`** — Purpose: arrival, check-in and the waiting list, with the
first non-clinical provider-side role. Roles: new Front Desk authority (**D-1**) + clinician. Backend:
new role, `require_*` gate, patient-directory read authority change, visit transitions. Frontend:
waiting list, arrival action, Front Desk `Aujourd'hui`. Safety invariant: Front Desk must not gain
clinical read — no result values, no clinical note. Out of scope: calendar, billing. Qualification:
real-browser, plus negative controls proving a Front Desk token is refused clinical endpoints.

**4 · `DHEALTH-BILLING-001`** — Purpose: charge, take payment, issue a receipt, show a balance. Roles:
cashier (**D-1**), Org Admin for prices. Backend: `CLINIC_SERVICE`, invoice, line, payment,
polymorphic billable source, audit, migration. Frontend: services and prices admin, invoice, payment,
printable receipt. Safety invariants: **billing never gates clinical release** (D-2b default); billing
roles see no clinical content; balance derived, never stored. Out of scope: insurance, ledger,
instalments, multi-currency. Qualification: real-browser; PostgreSQL concurrency on payment;
negative control proving an unpaid invoice does not block release.

**5 · `DHEALTH-MIGRATE-PATIENTS-001`** — Purpose: import an existing patient directory. Roles: Org
Admin. Backend: import job, provenance, idempotency, report. Frontend: upload, column mapping, dry
run, error report. Safety invariant: **no fuzzy matching, no silent merge** (ADR-051); duplicates go
to a human. Out of scope: clinical history, merge. Qualification: real file fixtures including
malformed rows; an idempotency test proving re-import creates nothing.

**6 · `DHEALTH-LAB-RECEPTION-001`** — Purpose: the person at the laboratory counter can confirm who
this is and what they are here for. Roles: lab reception (**D-1**). Backend: identity-confirmation
read scoped to an assigned order (**D-4**); specimen entity only if **D-3** requires it. Frontend:
`Réception`. Safety invariant: reception sees the identity attributes needed and **no** clinical
history. Out of scope: labels, analysers, walk-in lab patient (**D-6**). Qualification: real-browser;
negative control proving reception cannot read unrelated orders.

**7 · `DHEALTH-CLINIC-CALENDAR-001`** — Purpose: book, arrive, cancel, no-show. Roles: Front Desk,
clinician. Backend: appointment, states, visit linkage. Frontend: `Rendez-vous`. Safety invariant: an
appointment is never a clinical record; walk-ins never require a retroactive appointment. Out of scope:
recurrence, reminders, resources, self-booking. Qualification: real-browser.

---

## 36. First implementation ticket — `DHEALTH-CLINIC-VISIT-001`

Implementation-ready. The next agent should be able to start from this.

### Purpose

Give DHealth a clinical thread. A clinician can record that a patient was seen, why, what was found,
what was ordered, and that the encounter is finished — and can see that history next time.

### Why first

Everything else in the clinic depends on it: a waiting list is a list of visits, an invoice is for a
visit, a chart is a history of visits. It is also the only P0 that depends on **no** open decision.

### Deliberately clinician-only

Every action is performed by a `CLINICIAN` using the existing `require_clinician` gate. **No new
backend role**, so **D-1 is not blocking**. The state model is designed so that adding Front Desk
later changes *who may call a transition*, never the schema.

### Scope

**Domain (new):** `Visit` — `id`, `provider_organization_id`, `patient_id`, `clinician_identity_id`
(nullable), `status`, `reason_for_visit` (short free text), `appointment_id` (nullable, unused until
ticket 7), `started_at`, `completed_at`, `created_at`. `VisitNote` — append-only revisions, free text,
each with author and timestamp; earlier revisions retained.

**States:** `CHECKED_IN` → `IN_PROGRESS` → `COMPLETED`, plus `CANCELLED`. **`PLANNED` and `NO_SHOW`
are omitted** until a calendar exists — a state reachable by nothing is decoration (§12).

**Order linkage:** add a **nullable** `visit_id` to `LabOrder`. Nullable is required for backward
compatibility — existing orders have no visit and must keep working (`AGENTS.md`). It must be explicit
and not inferred: a `Patient` may hold several `PatientReference` rows and orders attach to the
reference, so "the patient's orders" is not "this visit's orders".

**API:** create visit; list visits for an organization filtered by status; get one visit with its
notes, orders and released results; take a visit (`CHECKED_IN` → `IN_PROGRESS`); append a note;
complete; cancel with a reason. All organization-scoped through the existing gate.

**Frontend:** a `Visites` staff section; the visit workspace; visits on the patient chart; visits
surfaced on `Aujourd'hui`; the existing new-order flow gains an optional visit context.

**Audit:** new transactional event types for visit created / taken / completed / cancelled and note
appended, in the same transaction as the change, following the established pattern. Migration widens
the existing CHECK constraint.

### Safety invariants

1. Organization-scoped: a clinician of one facility can never see another's visits. Anti-enumeration
   404/403 shapes preserved.
2. Clinical authority required — `require_clinician`. `ORG_ADMIN` alone is refused.
3. **No clinical interpretation.** Free text only. No structured vitals, no diagnosis codes, no
   scoring, no suggestion. Any structured clinical field needs clinical-lead sign-off first.
4. Notes are **append-only**. A correction is a new revision; earlier text is retained.
5. A visit never auto-completes. Results can arrive days later; a self-reopening visit is a status
   nobody owns.
6. `visit_id` on `LabOrder` stays nullable forever.
7. A completed visit is never deleted (§26).

### Explicitly out of scope

Front Desk role and arrival (ticket 3) · calendar (7) · billing (4) · vitals or any structured
clinical content · diagnosis coding (**D-5**) · visit templates · inpatient anything · patient merge ·
and the `Aujourd'hui` waiting list, which needs ticket 3's role.

### Qualification requirement

Unit tests at the service and API layer including refusals. **Real-browser** on the canonical Native
topology: a clinician signs in through real Keycloak, creates a visit for a seeded patient, documents
it, orders a test from it, completes it, and finds it on the patient chart afterwards. **Negative
controls**, executed and reverted, proving: an organization-scope break lets a clinician read another
facility's visits; removing the clinical gate lets a non-clinician create one. Existing suites — golden
journey, security, access lifecycle — must stay green. Real PostgreSQL only if concurrency is
load-bearing; a single-row status transition under a lock probably is not, and the ticket should say so
rather than perform ceremony.

---

## 37. What this document may and may not claim

**May claim:** DHealth Native's existing capability is inventoried at a stated evidence level; a
switch-ready definition exists with tests for necessity; the P0 set is tied to concrete operating
failures; the role model's one real blocker is identified; and the next seven tickets are sequenced
with the first ready to build.

**May not claim:** that any of it is built; that a facility could switch today; that participants
were consulted; that the severities are user findings; that the product is usable, deployed,
clinically approved or certified; or that any pilot gate moved. None did.

---

## 38. `DHEALTH-CLINIC-VISIT-001` as delivered (2026-09-27)

§36 above is the **brief**, left unedited as the instruction that was given. This section is what was
actually built, including the three places it departs from §36 and the gaps it deliberately left open.
ADR-065 carries the reasoning; this is the delta.

### Delivered

| | |
|---|---|
| Schema | `0034_clinic_visits`: `exchange_visits`, `exchange_visit_notes`, nullable `visit_id` on **both** `exchange_lab_orders` and `exchange_lab_order_intents`, six new audit event types, one new `resource_type`. Alembic head `0034_clinic_visits`. |
| Service | `adapter/src/daher_adapter/exchange/visits.py` — six operations, three reads, every transition under a `FOR UPDATE` row lock with the status re-read inside it. |
| API | Nine routes under `/organizations/{provider_id}/`: `POST visits`, `GET visits`, `GET patients/{id}/visits`, `GET visits/{id}`, `POST visits/{id}/take`, `POST .../complete`, `POST .../cancel`, `PUT .../reason`, `POST .../notes`. Every one gated by `_require_clinician` as its first statement. |
| Frontend | `Visites` staff section (`ui/src/features/visits/`), the worklist, the arrival form, the visit workspace with note revisions, and the order hand-off into `Patients`. |
| Qualification | 63 adapter tests; 11 real-PostgreSQL concurrency/constraint tests; 9 real-browser phases; 20 UI unit tests for the screen's refusals. |

### Departures from §36, and why

**1. Four states, not five — §36's own instruction, against the ticket's §6 wording.** The ticket that
commissioned this work said "use the exact five-state lifecycle recorded by the operating-product
specification". §36 (above) says `PLANNED` and `NO_SHOW` are omitted until a calendar exists, and the
ticket's own §32 independently forbids no-show handling. Two explicit sources against one adjective,
and no coherent five-state reading exists: keep `NO_SHOW` and §32 is violated; keep `PLANNED` and it
is unreachable, which §12 itself calls decoration. **Four states were built.** The database CHECK
constraint refuses `PLANNED` and `NO_SHOW`, so neither can be reintroduced by an application-layer
edit alone.

**2. `appointment_id` omitted.** §36 lists it as "nullable, unused until ticket 7". No appointment
table exists for it to reference, nothing would read it, and an unreferenced speculative column is
surface of exactly the kind DHEALTH-NATIVE-SEC-001's P1 was made of — a primitive with no caller. It
is added by the appointments ticket, together with the table that gives it meaning.

**3. `visit_id` is on the intent as well as the order.** §36 says "add a nullable `visit_id` to
`LabOrder`". That alone would have been unsafe: the UI orders through `LabOrderIntent`, and capturing
the visit at commit rather than at intent creation would let a lost-response retry move an order to a
different visit. The visit is captured on the immutable intent, copied forward by `commit` without
re-validation, and joined to the intent's replay comparison. ADR-065 §6 has the full argument.

**4. The visit read does not inline released results.** §36 says "get one visit with its notes, orders
and released results". Orders are returned; **result values are not**. Reaching them means opening the
order, through the read paths that already apply ADR-044's entry-is-not-release filtering. Projecting
results into a new response would have meant writing a second result-reading path whose release
filtering nobody had qualified, and an entered-but-unreleased value reaching a clinician through a new
screen is precisely what ADR-044 exists to prevent. The visit response has no field that could carry
one, which is asserted structurally.

**5. Cancellation takes no reason.** §36 says "cancel with a reason". `cancelled_at` is recorded and a
free-text cancellation reason is not, because §12 restricts cancellation to a visit that has **not
started** — nobody has seen the patient, and the honest content of such a reason is administrative
("did not wait", "wrong day") rather than clinical. A free-text field on a pre-consultation record is
a place for clinical content to be written where nothing reads it. If a real clinic asks for it, it is
a small additive change.

### Gaps this ticket deliberately left open, named rather than filled

| Gap | Why it was not filled here |
|---|---|
| **Amending a completed visit's note.** | A real clinical need. This codebase already has an explicit, audited, versioned amendment workflow for the analogous case (ADR-048, lab results); inventing a second, weaker one inside a lifecycle ticket would be worse than leaving the gap named. The interface says the note is closed rather than offering a field the server refuses. |
| **Reopening a completed or cancelled visit.** | Requires deciding what a reopened visit means for the record that said it was finished, and for the audit row that recorded the completion. Not a lifecycle question — a records question. |
| **`PLANNED` / `NO_SHOW` / appointments.** | Ticket 7. See departure 1. |
| **Who may register an arrival.** | **D-1**, unchanged and still not blocking. Today it needs `CLINICIAN` because that is the only clinical authority the backend has. `canRunVisits` in `workspaceCapabilities.ts` is the single place a front-desk answer will land, and the state model does not change when it does. |
| **Any structured clinical field** — vitals, SOAP, examination template, problem list, diagnosis code. | Clinical content, requiring the clinical lead's sign-off (`AGENTS.md`). **D-5** for coding specifically. The free-text note is unaffected. |
| **Waiting time, priority, triage order.** | Not a gap to be filled: a waiting time computed in a browser from two clocks is an operational signal DHealth must not produce, and triage is an explicit non-goal (§29). **[Tested against 2026-09-30, DHEALTH-FRONT-DESK-001.** That ticket's brief asked for a waiting duration explicitly, and §30's own screen inventory listed "waited-for". This rule won: the duration is **not built**, the recorded arrival time is shown instead, the subtraction is performed nowhere on the server or in the browser, and two tests assert the absence so a later contributor who reads the brief and helps is stopped by a failing test rather than by review. Raised as `Human Decision Required: Yes` in `PROJECT_STATE.md` with four options and no code written for any of them. This row is no longer only a principle — it has refused a real request.]** |

### What this does not make true

**Human usability is not claimed.** §32's usability qualification has not been run for this surface: no
clinician has used it, no timing has been measured against what it replaces, and product law 3
("faster than what it replaces") therefore remains a **validated target, not a current claim**. What is
demonstrated is that the workflow completes end to end in a real browser against a real stack.

**Pilot-ready is still not clinically live.** This adds a place to write a clinical note. It does not
add clinical sign-off on the order set or on any structured field, and nothing here is authorization
to use DHealth on a real patient.

---

## 39. `DHEALTH-BILLING-001` as delivered (2026-09-28)

**ADR-066.** Migration `0035_facility_billing`, `exchange/billing.py`, `exchange/capabilities.py`,
ten endpoints, the `Facturation` section. What follows is the delta between §18/§19's brief and what
was actually built — departures first, then the gaps that are named rather than filled.

### 39.1 Three departures from the brief, each reported rather than assumed

**(a) Billing did not wait for Front Desk.** §35's table made ticket 4 depend on ticket 3, because
Billing was assumed to borrow Front Desk's authority. It was given its own instead — `BILLING_CLERK`,
valid at PROVIDER and LAB — which removed the dependency rather than working around it. The Front
Desk half of D-1 is untouched and still open.

**(b) The authority is asked for as a capability, not a role.** The brief said "role". After
`require_billing_clerk` was built, it was **deleted** and replaced by `exchange/capabilities.py`, so
every billing call site asks *"may this person operate billing here"* rather than *"are you a
`BILLING_CLERK`"*. This is a departure in shape, not in scope: there is still no grant table, no
revocation and no per-user override. Those are `DHEALTH-ACCESS-CAPABILITIES-001` (§35, ticket 8), and
the point of the seam is that building it touches one dictionary and one function.

**(c) A tenth endpoint the brief did not anticipate: `GET /billing/patients?q=`.** §18 assumed a clerk
could reach a patient. They could not: a laboratory clerk starts from an order, but a clinic clerk has
no order to start from — a consultation is not one — and the patient directory is behind
`require_clinician`, which a cashier deliberately does not hold. This was raised as a blocking decision
rather than filled by instinct, because *who may read the patient directory without clinical authority*
is D-1's own subject. The answer taken: a narrow, separately gated lookup returning **id, display name
and date of birth** — not name only, because two people sharing a name is ordinary and a till that
cannot tell them apart bills the wrong one. Contact details, notes, visits, orders and results are
absent and structurally unreachable. It is not audited as a successful read, so **D-8 stays open**.

### 39.1a A navigation ordering that nearly changed a recorded decision

`Facturation` was first placed between `Résultats` and `Équipe et accès`, on the reasoning that
money follows the work that produced it. That is a defensible reading of one list and it was wrong,
for a reason the reasoning could not see: `resolveSection` lands on the **first** available section,
so an `ORG_ADMIN`-only account would have opened the application on `Facturation` instead of
`Équipe et accès`. DHEALTH-NATIVE-ACCESS-001 decided that landing explicitly — *"lands directly on
Équipe et accès, which is the only thing they came here to do"* — and `07-access-lifecycle.spec.ts`
asserts it in a real browser immediately after sign-in.

`Facturation` goes **last**, and `staffSections.ts` says why at the line that does it. The unit test
`still lands an administrator with no clinical role directly on the team section` now holds that
position deliberately rather than incidentally. A tidier navigation list is not a reason to move a
recorded product decision in passing — and the browser suite catching it is the argument for running
the sibling qualifications rather than only the new one.

### 39.2 What §18's laws became in code

| §18 | As built |
|---|---|
| Law 8 — balance is derived | No `balance` column and no `status` column; `test_bill51` asserts their absence in the schema rather than trusting the model |
| Money is exact | `Numeric(12, 2)`/`Decimal` throughout; no float touches money in the backend, and the frontend formats the server's **string** without ever parsing it |
| An invoice is immutable | Lines freeze price, description, quantity and currency at issue; a price change supersedes rather than edits |
| Payments are append-only | No update path, no delete path, no reversal; NC-BILL-6 confirms adding one reddens the suite |
| Four payment methods | `CASH`, `MOBILE_MONEY`, `CARD`, `BANK_TRANSFER` — **none of them an integration**: no provider, no merchant account, no secret |
| Receipts | Derived reference `{invoice_number}-R{n}`, so every receipt has a stable identity without a second counter |

### 39.3 Gaps named rather than filled

1. **Payment correction and voiding.** §26 left the policy undecided. The interface says a mis-keyed
   payment cannot yet be corrected rather than offering a control that rewrites financial history.
2. **Refunds, credits, discounts and patient balances across invoices.** Overpayment is *refused*
   because there is no credit model to hold the difference — not because refusal is ideal.
3. **Any inter-facility commercial arrangement** (D-2a): settlement, commissions, referral fees,
   revenue sharing, pass-through billing, markup, AP/AR between facilities.
4. **Insurance and third-party payers.** Not modelled at all. The invoice is to a patient.
5. **Tax, fiscal numbering and jurisdictional invoice rules.** `FAC-YYYY-NNNNNN` is a facility-scoped
   reference and says plainly that it is **not** a legal numbering scheme; a number is consumed and
   unused when a concurrent double-submit resolves to a replay, and that gap is accepted.
6. **A facility patient number.** DHealth has none to give — `PatientReference` is a provenance
   binding (ADR-051), not something a human reads aloud. Returning one would look like a patient
   number without being one.
7. **Reporting.** No daily takings, no revenue report, no export. A list of invoices is not a ledger.
8. **Human usability.** No cashier has used this and nothing has been timed against the paper ledger
   it replaces, so product law 3 remains a validated target.

### 39.4 What §35's table now says

Ticket 4 is **DONE**. Ticket 8, `DHEALTH-ACCESS-CAPABILITIES-001`, was added by this ticket and is
blocked by nothing. D-1's Billing portion and D-2a's interim answer are recorded in §28's table with
their still-open halves stated explicitly.

**Pilot-ready is still not clinically live.** This adds a till. It adds no clinical sign-off, no
pricing policy advice and no interpretation of any kind, and nothing here is authorization to use
DHealth on a real patient.

---

## 40. `DHEALTH-ACCESS-CAPABILITIES-001` as delivered (2026-09-28)

**ADR-067.** Migration `0036_capability_overrides`, `exchange/capabilities.py`, one resolver, every
gate migrated, three Team & Access endpoints, capability-driven navigation.

**Roles are templates. Capabilities are authority.** DHealth no longer needs to know somebody's job
title to decide whether they may perform a facility operation.

### 40.1 What a facility can now do that it could not

| Want | Before | Now |
|---|---|---|
| An administrator who also takes payment | Give them `BILLING_CLERK` as well, or invent `ADMIN_WITH_BILLING` | Grant `billing.operate` to that one person |
| A cashier who must not change prices | Not expressible | Deny `billing.configure_prices` for that one person |
| Two receptionists, one of whom bills | Two roles | One role, one override |
| A clinician covering the till on Saturday | Promote them | Grant, then withdraw |

None of this required a new role, and §62's combination-role explosion is now structurally
unnecessary rather than merely discouraged.

### 40.2 The capability catalog

Twelve, all derived from authority boundaries that already existed:

`patient.directory.read` · `patient.register` · `visit.read` · `visit.document` · `order.manage` ·
`result.review` · `lab.queue.work` · `lab.result.release` · `staff.access.manage` ·
`facility.configure` · `billing.operate` · `billing.configure_prices`

Each carries a French label, a description and the organization types that may hold it — served by
the API, so `Équipe et accès` shows human words and never an enum value.

### 40.3 Two corrections this ticket owed §18 and §27

**`Facturation` is now `billing.operate` only.** DHEALTH-BILLING-001 opened it for either billing
capability, so an administrator landed on the till and every control refused them. Price
configuration became its own section, `Services et tarifs`.

**`facility.configure` split from `staff.access.manage`.** `require_org_admin` guarded staff *and*
catalogue/result-template/directory configuration. `ORG_ADMIN` carries both, so no administrator's
authority changed.

**Neither is authority drift**, and that is checked rather than claimed: the authorization matrix
(10 personas × 16 boundaries = 160 cells, captured before the migration) is **identical** after it.

### 40.4 Gaps named rather than filled

1. **Custom organization role templates.** §13 permits deferring them and this ticket does. A
   facility composes through member overrides today. The seam is built: custom templates become a
   table and a resolver branch, with no feature code touched.
2. **Editable system templates.** Deliberately immutable (§24): editing a shared template can
   remove authority from many people at once, and doing that safely needs its own concurrency
   design.
3. **Per-resource scoping.** A capability is organization-wide. "May bill *this* patient" does not
   exist and is not needed by anything today.
4. **Time-bounded grants.** No expiry, no "until Friday". A grant is withdrawn by withdrawing it.
5. **D-8** (auditing successful sensitive reads) is still open and was deliberately not answered.
6. **Human usability.** No facility administrator has used the capability sheet, so product law 3
   remains a validated target.

### 40.5 What §35's table now says

Ticket 8 is **DONE**. Ticket 3 (`DHEALTH-CLINIC-FRONTDESK-001`) is still blocked by **D-1**, and
this ticket did not answer it — but answering it is now a product decision about *which
capabilities belong in a Front Desk template*, and implementing that answer touches no Billing,
Visits, patient-directory or check-in authorization code. That was the point.

**Pilot-ready is still not clinically live.** No staff member's access changed, and nothing here is
authorization to use DHealth on a real patient.

---

## 41. `DHEALTH-FRONT-DESK-001` as delivered (2026-09-30)

**ADR-069.** Migration `0038_visit_check_in`, `visit.check_in`, the arrival projection and its
endpoint, twenty-one route gates retargeted, the arrival surface on `Aujourd'hui`. **P0-2 is closed
and D-1 is resolved in full.** What follows is the delta between this specification's own brief and
what was actually built — the departures first, because they are the part a reader needs.

### Departures from this specification's own text, each deliberate

**1. There is no Front Desk *role*, and §6's matrix columns are wrong about that.** §7's role
visibility matrix has an `FD (D-1)` column and §30's screen inventory says `FD (D-1)` for five rows.
The answer turned out not to be a role at all: it is three independently grantable capabilities
(`patient.directory.read`, `patient.register`, `visit.check_in`), and §6's own §319 heading — *"Why
Front Desk cannot be a UI persona"* — was closer to the truth than the matrix that follows it. The
matrix rows are updated to name capabilities. **Nothing gates on a job title**, and the browser
qualification proves it by composing a *cashier* into a receptionist with three grants and the role
provably untouched.

**2. `Liste d'attente` is not a separate screen.** §30 lists it as its own row. It ships as part of
`Aujourd'hui`, and that is forced rather than chosen: `Visites` requires `visit.read` or
`visit.document`, which a reception-only membership deliberately lacks, so a waiting list living there
would be invisible to the person who runs it. `Aujourd'hui` is the one surface a receptionist and a
clinician both reach.

**3. Reception does not see the stated reason, though §7 permits it.** §7's minimum-data rules say
*"Front Desk sees the visit's reason but not its note"*. The arrival projection omits the reason too.
That is **narrower than this specification's ceiling**, not a contradiction of it: law 9 is minimum
necessary, a waiting room has no operational need for what the patient said about why they came, and
§7's own sentence ends *"If a facility disagrees, that is a policy decision, not a default."* This
ticket declines to make it the default. Re-adding it is a one-field change to
`WaitingArrivalResponse` plus the contract test, and a decision.

**4. Reception is not asked for a reason at all.** Following from 3: `reason_for_visit` became
nullable and the arrival form does not offer the field. A chief complaint typed at a reception desk is
clinical documentation under another name, which `AGENTS.md` puts behind the clinical lead's sign-off.
A clinician still records one, and still may correct it.

**5. 🛑 "Waited-for" is NOT built.** §30's `Liste d'attente` row lists it as minimum data and §25
lists "longest wait" as a reporting metric. §38 of this same document forbids it, `AGENTS.md`
(2026-09-27) forbids it, and both are more recent and more specific. The recorded arrival time is
shown; the subtraction is performed nowhere. **Raised as `Human Decision Required: Yes`** in
`PROJECT_STATE.md` with four options and no code written for any of them. Two tests assert the
absence, so the rule is pinned rather than merely observed. §38's row is annotated to record that it
has now refused a real request rather than only stated a principle.

**6. §49's "waiting duration not colour-only" is moot.** There is no duration to colour. Status
(`En attente` / `En consultation`) is rendered by `StatusBadge`, which pairs tone with a text label,
so the one distinction this surface draws is never colour-only.

### What §12's state table gained, and what it did not

Nothing. Still four states. `CHECKED_IN` **is** the waiting fact, as §12 already said, and this ticket
added no `WAITING` status — a second status meaning the identical thing would make two state machines
describe one patient. `PLANNED` and `NO_SHOW` remain absent with no appointment table; the future
calendar ticket feeds this *same* check-in operation.

### The authority boundary, as qualified

| A member holding… | can | cannot |
|---|---|---|
| `visit.check_in` only | register an arrival, read the waiting projection, cancel an arrival before clinical contact | open a visit, read a note, read the stated reason, start a consultation, complete, document, order, review, bill, touch the laboratory |
| `+ patient.directory.read` | find the right patient | — |
| `+ patient.register` | create a patient record | — |
| `+ billing.operate` | run the till as well (§28's one employee, both jobs) | — |
| `visit.document` | take a waiting patient, note, amend the reason, complete | — |

**A holder of `visit.check_in` cannot read a visit it created itself.** Creating a record is not a
licence to read it, and that asymmetry is the point of the split rather than an artefact of it.

### One correction this ticket owes §5's inventory

§5 and §7 both implied that "clinician authority" was one thing. It was: `require_clinician` was an
any-of over every provider capability, **including the two a receptionist legitimately holds**. So
until 2026-09-30 a reception member would have passed every gate behind it — order creation, doctor
review, the whole visit surface. That is now four capabilities rather than six, twenty-one route gates
name the capability they need, and **19 call sites still use the narrowed any-of** — the order,
routing, patient-access, notification and result-review surface: 15 reads and **4 mutations** (lab
assignment, patient-access issue and revoke, notification retry). Narrower than before, named here
rather than left to be discovered, and recorded as remaining work.

### What this does not make true

**Human usability is not claimed.** §32's usability qualification has not been run for this surface:
no receptionist and no clinician has used it, no timing has been measured against the paper register
it is meant to replace, and product law 3 ("faster than what it replaces") therefore remains a
**validated target, not a current claim**. P0-2's own problem statement — *"the clinic keeps a paper
register alongside DHealth"* — is addressed in software and **not yet demonstrated to be addressed in
a clinic**.

What *is* demonstrated is that the workflow completes end to end in a real browser against a real
stack, performed by a member with no clinical role and no role named for a front desk.

**Pilot-ready is still not clinically live.** This adds a place to record that somebody walked in. It
adds no clinical sign-off on any order set or structured field, and nothing here is authorization to
use DHealth on a real patient.

## 42. `DHEALTH-LAB-RECEPTION-001` as delivered (2026-09-30)

**No ADR. No migration. No new capability. No new order state.** One behavioural change — a sort
order — and the evidence that everything around it was already true. This section leads with that
because a reader who expects a feature will otherwise mistake the qualification for one.

### The finding that shaped the ticket

**Laboratory work intake was already built at `3e4b69b`.** DH-EX-006 built the incoming queue, the
order detail read, accept and reject, with the `LabOrder` row locked `FOR UPDATE` and idempotent
replay for a lost response. ADR-067 made the gate a capability. `DHEALTH-LAB-BLOCKED-001` added
`BLOCKED`. The interface has opened on `Nouveaux` — `SELECTED`, the incoming queue — since DH-LAB-001,
with accept and decline on the panel, and `LabHomePage` already counts new orders.

So the deliverable is a **qualification with one defect fixed**, and it is written that way rather
than as new work. §5's capability inventory needed no additions to support it.

### The one thing that was actually wrong

`list_for_lab` sorted **every** view `updated_at DESC`. For the six review views — `Terminés`,
`Refusés`, `En cours`, `Bloqués`, `Acceptés`, `Tous` — that is correct: the question there is what
changed recently. For `SELECTED` it is exactly backwards. `SELECTED` is the only *actionable backlog* a
laboratory holds, and newest-first buries the order that has been waiting longest underneath everything
that arrived since. That is how a routed order gets forgotten, which is the one failure a work queue
exists to prevent, and it fails §27's ownership test in the same way a silent `BLOCKED` state would
have.

The incoming view now sorts `LabSelection.selected_at ASC` with the order id as a stable tie-break.
Nothing else changed. `selected_at` is the routing fact rather than a convenient nearby column:
`LabSelection` is written once per order (`lab_order_id` is `unique=True`, `default=utc_now`, **no
`onupdate`**), so no later event can rewrite it and no reroute exists to move it. `updated_at` advances
on every mutation, so a backlog ordered by it reshuffles for reasons unrelated to how long the work has
been owed. `LabOrder.created_at` is when the *clinic* raised the order, which is a different fact.

**No duration is computed from it, anywhere.** Ordering by a recorded instant is not deriving a number
from one, and the second is what §38's named-gaps table and `AGENTS.md` (2026-09-27) forbid without the
clinical lead's sign-off. This is the same refusal `DHEALTH-FRONT-DESK-001` made on the clinic side,
applied to the laboratory side for consistency rather than re-argued. One browser phase asserts that no
queue card carries an elapsed time.

### Departures from this specification's own text, each deliberate

**1. `Réception` is not a new section, and §16's information-architecture table is updated to say so.**
That table lists `Réception` as **Add — P1**. It is not added, because `Ordres` opening on `Nouveaux`
*is* the reception surface: the same list, the same accept and decline, reached by the section a
laboratory already uses. Adding a second navigation entry over the same projection would have been the
dead-navigation failure `staffSections.ts`'s own rule forbids, arrived at from the other direction — two
doors into one room, with the reader left to guess which is authoritative. The row now reads *"Not
added — `Ordres` opening on `Nouveaux` is this surface (§42)."*

**2. The label stays `Nouveaux`, not `À recevoir` or `Réception`.** Established vocabulary that two
browser specs already reference, and `Nouveaux ordres` is the wording `Aujourd'hui` has used since
DH-LAB-001. Renaming it would have been a cosmetic change with a real regression surface and no product
gain.

**3. The decline carries no reason, and the ticket's own UI language assumed one exists.** It does not.
`reject()` is a pure `SELECTED → REJECTED` transition: no reason column, no taxonomy, nothing
reason-shaped reaching the clinic. This is **recorded rather than filled.** Inventing a reason
vocabulary here is exactly the *"any gap that obviously should be filled a particular way"* instinct
`AGENTS.md` names as the failure mode being guarded against — and this repository already has **D-11**
open because the block-reason vocabulary `DHEALTH-LAB-BLOCKED-001` shipped has not been validated by
anyone who works in a laboratory. Adding a second unvalidated vocabulary while the first waits on a
human would compound the problem rather than solve it.

Two tests and one browser phase pin the absence, so a later contributor who assumes a reason exists is
corrected by a failure rather than by reading the module. **The open question, stated narrowly:** should
a laboratory declining routed work be required to say why, and from what vocabulary? It belongs with
D-11, not ahead of it.

**4. Specimens are untouched, and `Prélèvements` is still not added.** **D-3** remains open. The
collection milestone (`LabSampleCollection`, `recorded_at` only) is what the browser qualification's
handoff phase records, and it stops there.

### The reception surface, as it actually is

| Question a laboratory counter asks | Where it is answered |
|---|---|
| What work has been routed to us and not yet answered? | `Ordres` → `Nouveaux` (`SELECTED`), oldest-routed first |
| Whose is it, and what was asked for? | the queue card: patient name and exact date of birth, ordering provider, the analyses requested |
| How many are waiting? | `Aujourd'hui` → `Nouveaux ordres` count |
| Do we take it? | `Accepter l'ordre` on the panel |
| Do we refuse it? | `Refuser l'ordre` → confirm. No reason is asked for (see departure 3) |
| What happens next? | the accepted order offers `Enregistrer la collecte`, then `Commencer l'analyse` — the workflow that already existed |

The authority for all of it is **`lab.queue.work`**. Not a role, and not a job title: a laboratory
*cashier* holding `BILLING_CLERK` and nothing else was composed into a reception worker by one grant
through `Équipe et accès`, with the role row provably unchanged, and then had the authority withdrawn
again — all by clicking, in a real browser. `lab.result.release` was never granted and the server
refused that same person a release. See `docs/NATIVE-SECURITY-QUALIFICATION.md` §12.

### What this does not make true

**Human usability is not claimed, and §32's plan has not been run for this surface.** No laboratory
worker has used it, no timing has been measured against whatever the laboratory does today, and product
law 3 ("faster than what it replaces") therefore remains a **validated target, not a current claim.**
What *is* demonstrated is that the workflow completes in a real browser against a real stack, performed
by a member with no laboratory role.

**The specimen question is not answered** (D-3), **the identity-confirmation attribute list is not
answered** (D-4), **successful-read auditing is not answered** (D-8), and **no reason vocabulary has been
validated by a laboratory participant** (D-11).

**Pilot-ready is still not clinically live.** This adds no clinical sign-off on any order set or
structured field, and nothing here is authorization to use DHealth on a real patient.
