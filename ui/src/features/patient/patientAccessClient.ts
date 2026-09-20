/**
 * DH-PATIENT-001 §24/§25: the patient-side API client, deliberately separate from
 * `ExchangeClient`.
 *
 * The two credentials are not the same kind of thing and must not share a code path:
 *
 *     clinician OIDC bearer (Keycloak JWT, identity-scoped, refreshable)
 *         !=
 *     patient opaque bearer (256-bit random, order-scoped, single-issuance, unrecoverable)
 *
 * Mixing them would make it possible for a patient credential to be attached to a clinician
 * endpoint, or for the OIDC token provider to be consulted on a patient request. Keeping a
 * separate, minimal class makes that structurally impossible rather than merely discouraged.
 *
 * This client exposes exactly the one patient endpoint the launch UI uses. DH-EX-005's
 * patient-marketplace endpoints (`/patient/order/lab-options`, `/patient/order/selection`) are
 * preserved and still served by the backend, but are deliberately absent here (§6/§67, AC19): for
 * the provider-assigned launch flow, the clinic chooses the laboratory, and a method that does
 * not exist cannot be called by accident.
 */

import type { PatientOrder } from '../../api/exchangeTypes'

/**
 * Why this is a bare union rather than a reuse of `ExchangeApiError`: the patient surface must
 * never surface a server-provided `detail` string to the viewer. The Exchange's patient routes
 * already collapse every credential failure (missing, malformed, unknown, expired, revoked) to
 * one identical generic 401 -- this type keeps that indistinguishability intact on the client
 * side too, by having no channel through which a reason could travel.
 *
 * `'invalid_access'` covers every authorization refusal. `'unavailable'` covers network/server
 * failure -- a temporary condition worth a retry button, and *not* a statement about the
 * credential.
 */
export type PatientAccessFailure = 'invalid_access' | 'unavailable'

export class PatientAccessError extends Error {
  readonly kind: PatientAccessFailure

  constructor(kind: PatientAccessFailure) {
    // The message is a fixed constant, never a server string and never the token: this object
    // may end up in a React error boundary or a console in some future refactor, and neither
    // should be able to reveal anything.
    super(kind === 'invalid_access' ? 'patient access refused' : 'patient service unavailable')
    this.name = 'PatientAccessError'
    this.kind = kind
  }
}

export interface PatientAccessClientOptions {
  baseUrl?: string
  fetchImpl?: typeof fetch
}

export class PatientAccessClient {
  private readonly baseUrl: string
  private readonly fetchImpl: typeof fetch

  constructor(options: PatientAccessClientOptions = {}) {
    const rawBaseUrl =
      options.baseUrl ||
      (typeof import.meta !== 'undefined' && import.meta.env?.VITE_EXCHANGE_BASE_URL) ||
      '/exchange/v1'
    this.baseUrl = rawBaseUrl.replace(/\/+$/, '')
    this.fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args))
  }

  /**
   * GET /exchange/v1/patient/order
   *
   * The grant alone determines which order is returned -- there is no order id in the path, the
   * query string, or a body, which is what structurally removes the IDOR class here. The token
   * travels in the `Authorization` header and nowhere else: never a query parameter, never a path
   * segment, never a log line.
   */
  async getOrder(rawToken: string, signal?: AbortSignal): Promise<PatientOrder> {
    let response: Response
    try {
      response = await this.fetchImpl(`${this.baseUrl}/patient/order`, {
        method: 'GET',
        headers: {
          Accept: 'application/json',
          Authorization: `Bearer ${rawToken}`,
        },
        signal,
      })
    } catch (error) {
      // An aborted request is a caller-initiated unmount/navigation, not a failure to report --
      // rethrow it untouched so the caller's own abort guard can ignore it.
      if (error instanceof DOMException && error.name === 'AbortError') throw error
      throw new PatientAccessError('unavailable')
    }

    if (response.status === 401 || response.status === 403 || response.status === 404) {
      throw new PatientAccessError('invalid_access')
    }
    if (!response.ok) {
      throw new PatientAccessError('unavailable')
    }

    try {
      return (await response.json()) as PatientOrder
    } catch {
      throw new PatientAccessError('unavailable')
    }
  }
}
