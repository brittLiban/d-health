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
 * Searches patients through the dev-only proxy in vite.config.ts, which is the
 * only thing that ever holds the OpenMRS credential (never this client, never
 * the bundle). See docs/UI-001-CLINICAL-UX.md §13a.1 for the runtime-verified
 * contract this wraps: name and identifier share one `q=` parameter, an empty
 * result set is a normal 200, and the raw error envelope can carry a full Java
 * stack trace — which the proxy strips before this function ever sees it.
 */
export async function searchPatients(query: string): Promise<OpenMrsPatient[]> {
  let response: Response
  try {
    response = await fetch(`/api/openmrs/patient?q=${encodeURIComponent(query)}`, {
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
