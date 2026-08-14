# Clinical sign-off — Daher Health

**Status: AWAITING THE CLINICAL LEAD. Nothing here is decided.**

This sheet exists so the clinical lead reviews a **list of what this deployment can already order**,
rather than a blank page or a panel an engineer guessed at.

> ### What this document is, and what it deliberately is not
>
> It **is** an inventory, generated from the reference data actually installed in this stack on
> 2026-08-13 — not typed from memory, not fetched from elsewhere.
>
> It is **not** a recommendation. No test below is proposed, preferred, or pre-ticked. The
> selection is the clinical lead's, and it is reserved to them by `AGENTS.md`: lab panel contents
> come from unmodified reference defaults and are never invented by whoever is building the system.
>
> Neither OpenMRS nor OpenELIS ships a prenatal panel. That was checked, not assumed: OpenMRS has
> **zero** order sets defined, and OpenELIS's four shipped panels are `Bilan Biochimique`, `NFS`,
> `Serologie VIH`, and `Typage lymphocytaire`. So the prenatal panel has to be *composed*, and
> composing it is a clinical decision.

---

## 1. What we need from you (DH-006)

**Tick the lab sets that belong in the routine prenatal panel.** Add anything missing in §4.

The system does not need this to be perfect on the first pass. It needs it to be *yours*, and
recorded. Ticking nothing is a valid answer that tells us the panel needs concepts this deployment
does not yet have.

## 2. Candidate lab sets already installed in OpenMRS

All 37 reference lab sets present in this deployment, alphabetically. `—` means the reference data
carries no LOINC code for that set; that is an upstream gap, not an error, and it is listed in §5
because it affects how the order reaches the laboratory.

| ☐ | Lab set (OpenMRS reference name) | LOINC | OpenMRS concept UUID |
|---|---|---|---|
| ☐ | Acid fast bacilli test | `11545-1` | `161461AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Bacterial culture panel | `6463-4` | `163679AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Basic metabolic panel | `—` | `162630AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Blood grouping test | `34530-6` | `161473AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | C-reactive protein test | `—` | `161501AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | CD4 panel | `—` | `657AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Cerebrospinal fluid panel | `—` | `a48601a5-1f8d-4e28-8020-fc570063f6a6` |
| ☐ | Complete blood count | `58410-2` | `1019AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Complete blood count with manual count and manual differential | `57782-5` | `163701AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Comprehensive metabolic panel | `24323-8` | `166374AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Culture panel | `—` | `161486AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | GeneXpert MTB/Rif Lab set | `—` | `164945AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Glucose 6 phosphate dehydrogenase | `2359-8` | `161514AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Glucose tolerance test 5 specimens (75g) panel | `—` | `163708AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Hemoglobin with electrophoresis | `43113-0` | `161422AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Hepatitis B serological test | `62871-9` | `161475AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Hepatitis C serological test | `62868-5` | `161474AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Human immunodeficiency virus serological test | `42600-7` | `161480AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Kidney function test set | `—` | `7b2b551b-cb88-49bd-bc17-0f359070032c` |
| ☐ | Lipase test | `—` | `161515AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Lipid panel | `24331-1` | `1010AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Liver function tests | `24325-3` | `953AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Malarial parasites by smear test | `32700-7` | `161426AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Prostate specific antigen test | `—` | `161520AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Prothrombin time (with INR) | `34528-0` | `161152AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Red blood cell morphology | `58408-6` | `161424AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Renal function panel | `24362-6` | `161488AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Routine urine | `18729-4` | `161446AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Serum chemistry panel | `—` | `161483AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Serum electrolytes | `—` | `5473AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Sickling test only | `—` | `161431AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Stool test | `—` | `161451AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Toxoplasma Gondii IgG And IgM Panel - Serum | `57770-0` | `163625AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Tuberculosis culture construct | `—` | `159981AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Typhoid serological test | `14227-3` | `161477AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |
| ☐ | Urine macroscopy panel (dipstick) | `24357-6` | `885499fa-ba0f-43bf-9fdb-6f1a07f5b8bc` |
| ☐ | Urine microscopy panel | `—` | `163697AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` |

## 3. Panels already defined in OpenELIS

These exist on the laboratory side today, shipped by OpenELIS, in French. They are listed so you can
see what the laboratory already recognises — not because any of them is proposed as the prenatal panel.

| OpenELIS panel | Member tests |
|---|---|
| Bilan Biochimique | 9 |
| NFS | 18 |
| Serologie VIH | 2 |
| Typage lymphocytaire | 2 |

## 4. Anything missing

If the prenatal panel needs a test that is not in §2, write it here. Adding a concept that does not
exist yet is real work but entirely normal — it is much better than silently substituting a
near-match.

| Test wanted | Notes from the clinical lead |
|---|---|
|  |  |
|  |  |

## 5. One technical constraint you should know about

**LOINC coverage is partial on both sides**, measured on this deployment:

- OpenELIS: **42 of 177** active tests carry a LOINC code (24%).
- OpenMRS: **21 of 37** lab sets carry a LOINC code (57%).

So LOINC alone cannot be the link between an order placed in OpenMRS and a test performed in
OpenELIS. Wherever a chosen test lacks a code on either side, that pair needs an explicit mapping.
This does not change *what* you choose — it changes how much wiring each choice needs, and it is
better known now than discovered during the pilot.

## 6. Partograph fields (DH-004) — same process, separate sheet

The partograph form is the other item needing your sign-off. Per **ADR-006** it ships as
**data entry only**: it records what a midwife observes and does nothing else. It has

- no alert line, no action line,
- no prolonged-labour flagging,
- no risk score, no triage, no recommendation of any kind.

That is a deliberate decision, not an oversight and not a limitation to be "fixed" later without a
new decision. This system records and routes; it does not interpret.

## 7. Recording the decision

When this sheet comes back, the ticked lab sets are implemented exactly as ticked — the seeding
mechanism is already built and ships **empty** precisely so that nothing enters the panel without
passing through here. The response is then recorded against **DH-006** in `PROJECT_STATE.md` with
the date and who gave it.

**Until that happens, DH-006 stays open even if every other part of it is finished.**
