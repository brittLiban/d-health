import {
  ApiError,
  type IdentifierSource,
  type OpenMrsPatient,
  type PatientRegistration,
  type ErrorCode,
} from './types'

interface NormalizedErrorBody {
  error?: { code?: string; message?: string }
}

// ADR-027's app.$BASE_DOMAIN CSRF boundary (UI-004): a mutating verb under
// /openmrs/* with no custom header 403s at Caddy before reaching OpenMRS at
// all. UI-011's logout module established this same pattern; every write
// below reuses it rather than inventing a second convention.
const MUTATION_HEADERS = { 'X-Daher-App-Request': '1' } as const

const KNOWN_CODES: ErrorCode[] = [
  'VALIDATION_ERROR',
  'NOT_AUTHORIZED',
  'NOT_FOUND',
  'CONFLICT',
  'NETWORK_ERROR',
  'SERVER_ERROR',
  'AMBIGUOUS_WRITE',
]

function asErrorCode(value: string | undefined): ErrorCode {
  return KNOWN_CODES.includes(value as ErrorCode) ? (value as ErrorCode) : 'SERVER_ERROR'
}

/**
 * Fetches `/openmrs/*` same-origin — in production this is Caddy's
 * app.$BASE_DOMAIN route straight to openmrs-gateway (ADR-027), authenticated
 * by whatever real OpenMRS session cookie the browser already holds; this
 * code has no notion of a credential. In development, the identical path is
 * caught by vite.devOpenmrsProxy.ts, which is the only thing that ever holds
 * OMRS_ADMIN_PASSWORD (never this client, never the bundle) and forwards
 * with Basic Auth added server-side. See docs/UI-001-CLINICAL-UX.md §13a.1
 * for the runtime-verified contract this wraps: name and identifier share
 * one `q=` parameter, an empty result set is a normal 200, and the raw error
 * envelope can carry a full Java stack trace — the dev proxy strips this
 * before it ever reaches the browser; the production Caddy route (UI-004)
 * does not yet do the equivalent body rewrite, a known gap this ticket does
 * not close (see ui/README.md).
 */
export async function searchPatients(query: string): Promise<OpenMrsPatient[]> {
  let response: Response
  try {
    response = await fetch(`/openmrs/ws/rest/v1/patient?q=${encodeURIComponent(query)}`, {
      headers: { Accept: 'application/json' },
    })
  } catch {
    throw new ApiError('NETWORK_ERROR', 'Connexion au serveur impossible.')
  }

  let body: unknown
  try {
    body = await response.json()
  } catch {
    body = undefined
  }

  if (!response.ok) {
    const normalized = (body as NormalizedErrorBody | undefined)?.error
    throw new ApiError(
      asErrorCode(normalized?.code),
      normalized?.message ?? 'Une erreur du serveur est survenue.',
    )
  }

  const results = (body as { results?: OpenMrsPatient[] } | undefined)?.results
  return Array.isArray(results) ? results : []
}

/**
 * UI-001A measured (docs/UI-001-CLINICAL-UX.md §13a.2) that an unauthorized
 * patient write does NOT cleanly 401 — it comes back as a structurally valid
 * `400` whose nested validation text happens to mention "Privileges
 * required". The rule stated there, applied here for the first time in code:
 * treat that substring as NOT_AUTHORIZED regardless of the HTTP status
 * observed. `rawText` may contain a full Java stack trace (OpenMRS's
 * `detail` field) — it is scanned and then discarded here, never rendered,
 * never logged, never returned to a caller.
 */
function writeErrorFromBody(status: number, rawText: string): ApiError {
  if (rawText.includes('Privileges required')) {
    return new ApiError('NOT_AUTHORIZED', 'Accès refusé.')
  }
  if (rawText.includes('already in use by another patient')) {
    return new ApiError('CONFLICT', 'Identifiant déjà utilisé.')
  }
  if (status === 401) {
    return new ApiError('NOT_AUTHORIZED', 'Accès refusé.')
  }
  if (status === 400) {
    return new ApiError('VALIDATION_ERROR', 'Requête invalide.')
  }
  return new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.')
}

/**
 * GET /idgen/identifiersource — UI-012 measured exactly one source on this
 * pinned stack ("Generator for OpenMRS ID"). Zero or more than one is a
 * configuration state this ticket never proved against, so it fails closed
 * as SERVER_ERROR rather than guessing which source to use.
 */
