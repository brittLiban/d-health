// Shape observed against the real pinned OpenMRS build by UI-001A
// (docs/UI-001-CLINICAL-UX.md §13a.1). Only the fields the search response was
// actually seen to carry are modeled; everything else stays optional and
// unrendered rather than assumed.
export interface OpenMrsPatientIdentifier {
  identifier: string
  identifierType?: { display?: string }
}

export interface OpenMrsPerson {
  gender?: string
  birthdate?: string
  age?: number
}

export interface OpenMrsPatient {
  uuid: string
  display: string
  identifiers?: OpenMrsPatientIdentifier[]
  person?: OpenMrsPerson
}

// UI-012: the one runtime-verified identifier source on this pinned stack
// (docs/UI-001-CLINICAL-UX.md §13a.2 + live re-verification). `locationBehavior`
// is read, not assumed — `NOT_USED` is the only value this ticket implements
// against; anything else is treated as a configuration blocker, not guessed at.
export interface IdentifierSource {
  uuid: string
  identifierTypeUuid: string
  locationBehavior: string
}

// The verified minimum registration payload — no field OpenMRS merely
// supports theoretically. Gender is OpenMRS's own single-letter code.
export interface PatientRegistration {
  givenName: string
  familyName: string
  gender: 'M' | 'F'
  birthdate: string
}

// GET /session — shape observed live against the pinned OpenMRS build (UI-013,
// matching ADR-017E2's own characterization): `user.roles` carries the exact
// OpenMRS role names oauth2login assigns from the Keycloak `roles` claim;
// `currentProvider` is `null` unless the session's exact "Provider" role
// activated one (ADR-017E2). Only the fields this ticket actually reads.
export interface OpenMrsSession {
  authenticated: boolean
  user?: {
    uuid: string
    roles?: Array<{ name: string }>
  }
  currentProvider?: { uuid: string } | null
}

// GET /visit?v=custom:(...) — the read contract UI-013 measured live
// (docs/UI-013 handoff): one call, embedded encounters, no obs/orders/
// diagnoses. `stopDatetime` present means the visit is closed; the server's
// own `includeInactive=false` filter is the source of truth for "active",
// not a client-side re-derivation of it.
export interface AdmissionEncounterRef {
  uuid: string
  encounterType: { uuid: string; display?: string }
  voided: boolean
}

export interface ActiveVisit {
  uuid: string
  visitType: { uuid: string; display?: string }
  location: { uuid: string; display?: string }
  stopDatetime?: string | null
  encounters: AdmissionEncounterRef[]
}

// UI-014 — GET /visit?location=&visitType=&includeInactive=false, the
// facility-wide (not per-patient) query the maternity board uses. Measured
// live: the embedded `patient` sub-representation honors `uuid`/`display`/
// `identifiers`, but NOT a nested `person` field at any representation
// depth tried (custom or full) — contradicting UI-001A §13a.1's claim that
// search results carry age/DOB. Birth info is a genuinely separate resource
// (`GET /person/{uuid}`), never embedded here — this type has no `person`
// field because nothing populates one via this call.
export interface MaternityBoardVisit {
  uuid: string
  startDatetime: string
  stopDatetime?: string | null
  visitType: { uuid: string; display?: string }
  location: { uuid: string; display?: string }
  patient: OpenMrsPatient
}

// Fixed frontend-facing error vocabulary (docs/UI-001-CLINICAL-UX.md §20).
// Never a raw backend message or stack trace — the dev proxy in vite.config.ts
// already strips those before they reach this client.
export type ErrorCode =
  | 'VALIDATION_ERROR'
  | 'NOT_AUTHORIZED'
  | 'NOT_FOUND'
  | 'CONFLICT'
  | 'NETWORK_ERROR'
  | 'SERVER_ERROR'
  | 'AMBIGUOUS_WRITE'

export class ApiError extends Error {
  code: ErrorCode
  constructor(code: ErrorCode, message: string) {
    super(message)
    this.code = code
    this.name = 'ApiError'
  }
}
