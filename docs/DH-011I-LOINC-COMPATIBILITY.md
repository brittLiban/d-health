# DH-011I exact LOINC compatibility — technical inventory, not clinical approval

**Measured 2026-08-31 from a fresh disposable database created from the pinned
`itechuw/openelis-global-2-database:3.2.1.11` digest.** No facility stack or patient data was
queried. The complete native extraction is
`docs/DH-011I-OPENELIS-TEST-INVENTORY.csv` (`name | LOINC | active`).

The prior `177 / 42` packet numbers are not current. The native `clinlims.test` table contains
**189 rows: 177 active and 12 inactive**. Exactly **33 rows carry a nonblank LOINC**, and all 33
are active. The committed OpenMRS candidate table in `docs/CLINICAL-SIGNOFF.md` remeasures to
**37 candidates: 21 with LOINC and 16 without**.

Comparison below uses literal LOINC equality only. Names, abbreviations, specimen types, and
display text are never matching inputs. Nothing in this document is a clinical recommendation.

## 1. Exact LOINC match

**None.** Zero of the 21 coded OpenMRS candidate lab sets has the same LOINC as any active
OpenELIS test. There is therefore no row eligible for the clinical sign-off table yet.

## 2. OpenMRS has LOINC but no active OpenELIS match

| OpenMRS candidate | LOINC |
|---|---|
| Acid fast bacilli test | `11545-1` |
| Bacterial culture panel | `6463-4` |
| Blood grouping test | `34530-6` |
| Complete blood count | `58410-2` |
| Complete blood count with manual count and manual differential | `57782-5` |
| Comprehensive metabolic panel | `24323-8` |
| Glucose 6 phosphate dehydrogenase | `2359-8` |
| Hemoglobin with electrophoresis | `43113-0` |
| Hepatitis B serological test | `62871-9` |
| Hepatitis C serological test | `62868-5` |
| Human immunodeficiency virus serological test | `42600-7` |
| Lipid panel | `24331-1` |
| Liver function tests | `24325-3` |
| Malarial parasites by smear test | `32700-7` |
| Prothrombin time (with INR) | `34528-0` |
| Red blood cell morphology | `58408-6` |
| Renal function panel | `24362-6` |
| Routine urine | `18729-4` |
| Toxoplasma Gondii IgG And IgM Panel - Serum | `57770-0` |
| Typhoid serological test | `14227-3` |
| Urine macroscopy panel (dipstick) | `24357-6` |

## 3. Active OpenELIS tests with LOINC not referenced by an OpenMRS candidate

Every coded OpenELIS row is in this group because group 1 is empty. Repeated rows are retained:
they are distinct native tests, and collapsing them would hide ambiguity.

| OpenELIS native test | LOINC |
|---|---|
| COVID-19 ANTIBODY IgG | `94547-7` |
| COVID-19 ANTIBODY IgG | `94547-7` |
| COVID-19 ANTIBODY IgM | `94547-7` |
| COVID-19 ANTIBODY IgM | `94547-7` |
| COVID-19 PCR | `94500-6` |
| COVID-19 PCR | `94500-6` |
| COVID-19 PCR | `94500-6` |
| DENGUE PCR | `7855-0` |
| DENGUE PCR | `7855-0` |
| DENGUE PCR | `7855-0` |
| HCT(Whole Blood) | `4544-3` |
| HEPATITIS B VIRAL LOAD | `29615-2` |
| HEPATITIS B VIRAL LOAD | `29615-2` |
| HEPATITIS C VIRAL LOAD | `11011-4` |
| HEPATITIS C VIRAL LOAD | `11011-4` |
| HGB(Whole Blood) | `718-7` |
| HIV VIRAL LOAD | `10351-5` |
| HIV VIRAL LOAD | `10351-5` |
| HIV VIRAL LOAD | `10351-5` |
| Lymphocytes (%) | `736-9` |
| Lymphocytes (Abs) | `731-0` |
| LYM#(Whole Blood) | `731-0` |
| MCHC(Whole Blood) | `786-4` |
| MCH(Whole Blood) | `785-6` |
| MCV(Whole Blood) | `787-2` |
| Monocytes (%) | `5905-5` |
| Monocytes (Abs) | `742-7` |
| MPV(Whole Blood) | `32623-1` |
| NEU#(Whole Blood) | `751-8` |
| PLT(Whole Blood) | `777-3` |
| RBC(Whole Blood) | `789-8` |
| RDW(Whole Blood) | `788-0` |
| WBC(Whole Blood) | `6690-2` |

### Duplicate active LOINC ambiguity

These codes cannot identify one native OpenELIS test. They are excluded from any ready mapping;
engineering must not select a row by order, name, or internal id.

| LOINC | Active native rows | Native names |
|---|---:|---|
| `10351-5` | 3 | HIV VIRAL LOAD ×3 |
| `11011-4` | 2 | HEPATITIS C VIRAL LOAD ×2 |
| `29615-2` | 2 | HEPATITIS B VIRAL LOAD ×2 |
| `731-0` | 2 | Lymphocytes (Abs); LYM#(Whole Blood) |
| `7855-0` | 3 | DENGUE PCR ×3 |
| `94500-6` | 3 | COVID-19 PCR ×3 |
| `94547-7` | 4 | COVID-19 ANTIBODY IgG ×2; COVID-19 ANTIBODY IgM ×2 |

## 4. OpenMRS candidates with no LOINC

| OpenMRS candidate |
|---|
| Basic metabolic panel |
| C-reactive protein test |
| CD4 panel |
| Cerebrospinal fluid panel |
| Culture panel |
| GeneXpert MTB/Rif Lab set |
| Glucose tolerance test 5 specimens (75g) panel |
| Kidney function test set |
| Lipase test |
| Prostate specific antigen test |
| Serum chemistry panel |
| Serum electrolytes |
| Sickling test only |
| Stool test |
| Tuberculosis culture construct |
| Urine microscopy panel |

## Clinical/lab sign-off table

Only exact one-to-one LOINC matches may appear here. The mechanical comparison produced none, so
the table intentionally has no candidate rows and no selection has been made.

| Test clinicians see | LOINC | Matching OpenELIS test | Include in pilot? |
|---|---|---|---|

The clinical/lab team still chooses the real pilot list. Engineering can resolve machine
identifiers only after those choices are returned; this report does not approve a panel.

## Mechanical guard

`openmrs-config/seed/lab_mapping_audit.py` implements the audit rule. It compares literal LOINC
strings, ignores inactive target rows, and raises on zero or multiple active target matches.
Its tests pin the two source counts, the current empty exact-match group, and all seven duplicate
active LOINCs. It is an audit/validation mechanism, not an activated production registry.
