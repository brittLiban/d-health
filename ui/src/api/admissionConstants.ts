// Canonical Daher maternity admission contract (ADR-028, ADR-017D).
// Pinned literal UUIDs, not runtime-discovered — §3 of UI-013 requires
// failing closed on drift, which only means something if the payload
// itself never silently follows whatever a metadata lookup returns.
// Metadata reads (see client.ts's admission metadata functions) are a
// drift *check* against these constants, never the source of the payload.

// Daher-owned Visit type (openmrs-config/initializer/visittypes/visittypes-daher.csv).
export const DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID = '194bf546-68d7-42c0-8371-5b0f2b1cf5f3'

// Upstream reference-data Encounter type, live-confirmed as the sole
// "Admission" match on this pinned stack (docs/UI-001-CLINICAL-UX.md §13a.5;
// re-confirmed UI-013).
export const ADMISSION_ENCOUNTER_TYPE_UUID = 'e22e39fd-7db2-45e7-80f1-60fa0d5a4378'

// Daher-owned Location (openmrs-config/initializer/locations/locations-daher.csv).
export const MATERNITE_LOCATION_UUID = '6538f097-382d-49b6-a839-601d03c888c0'

// The exact OpenMRS role name ADR-017D committed. Frontend gating with this
// is UX only — the server privilege set is the real boundary (ADR-017D §5).
export const DAHER_CLINICIAN_ADMISSION_ROLE = 'Daher Clinician Admission'
