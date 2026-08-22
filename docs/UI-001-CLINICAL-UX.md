# UI-001 — Unified Clinical UX + Data Contract Map

**Status:** Architecture/product specification. UI-002 built the first real frontend (app shell,
design system, read-only patient search) against this spec; registration/admission/bed/order/result
writes, the BFF question, and SSO activation remained open going into UI-003.
**Date:** 2026-08-21 · **Audited against master `66536dd`** (`feat(DH-027H)`).
**Updated 2026-08-22 (UI-001A):** several `UNVERIFIED` contracts below are now `RUNTIME VERIFIED`
against the real, running pinned stack — see §13a for the full evidence (request/response shape,
auth, negative controls, limitations) behind every claim in the summary matrix. §17's browser-PHI
guidance and the ambiguous-write wording throughout were also corrected — see §17a and the network
table.
**Updated 2026-08-22 (UI-003):** the production identity question §15/§15a left `UNRESOLVED` is now
**decided** — no BFF in V1, the browser holds the real upstream sessions directly — see new §15b and
`DECISIONS.md`'s ADR-027. §14's BFF boundary is marked decided-against, not merely candidate.

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
misrepresented to an operator as access control. **Turned into an explicit decision request in
§24a (UI-001A)** rather than left as a passive note.

---

## 2. Roles / personas

Five personas, UX-level, mapped onto the three ADR-017 realm roles. "Auth role" is what the
server actually enforces; everything else here is Daher-UI-only navigation and defaults.

| Persona | Auth role (ADR-017) | Primary goal | Sees | Can change | Cannot change |
|---|---|---|---|---|---|
| **Réception** | `clinician` (no dedicated role — see §1) | Get an arriving patient found or registered and admitted | Patient search, registration, admissions list, bed board (read) | Patient demographics, new registrations, admission records | Clinical documentation, lab results, orders (not prevented server-side today — UI convention only, see §1) |
| **Sage-femme (Midwife)** | `clinician` | Run labour/maternity care for admitted patients | Patient workspace, maternity dashboard, labour workspace, bed board, lab orders/results | Maternity/labour observations (data-entry only, §9), bed assignment, lab orders | Lab result values, lab workflow states, other facilities' data |
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
| Travail | Labour | labour/partograph data entry (not yet built — §9) |
| Ordonnance de laboratoire | Lab order | FHIR `ServiceRequest` (OpenMRS) → OpenELIS accession (never shown) |
| Échantillon | Specimen/sample | OpenELIS `sample`/`specimen` |
| Résultat | Result | FHIR `Observation`/`DiagnosticReport` (OpenELIS → OpenMRS) |
| Résultat préliminaire | Preliminary result | OpenELIS result status pre-validation — **PENDING DH-012 EVIDENCE**, §12 |
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
  │  read: OpenMRS patient search REST — RUNTIME VERIFIED against this exact pinned build (§13a.1)
      │
  ┌───┴────┐
 found   not found
  │          │
  ▼          ▼