export async function discoverIdentifierSource(): Promise<IdentifierSource> {
  let response: Response
  try {
    response = await fetch('/openmrs/ws/rest/v1/idgen/identifiersource?v=default', {
      headers: { Accept: 'application/json' },
    })
  } catch {
    throw new ApiError('NETWORK_ERROR', 'Connexion au serveur impossible.')
  }

  const rawText = await response.text()
  if (!response.ok) {
    throw writeErrorFromBody(response.status, rawText)
  }

  let body: { results?: Array<{ uuid: string; identifierType?: { uuid?: string }; locationBehavior?: string }> }
  try {
    body = JSON.parse(rawText)
  } catch {
    throw new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.')
  }

  const results = body.results ?? []
  if (results.length !== 1 || !results[0].identifierType?.uuid) {
    throw new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.')
  }
  return {
    uuid: results[0].uuid,
    identifierTypeUuid: results[0].identifierType.uuid,
    locationBehavior: results[0].locationBehavior ?? 'NOT_USED',
  }
}

/** POST /idgen/identifiersource/{uuid}/identifier — never a client-generated ID. */
export async function generateIdentifier(sourceUuid: string): Promise<string> {
  let response: Response
  try {
    response = await fetch(`/openmrs/ws/rest/v1/idgen/identifiersource/${sourceUuid}/identifier`, {
      method: 'POST',
      headers: { ...MUTATION_HEADERS, 'Content-Type': 'application/json', Accept: 'application/json' },
      body: '{}',
    })
  } catch {
    throw new ApiError('NETWORK_ERROR', 'Connexion au serveur impossible.')
  }

  const rawText = await response.text()
  if (!response.ok) {
    throw writeErrorFromBody(response.status, rawText)
  }

  let body: { identifier?: string }
  try {
    body = JSON.parse(rawText)
  } catch {
    throw new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.')
  }
  if (!body.identifier) {
    throw new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.')
  }
  return body.identifier
}

/**
 * GET /patient?identifier=<exact> — an EXACT match, not the fuzzy `q=` search
 * above (UI-012 verified live: a nonexistent identifier returns zero results,
 * never a fuzzy near-match). This is the reconciliation primitive: the
 * generated identifier is known before the create POST is even sent, so it
 * doubles as an idempotency key.
 */
export async function findPatientByIdentifier(identifier: string): Promise<OpenMrsPatient[]> {
  let response: Response
  try {
    response = await fetch(`/openmrs/ws/rest/v1/patient?identifier=${encodeURIComponent(identifier)}`, {
      headers: { Accept: 'application/json' },
    })
  } catch {
    throw new ApiError('NETWORK_ERROR', 'Connexion au serveur impossible.')
  }

  const rawText = await response.text()
  if (!response.ok) {
    throw writeErrorFromBody(response.status, rawText)
  }

  let body: { results?: OpenMrsPatient[] }
  try {
    body = JSON.parse(rawText)
  } catch {
    throw new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.')
  }
  return Array.isArray(body.results) ? body.results : []
}

/**
 * POST /patient. `identifierSource.locationBehavior` is read, not assumed:
 * only `NOT_USED` (the one value measured live on this stack) is handled by
 * omitting `location` entirely. Anything else throws SERVER_ERROR rather
 * than picking a location — UI-012 found no safe deterministic rule for
 * that, and it is recorded as a human/facility decision, not guessed here.
 */
export async function createPatient(
  registration: PatientRegistration,
  identifier: string,
  identifierSource: IdentifierSource,
): Promise<OpenMrsPatient> {
  if (identifierSource.locationBehavior !== 'NOT_USED') {
    throw new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.')
  }

  let response: Response
  try {
    response = await fetch('/openmrs/ws/rest/v1/patient', {
      method: 'POST',
      headers: { ...MUTATION_HEADERS, 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        person: {
          names: [{ givenName: registration.givenName, familyName: registration.familyName }],
          gender: registration.gender,
          birthdate: registration.birthdate,
        },
        identifiers: [
          {
            identifier,
            identifierType: identifierSource.identifierTypeUuid,
            preferred: true,
          },
        ],
      }),
    })
  } catch {
    // Ambiguous by construction: the caller cannot tell from this exception
    // alone whether OpenMRS committed the patient before the connection was
    // lost. Never retried automatically here — the caller reconciles using
    // `identifier` via findPatientByIdentifier.
    throw new ApiError('AMBIGUOUS_WRITE', "L'état de l'enregistrement est incertain.")
  }

  const rawText = await response.text()
  if (!response.ok) {
    throw writeErrorFromBody(response.status, rawText)
  }

  try {
    return JSON.parse(rawText) as OpenMrsPatient
  } catch {
    // A 2xx with an unparsable body is exactly as uncertain as a dropped
    // connection — the server-side outcome is not something this response
    // can confirm, so it is treated the same way: reconcile, never retry.
    throw new ApiError('AMBIGUOUS_WRITE', "L'état de l'enregistrement est incertain.")
  }
}
