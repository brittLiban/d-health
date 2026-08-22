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
