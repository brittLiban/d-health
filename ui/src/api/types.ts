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
