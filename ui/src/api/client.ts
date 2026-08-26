import {
  ApiError,
  type ActiveVisit,
  type IdentifierSource,
  type OpenMrsPatient,
  type OpenMrsSession,
  type PatientRegistration,
  type ErrorCode,
} from './types'
import {
  ADMISSION_ENCOUNTER_TYPE_UUID,
  DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID,
  MATERNITE_LOCATION_UUID,
} from './admissionConstants'

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

// ---------------------------------------------------------------------------
// UI-013 — clinician maternity admission.
//
// Two mutation boundaries (Visit, then linked Encounter), not one. ADR-017C
// found the Encounter POST can persist while the response itself 500s
// (a serialization failure unrelated to whether the write committed) — so
// unlike `writeErrorFromBody` above, a 5xx on either admission write is
// never treated as a definite failure. `admissionWriteErrorFromBody` is a
// deliberately different, more conservative classifier for exactly this
// reason: only a body containing "Privileges required" or a 4xx status is
// evidence of a genuine pre-save rejection; everything else — including a
// 5xx — is AMBIGUOUS_WRITE and must be reconciled against server state,
// never retried blindly.
function admissionWriteErrorFromBody(status: number, rawText: string): ApiError {
  if (rawText.includes('Privileges required')) {
    return new ApiError('NOT_AUTHORIZED', 'Accès refusé.')
  }
  if (status === 401) {
    return new ApiError('NOT_AUTHORIZED', 'Accès refusé.')
  }
  if (status >= 400 && status < 500) {
    return new ApiError('VALIDATION_ERROR', 'Requête invalide.')
  }
  return new ApiError('AMBIGUOUS_WRITE', "L'état de l'admission est incertain.")
}

interface MetadataCandidate {
  uuid: string
  retired?: boolean
}

async function fetchMetadataCandidates(resource: string, query: string): Promise<MetadataCandidate[]> {
  let response: Response
  try {
    response = await fetch(
      `/openmrs/ws/rest/v1/${resource}?q=${encodeURIComponent(query)}&v=custom:(uuid,retired)`,
      { headers: { Accept: 'application/json' } },
    )
  } catch {
    throw new ApiError('NETWORK_ERROR', 'Connexion au serveur impossible.')
  }
  const rawText = await response.text()
  if (!response.ok) {
    throw writeErrorFromBody(response.status, rawText)
  }
  let body: { results?: MetadataCandidate[] }
  try {
    body = JSON.parse(rawText)
  } catch {
    throw new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.')
  }
  return Array.isArray(body.results) ? body.results : []
}

function assertPinnedMatch(candidates: MetadataCandidate[], expectedUuid: string): void {
  const active = candidates.filter((candidate) => !candidate.retired)
  if (active.length !== 1 || active[0].uuid !== expectedUuid) {
    // Fail closed on 0, >1, a retired-only result, or a UUID that no
    // longer matches the pinned Daher-owned artifact (§3) — never silently
    // fall back to whichever result comes back first.
    throw new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.')
  }
}

/**
 * Drift check only — never the source of the mutation payload (§3, §13,
 * §14 use the pinned constants directly). Confirms exactly one, non-retired,
 * UUID-matching result for each of the three canonical admission artifacts.
 * Never falls back to an upstream generic (Facility Visit, Inpatient Ward)
 * on a mismatch — it throws instead.
 */
export async function verifyAdmissionMetadata(): Promise<void> {
  const [visitTypes, encounterTypes, locations] = await Promise.all([
    fetchMetadataCandidates('visittype', 'Daher Maternity Admission'),
    fetchMetadataCandidates('encountertype', 'Admission'),
    fetchMetadataCandidates('location', 'Maternité'),
  ])
  assertPinnedMatch(visitTypes, DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID)
  assertPinnedMatch(encounterTypes, ADMISSION_ENCOUNTER_TYPE_UUID)
  assertPinnedMatch(locations, MATERNITE_LOCATION_UUID)
}

/**
 * GET /session. `user.roles` is what the frontend uses for UX-only role
 * gating (ADR-017D §5 — the server privilege set is the real boundary);
 * `currentProvider` is what ADR-017E2 proved is non-null only for the exact
 * "Provider" client role, with no second truth source.
 */
export async function getSession(): Promise<OpenMrsSession> {
  let response: Response
  try {
    response = await fetch('/openmrs/ws/rest/v1/session', {
      headers: { Accept: 'application/json' },
    })
  } catch {
    throw new ApiError('NETWORK_ERROR', 'Connexion au serveur impossible.')
  }

  const rawText = await response.text()
  if (!response.ok) {
    throw writeErrorFromBody(response.status, rawText)
  }

  try {
    return JSON.parse(rawText) as OpenMrsSession
  } catch {
    throw new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.')
  }
}

