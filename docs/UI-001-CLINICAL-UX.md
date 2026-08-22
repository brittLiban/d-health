# UI-001 — Unified Clinical UX + Data Contract Map

**Status:** Architecture/product specification. No frontend code exists yet. Nothing here is
implemented; UI-002 implements it.
**Date:** 2026-08-21 · **Audited against master `66536dd`** (`feat(DH-027H)`).

> **What this document is not.** It is not clinical content, and it invents none. Lab panel
> contents, ANC/partograph fields, danger thresholds, and staff-role assignments remain reserved
> to the clinical lead and Liban (`AGENTS.md`, `docs/CLINICAL-SIGNOFF.md`). Where this document
> needs one of those to be concrete, it says so and leaves the value blank rather than guessing.

---

## 0. Audit findings (read this before anything else)

**CURRENT HEAD:** `66536dd` — `feat(DH-027H): report scheduled backup evidence to the control plane`

**CURRENT TEST COUNT (measured against the real CI run, not copied):** adapter 94, provisioning
549, seed 16, form-safety 19 — **678 total**.

**CURRENT CLINICAL BLOCKERS (unchanged since last audit — see priority check below):**
- BLOCKER-6 (DH-011 target leg — how an order reaches OpenELIS): **open**, human decision.
- Numeric OpenELIS site/accession code: **not supplied**.
- ANC/partograph form fields: **not composed** — no form exists yet (`docs/CLINICAL-SIGNOFF.md` §6).
- Prenatal lab panel: **not composed** — nothing pre-selected (`docs/CLINICAL-SIGNOFF.md` §1–4).
- SSO staff names (BLOCKER-5 remainder): **not supplied** — role *types* are decided (ADR-017),
  named individuals are not.

**CURRENT PROVISIONING BLOCKERS:** DH-017/DH-027J need a fresh VM; DH-014's off-site leg needs
approved external storage credentials. Both external, both unchanged.

**PRIORITY CHECK (§35 of the brief):** none of the three named blockers — numeric OpenELIS site
code, a fresh VM, approved off-site backup credentials — have cleared as of this audit. Proceeding
with UI-001 as instructed.