open      REGISTER PATIENT ── write: OpenMRS patient REST — RUNTIME VERIFIED endpoint shape (§13a.2)
record       │  actor: authenticated staff Keycloak identity (§15)
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
     write: structured, time-stamped observations only (ADR-006 — §9)
     system of record: OpenMRS (form not yet built — CLINICAL-SIGNOFF.md §6)
       │
       ▼
   LAB ORDER ── screen: Laboratoire / Nouvelle ordonnance
     role: Sage-femme, Médecin
     write: OpenMRS native order resource — NOT FHIR ServiceRequest (confirmed read-only, §0)
     system of record: OpenMRS (order), OpenELIS (once ingested)
     ⚠️ delivery to OpenELIS is BLOCKED ON BLOCKER-6 — see §0
       │
       ▼
   LAB WORKFLOW ── screen: native OpenELIS UI or Daher lab workspace (§14)
     role: Technicien de laboratoire
     write: OpenELIS specimen/sample/analysis state
       │
       ▼
   RESULT ── read: FHIR DiagnosticReport/Observation (OpenELIS → OpenMRS, via adapter)
     ⚠️ reverse leg is UNVERIFIED end to end (docs/ARCHITECTURE.md §6: "no delivery has been
     observed"; corrected-result semantics untested — §12)
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

**Every write step above needs the states in §16** (Saving… / Saved / Save failed / Connection
lost — save status unknown) — not designed per-step twice; §16 is the one contract every write
screen follows.

---

## 6. Patient search

- **Fields:** name and identifier share one `q=` query parameter — **RUNTIME VERIFIED against this
  exact pinned build, §13a.1**. Date-of-birth as a separate structured search field was not
  independently exercised (name/identifier search covers it via `q=` on this deployment's data, but
  a dedicated DOB parameter was not proven).
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
| Résumé | OpenMRS | demographics, current admission (visit+encounter shape RUNTIME VERIFIED, §13a.5), recent encounters | |
| Grossesse | OpenMRS | pregnancy-context observations (ANC form) | **form does not exist yet** — CLINICAL-SIGNOFF.md §6 |
| Travail & Accouchement | OpenMRS | labour observations, data-entry only (§12) | **form does not exist yet** |
| Ordonnances & Résultats | OpenMRS (orders) + OpenELIS (results, via adapter) | orders placed, results received | see §12 for what "corrected" means — unresolved |
| Documents | OpenMRS | any attached complex observations | not otherwise scoped by this ticket |

**No tab stores independent Daher-UI clinical truth.** Every tab is a read (and, where noted, a
write) against OpenMRS or OpenELIS; the Daher UI API composes reads, it does not own data (§14).

---

## 8. Maternity dashboard

| Card | Meaning | System of record | API | Refresh | Aggregation-safe? | Permission |
|---|---|---|---|---|---|---|
| Patientes admises | Count, currently admitted to Maternité | OpenMRS | admission-location query — UNVERIFIED exact endpoint | on load + manual refresh | yes — a count, not a list of values | `clinician` |
| En travail | Count, patients with an open labour episode | OpenMRS | **no verified query exists** — labour has no encounter type yet because the form doesn't exist | — | — | — |
| Lits disponibles | Available maternity beds | OpenMRS `bed` resource, filtered by `locationUuid` | **RUNTIME VERIFIED, §13a.4** — `status: "AVAILABLE"` is a real, observed field value | on load | yes — a client-side count of beds with that status, no server-side count endpoint needed | `clinician` |
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
- the recording provider's identity (from Keycloak — §15)

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
| Patients | Search | OpenMRS | OpenMRS REST patient search | — | `/ws/rest/v1/patient?q=` | any authenticated | n/a (read) | on query | **RUNTIME VERIFIED — §13a.1** | none |
| Patients | Register | OpenMRS | — | OpenMRS REST | `/ws/rest/v1/patient` + `/ws/rest/v1/idgen/identifiersource/{uuid}/identifier` | `clinician` | **RUNTIME VERIFIED** — authenticated principal, §13a.2 | n/a | **RUNTIME VERIFIED — §13a.2** | none |
| Patient workspace | Résumé | OpenMRS | OpenMRS REST/FHIR | — | patient + active visit | `clinician` | n/a | on load | Visit shape RUNTIME VERIFIED (§13a.5); full "résumé" composition UNVERIFIED | none |
| Patient workspace | Admettre | OpenMRS | — | OpenMRS visit **+ encounter** | `/ws/rest/v1/visit`, `/ws/rest/v1/encounter` | `clinician` | RUNTIME VERIFIED, §13a.5 | n/a | **RUNTIME VERIFIED — §13a.5** | none |
| Maternité | Grossesse tab | OpenMRS | ANC form encounters | ANC form encounters | **form does not exist** | `clinician` | Keycloak identity | on load | Known gap | clinical sign-off, CLINICAL-SIGNOFF.md §6 |
| Travail | Observations | OpenMRS | labour form encounters | labour form encounters | **form does not exist** | `clinician` | Keycloak identity | on load | Known gap | clinical sign-off |
| Lits | Bed board | OpenMRS | `bed`, `bedtype` | `POST/DELETE /ws/rest/v1/beds/{bedId}` | verified resource names AND assignment/release shape — §13a.4 | `clinician` | RUNTIME VERIFIED, §13a.4 | on load + manual | **RUNTIME VERIFIED — §13a.4** | none |
| Laboratoire | Nouvelle ordonnance | OpenMRS (order) | — | OpenMRS **native order resource, not FHIR** | `/ws/rest/v1/order` with `type=testorder` — **required-field contract verified**; concept selection deliberately not exercised | `clinician` | Keycloak identity | n/a | **PARTIALLY VERIFIED — §13a.3, stopped at the clinical-concept boundary by instruction** | delivery to OpenELIS blocked, BLOCKER-6 |
| Laboratoire | Worklist/results entry | OpenELIS | OpenELIS native | OpenELIS native | OpenELIS business API / native UI | `lab-tech` | OpenELIS user or Daher-UI-propagated identity — **UNRESOLVED**, §15 | on load | Known, OpenELIS-owned | none for entry itself |
| Résultats | Result display | OpenELIS → OpenMRS | OpenMRS chart (post adapter write-back) | — | FHIR `Observation`/`DiagnosticReport` (adapter-mediated) | `clinician` | n/a (read) | on load | **UNVERIFIED reverse leg** — out of this ticket's scope, adapter-side | BLOCKER-6 + DH-012 |
| Tableau de bord | Patientes admises | OpenMRS | admission-location query | — | UNVERIFIED aggregate query; the visit-list-by-location primitive is now verified (§13a.5), the count/aggregation query itself is not | `clinician` | n/a | on load | Partially verified | none |
| Tableau de bord | En travail | OpenMRS | — | — | **no query exists** | `clinician` | n/a | — | Unavailable by design (§8) | clinical sign-off (form doesn't exist) |
| Tableau de bord | Lits disponibles | OpenMRS | `bed` | — | verified resource **and** status vocabulary (`AVAILABLE`/`OCCUPIED`, §13a.4) | `clinician` | n/a | on load | **RUNTIME VERIFIED** | none |
| Tableau de bord | Ordonnances en attente | OpenMRS+OpenELIS | composed | — | **no join exists** | `clinician` | n/a | — | Unavailable by design (§8) | BLOCKER-6 |
| Tableau de bord | Résultats prêts | OpenMRS | chart write-back | — | depends on reverse leg | `clinician` | n/a | — | Unavailable by design (§8) | BLOCKER-6, DH-012 |
| Paramètres (admin) | Any config | OpenMRS/OpenELIS native admin | native | native | native admin consoles, not Daher UI | `sys-admin` | native system's own audit | n/a | Known — pass-through, not built | none, by design (§17) |

**Reading this table correctly:** `RUNTIME VERIFIED` means the exact claim was exercised against
the real, running pinned stack in this session (§13a has the request/response/auth/negative-control
evidence). Everything still marked UNVERIFIED either genuinely was not reachable without clinical
content that remains reserved (order concept selection), or was out of this ticket's stated scope
(the adapter's reverse leg, cross-system aggregation queries) — not left unverified by oversight.

---

## 13a. Runtime verification evidence (UI-001A, 2026-08-22)

**Method.** Exercised directly against the real pinned OpenMRS backend (`daher-health-openmrs-backend-1`,
already running, 5+ hours up at the time of this session) via `docker exec` onto the container's own
`localhost:8080` — never through a published host port, never through a new component. Authenticated
as the existing `admin` operator credential already in `.env`. All patient/encounter/visit data
created is clearly synthetic and named as such; the pre-existing `10001MJ` fixture (used by DH-014/
DH-015's backup verification) was read but never modified. One synthetic patient was created,
exercised, and then **voided** (not deleted) via the application's own supported mechanism before
this session ended — confirmed absent from search afterward, confirmed the pre-existing fixture and
all 7 Maternité beds were left exactly as found (`AVAILABLE`).

### 13a.1 Patient search — RUNTIME VERIFIED

`GET /ws/rest/v1/patient?q=<term>`, Basic Auth.

| Case | Result |
|---|---|
| Known identifier (`10001MJ`) | `HTTP 200`, one result, full patient representation |
| Known name fragment (`Verification`) | `HTTP 200`, same result — name and identifier share one `q=` search |
| Nonexistent term | `HTTP 200`, `{"results": []}` — **not a 404**; an empty result set is a normal 200, not an error |
| Ambiguous/partial term (`Test`) | `HTTP 200`, one result (only one patient existed in this environment at test time — genuine multi-result ambiguity was not observable without more fixture data) |
| No credentials | `HTTP 401`, clean top-level `{"error":{"message":"...Privileges required: Get Patients","code":...}}` |
| Wrong password | `HTTP 401`, **identical message to "no credentials"** — does not distinguish "unknown user" from "wrong password", a good property (no user-enumeration signal) |

**Response shape (`v=default`):** top-level `{"results": [...]}` only — **no `links`/`totalCount`
pagination envelope observed** at this result-set size. Pagination behavior with `limit`/`startIndex`
against a >1 result set was **not exercised** — this environment has too little fixture data to
observe it honestly; do not assume the standard OpenMRS pagination envelope without testing it
against a larger set before UI-002 relies on it.

**Error envelope, observed exactly once and representative:** `{"error": {"message", "code", "detail",
"rawMessage", "translatedMessage"}}` — `detail`/`rawMessage` carry a **full Java stack trace**, several
KB long. **This must never reach a clinical user or a browser** — the BFF's error-translation layer
(§20) must strip everything except `message`/`code` at minimum, and even `message` should be mapped
to the fixed vocabulary rather than passed through, since it can itself be implementation-specific.

### 13a.2 Patient registration — RUNTIME VERIFIED

`POST /ws/rest/v1/patient`, Basic Auth, JSON body.

**Required fields, established by negative control (empty body → progressively completed):**
`person` (object; at minimum `names`, `gender`, `birthdate` observed used) and `identifiers` (array;
at minimum one `{identifier, identifierType, location}`).

**Identifier behavior — the one finding worth flagging on its own.** The "OpenMRS ID" identifier
type is **not freely assignable** and is **not auto-generated by the create-patient call**:

- Supplying an arbitrary string (`"UI001A-SYNTH-01"`) is **rejected**: `HTTP 400`,
  `"Identifier ... is not appropriate for validation scheme Luhn Mod-30 Check-Digit Validator"` — a
  real check-digit algorithm is enforced server-side.
- Omitting the identifier value entirely is also rejected: `"Identifier cannot be null or blank"` —
  `POST /ws/rest/v1/patient` does **not** self-generate one.
- The correct sequence, verified: `POST /ws/rest/v1/idgen/identifiersource/{uuid}/identifier` with an
  **empty JSON body** (`{}`) returns `HTTP 201` and `{"identifier": "10003D2"}` — a fresh,
  check-digit-valid identifier. **A UI/BFF must call idgen first, then use its result in the patient
  create call — it must never construct an identifier itself.**
- The identifier-source UUID for "OpenMRS ID" is discoverable via
  `GET /ws/rest/v1/idgen/identifiersource` (filter by `identifierType.uuid`).

**Successful creation, full round trip proven:** with a valid generated identifier,
`POST /ws/rest/v1/patient` → `HTTP 201`, response includes `uuid` and `display`. Read-back via
`GET /ws/rest/v1/patient/{uuid}?v=full` → `HTTP 200`, same `uuid`. **`v=full` is required to see
audit fields** — `v=default` omits them. Observed: `auditInfo: {"creator": {"uuid", "display":
"admin"}, "dateCreated": "<timestamp>", "changedBy": null, "dateChanged": null}` — **the
authenticated principal of the HTTP call is recorded as creator**, not a separate "acting on behalf
of" field. This is the load-bearing fact behind §15's identity finding below.

**Negative controls:**
- Duplicate identifier → `HTTP 400`, `"Identifier 10003D2 already in use by another patient"` —
  enforced server-side, not left to client discipline.
- Unauthenticated `POST` — **does not cleanly 401.** An empty/malformed body still returns `HTTP 400`
  (validation runs regardless of auth). A **structurally complete** but unauthenticated payload
  returns `HTTP 400` too, but with the authorization failure **embedded inside a validation-shaped
  error**: `"identifiers on class org.openmrs.Patient => identifierType ... => Privileges required:
  Get Identifier Types"`. **This is a real inconsistency the BFF must account for**: unlike search's
  clean top-level 401, `POST /patient`'s authorization failure can arrive as a 400 with the reason
  buried in nested message text. A BFF cannot reliably distinguish "bad request" from "not
  authorized" for this endpoint by status code alone — treat any `POST /patient` failure whose
  message mentions "Privileges required" as `NOT_AUTHORIZED` regardless of the HTTP status observed.

**Cleanup, supported mechanism confirmed:** `DELETE /ws/rest/v1/patient/{uuid}` with a JSON body
`{"reason": "..."}` → `HTTP 204`. This **voids**, not purges (no `?purge=true` was used): a
subsequent `GET` by UUID still returns the record, but with `display` emptied and `identifiers: []`;
a subsequent **search** correctly returns nothing. This is the "supported voiding/test-isolation
mechanism" the ticket asked for, now confirmed rather than assumed.

### 13a.3 Order creation — PARTIALLY VERIFIED, stopped deliberately at the clinical-concept boundary

`POST /ws/rest/v1/order` (the generic polymorphic `Order` resource — **not** a dedicated
`/testorder` path, which returns `HTTP 404 Unknown resource` when tried directly).

**Required-field contract, established by negative control:**

| Step | Payload | Result |
|---|---|---|
| `{}` | empty | `HTTP 500`: *"you must indicate the particular subclass with a `type` property"* — confirms this is a polymorphic resource |
| `{"type": "testorder"}` | | `HTTP 400`: *"Some required properties are missing: encounter"* |
| `+ patient, encounter` | (using the visit/encounter from §13a.5) | `HTTP 400`, `fieldErrors: {"orderer": "Cannot be empty or null", "concept": "No Concepts found - Invalid Concept Id"}` |

**Confirmed required fields for a Test Order:** `type` (`"testorder"`), `patient`, `encounter`,
`orderer` (a `Provider` reference — confirmed via `GET /ws/rest/v1/provider` that non-clinical
provider records exist, e.g. the `admin` account's own provider record), and `concept`.

**Deliberately not completed.** Per this ticket's own instruction, going further would require
choosing a specific orderable `concept` — i.e., a specific lab test — which is exactly the clinical
mapping reserved to the clinical lead (`docs/CLINICAL-SIGNOFF.md`). **Stopping here, not by
oversight:** `order` number/id format, the full response shape of a created order, and
discontinue/cancel semantics **remain UNVERIFIED** as a direct, named consequence of stopping at
this boundary. This does not block UI-002 from building the order-creation *form* (every field
except the concept picker is now known); it blocks proving the *submit* end of that form until a
concept exists to submit.

**Two distinct error envelope shapes observed across this API**, both must be handled by the BFF's
error translation: a flat `globalErrors: [{message, code}]` list (structural/type errors), and a
per-field `fieldErrors: {fieldName: [{message, code}]}` map (validation errors) — different shapes
for different failure classes, not one consistent envelope.

### 13a.4 Bed management — RUNTIME VERIFIED

Resources already known from DH-003's seed job (`bed`, `bedtype`) now exercised for the
previously-unverified vocabulary and mutations.

**Status vocabulary, observed directly:** `"AVAILABLE"` and `"OCCUPIED"` (string values on the `bed`
resource's `status` field). Maintenance/out-of-service states exist in the bedmanagement module's own
concept space but were **not observed** — this deployment's 7 beds never left the
available/occupied pair during this session, so those states remain UNVERIFIED strings, not
disproven.

**Assignment — the load-bearing sequencing finding.** `POST /ws/rest/v1/beds/{bedId}` (integer
`bedId`, not the bed's UUID) with `{"patientUuid": "...", "encounterUuid": "..."}`:

- With only `patientUuid` (no open visit for that patient yet) → `HTTP 500`,
  *"Cannot invoke Encounter.getEncounterDatetime() because encounter is null"* — a raw
  `NullPointerException` leaked through the REST layer, not a clean validation error.
- Creating an open visit for the patient did **not** resolve this — the same NPE recurred.
- Creating an **Admission-type encounter** tied to that visit, and passing its UUID explicitly as
  `encounterUuid` in the assignment body, is what actually worked: `HTTP 200`. **A bed cannot be
  assigned from a bare patient reference — the caller must supply an existing encounter UUID
  explicitly; the endpoint does not look one up from the patient's active visit on its own,**
  contrary to what the first NPE's wording might suggest.
- Confirmed by re-fetching the bed: `status` changed `AVAILABLE → OCCUPIED`.

**Release:** `DELETE /ws/rest/v1/beds/{bedId}?patientUuid={uuid}` → `HTTP 204`. Re-fetching confirmed
`status` reverted to `AVAILABLE`.

**Negative controls:**
- Unauthenticated assignment attempt → clean `HTTP 401` (consistent with search, unlike patient
  creation's buried-401 behavior above — **the two endpoints are inconsistent with each other**, so
  the BFF cannot assume one auth-failure shape applies API-wide).
- Assignment to a **nonexistent** `patientUuid` → `HTTP 500`,
  *"not-null property references a null or transient value : ...BedPatientAssignment.patient"* — a
  raw Hibernate constraint violation, not a graceful `404`/`400`. **The BFF must validate patient
  existence itself before calling this endpoint** — the backend will not do it gracefully.

### 13a.5 Admission / active-visit shape — RUNTIME VERIFIED

`POST /ws/rest/v1/visit` with `{"patient": "<uuid>", "visitType": "<uuid>", "location": "<uuid>"}` →
`HTTP 201`. Response includes `uuid`, `startDatetime`, `location.display`, `visitType.display`.
Visit types are a real, queryable resource (`GET /ws/rest/v1/visittype`) — this deployment has
`Facility Visit`, `Home Visit`, `OPD Visit`, `Offline Visit`, `Group Session`; **`Facility Visit`**
is the one used for this admission-shaped test, chosen as the closest generic match, not a clinical
decision.

**A visit alone is not sufficient for bed assignment (§13a.4) or, very likely, orders (§13a.3) —**
an **Encounter** within that visit is the actual unit both downstream operations key off.
`POST /ws/rest/v1/encounter` with `{"patient", "encounterType", "location", "visit"}` → `HTTP 201`.
An `"Admission"` encounter type already exists in this deployment
(`GET /ws/rest/v1/encountertype?q=Admis`) — reference data, not invented.

**Corrected data-contract implication for UI-002:** the "Admettre" write path is **two API calls,
not one** — create the visit, then create an encounter within it — and the encounter is what later
operations (bed assignment, and plausibly order placement) actually require, not the visit alone.

### 13a.6 Audit identity — RUNTIME VERIFIED

For every successful write above (`patient` create, `visit` create, `encounter` create, bed
assign/release), the pattern is consistent: **the authenticated HTTP principal is what OpenMRS
records**, via `auditInfo.creator`/`dateCreated` on `v=full` reads (confirmed directly for patient
creation, §13a.2; structurally consistent — not independently re-confirmed per resource type — for
the others, since they share the same `BaseDelegatingResource` audit-field pattern OpenMRS uses
platform-wide).

**OpenMRS does not separately model "authenticated application user" vs. "clinical
provider/orderer" for this purpose in the way the ticket's question anticipated** — they are
related but distinct: the `Order.orderer` field is explicitly a `Provider` reference (verified,
§13a.3's field-error list), while `auditInfo.creator` on any resource is a `User` reference. A
`User` and a `Provider` are linked (the `admin` account has both), but nothing in this exercise
proves a `User` who lacks a `Provider` record could still populate `orderer` — **this specific
edge case (a Réception-persona account, which per §1 has no distinct role, attempting to place an
order that requires a `Provider`) was not tested** and is a real open question for UI-002 if
front-desk accounts are ever given order-adjacent capability.

**The core implication for the BFF is now evidence-backed, not theoretical:** whichever credential
authenticates the HTTP call to OpenMRS is what gets recorded. A BFF that authenticates all backend
calls with **its own service credential** — regardless of which staff member triggered the action in
the browser — **will record every write as that one service account**, exactly the failure mode
§15 exists to prevent. See §15 for what this implies about the BFF's design.

---

## 14. The Daher UI API / BFF — candidate boundary, not implemented

**Decided against for V1 (UI-003, ADR-027): no BFF is built.** This section is kept as the record of
what was considered and why it was rejected, not as a pending build. The browser holds the real
OpenMRS/OpenELIS sessions directly instead — see §15b. If a genuine future need for server-side
composition arises, it must be a strict pass-through of the user's own upstream session, never a
service account minting its own identity, per ADR-027's acceptance criterion.

**Purpose (as originally scoped, for the record):** compose screen-oriented reads, reduce browser
chatter, centralize authorization-aware aggregation, normalize frontend error semantics, propagate
staff identity.

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
§5). §13a.2/§13a.6 now prove exactly why this matters: OpenMRS records **whichever credential
authenticated the HTTP call** as the audit `creator`, with no separate "acting on behalf of" field.
A BFF authenticating with its own service credential would record every write as that one account —
not a hypothetical risk, an observed mechanism.

### 15a. Findings from source inspection (UI-001A) — not a live SSO test

**Real SSO remains `SSO_ENABLED=false`** (ADR-013/DH-008 activation is blocked on BLOCKER-5's staff
roster) and this ticket does not activate it or invent staff names, per instruction. The findings
below come from reading the actual installed integration — `openmrs-config/configure-sso.sh`
(installs `openmrs-module-oauth2login` 1.5.0) and `openelis-config/properties/common.properties.template`
(`org.itech.login.saml=...`, `registrationId=keycloak`) — not from theory, and not from a runtime
SSO session.

**Both integrations are browser-redirect, session-cookie SSO — neither is a bearer-token-per-API-call
mechanism.** `openmrs-module-oauth2login` is exactly what its name says: a Spring Security OAuth2
*login* flow. It authenticates a browser through a redirect to Keycloak and, on return, establishes a
conventional OpenMRS web session (a session cookie) — it does not implement validating an arbitrary
Keycloak-issued Bearer token presented on a REST API call. OpenELIS's SAML integration
(`org.itech.login.saml`) is the same shape: a browser-redirect SAML login that ends in an OpenELIS
session, not a token-validating API filter.

**Answering the ticket's specific questions, to the extent source inspection (not a live token test)
can:**

| Question | Finding |
|---|---|
| Can the BFF use the staff member's token directly with OpenMRS? | **Unlikely as designed.** `oauth2login` handles the login redirect, not per-request Bearer validation. Not proven impossible by source reading alone — a live SSO activation would be needed to fully close this — but nothing in the installed module suggests it accepts a raw Keycloak access token on `ws/rest` calls. |
| Does OpenMRS require a browser session/cookie instead? | **Yes, as designed.** The standard OpenMRS REST API already accepts either Basic Auth (used throughout §13a) or an existing session cookie from the same browser — which is exactly what `oauth2login`'s redirect produces. |
| Can staff identity be propagated without one shared service account? | **Only by making the actual OpenMRS/OpenELIS request carry that staff member's own session or credential** — confirmed necessary by §13a.6's audit-field finding. A BFF that re-authenticates with its own credential cannot do this; a BFF that forwards the browser's own session cookie can. |
| How would OpenELIS's SAML session interact with a BFF? | Same shape as OpenMRS's: a cookie-bearing browser session established via redirect. A BFF would need to hold and forward **two separate session cookies** (one per backend), not one unified token — added complexity a reverse-proxy model absorbs more naturally than a token-minting BFF. |
| Would a reverse-proxy/browser-direct model preserve identity better than BFF-proxied writes? | **Yes, on this evidence.** For any write that must carry real human provenance, the browser's own already-established OpenMRS/OpenELIS session (from the SSO redirect) making the call directly — or a BFF acting as a transparent cookie-forwarding reverse proxy rather than an independently-authenticating service — is the design this evidence points toward. A token-minting BFF that authenticates itself and asserts an identity on the user's behalf is the shape most likely to collapse into the shared-service-account failure mode. |

**Conclusion: UNRESOLVED — requires SSO activation to close fully**, stated as the truthful boundary
per instruction. What this session adds beyond UI-001's original silence: a specific, evidence-backed
default (reverse-proxy/cookie-forwarding over token-minting) for UI-002 to build toward, and the
concrete mechanism (`auditInfo.creator` = the HTTP call's own authenticated principal) that makes the
question matter. Proving it end-to-end still needs a real Keycloak login through `oauth2login`,
which needs the BLOCKER-5 roster this ticket is explicitly not allowed to invent.

### 15b. Architecture decided (UI-003, 2026-08-22) — see ADR-027

§15a's "reverse-proxy/cookie-forwarding over token-minting" default is now a recorded decision, not
just a leaning: **ADR-027 (`DECISIONS.md`) — no BFF in V1, the browser holds the real OpenMRS and
OpenELIS sessions directly, same-origin under a new `app.$BASE_DOMAIN`** that proxies each backend's
own API prefix (`/openmrs/*`, `/OpenELIS-Global/*`), not its SPA. `emr.`/`lab.` are untouched.

What changed since §15a to justify moving from "unresolved leaning" to "decided": UI-003 measured,
rather than inferred, that (1) neither backend has any bearer-token/token-exchange path at all — a
live `Authorization: Bearer` probe against OpenMRS was ignored outright; (2) `openmrs-gateway` and
`openelis-webapp` both route by path, not Host header, so one backend instance can answer for two
origins, which is what makes same-origin mounting under `app.` viable without touching `emr.`/`lab.`;
(3) a **read-only** service-account BFF — floated as a softer middle ground during this ticket's own
analysis — was rejected, not merely deferred: it would replace each user's own OpenMRS read
authorization with the service account's, which is a bigger hole than the audit-identity problem it
would dodge, and is exactly the "identity silently falls back to a service identity" failure this
document's own acceptance criteria rule out (§3 of the UI-003 brief).

**Still exactly as unresolved as §15a said:** the live end-to-end demonstration needs
`SSO_ENABLED=true` and BLOCKER-5's approved role mapping — neither was done, and none will be invented
here. Coordinated logout across the Keycloak SSO session, OpenMRS, and OpenELIS is a new, separate,
real gap ADR-027 identifies and does not solve (see its own text for why). OpenELIS's SAML ACS
derivation (fixed-origin vs. per-request, unlike OpenMRS's confirmed per-request behavior) is also
unverified — ADR-027 states the fallback if it turns out to be fixed.

**§14 is updated accordingly:** the "candidate BFF" it describes is not being built in V1 per this
decision; see the note at the top of that section.

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

States, used uniformly across every write screen rather than invented per-screen. **Corrected in
UI-001A** (§9 of that ticket): a timeout after the request may have already reached the server is
not evidence the write failed, and must not be worded as though it were.

`Idle → Saving… → Saved` / `Save failed` (the server responded and rejected the write — this alone
may say "not saved", because the server actually said so) / **`Connection lost — save status
unknown`** (the request timed out or the connection dropped **after** submission — the server may
have accepted it; the UI does not know, and must not claim to)

**What resolves an unknown save status:** re-fetching the record after reconnection and comparing —
never re-submitting blind. If the re-fetch shows the write landed, the UI says `Saved` (late-
confirmed); if it shows the write did not land, the UI says `Save failed` and offers retry; until
that re-fetch completes, the UI keeps saying `save status unknown`, not a guess in either direction.

---

## 17. Low-connectivity behavior (V1 scope)

**Not designed: full offline clinical sync.** Explicitly out of scope, per instruction.

**Designed for V1:**

| Condition | Behavior |
|---|---|
| Brief packet loss (a few seconds), before submission | In-flight typing is unaffected; nothing has been sent yet |
| Brief packet loss, during a submitted write | In-flight request retried by the HTTP layer's own transient-error policy; UI shows `Saving…` throughout, no user action needed |
| ~5s interruption during a write | Same as above; if it resolves within the request timeout, invisible to the user |
| ~30s interruption during a write | Request times out; UI shows **`Connection lost — save status unknown`** (not "not saved" — see §16); on reconnection the UI re-fetches to resolve it before showing anything else |
| ~30s interruption while still typing (nothing submitted yet) | Nothing is ambiguous — no request was sent. The in-progress input itself is handled by §17a, not this row |
| Browser refresh mid-form | Unsaved input is not silently lost, subject to §17a's storage constraints — memory-only by default, so a refresh **does** lose it unless a secure draft mechanism is deliberately built; the UI must warn before an in-app navigation that would lose unsaved input, which is a weaker but always-available guarantee |
| Request timeout | Treated as `AMBIGUOUS_WRITE` (§20) if the request could plausibly have reached the server; the UI must not represent this as either success or failure until confirmed by re-fetching |
| Ambiguous write | **Never automatically resubmitted.** The user is shown the ambiguity explicitly and must confirm before any retry, unless the specific endpoint has been proven idempotent (§16) — and per §13a, **no endpoint in this document has been proven idempotent yet** |
| Read failure | Last-known value may be shown **in memory only** (this session, this tab), clearly marked stale with its age — never a persisted cross-session cache, per §17a |

---

## 17a. Browser PHI storage — corrected in UI-001A, treated as a security decision, not a convenience

**UI-001's original wording proposed `localStorage` for unsaved form drafts and cached reads without
qualification. That was too strong a commitment for clinical data and is corrected here.**

**The question is not "is this convenient" — it is "what does persisting patient data in browser
storage expose, and to whom."** Assessed against the realistic environment this system runs in:

| Risk | Why it matters here specifically |
|---|---|
| Shared clinic workstations | Multiple staff use the same browser profile across a shift; `localStorage` persists **across logout and across users** on the same device unless explicitly cleared |
| Logout | Must not leave clinical drafts/cached reads readable by the next person who signs in on that device |
| Browser crash / unclean close | `localStorage`/IndexedDB survive a crash — arguably a feature for drafts, but only if the *next opener is the same authorized person*, which a crash cannot guarantee |
| Device theft | A stolen device with `localStorage` full of cached patient summaries is a PHI exposure incident, not a UX bug |
| Another staff member signing in | The Réception/Sage-femme/Médecin distinction in §1 is UX-only, not a security boundary — **and neither is separating browser storage by persona**, since nothing prevents any signed-in account from reading storage another persona's session wrote |
| Persistent disk caches | `localStorage`/IndexedDB write to disk, not just memory — a forensic disk read after the fact can recover them even post-logout unless the browser profile itself is encrypted at rest, which this document cannot assume for every deployed device |
| Expiration | `localStorage` has **no built-in expiry** — anything written persists until explicitly cleared by code or the user, which is the opposite of what a clinical draft needs |
| Encryption feasibility | Encrypting client-side with a key that is *also* client-side protects against nothing an attacker with the same browser access couldn't also extract; genuine protection needs a key the browser session doesn't hold at rest, which is a real design (out of scope to invent here) |

**Preferred default until a deliberate, explicit secure-draft mechanism is designed and reviewed:**

- **Unsaved form input lives in memory only** (component/application state), not `localStorage`,
  `sessionStorage`, or IndexedDB.
- The UI **warns before any navigation or connection loss that would discard it** — this is a
  weaker guarantee than surviving a refresh, but it does not create a persistent PHI-bearing
  artifact on disk.
- A browser refresh or crash **does lose the draft** under this default. That is the accepted
  trade-off until a secure mechanism (e.g., a short-lived, encrypted, session-scoped store with an
  explicit expiry and explicit clearing on logout) is designed as its own decision — **not
  designed in this document**, and not assumed available to UI-002 by default.
- **Cached reads are the same question.** §7's "last-known value shown, clearly marked stale" (§17's
  table) is **memory-only, current-session, current-tab** — not a persisted cross-session cache.
  UI-002 must not promise a cached read survives a refresh or a new session unless it deliberately
  designs the storage/lifetime/privacy model first, exactly as for drafts.

**This is a correction to UI-001, recorded rather than silently edited away:** the original text
was wrong to suggest `localStorage` casually. Nothing about this is a live-tested finding — no
runtime verification was needed or performed for this section; it is an architecture correction
made because the original wording was too permissive for clinical data.

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

**Resolved by UI-001A (§13a), removed from this list:** patient search/registration endpoints, bed
assignment/release/status vocabulary, the visit+encounter admission shape, and the order-write
required-field contract (through the concept boundary) are no longer gaps — see §13a for each.

| Gap | Where it blocks |
|---|---|
| No realm role distinguishes reception from clinical staff | §1, §24a below — the one gap this ticket turns into an explicit decision request rather than leaving as a passive note |
| Order `concept` selection not exercised, deliberately | §13a.3 — reserved to the clinical lead, not a technical gap |
| Order response shape / discontinue semantics unverified | §13a.3 — direct consequence of stopping at the concept boundary |
| BFF-to-dual-protocol (OIDC+SAML) identity propagation not proven end-to-end | §15/§15a — source-grounded default now exists (reverse-proxy/cookie-forwarding); full proof needs live SSO, blocked on BLOCKER-5 |
| No admission-location **aggregate** query exercised (the single-visit primitive is now verified, §13a.5) | §8 |
| No lab-order↔result join ever exercised | §8, §13 |
| Preliminary/final/corrected result semantics unverified (DH-012) | §12 |
| ANC and partograph forms do not exist | §7, §9, §13 — clinical sign-off, not a backend gap in the technical sense |
| Whether a `Provider`-less account (e.g. a future Réception-only account) can populate `Order.orderer` | §13a.6 — untested edge case, relevant only if reception ever gets order-adjacent capability |

None of these are built or worked around in this ticket, per instruction.

### 24a. Decision request — reception/front-desk authorization (UI-001A)

**Framed explicitly, per instruction, rather than left as a passive note.**

**Question for Liban:** *Should Daher add a dedicated reception/front-desk authorization role,
distinct from `clinician`?*

**Current state (ADR-017, unchanged by this ticket):** exactly three realm roles exist —
`clinician`, `lab-tech`, `sys-admin`. Front-desk/registration staff have **no role of their own**;
today, giving a receptionist Daher UI access at all means giving them the `clinician` role, because
that is the only role OpenMRS registration/search privileges are attached to in this deployment.

**What this means concretely today:** a receptionist's account — the moment it exists — is
authorized for exactly what a midwife's or doctor's account is: full OpenMRS clinical read/write,
not just registration and admission. The Daher UI's navigation (§3) hides clinical screens from the
Réception persona, but **hiding is not authorization** (§5's own stated rule). Nothing server-side
stops a receptionist's account from calling any OpenMRS endpoint a `clinician` could.

**What would differ if a dedicated role existed:** narrower OpenMRS privileges — patient
search/registration/admission (confirmed working end-to-end in §13a.2/§13a.5) without the
privileges §13a's other findings depend on: creating encounters beyond registration-shaped ones,
placing orders (§13a.3), or assigning beds (§13a.4) if that is meant to stay a clinical action.
The exact privilege set is an OpenMRS role-configuration decision, not designed here — this
document only establishes *that* the current model cannot express the distinction, not what the
new role's privileges should be.

**Not decided by this ticket, and not invented:** whether this role should exist, its name, or its
exact privilege set. If the answer is "no, `clinician` for reception is an accepted pilot
trade-off," that is also a valid, explicit answer — the point is that it be a decision, not a
default nobody chose.

---

## 25. Definition of done — self-check

A UI-002 engineer reading this document can answer, per screen in §13: who uses it, what their
goal is, what data is shown and where it originates, where writes go, what permission is required
(§1–§3), how the human actor is preserved (§15/§15a/§15b, ADR-027), what happens during loading/
network failure (§16–§17a), what is not yet proven (§13's Known/Unverified column, §13a, §24), and
which pieces depend on a clinical or product decision outside this ticket's authority (§7, §9, §12,
§24, §24a).