// The one read call UI-013 measured live (docs/UI-013 handoff §9): a single
// custom representation carrying visitType/location plus embedded encounters
// (encounterType, voided) — no obs/orders/diagnoses, so no clinical content
// beyond what admission-state derivation needs ever crosses the wire. This
// is reused for the preflight read AND both post-write reconciliation reads
// (§16, §17) — one mechanism, not three.
const ACTIVE_VISIT_REPRESENTATION =
  'custom:(uuid,visitType:(uuid,display),location:(uuid,display),stopDatetime,' +
  'encounters:(uuid,encounterType:(uuid,display),voided))'

/**
 * GET /visit?patient=<uuid>&includeInactive=false&v=custom:(...). Returns
 * every active visit for the patient, of any type — admission-specific
 * filtering (Daher Maternity Admission + Maternité) happens in
 * `deriveAdmissionState`, not here, so this function has exactly one
 * server contract to characterize regardless of what the caller does with
 * the result.
 */
export async function getActiveVisits(patientUuid: string): Promise<ActiveVisit[]> {
  let response: Response
  try {
    response = await fetch(
      `/openmrs/ws/rest/v1/visit?patient=${encodeURIComponent(patientUuid)}` +
        `&includeInactive=false&v=${encodeURIComponent(ACTIVE_VISIT_REPRESENTATION)}`,
      { headers: { Accept: 'application/json' } },
    )
  } catch {
    throw new ApiError('NETWORK_ERROR', 'Connexion au serveur impossible.')
  }

  const rawText = await response.text()
  if (!response.ok) {
    throw writeErrorFromBody(response.status, rawText)
  }

  let body: { results?: ActiveVisit[] }
  try {
    body = JSON.parse(rawText)
  } catch {
    throw new ApiError('SERVER_ERROR', 'Une erreur du serveur est survenue.')
  }
  return Array.isArray(body.results) ? body.results : []
}

/**
 * POST /visit with the pinned Daher Maternity Admission visit type and
 * Maternité location (ADR-028) — the exact payload shape UI-001A proved
 * (`{patient, visitType, location}`, docs/UI-001-CLINICAL-UX.md §13a.5).
 * A dropped connection or unparsable 2xx is AMBIGUOUS_WRITE, exactly like
 * `createPatient` above: never retried here, only reconciled by the caller
 * via `getActiveVisits`.
 */
export async function createMaternityVisit(patientUuid: string): Promise<{ uuid: string }> {
  let response: Response
  try {
    response = await fetch('/openmrs/ws/rest/v1/visit', {
      method: 'POST',
      headers: { ...MUTATION_HEADERS, 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        patient: patientUuid,
        visitType: DAHER_MATERNITY_ADMISSION_VISIT_TYPE_UUID,
        location: MATERNITE_LOCATION_UUID,
      }),
    })
  } catch {
    throw new ApiError('AMBIGUOUS_WRITE', "L'état de l'admission est incertain.")
  }

  const rawText = await response.text()
  if (!response.ok) {
    throw admissionWriteErrorFromBody(response.status, rawText)
  }

  try {
    const body = JSON.parse(rawText) as { uuid?: string }
    if (!body.uuid) throw new Error('missing uuid')
    return { uuid: body.uuid }
  } catch {
    throw new ApiError('AMBIGUOUS_WRITE', "L'état de l'admission est incertain.")
  }
}

/**
 * POST /encounter, only ever called with an already-confirmed Visit uuid
 * (§12/§14). Deliberately does NOT send `encounterProviders` — ADR-017E2
 * proved a valid admission completes with `encounterProviders: []` even
 * when `currentProvider` exists, and this ticket preserves that exact
 * contract rather than inferring provider attribution (§6).
 */
export async function createAdmissionEncounter(
  patientUuid: string,
  visitUuid: string,
): Promise<{ uuid: string }> {
  let response: Response
  try {
    response = await fetch('/openmrs/ws/rest/v1/encounter', {
      method: 'POST',
      headers: { ...MUTATION_HEADERS, 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        patient: patientUuid,
        encounterType: ADMISSION_ENCOUNTER_TYPE_UUID,
        location: MATERNITE_LOCATION_UUID,
        visit: visitUuid,
      }),
    })
  } catch {
    throw new ApiError('AMBIGUOUS_WRITE', "L'état de l'admission est incertain.")
  }

  const rawText = await response.text()
  if (!response.ok) {
    throw admissionWriteErrorFromBody(response.status, rawText)
  }

  try {
    const body = JSON.parse(rawText) as { uuid?: string }
    if (!body.uuid) throw new Error('missing uuid')
    return { uuid: body.uuid }
  } catch {
    throw new ApiError('AMBIGUOUS_WRITE', "L'état de l'admission est incertain.")
  }
}