**CURRENT UI IMPLEMENTATION STATE: none.** There is no Daher UI and no Daher UI API/BFF anywhere
in this repository. Staff today would use OpenMRS O3's native SPA and OpenELIS's native React
frontend **separately**, each with its own login screen (SSO activation is itself blocked on
BLOCKER-5's remainder). The adapter (`adapter/`) exposes exactly two HTTP endpoints —
`/health/live`, `/health/ready` — and nothing else; it has never had a UI-facing surface, and
`docs/ARCHITECTURE.md` §6 describes it as a background synchronization boundary, not a frontend
API. **Every "candidate endpoint" in this document is a design proposal, not an inventory of
something that exists.**

**EXISTING OPENMRS UI/API SURFACES, verified from source (`docs/ARCHITECTURE.md`, adapter code,
DH-011B's spike):**
- O3 3.7.x: Tomcat backend (REST `webservices.rest` module + FHIR R4 via `fhir2` 4.2.0) + a
  separate nginx-served React SPA. Pinned frontend already ships `esm-ward-app` and
  `esm-bed-management-app`.
- FHIR `ServiceRequest` on this server is confirmed **search/read-only** — the adapter's own code
  comment states this outright (`adapter/src/daher_adapter/openmrs.py`): *"ServiceRequest on this
  server is search/read only, so the adapter cannot write an order back into OpenMRS."* This is
  load-bearing for §17 below — a lab order cannot be created via FHIR on this server.
  Order creation, if the Daher UI does it, must go through OpenMRS's native REST `order`
  resource or the O3 order-basket workflow — **UNVERIFIED which**, not exercised by anything in
  this repo yet.
- Bed management verified in use: REST resources `bed` and `bedtype`, queried with
  `locationUuid` (`openmrs-config/seed/seed_maternity.py`) — the only OpenMRS bed API this repo
  has actually called.
- `fhir2` 4.2.0 accepts `_sort` and **silently ignores it** (found at DH-011E) — any UI/BFF code
  that lists FHIR resources must not assume server-side ordering.
- No antenatal or partograph form exists in this stack (`docs/CLINICAL-SIGNOFF.md` §6, checked by
  searching the whole distribution). The only 10 forms present are upstream demo content.
- Patient registration/search REST endpoints (`/ws/rest/v1/patient`) are the OpenMRS platform's
  standard, well-documented resource — **not independently re-verified against this exact pinned
  build in this audit**; marked UNVERIFIED below rather than assumed.

**EXISTING OPENELIS UI/API SURFACES, verified from source (DH-011B's spike, ADR-026):**
- Two distinct FHIR servers: the webapp's own embedded HAPI 7.0.2 exposing 8 **business**
  providers (`ServiceRequestProvider`, `PatientProvider`, `PractitionerProvider`,
  `SpecimenProvider`, `DiagnosticReportProvider`, `ObservationProvider`, `OrganizationProvider`,
  `FhirProviderUtils`) that write real `clinlims` domain rows through
  `SamplePatientEntryService`; and a separate `openelis-fhir` container (HAPI 6.6.0, generic JPA
  store, mutual TLS) that is **not** the business path.
- `ServiceRequest` creation through the business API is **currently refused** on this deployment:
  `SITEYEARNUM` accession formatting requires a 13-digit all-numeric site code, and the configured
  prefix is upstream's alphabetic `DEV01` (a numeric facility site code has never been supplied).
- A submitted `identifier` on `ServiceRequest` creation is **discarded** — OpenELIS generates its
  own; exact-token *search* does work (corrected finding, 2026-08-17), but *persistence* of an
  inbound correlation does not (DH-011D, conditional on the site code existing first).
- Native OpenELIS roles, confirmed in the running realm mapping (ADR-017): `Reception`,
  `Results`, `Validation`, `Global Administrator`. These are the OpenELIS-side authorization
  surface the Daher UI's `lab-tech` persona maps onto.
- OpenELIS's own React frontend is the only lab UI that has ever actually run against this stack.

**DOCUMENTATION DRIFT found during this audit:** none beyond what was already corrected in
DH-027H (PROJECT_STATE.md's header date, DH-027B's terminology). No new drift found affecting
this ticket's scope.

**PROPOSED UI-001 OUTPUT:** this document, `docs/UI-001-CLINICAL-UX.md`, plus a `PROJECT_STATE.md`
entry recording it as done and listing what remains for UI-002 and for clinical/product decisions.

---

## 1. The authorization model this document must not re-litigate (ADR-017)

**Exactly three Keycloak realm roles exist, decided and recorded — not proposed here:**

| Realm role | OpenMRS native role(s) | OpenMRS provider? | OpenELIS native role(s) |
|---|---|---|---|
| `clinician` | `Provider`, `Organizational: Nurse` | yes | — |
| `lab-tech` | — | no | `Reception`, `Results`, `Validation` |
| `sys-admin` | `Organizational: System Administrator` | no | `Global Administrator` |

**This has a direct, load-bearing consequence for the persona map in §2.** ADR-017 explicitly
folds midwives *and* doctors into the single `clinician` role for the pilot ("Doctors receive the
same access for the pilot rather than adding a second realm role"). **There is no separate
front-desk/reception realm role at all.** Reception, Midwife, and Clinician/Doctor personas below
are UX groupings over the *same* underlying `clinician` authorization — the Daher UI can present
different navigation and defaults to each, but it **cannot** enforce different server-side
permissions between them without a new realm role, which is a human decision (ADR-017 pattern),
not something this ticket or UI-002 may invent.

**Flagged as a genuine backend/product gap, not solved here:** if reception staff must be
*restricted* from clinical documentation (as opposed to merely not being shown it in the
navigation), that requires a fourth realm role and a superseding ADR. Until that decision exists,
the Daher UI's front-desk persona is a **navigation convenience, not a security boundary** — the
underlying account is exactly as privileged as any other `clinician`. This must never be
misrepresented to an operator as access control.

---

## 2. Roles / personas

Five personas, UX-level, mapped onto the three ADR-017 realm roles. "Auth role" is what the
server actually enforces; everything else here is Daher-UI-only navigation and defaults.

| Persona | Auth role (ADR-017) | Primary goal | Sees | Can change | Cannot change |
|---|---|---|---|---|---|
| **Réception** | `clinician` (no dedicated role — see §1) | Get an arriving patient found or registered and admitted | Patient search, registration, admissions list, bed board (read) | Patient demographics, new registrations, admission records | Clinical documentation, lab results, orders (not prevented server-side today — UI convention only, see §1) |
| **Sage-femme (Midwife)** | `clinician` | Run labour/maternity care for admitted patients | Patient workspace, maternity dashboard, labour workspace, bed board, lab orders/results | Maternity/labour observations (data-entry only, §12), bed assignment, lab orders | Lab result values, lab workflow states, other facilities' data |
| **Médecin (Clinician/Doctor)** | `clinician` | Same access as Sage-femme for the pilot (ADR-017) | Same as Sage-femme | Same as Sage-femme | Same as Sage-femme — **no scope-of-practice split exists yet**; if one is wanted, that is a new realm role (ADR-017 says so explicitly) |
| **Technicien de laboratoire (Lab tech)** | `lab-tech` (OpenELIS `Reception`+`Results`+`Validation`) | Run the lab worklist end to end, alone if needed | Lab workspace, result entry, validation, specimen handling | Sample/specimen state, result values, validation status | Patient demographics, maternity/labour records, admissions |
| **Administrateur** | `sys-admin` (OpenMRS `System Administrator` + OpenELIS `Global Administrator`) | Operate the deployment, not treat patients | System/user configuration surfaced by each native admin console; **not** a Daher-UI-native admin screen in V1 (see §17) | Configuration exposed by OpenMRS/OpenELIS's own admin UIs | Nothing new — `sys-admin` is a pass-through to native admin consoles, not a Daher UI feature |

**What this table cannot yet state, and why:** which named individuals hold which role
(BLOCKER-5's remainder) — a roster fact, not a design one, and out of scope for UI-001 by
instruction (§29 of the brief: do not touch staff assignments).

---

## 3. Navigation map (role-aware)

Base items, from current product direction, gated per persona. No item is shown to a persona that
has no verified reason to see it — this is a UX convenience only (§1: not a security boundary).

| Nav item | Réception | Sage-femme | Médecin | Lab tech | Admin |
|---|:-:|:-:|:-:|:-:|:-:|
| Tableau de bord | ✅ (front-desk view) | ✅ (maternity view) | ✅ (maternity view) | ✅ (lab view) | ✅ (system view) |
| Patients | ✅ | ✅ | ✅ | — | — |
| Maternité | — | ✅ | ✅ | — | — |
| Travail | — | ✅ | ✅ | — | — |
| Lits | ✅ (read) | ✅ | ✅ | — | — |
| Laboratoire | — | ✅ (order/view) | ✅ (order/view) | ✅ (full workflow) | — |
| Résultats | — | ✅ | ✅ | ✅ | — |
| Notifications | ✅ | ✅ | ✅ | ✅ | ✅ |
| Paramètres | account only | account only | account only | account only | ✅ full |

"Tableau de bord" is not one screen — it is one route rendering a persona-specific card set (§11).

---

## 4. French terminology glossary

Canonical term the UI uses; the technical name it must never surface to a clinical user.

| Canonical French (UI-facing) | English gloss | Technical name (never shown to staff) |
|---|---|---|
| Patient | Patient | `Patient` (FHIR/OpenMRS resource) |
| Dossier patient | Patient record | patient chart |
| Admission | Admission | OpenMRS `Encounter`/`Visit`, admission location |
| Maternité | Maternity ward | `Maternité` admission location (already deployed, DH-003) |
| Salle d'accouchement | Delivery bed type | `bedType` = delivery (already deployed) |
| Post-partum | Postpartum bed type | `bedType` = postpartum (already deployed) |
| Lit | Bed | `bed` (OpenMRS REST resource) |
| Travail | Labour | labour/partograph data entry (not yet built — §6, §12) |
| Ordonnance de laboratoire | Lab order | FHIR `ServiceRequest` (OpenMRS) → OpenELIS accession (never shown) |
| Échantillon | Specimen/sample | OpenELIS `sample`/`specimen` |
| Résultat | Result | FHIR `Observation`/`DiagnosticReport` (OpenELIS → OpenMRS) |
| Résultat préliminaire | Preliminary result | OpenELIS result status pre-validation — **PENDING DH-012 EVIDENCE**, §15 |
| Résultat validé | Validated/final result | OpenELIS result status post-validation — **PENDING DH-012 EVIDENCE** |
| Panneau prénatal | Prenatal panel | not yet composed — `docs/CLINICAL-SIGNOFF.md` |
| Partogramme | Partograph | not yet built — data-entry only by decision (ADR-006) |

**Deliberately one canonical term per concept.** "Ordonnance" is used for the lab-order concept
throughout — not "commande" or "demande" — because it is the term already used informally in
`docs/CLINICAL-SIGNOFF.md` and matches standard Francophone clinical usage. This is a UI-001
naming decision, not yet clinically signed off; the clinical lead may override it when the
prenatal panel/partograph sign-off happens.

**Never surfaced to clinical users, anywhere in the UI:** `ServiceRequest`, `DiagnosticReport`,
`Observation`, FHIR, HAPI, UUID, resource version, adapter ledger, PostgreSQL, Docker,
`clinlims`, realm role names (`clinician`/`lab-tech`/`sys-admin` are backend identifiers, not
UI copy).

---

## 5. Main user journey — patient arrival to discharge

```
PATIENT ARRIVES
      │
      ▼
SEARCH PATIENT ── screen: Patients / search
  │  role: Réception (primarily)
  │  read: OpenMRS patient search REST — UNVERIFIED against this exact pinned build
      │
  ┌───┴────┐
 found   not found
  │          │
  ▼          ▼
open      REGISTER PATIENT ── write: OpenMRS patient REST — UNVERIFIED endpoint shape
record       │  actor: authenticated staff Keycloak identity (§22)
  │          │  failure mode: validation error, duplicate-candidate warning (not auto-merge)
  └────┬─────┘
       ▼
   ADMISSION ── screen: Patients / patient workspace / Admettre
     role: Réception or Sage-femme
     write: OpenMRS visit/encounter + admission location = Maternité
     failure mode: no bed available → visible in Lits before admission is attempted
       │
       ▼
   MATERNITY WORKSPACE ── screen: Maternité / patient workspace tab
     role: Sage-femme, Médecin
     read: patient summary, pregnancy context (source: OpenMRS)
       │
       ▼
   LABOUR / OBSERVATIONS ── screen: Travail
     role: Sage-femme, Médecin
     write: structured, time-stamped observations only (ADR-006 — §12)
     system of record: OpenMRS (form not yet built — CLINICAL-SIGNOFF.md §6)
       │
       ▼
   LAB ORDER ── screen: Laboratoire / Nouvelle ordonnance
     role: Sage-femme, Médecin
     write: OpenMRS native order resource — NOT FHIR ServiceRequest (confirmed read-only, §0)
     system of record: OpenMRS (order), OpenELIS (once ingested)
     ⚠️ delivery to OpenELIS is BLOCKED ON BLOCKER-6 — see §17
       │
       ▼
   LAB WORKFLOW ── screen: native OpenELIS UI or Daher lab workspace (§14)
     role: Technicien de laboratoire
     write: OpenELIS specimen/sample/analysis state
       │
       ▼
   RESULT ── read: FHIR DiagnosticReport/Observation (OpenELIS → OpenMRS, via adapter)
     ⚠️ reverse leg is UNVERIFIED end to end (docs/ARCHITECTURE.md §6: "no delivery has been
     observed"; corrected-result semantics untested — §15)
       │
       ▼
   RESULT APPEARS IN PATIENT RECORD ── screen: patient workspace / Ordonnances & Résultats
     read: OpenMRS chart (once the adapter has written it back)
       │
       ▼
   DELIVERY / DISCHARGE ── screen: patient workspace / Maternité
     write: OpenMRS encounter, bed release
     failure mode: bed not released → visible immediately in Lits
```

**Every write step above needs the states in §18** (Saving… / Saved / Save failed / Connection
lost — not saved) — not designed per-step twice; §18 is the one contract every write screen
follows.

---

## 6. Patient search

- **Fields:** name, identifier, date of birth — the standard OpenMRS patient-search parameters.
  **UNVERIFIED against this exact pinned build**; no search call has been exercised in this repo.
- **Result cards:** name, age/DOB, identifier, most recent admission status if any.
- **Duplicate handling:** the UI must **never auto-merge**. A search returning a close-but-not-exact
  match surfaces both as separate cards with a "possible duplicate" indicator; merging patient
  identities is an OpenMRS administrative action, out of scope for the Daher UI in V1.
- **No result:** direct path to "Nouveau patient" (registration), pre-filling whatever search terms
  were typed.
- **Extension point, not built:** result cards must be able to carry `source_facility`,
  `source_record_id`, `source_timestamp`, `source_provider`, and a local/external indicator without
  a redesign, so a future HIE-sourced record can render without restructuring the card component.
  **HIE is explicitly not implemented by this ticket or any ticket now** (`docs/ARCHITECTURE.md`
  §14, out of scope for v1).

---

## 7. Patient workspace — information architecture

```
┌─────────────────────────────────────────────────────────┐
│ PATIENT HEADER                                           │
│ Name · age/DOB · identifier · pregnancy context ·        │
│ admission state (bed, ward)                               │
├─────────────────────────────────────────────────────────┤
│ [Résumé] [Grossesse] [Travail & Accouchement]            │
│ [Ordonnances & Résultats] [Documents]                     │
└─────────────────────────────────────────────────────────┘
```

| Tab | Owning system | What it shows | Notes |
|---|---|---|---|
| Résumé | OpenMRS | demographics, current admission, recent encounters | |
| Grossesse | OpenMRS | pregnancy-context observations (ANC form) | **form does not exist yet** — CLINICAL-SIGNOFF.md §6 |
| Travail & Accouchement | OpenMRS | labour observations, data-entry only (§12) | **form does not exist yet** |
| Ordonnances & Résultats | OpenMRS (orders) + OpenELIS (results, via adapter) | orders placed, results received | see §15 for what "corrected" means — unresolved |
| Documents | OpenMRS | any attached complex observations | not otherwise scoped by this ticket |

**No tab stores independent Daher-UI clinical truth.** Every tab is a read (and, where noted, a
write) against OpenMRS or OpenELIS; the Daher UI API composes reads, it does not own data (§17).

---

## 8. Maternity dashboard

| Card | Meaning | System of record | API | Refresh | Aggregation-safe? | Permission |
|---|---|---|---|---|---|---|
| Patientes admises | Count, currently admitted to Maternité | OpenMRS | admission-location query — UNVERIFIED exact endpoint | on load + manual refresh | yes — a count, not a list of values | `clinician` |
| En travail | Count, patients with an open labour episode | OpenMRS | **no verified query exists** — labour has no encounter type yet because the form doesn't exist | — | — | — |
| Lits disponibles | Available maternity beds | OpenMRS `bed` resource, filtered by `locationUuid` | verified resource, unverified "available" filter shape | on load | yes | `clinician` |
| Ordonnances laboratoire (en attente) | Orders placed, not yet resulted | OpenMRS orders + OpenELIS state, composed | **UNVERIFIED** — no order-status join has ever been exercised | — | needs care — see below | `clinician` |
| Résultats prêts | Results received, not yet reviewed | OpenMRS chart write-back | depends on the adapter's reverse leg, which is **unverified end to end** (§0) | — | — | `clinician` |

**"En travail" and "Résultats prêts" are marked `UNAVAILABLE` in the V1 build, not implemented with
an invented query.** Per the brief's own instruction: if an accurate number cannot currently be
obtained from an approved API, the card says so rather than querying internal tables directly.
This is not a placeholder to fill in casually later — it depends on the ANC/partograph forms
existing (clinical sign-off) and the adapter's reverse leg being demonstrated (DH-011, blocked on
BLOCKER-6), both genuine blockers, not UI work.

---

## 9. Labour workspace

**UI-001 defines the input architecture only. It defines zero clinical fields.**

What the UI may record, once the form exists (clinical sign-off pending):
- timestamp of each observation
- the approved observation fields themselves (values TBD by the clinical lead)
- documented measurements as entered
- free-text notes
- the recording provider's identity (from Keycloak — §22)

**What this document explicitly forbids designing, per the brief and ADR-006:**
- danger thresholds, alert lines, action lines
- risk scores, triage logic, prolonged-labour flags
- any generated recommendation or automatic interpretation

`openmrs-config/forms/validate_form_safety.py` already enforces this at the form-schema level for
whatever form is eventually authored — a computed value, alert, or expression validator fails the
build. The Daher UI's labour screen is a **structured, chronological data-entry and display
surface**, nothing more, matching the ADR-006 boundary exactly.

---

## 10. Bed board

| Concept | Source | API (verified) |
|---|---|---|
| Wards/rooms | OpenMRS location | `Maternité` admission location (DH-003, deployed) |
| Beds | OpenMRS `bed` resource | `bed`, queried by `locationUuid` (verified — `seed_maternity.py`) |
| Bed type | OpenMRS `bedtype` resource | verified in the same seed job |
| Occupied/available/maintenance/out-of-service | OpenMRS bed status | **UNVERIFIED which status vocabulary bedmanagement 7.2.0 actually exposes over REST** — not exercised by anything in this repo beyond create/list |
| Patient assignment | OpenMRS bed-patient assignment | **UNVERIFIED** |
| Transfers | OpenMRS bed-patient reassignment | **UNVERIFIED** |

**Bed counts shown in any mockup deriving from this document are illustrative only** — real
counts are facility configuration (`MATERNITY_DELIVERY_BEDS`/`MATERNITY_POSTPARTUM_BEDS`), still
placeholders (`docs/ARCHITECTURE.md` §7), and must never be hand-typed into the UI spec as if real.

---

## 11. Laboratory workspace

```
NOUVELLES ORDONNANCES → ÉCHANTILLON / PRÉLÈVEMENT → EN COURS → SAISIE DU RÉSULTAT →
VALIDATION → TERMINÉ            (ANNULÉ — only where an OpenELIS state proves it's reachable)
```

**Business-rule ownership, decided by this document rather than left ambiguous:** every state
transition above is an **OpenELIS business rule**, owned by OpenELIS. The Daher UI must call
OpenELIS's authoritative API for each transition — it must **not** reimplement lab workflow logic
in the frontend. Where OpenELIS's own React UI already implements a step well (specimen
accessioning, result entry, validation), the pragmatic default is to **link out to it** rather
than rebuild it, until a specific screen is proven worth re-hosting inside the Daher UI. This
document does not decide that per-screen call now — it is a UI-002 implementation choice, not an
architecture one, **provided** the underlying rule stays server-side in OpenELIS either way.

**Cancellation:** ADR-018 already decided the *adapter's* behavior (revoke if not yet accepted,
leave in-progress work for manual handling) — this UI must reflect that state honestly (an
order cannot be shown as "cancelled" in the Daher UI unless OpenELIS confirms it was actually
revoked), not assume the OpenMRS-side discontinuation alone succeeded.

**Native OpenELIS role mapping:** `lab-tech` → `Reception` + `Results` + `Validation` (ADR-017,
deliberately unsplit for a small lab). The Daher UI's lab workspace therefore never needs a
narrower in-app permission than OpenELIS itself enforces — there is no lab-tech sub-role to design
around.

---

## 12. Result workspace

| Field | Source | Notes |
|---|---|---|
| Test name | OpenELIS → OpenMRS chart | |
| Result value | OpenELIS → OpenMRS chart | |
| Unit | OpenELIS | |
| Reference range | OpenELIS, if supplied authoritatively | do not compute one client-side |
| Status | OpenELIS | preliminary/final vocabulary — **PENDING DH-012 EVIDENCE**, see below |
| Collected time | OpenELIS | |
| Finalized time | OpenELIS | |
| Source laboratory | OpenELIS (this facility only in V1) | extension point for `source_facility`, not built (§6) |
| Provider/validator | OpenELIS `Validation` role holder | |
| Correction history indicator | **not designed** | |

**Explicitly marked PENDING DH-012 EVIDENCE, per instruction:** `docs/ARCHITECTURE.md` §6 states
plainly that "corrected-result version/timestamp behavior is also untested and must be
characterised before trusting the result path." This document does **not** invent
preliminary/final/corrected UI semantics ahead of that characterization. The result screen's
status badge must not claim a distinction (preliminary vs. final vs. corrected) that has not been
proven to exist cleanly in this OpenELIS build's actual FHIR output. Until DH-012 produces that
evidence, the result screen shows OpenELIS's status field verbatim (translated to French, not
reinterpreted) rather than a Daher-invented three-state model.

---

## 13. Data-contract matrix

The central deliverable. Every UI element a future engineer will ask "where does this come from"
about.

| Screen | Field/action | Authoritative system | Read source | Write destination | API/resource | Permission | Audit actor | Refresh | Known/Unverified | Blocker |
|---|---|---|---|---|---|---|---|---|---|---|
| Patients | Search | OpenMRS | OpenMRS REST patient search | — | `/ws/rest/v1/patient?q=` (standard) | any authenticated | n/a (read) | on query | **UNVERIFIED** | none — needs exercising |
| Patients | Register | OpenMRS | — | OpenMRS REST | `/ws/rest/v1/patient` (standard) | `clinician` | Keycloak identity | n/a | **UNVERIFIED** | none |
| Patient workspace | Résumé | OpenMRS | OpenMRS REST/FHIR | — | patient + active visit | `clinician` | n/a | on load | UNVERIFIED shape | none |
| Patient workspace | Admettre | OpenMRS | — | OpenMRS visit/encounter | admission-location write | `clinician` | Keycloak identity | n/a | UNVERIFIED | none |
| Maternité | Grossesse tab | OpenMRS | ANC form encounters | ANC form encounters | **form does not exist** | `clinician` | Keycloak identity | on load | Known gap | clinical sign-off, CLINICAL-SIGNOFF.md §6 |
| Travail | Observations | OpenMRS | labour form encounters | labour form encounters | **form does not exist** | `clinician` | Keycloak identity | on load | Known gap | clinical sign-off |
| Lits | Bed board | OpenMRS | `bed`, `bedtype` | bed-patient assignment | verified resource names; assignment/status UNVERIFIED | `clinician` | Keycloak identity | on load + manual | Partially verified | none |
| Laboratoire | Nouvelle ordonnance | OpenMRS (order) | — | OpenMRS **native order resource, not FHIR** | `/ws/rest/v1/order` (standard) — UNVERIFIED against this build | `clinician` | Keycloak identity | n/a | UNVERIFIED write path | delivery to OpenELIS blocked, BLOCKER-6 |
| Laboratoire | Worklist/results entry | OpenELIS | OpenELIS native | OpenELIS native | OpenELIS business API / native UI | `lab-tech` | OpenELIS user or Daher-UI-propagated identity — **UNRESOLVED**, §22 | on load | Known, OpenELIS-owned | none for entry itself |
| Résultats | Result display | OpenELIS → OpenMRS | OpenMRS chart (post adapter write-back) | — | FHIR `Observation`/`DiagnosticReport` (adapter-mediated) | `clinician` | n/a (read) | on load | **UNVERIFIED reverse leg** | BLOCKER-6 + DH-012 |
| Tableau de bord | Patientes admises | OpenMRS | admission-location query | — | UNVERIFIED endpoint | `clinician` | n/a | on load | UNVERIFIED | none |
| Tableau de bord | En travail | OpenMRS | — | — | **no query exists** | `clinician` | n/a | — | Unavailable by design (§8) | clinical sign-off (form doesn't exist) |
| Tableau de bord | Lits disponibles | OpenMRS | `bed` | — | verified resource, unverified filter | `clinician` | n/a | on load | Partially verified | none |
| Tableau de bord | Ordonnances en attente | OpenMRS+OpenELIS | composed | — | **no join exists** | `clinician` | n/a | — | Unavailable by design (§8) | BLOCKER-6 |
| Tableau de bord | Résultats prêts | OpenMRS | chart write-back | — | depends on reverse leg | `clinician` | n/a | — | Unavailable by design (§8) | BLOCKER-6, DH-012 |
| Paramètres (admin) | Any config | OpenMRS/OpenELIS native admin | native | native | native admin consoles, not Daher UI | `sys-admin` | native system's own audit | n/a | Known — pass-through, not built | none, by design (§17) |

**Reading this table correctly:** "Known/Unverified" is honest about what has actually been
exercised in this repo (mostly: bed resources, and the fact that `ServiceRequest` is read-only on
OpenMRS). Everything marked UNVERIFIED is a standard, documented OpenMRS/OpenELIS capability that
is plausible but **has never been called against this exact pinned deployment**, and must be
proven, not assumed, before UI-002 builds against it.

---

## 14. The Daher UI API / BFF — candidate boundary, not implemented

**Purpose:** compose screen-oriented reads, reduce browser chatter, centralize
authorization-aware aggregation, normalize frontend error semantics, propagate staff identity.

**It must not, ever (repeating the brief's own constraint because it is load-bearing):**
- store independent clinical truth
- perform asynchronous OpenMRS/OpenELIS synchronization (that is the **adapter's** job —
  `docs/ARCHITECTURE.md` §6 — and this document does not change that boundary)
- interpret clinical results, generate diagnoses, or score risk
- replace OpenMRS or OpenELIS
- become an integration event ledger

**Candidate endpoint shapes** (illustrative only — names are not committed; a real implementation
must check whatever service-naming convention the provisioning/adapter code already established
before choosing final paths):

```
GET  /ui/patients/search?q=
GET  /ui/patients/{id}/summary
GET  /ui/patients/{id}/laboratory
GET  /ui/maternity/dashboard
GET  /ui/beds
POST /ui/patients/{id}/admissions
POST /ui/orders/laboratory
```

Every one of these is a **composition** over an existing OpenMRS/OpenELIS API already in the
data-contract matrix (§13) — none is a new source of truth. Where §13 marks the underlying source
UNVERIFIED, the corresponding BFF endpoint is equally unverified and must not be built first.

**Relationship to the adapter, stated once, clearly:** the adapter (`adapter/`) and this BFF are
two different systems with two different jobs, per the brief's own instruction (§3). The adapter
is deterministic, asynchronous, and owns the OpenMRS↔OpenELIS clinical-safety ledger. The BFF is
synchronous, screen-oriented, and owns nothing durable. **They must not be merged**, and this
document does not propose merging them.

---

## 15. Authentication / authorization propagation

```
browser → Keycloak identity (OIDC, realm `daher`) → Daher UI API → OpenMRS (OIDC) / OpenELIS (SAML)
```

**Human provenance must survive the hop through the BFF.** A write from the Daher UI must not
collapse into `system_user = daher-service` with no trace of which staff member acted — that
would be a regression from what the adapter itself already gets right (its own Keycloak service
account is a *machine* identity, deliberately distinct from any clinician's — `docs/ARCHITECTURE.md`
§5). The BFF must forward the authenticated staff member's identity on every write it proxies.

**Unresolved seam, flagged rather than solved:** OpenMRS's OIDC session and OpenELIS's SAML
session are two different protocols with two different upstream identity assertions. Whether the
Daher UI BFF can cleanly propagate one browser-side Keycloak session into both backend protocols
without either backend perceiving the Daher UI as a shared "service" identity is **UNVERIFIED —
this is a real integration question for UI-002, not answered by this document.** If it cannot be
done cleanly, the fallback (each backend call using its own already-established SSO session
cookie/token, with the BFF acting as a thin reverse proxy rather than a token-minting service) must
be evaluated before committing to a design — that evaluation is explicitly **not done here**.

---

## 16. Write-path discipline

For every mutation in §13:

- **Authoritative system:** OpenMRS or OpenELIS, never the BFF (§14).
- **Actor:** the authenticated staff Keycloak identity (§15), not a service account.
- **Network drop after server acceptance:** the write already happened upstream; the browser must
  not assume it did not. The UI must re-fetch to confirm state rather than silently retrying a
  write whose result is unknown.
- **Safe retry:** only for operations proven idempotent by the backend (e.g., a GET-then-write
  pattern with a stable key). **No endpoint in this document is asserted idempotent** — that must
  be verified per-endpoint in UI-002, not assumed from this table.
- **Duplicate submission:** prevented client-side by disabling the control during a pending write,
  and server-side only where the backend actually enforces it (unverified per-endpoint, §13).
- **Confirmation:** the UI shows `Saved` only after a server acknowledgement, never optimistically.

States, used uniformly across every write screen rather than invented per-screen:

`Idle → Saving… → Saved` / `Save failed` (retry available) / `Connection lost — not saved` (must
re-attempt explicitly, never silently)

---

## 17. Low-connectivity behavior (V1 scope)

**Not designed: full offline clinical sync.** Explicitly out of scope, per instruction.

**Designed for V1:**

| Condition | Behavior |
|---|---|
| Brief packet loss (a few seconds) | In-flight request retried by the HTTP layer's own transient-error policy; UI shows `Saving…` throughout, no user action needed |
| ~5s interruption | Same as above; if it resolves within the request timeout, invisible to the user |
| ~30s interruption | Request times out; UI shows `Connection lost — not saved`; the form's contents are preserved locally (not submitted) so the user can retry without re-typing |
| Browser refresh mid-form | Unsaved input is not silently lost — local draft preservation (e.g. per-field local storage keyed to the form+patient) is a UI-002 requirement, not designed in field-level detail here |
| Request timeout | Treated as `AMBIGUOUS_WRITE` (§20) if the request could plausibly have reached the server; the UI must not represent this as either success or failure until confirmed by re-fetching |
| Ambiguous write | **Never automatically resubmitted.** The user is shown the ambiguity explicitly and must confirm before any retry, unless the specific endpoint has been proven idempotent (§16) |
| Read failure | Cached last-known value shown, clearly marked stale (age shown, not a silent stale success) |

---

## 18. Responsive / tablet

Primary workflows (patient search, patient workspace, labour observation entry, bed board, lab
result entry) must remain usable on desktop, clinic laptop, and tablet. Dense tables (bed board,
lab worklist) get an intentional tablet layout — cards or a condensed table with expandable rows —
rather than a desktop table simply shrunk until unreadable. Exact breakpoints are a UI-002 design-
system decision (§21), not fixed here.

---

## 19. Accessibility requirements

- Full keyboard navigation for every primary workflow; no mouse-only interaction.
- Visible focus state on every interactive element.
- Every form field has an associated, visible label — not placeholder-text-as-label.
- Screen-reader semantics: proper landmark regions, `aria-live` for save-state changes (§16), form
  errors associated with their field via `aria-describedby`.
- Minimum touch target size suitable for tablet use in a clinical setting (gloved or hurried use).
- **Status is never color-only** — every status badge (bed occupancy, lab result state, save
  state) pairs color with a text label, per §4's glossary terms.

---

## 20. Error model

Frontend-facing vocabulary only — never a raw backend stack trace or exception string to a
clinical user:

`VALIDATION_ERROR` · `NOT_AUTHORIZED` · `NOT_FOUND` · `CONFLICT` · `NETWORK_ERROR` ·
`SERVER_ERROR` · `AMBIGUOUS_WRITE`

**No clinical-data-bearing error content may be sent to third-party telemetry** — an error
payload logged externally must be scrubbed to this vocabulary plus a generic code, never a
patient name, identifier, or result value.

---

## 21. UI safety constraints (restated, because they are non-negotiable)

No AI diagnosis. No automatic treatment suggestion. No risk scoring. No unapproved warning
thresholds. No generated clinical recommendation. **The Daher UI is a better human interface to
OpenMRS and OpenELIS — not an autonomous clinical decision system.** Every value on screen is a
value a human entered or a system-of-record fact (bed state, order state) — never a derived
clinical judgement.

---

## 22. Cross-facility extensibility (not built)

The information architecture in §7 and the search-result cards in §6 must be able to carry
`source_facility`, `source_record_id`, `source_timestamp`, `source_provider`, and a local/external
indicator without a redesign. **This is a documented extension point only.** No exchange, no
cross-facility patient matching, and no assumption that `patient_id` is globally unique is
implemented or designed further than this paragraph. HIE remains explicitly future work
(`docs/ARCHITECTURE.md` §14).

---

## 23. Design-system component families (input to UI-002, not built here)

`AppShell` · `Navigation` (role-aware, §3) · `PatientHeader` · `Button` · `TextInput` · `Select` ·
`Search` · `Card` · `MetricCard` (with an explicit "unavailable" state, §8) · `Table` ·
`StatusBadge` (text + color, §19) · `Tabs` · `Timeline` · `Modal` · `Drawer` (only if a
UI-002 screen genuinely needs it) · `FormField` · `DateTimeField` · `LoadingSkeleton` ·
`EmptyState` · `ErrorState` (mapped to §20's vocabulary) · `ConnectionState` (mapped to §17/§16).

---

## 24. Backend gaps found by this ticket (not built here, by instruction)

| Gap | Where it blocks |
|---|---|
| No realm role distinguishes reception from clinical staff | §1 — front-desk persona is UX-only, not a security boundary |
| OpenMRS native order-write path never exercised against this build | §17 data contract, "Nouvelle ordonnance" |
| Patient search/registration REST endpoints never exercised against this pinned build | §6, §13 |
| Bed status/assignment/transfer vocabulary never exercised beyond create/list | §10, §13 |
| No BFF-to-dual-protocol (OIDC+SAML) identity propagation design proven | §15 |
| No admission-location aggregate query exercised | §8 |
| No lab-order↔result join ever exercised | §8, §13 |
| Preliminary/final/corrected result semantics unverified (DH-012) | §12 |
| ANC and partograph forms do not exist | §7, §9, §13 — clinical sign-off, not a backend gap in the technical sense |

None of these are built or worked around in this ticket, per instruction (§30 of the brief).

---

## 25. Definition of done — self-check

A UI-002 engineer reading this document can answer, per screen in §13: who uses it, what their
goal is, what data is shown and where it originates, where writes go, what permission is required
(§1–§3), how the human actor is preserved (§15), what happens during loading/network failure
(§16–§17), what is not yet proven (§13's Known/Unverified column, §24), and which pieces depend on
a clinical or product decision outside this ticket's authority (§7, §9, §12, §24).
