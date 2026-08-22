import { ApiError, type OpenMrsPatient, type ErrorCode } from './types'

interface NormalizedErrorBody {
  error?: { code?: string; message?: string }
}

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
