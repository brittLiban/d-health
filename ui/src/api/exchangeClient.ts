/**
 * DHealth Exchange API typed frontend client (DH-CLINIC-001).
 *
 * Requirements (ADR-042, ADR-043, docs/MVP-1.0.md):
 * - One configured Exchange base path.
 * - Strict typed responses.
 * - Normalized error handling (FastAPI { detail: ... } parsing).
 * - No clinical interpretation or decision support.
 * - Zero hard-coded patient or test data.
 * - No mock production fallback (fails closed with truthful status).
 * - Authorization failures handled explicitly.
 * - Network failures visibly recoverable.
 * - Zero calls to /openmrs/*.
 */

import type { ErrorCode } from './types'
import type {
  Amendment,
  AmendmentInspection,
  AmendmentListResponse,
  AmendmentQueue,
  AmendmentReleaseRequest,
  AssignLabRequest,
  BindPatientReferenceOutcome,
  CatalogEntry,
  ClinicPatientSummary,
  DoctorReviewResponse,
  EntryCompletionResponse,
  EnrollmentBlockerCode,
  ExchangeErrorCode,
  ExchangeMeResponse,
  FacilityEnrollment,
  FacilityReadiness,
  IdentityLookupResult,
  InterfaceType,
  InterfaceVerificationState,
  OnboardingStatus,
  ParticipationMode,
  LabAssignmentResponse,
  LabCollectionResponse,
  LabObservation,
  LabObservationHistory,
  LabOrder,
  LabOrderCreateOutcome,
  LabOrderCreateRequest,
  LabOrderIntent,
  LabOrderIntentCreateOutcome,
  LabOrderIntentCreateRequest,
  LabOrderStatus,
  LabOrderSummary,
  LabReleaseVersionContent,
  LabReleaseVersionHistory,
  LabReleaseVersionSummary,
  LabResultInspection,
  LabResultReleaseOutcome,
  LabWorkflowOrder,
  NativeCorrectionRequest,
  NativeResultValueBatchResponse,
  PatientAccessIssueOutcome,
  PatientAccessMetadata,
  PatientCreateOutcome,
  PatientCreateRequest,
  PatientDirectoryEntry,
  PatientReference,
  PatientReferenceBindRequest,
  ProviderEligibleLab,
  ProviderLabRoute,
  ProviderOrderResults,
  ProviderReleaseVersionContent,
  ProviderReleaseVersionHistory,
  ProviderResultReviewQueueResponse,
  ReleaseQueue,
  ResultEntryPlan,
  ResultEntryWorkspace,
  ReviewQueueStatusFilter,
} from './exchangeTypes'

export function mapExchangeErrorToCode(code: ExchangeErrorCode): ErrorCode {
  switch (code) {
    case 'UNAUTHORIZED':
    case 'FORBIDDEN':
      return 'NOT_AUTHORIZED'
    case 'VALIDATION_ERROR':
      return 'VALIDATION_ERROR'
    case 'NOT_FOUND':
      return 'NOT_FOUND'
    case 'CONFLICT':
      return 'CONFLICT'
    case 'NETWORK_ERROR':
      return 'NETWORK_ERROR'
    case 'SERVER_ERROR':
    default:
      return 'SERVER_ERROR'
  }
}

export function isDefinitiveExchangeError(error: unknown): boolean {
  if (error instanceof ExchangeApiError) {
    return (
      error.status === 400 ||
      error.status === 401 ||
      error.status === 403 ||
      error.status === 404 ||
      error.status === 409 ||
      error.status === 422 ||
      error.code === 'UNAUTHORIZED' ||
      error.code === 'FORBIDDEN' ||
      error.code === 'NOT_FOUND' ||
      error.code === 'CONFLICT' ||
      error.code === 'VALIDATION_ERROR'
    )
  }
  return false
}

export function isAmbiguousExchangeError(error: unknown): boolean {
  if (error instanceof ExchangeApiError) {
    return (
      error.status === 0 ||
      error.status === 408 ||
      error.status >= 500 ||
      error.code === 'NETWORK_ERROR' ||
      error.code === 'SERVER_ERROR'
    )
  }
  return true
}


export class ExchangeApiError extends Error {
  readonly status: number
  readonly code: ExchangeErrorCode
  readonly detail: string
  readonly isAuthError: boolean
  readonly isNetworkError: boolean
  /**
   * The parsed error body, when the server sent one.
   *
   * Some refusals carry structure a person needs -- a readiness refusal names
   * which conditions failed and at which end of the pair. Flattening every error
   * to one string would drop exactly the part an operator can act on, so the body
   * is kept, typed as unknown: a caller that wants it must narrow it.
   */
  readonly body: unknown

  constructor(status: number, code: ExchangeErrorCode, detail: string, body?: unknown) {
    super(detail)
    this.name = 'ExchangeApiError'
    this.status = status
    this.code = code
    this.detail = detail
    this.body = body
    this.isAuthError = code === 'UNAUTHORIZED' || code === 'FORBIDDEN'
    this.isNetworkError = code === 'NETWORK_ERROR'
  }
}

export interface ExchangeClientOptions {
  baseUrl?: string
  getAuthToken?: () => string | null | Promise<string | null>
}

function parseFastApiDetail(data: unknown, fallback: string): string {
  if (!data || typeof data !== 'object') {
    return fallback
  }

  const record = data as Record<string, unknown>
  if (typeof record.detail === 'string') {
    return record.detail
  }

  // Pydantic validation errors format: { detail: [{ loc: [...], msg: "...", type: "..." }] }
  if (Array.isArray(record.detail)) {
    const messages = record.detail
      .map((item) => (item && typeof item === 'object' && 'msg' in item ? String(item.msg) : null))
      .filter((msg): msg is string => Boolean(msg))
    if (messages.length > 0) {
      return messages.join('; ')
    }
  }

  return fallback
}

/**
 * HTTP status -> `ExchangeErrorCode`. Extracted to module scope by DH-PATIENT-001 so the
 * JSON-body and no-content request paths share exactly one mapping rather than two copies that
 * could quietly drift apart.
 */
function mapStatusToExchangeErrorCode(status: number): ExchangeErrorCode {
  if (status === 400 || status === 422) return 'VALIDATION_ERROR'
  if (status === 401) return 'UNAUTHORIZED'
  if (status === 403) return 'FORBIDDEN'
  if (status === 404) return 'NOT_FOUND'
  if (status === 409) return 'CONFLICT'
  return 'SERVER_ERROR'
}

export class ExchangeClient {
  private readonly baseUrl: string
  private readonly getAuthToken?: () => string | null | Promise<string | null>

  constructor(options: ExchangeClientOptions = {}) {
    const rawBaseUrl =
      options.baseUrl ||
      (typeof import.meta !== 'undefined' && import.meta.env?.VITE_EXCHANGE_BASE_URL) ||
      '/exchange/v1'
    // Normalize: strip trailing slash
    this.baseUrl = rawBaseUrl.replace(/\/+$/, '')
    this.getAuthToken = options.getAuthToken
  }

  getBaseUrl(): string {
    return this.baseUrl
  }

  private async buildHeaders(customToken?: string): Promise<Record<string, string>> {
    const headers: Record<string, string> = {
      Accept: 'application/json',
    }

    let token = customToken
    if (!token && this.getAuthToken) {
      const resolved = await this.getAuthToken()
      if (resolved) {
        token = resolved
      }
    }

    if (token) {
      headers.Authorization = `Bearer ${token.trim()}`
    }

    return headers
  }

  private async requestWithResponse<T>(
    path: string,
    options: RequestInit = {},
    customToken?: string,
  ): Promise<{ data: T; status: number }> {
    const url = `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`
    const headers = await this.buildHeaders(customToken)

    let response: Response
    try {
      response = await fetch(url, {
        ...options,
        headers: {
          ...headers,
          ...(options.headers as Record<string, string> | undefined),
        },
      })
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Connexion au serveur DHealth Exchange impossible.'
      throw new ExchangeApiError(0, 'NETWORK_ERROR', message)
    }

    if (response.ok) {
      const data = (await response.json()) as T
      return { data, status: response.status }
    }

    let errorDetail = response.statusText || 'Erreur du serveur DHealth Exchange.'
    let errorBody: unknown
    try {
      errorBody = await response.json()
      errorDetail = parseFastApiDetail(errorBody, errorDetail)
    } catch {
      // Body not json, retain statusText
    }

    throw new ExchangeApiError(
      response.status,
      mapStatusToExchangeErrorCode(response.status),
      errorDetail,
      errorBody,
    )
  }

  private async request<T>(
    path: string,
    options: RequestInit = {},
    customToken?: string,
  ): Promise<T> {
    const { data } = await this.requestWithResponse<T>(path, options, customToken)
    return data
  }

  /**
   * DH-PATIENT-001: the sibling of `requestWithResponse` for endpoints that legitimately answer
   * `204 No Content` -- the Exchange's `DELETE .../patient-access` being the first one this client
   * calls.
   *
   * It exists because `requestWithResponse` unconditionally `await response.json()`s a successful
   * response. A 204 has an empty body, so that parse throws a raw `SyntaxError` -- not an
   * `ExchangeApiError` -- which `isAmbiguousExchangeError` then classifies as *ambiguous*. The
   * concrete consequence without this method: every **successful** revoke would look like a lost
   * response and drive the caller into DH-PATIENT-001 §48's ambiguity-recovery path, telling the
   * clinician their revoke may not have committed when it definitely did. Error handling below is
   * otherwise identical to `requestWithResponse`, deliberately sharing its status-to-code mapping
   * rather than duplicating a second, driftable copy.
   */
  private async requestNoContent(
    path: string,
    options: RequestInit = {},
    customToken?: string,
  ): Promise<void> {
    const url = `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`
    const headers = await this.buildHeaders(customToken)

    let response: Response
    try {
      response = await fetch(url, {
        ...options,
        headers: {
          ...headers,
          ...(options.headers as Record<string, string> | undefined),
        },
      })
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Connexion au serveur DHealth Exchange impossible.'
      throw new ExchangeApiError(0, 'NETWORK_ERROR', message)
    }

    if (response.ok) {
      return
    }

    let errorDetail = response.statusText || 'Erreur du serveur DHealth Exchange.'
    let errorBody: unknown
    try {
      errorBody = await response.json()
      errorDetail = parseFastApiDetail(errorBody, errorDetail)
    } catch {
      // Body not json, retain statusText
    }

    throw new ExchangeApiError(
      response.status,
      mapStatusToExchangeErrorCode(response.status),
      errorDetail,
      errorBody,
    )
  }

  /**
   * GET /exchange/v1/me
   * Returns current authenticated identity and organization memberships.
   */
  async getMe(token?: string): Promise<ExchangeMeResponse> {
    return this.request<ExchangeMeResponse>('/me', { method: 'GET' }, token)
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/orders
   * Lists diagnostic lab orders for a provider organization.
   */
  async listOrders(
    providerId: string,
    options: { limit?: number; token?: string } = {},
  ): Promise<LabOrderSummary[]> {
    const params = new URLSearchParams()
    if (options.limit !== undefined) {
      params.set('limit', String(options.limit))
    }
    const query = params.toString() ? `?${params.toString()}` : ''
    return this.request<LabOrderSummary[]>(
      `/organizations/${encodeURIComponent(providerId)}/orders${query}`,
      { method: 'GET' },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/orders/{order_id}
   * Retrieves single order details including ordered items.
   */
  async getOrder(providerId: string, orderId: string, token?: string): Promise<LabOrder> {
    return this.request<LabOrder>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}`,
      { method: 'GET' },
      token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/orders/{order_id}/results
   * Retrieves released results for a completed order. Returns 409 if not completed.
   */
  async getOrderResults(
    providerId: string,
    orderId: string,
    token?: string,
  ): Promise<ProviderOrderResults> {
    return this.request<ProviderOrderResults>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/results`,
      { method: 'GET' },
      token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/orders/{order_id}/release-versions
   * Retrieves released versions history and review obligations for an order.
   */
  async getReleaseVersions(
    providerId: string,
    orderId: string,
    token?: string,
  ): Promise<ProviderReleaseVersionHistory> {
    return this.request<ProviderReleaseVersionHistory>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/release-versions`,
      { method: 'GET' },
      token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/orders/{order_id}/release-versions/{release_version_id}
   * DH-CLINIC-007 §19/§20: the exact, immutable content of one specific released version -- this
   * is the content that must actually be displayed before any review mutation targets that exact
   * version. Never derived from live/latest state.
   */
  async getReleaseVersion(
    providerId: string,
    orderId: string,
    releaseVersionId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<ProviderReleaseVersionContent> {
    return this.request<ProviderReleaseVersionContent>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/release-versions/${encodeURIComponent(releaseVersionId)}`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/result-reviews
   * DH-CLINIC-007 §8/§9: the provider-scoped review queue -- one entry per outstanding review
   * *obligation* (never one per order). `status` defaults to `'PENDING'`; pass `'ALL'` for
   * history. No result/observation value of any kind is ever present on an entry.
   */
  async listResultReviews(
    providerId: string,
    options: {
      status?: ReviewQueueStatusFilter
      limit?: number
      token?: string
      signal?: AbortSignal
    } = {},
  ): Promise<ProviderResultReviewQueueResponse> {
    const params = new URLSearchParams()
    if (options.status !== undefined) {
      params.set('status', options.status)
    }
    if (options.limit !== undefined) {
      params.set('limit', String(options.limit))
    }
    const query = params.toString() ? `?${params.toString()}` : ''
    return this.request<ProviderResultReviewQueueResponse>(
      `/organizations/${encodeURIComponent(providerId)}/result-reviews${query}`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST .../release-versions/{release_version_id}/review/no-follow-up-required
   * DH-CLINIC-007 §6/§31: the safe, version-specific review mutation -- always prefer this over
   * the legacy order-only route whenever a release version exists, even if it is currently the
   * only version.
   */
  async reviewReleaseVersionNoFollowUp(
    providerId: string,
    orderId: string,
    releaseVersionId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<DoctorReviewResponse> {
    return this.request<DoctorReviewResponse>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/release-versions/${encodeURIComponent(releaseVersionId)}/review/no-follow-up-required`,
      { method: 'POST', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST .../release-versions/{release_version_id}/review/follow-up-required
   * DH-CLINIC-007 §6/§31: the safe, version-specific review mutation. §25: this records only
   * that the clinician judged follow-up necessary -- it does not schedule, contact, or notify.
   */
  async reviewReleaseVersionFollowUp(
    providerId: string,
    orderId: string,
    releaseVersionId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<DoctorReviewResponse> {
    return this.request<DoctorReviewResponse>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/release-versions/${encodeURIComponent(releaseVersionId)}/review/follow-up-required`,
      { method: 'POST', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST .../orders/{order_id}/review/no-follow-up-required
   * DH-CLINIC-007 §7/§31: the legacy order-only review mutation -- correct *only* for a
   * grandfathered COMPLETED-without-release-version order (`legacy_unversioned: true`). Never
   * used by versioned UI even when only one version currently exists.
   */
  async reviewLegacyOrderNoFollowUp(
    providerId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<DoctorReviewResponse> {
    return this.request<DoctorReviewResponse>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/review/no-follow-up-required`,
      { method: 'POST', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST .../orders/{order_id}/review/follow-up-required
   * DH-CLINIC-007 §7/§31: the legacy order-only review mutation -- same scope restriction as
   * `reviewLegacyOrderNoFollowUp`.
   */
  async reviewLegacyOrderFollowUp(
    providerId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<DoctorReviewResponse> {
    return this.request<DoctorReviewResponse>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/review/follow-up-required`,
      { method: 'POST', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/orders/{order_id}/eligible-labs
   * DH-CLINIC-006 §13: labs this provider is explicitly, currently configured to route to that
   * can also fulfil every item on this order. Order id alone determines the required tests --
   * never a client-submitted catalog list. An empty array is a real, valid state (§15), never an
   * error. No price field anywhere in the response (§16/§32).
   */
  async getEligibleLabs(
    providerId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<ProviderEligibleLab[]> {
    return this.request<ProviderEligibleLab[]>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/eligible-labs`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{provider_id}/orders/{order_id}/assign-lab
   * DH-CLINIC-006/DH-EX-010: routes an order to a provider-configured lab -- no price/consent
   * step. Same-lab replay is idempotent (200); a different lab already selected is a 409
   * conflict; an inactive/missing route is also a 409 conflict.
   */
  async assignLab(
    providerId: string,
    orderId: string,
    body: AssignLabRequest,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabAssignmentResponse> {
    return this.request<LabAssignmentResponse>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/assign-lab`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: options.signal,
      },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/lab-routes
   * DH-CLINIC-006 §12: this provider's own configured routing directory (CLINICIAN minimum). No
   * price, no cross-provider exposure -- included for completeness; the order-scoped
   * `getEligibleLabs` above is the endpoint the routing UI actually uses.
   */
  async listProviderLabRoutes(
    providerId: string,
    token?: string,
  ): Promise<ProviderLabRoute[]> {
    return this.request<ProviderLabRoute[]>(
      `/organizations/${encodeURIComponent(providerId)}/lab-routes`,
      { method: 'GET' },
      token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{provider_id}/patient-references
   * Exact-match get-or-create binding for a provider-scoped patient reference (DH-EX-009).
   */
  async bindPatientReference(
    providerId: string,
    body: PatientReferenceBindRequest,
    token?: string,
  ): Promise<PatientReference> {
    const outcome = await this.bindPatientReferenceWithOutcome(providerId, body, token)
    return outcome.reference
  }

  async bindPatientReferenceWithOutcome(
    providerId: string,
    body: PatientReferenceBindRequest,
    token?: string,
  ): Promise<BindPatientReferenceOutcome> {
    const { data, status } = await this.requestWithResponse<PatientReference>(
      `/organizations/${encodeURIComponent(providerId)}/patient-references`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      token,
    )
    return { reference: data, created: status === 201 }
  }

  async listPatientReferences(
    providerId: string,
    options: { query?: string; limit?: number; token?: string } = {},
  ): Promise<ClinicPatientSummary[]> {
    const limit = options.limit ?? 50
    if (options.query?.trim()) {
      return this.request<ClinicPatientSummary[]>(
        `/organizations/${encodeURIComponent(providerId)}/patient-references/search`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: options.query.trim(), limit }),
        },
        options.token,
      )
    }

    const params = new URLSearchParams({ limit: String(limit) })
    return this.request<ClinicPatientSummary[]>(
      `/organizations/${encodeURIComponent(providerId)}/patient-references?${params.toString()}`,
      { method: 'GET' },
      options.token,
    )
  }

  async getPatientReference(
    providerId: string,
    patientReferenceId: string,
    token?: string,
  ): Promise<ClinicPatientSummary> {
    return this.request<ClinicPatientSummary>(
      `/organizations/${encodeURIComponent(providerId)}/patient-references/${encodeURIComponent(patientReferenceId)}`,
      { method: 'GET' },
      token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{provider_id}/patients
   * DH-IDENTITY-001: native, provider-scoped Patient directory registration. Server owns the
   * Patient id and its native PatientReference identity -- the caller supplies only demographic
   * fields plus a request-identity Idempotency-Key, never a patient_id or source identity.
   */
  async createPatientWithOutcome(
    providerId: string,
    idempotencyKey: string,
    body: PatientCreateRequest,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<PatientCreateOutcome> {
    const { data, status } = await this.requestWithResponse<PatientDirectoryEntry>(
      `/organizations/${encodeURIComponent(providerId)}/patients`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify(body),
        signal: options.signal,
      },
      options.token,
    )
    return { patient: data, created: status === 201 }
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/patient-registrations/{idempotency_key}
   * DH-IDENTITY-001 §19: server-side recovery for an ambiguous createPatientWithOutcome response.
   * Returns null on 404 (registration never committed under this provider), throws on other
   * errors.
   */
  async getPatientRegistration(
    providerId: string,
    idempotencyKey: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<PatientDirectoryEntry | null> {
    try {
      return await this.request<PatientDirectoryEntry>(
        `/organizations/${encodeURIComponent(providerId)}/patient-registrations/${encodeURIComponent(idempotencyKey)}`,
        { method: 'GET', signal: options.signal },
        options.token,
      )
    } catch (error) {
      if (error instanceof ExchangeApiError && error.status === 404) {
        return null
      }
      throw error
    }
  }

  async listPatients(
    providerId: string,
    options: { limit?: number; token?: string; signal?: AbortSignal } = {},
  ): Promise<PatientDirectoryEntry[]> {
    const limit = options.limit ?? 50
    const params = new URLSearchParams({ limit: String(limit) })
    return this.request<PatientDirectoryEntry[]>(
      `/organizations/${encodeURIComponent(providerId)}/patients?${params.toString()}`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  async searchPatients(
    providerId: string,
    query: string,
    options: { limit?: number; token?: string; signal?: AbortSignal } = {},
  ): Promise<PatientDirectoryEntry[]> {
    return this.request<PatientDirectoryEntry[]>(
      `/organizations/${encodeURIComponent(providerId)}/patients/search`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, limit: options.limit ?? 50 }),
        signal: options.signal,
      },
      options.token,
    )
  }

  async getPatient(
    providerId: string,
    patientId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<PatientDirectoryEntry> {
    return this.request<PatientDirectoryEntry>(
      `/organizations/${encodeURIComponent(providerId)}/patients/${encodeURIComponent(patientId)}`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/catalog/tests
   * Lists active synthetic diagnostic test catalog entries (DH-CLINIC-003).
   */
  async listCatalogTests(
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<CatalogEntry[]> {
    return this.request<CatalogEntry[]>(
      '/catalog/tests',
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{provider_id}/orders
   * Idempotent lab order creation (DH-EX-004, DH-CLINIC-003).
   */
  async createOrderWithOutcome(
    providerId: string,
    idempotencyKey: string,
    body: LabOrderCreateRequest,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabOrderCreateOutcome> {
    const { data, status } = await this.requestWithResponse<LabOrder>(
      `/organizations/${encodeURIComponent(providerId)}/orders`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify(body),
        signal: options.signal,
      },
      options.token,
    )
    return { order: data, created: status === 201 }
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/patient-references/{patient_reference_id}/orders
   * Lists diagnostic lab orders for a specific patient reference under a provider (DH-CLINIC-003).
   */
  async listPatientOrders(
    providerId: string,
    patientReferenceId: string,
    options: { limit?: number; token?: string; signal?: AbortSignal } = {},
  ): Promise<LabOrder[]> {
    const params = new URLSearchParams()
    if (options.limit !== undefined) {
      params.set('limit', String(options.limit))
    }
    const query = params.toString() ? `?${params.toString()}` : ''
    return this.request<LabOrder[]>(
      `/organizations/${encodeURIComponent(providerId)}/patient-references/${encodeURIComponent(patientReferenceId)}/orders${query}`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/orders/by-idempotency-key/{idempotency_key}
   * Reconciles an order by its idempotency key under a provider (DH-CLINIC-003A).
   * Returns null on 404 (order not found under this provider), throws on other errors.
   */
  async getOrderByIdempotencyKey(
    providerId: string,
    idempotencyKey: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabOrder | null> {
    try {
      return await this.request<LabOrder>(
        `/organizations/${encodeURIComponent(providerId)}/orders/by-idempotency-key/${encodeURIComponent(idempotencyKey)}`,
        { method: 'GET', signal: options.signal },
        options.token,
      )
    } catch (error) {
      if (error instanceof ExchangeApiError && error.status === 404) {
        return null
      }
      throw error
    }
  }

  /**
   * POST /exchange/v1/organizations/{provider_id}/order-intents
   * Backend-owned durable order intent creation (DH-CLINIC-003C).
   */
  async createOrderIntentWithOutcome(
    providerId: string,
    idempotencyKey: string,
    body: LabOrderIntentCreateRequest,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabOrderIntentCreateOutcome> {
    const { data, status } = await this.requestWithResponse<LabOrderIntent>(
      `/organizations/${encodeURIComponent(providerId)}/order-intents`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify(body),
        signal: options.signal,
      },
      options.token,
    )
    return { intent: data, created: status === 201 }
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/order-intents/by-idempotency-key/{idempotency_key}
   * Retrieves an order intent by its idempotency key (DH-CLINIC-003C).
   * Returns null on 404, throws on other errors.
   */
  async getOrderIntentByIdempotencyKey(
    providerId: string,
    idempotencyKey: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabOrderIntent | null> {
    try {
      return await this.request<LabOrderIntent>(
        `/organizations/${encodeURIComponent(providerId)}/order-intents/by-idempotency-key/${encodeURIComponent(idempotencyKey)}`,
        { method: 'GET', signal: options.signal },
        options.token,
      )
    } catch (error) {
      if (error instanceof ExchangeApiError && error.status === 404) {
        return null
      }
      throw error
    }
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/order-intents/{intent_id}
   * Retrieves an order intent by intent_id (DH-CLINIC-003C).
   * Returns null on 404, throws on other errors.
   */
  async getOrderIntent(
    providerId: string,
    intentId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabOrderIntent | null> {
    try {
      return await this.request<LabOrderIntent>(
        `/organizations/${encodeURIComponent(providerId)}/order-intents/${encodeURIComponent(intentId)}`,
        { method: 'GET', signal: options.signal },
        options.token,
      )
    } catch (error) {
      if (error instanceof ExchangeApiError && error.status === 404) {
        return null
      }
      throw error
    }
  }

  /**
   * POST /exchange/v1/organizations/{provider_id}/order-intents/{intent_id}/commit
   * Commits an immutable backend order intent into a LabOrder (DH-CLINIC-003C).
   */
  async commitOrderIntentWithOutcome(
    providerId: string,
    intentId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabOrderCreateOutcome> {
    const { data, status } = await this.requestWithResponse<LabOrder>(
      `/organizations/${encodeURIComponent(providerId)}/order-intents/${encodeURIComponent(intentId)}/commit`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        signal: options.signal,
      },
      options.token,
    )
    return { order: data, created: status === 201 }
  }

  /**
   * GET /exchange/v1/organizations/{lab_id}/lab-orders
   * DH-LAB-001 §14: the selected lab's own bounded, deterministically-ordered assigned-order
   * work queue. `status` filters by the existing `LabOrderStatus` workflow status; omit for the
   * unfiltered queue.
   */
  async listLabOrders(
    labId: string,
    options: {
      status?: LabOrderStatus
      limit?: number
      token?: string
      signal?: AbortSignal
    } = {},
  ): Promise<LabWorkflowOrder[]> {
    const params = new URLSearchParams()
    if (options.status !== undefined) {
      params.set('status', options.status)
    }
    if (options.limit !== undefined) {
      params.set('limit', String(options.limit))
    }
    const query = params.toString() ? `?${params.toString()}` : ''
    return this.request<LabWorkflowOrder[]>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders${query}`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{lab_id}/lab-orders/{order_id}
   * DH-LAB-001 §31: the lab's own narrow clinical-work detail view of one assigned order.
   */
  async getLabOrder(
    labId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabWorkflowOrder> {
    return this.request<LabWorkflowOrder>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/accept
   * DH-LAB-001 §32/§36: SELECTED -> ACCEPTED. Idempotent replay-safe on the server; §36's own
   * lost-response recovery (re-read via getLabOrder, never blind retry) is a UI-level pattern
   * built on top of this call, not something this method itself implements.
   */
  async acceptLabOrder(
    labId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabWorkflowOrder> {
    return this.request<LabWorkflowOrder>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/accept`,
      { method: 'POST', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/reject
   * DH-LAB-001 §33/§37: SELECTED -> REJECTED, terminal. No reason/free-text is accepted by this
   * route -- the backend contract carries no such field (§33's own explicit constraint).
   */
  async rejectLabOrder(
    labId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabWorkflowOrder> {
    return this.request<LabWorkflowOrder>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/reject`,
      { method: 'POST', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/record-collection
   * DH-LAB-002 §21/§25: records the explicit, order-level sample/specimen collection workflow
   * milestone. No request body -- the order and lab come from the URL, the actor from
   * authentication, the timestamp from the server clock. Idempotent replay-safe on the server; the
   * UI-level lost-response recovery (re-read via getLabOrder, never a blind retry) is built on top
   * of this call, not implemented here.
   */
  async recordLabOrderCollection(
    labId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabCollectionResponse> {
    return this.request<LabCollectionResponse>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/record-collection`,
      { method: 'POST', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/start-processing
   * DH-LAB-002 §21/§26: ACCEPTED -> IN_PROGRESS, gated server-side on an existing collection
   * milestone. Idempotent replay-safe on the server for an order already IN_PROGRESS.
   */
  async startProcessingLabOrder(
    labId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabWorkflowOrder> {
    return this.request<LabWorkflowOrder>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/start-processing`,
      { method: 'POST', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/result-entry-plan
   * DH-LAB-003 §34-§36: a safe, read-only answer to "what result structure would Native DHealth
   * Lab use for this order right now" -- never a mutation. The backend returns 409 unless the
   * order is currently IN_PROGRESS.
   */
  async getResultEntryPlan(
    labId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<ResultEntryPlan> {
    return this.request<ResultEntryPlan>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/result-entry-plan`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/result-entry-workspace
   * DH-LAB-004 §46-48: what result structure governs entry right now for each item (pinned or
   * currently active) plus what has already been entered. LAB_TECH only; the backend returns 409
   * unless the order is currently IN_PROGRESS.
   */
  async getResultEntryWorkspace(
    labId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<ResultEntryWorkspace> {
    return this.request<ResultEntryWorkspace>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/result-entry-workspace`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}/native-result-values
   * DH-LAB-004 §7-19: the Native controlled batch value write -- template identity, definition
   * identity, and value only. Never sends result_type/source_code/display_label/numeric_unit/
   * reference ranges/source_system; the server derives every one of those from the pinned
   * template's own controlled observation definition.
   */
  async recordNativeResultValues(
    labId: string,
    orderId: string,
    itemId: string,
    body: {
      template_id: string
      values: Array<{ definition_id: string; numeric_value?: string; text_value?: string }>
    },
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<NativeResultValueBatchResponse> {
    return this.request<NativeResultValueBatchResponse>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/items/${encodeURIComponent(itemId)}/native-result-values`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: options.signal,
      },
      options.token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}/complete-result-entry
   * DH-EX-012C: the single explicit signal that this item's result entry is finished -- never
   * inferred from observation count. No request body. Idempotent replay-safe on the server.
   */
  async completeResultEntry(
    labId: string,
    orderId: string,
    itemId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<EntryCompletionResponse> {
    return this.request<EntryCompletionResponse>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/items/${encodeURIComponent(itemId)}/complete-result-entry`,
      { method: 'POST', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{lab_id}/release-queue
   * DH-LAB-004 §49-50: this lab's own dedicated, least-privilege release queue --
   * LAB_RESULT_RELEASER only, never the LAB_TECH work queue broadened.
   */
  async getReleaseQueue(
    labId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<ReleaseQueue> {
    return this.request<ReleaseQueue>(
      `/organizations/${encodeURIComponent(labId)}/release-queue`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/results
   * DH-EX-012, extended by DH-LAB-004: the selected lab's own pre-release inspection read --
   * either LAB_TECH or LAB_RESULT_RELEASER may call this. Carries `result_fingerprint`, the exact
   * value a release request must echo back.
   */
  async getLabOrderResults(
    labId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabResultInspection> {
    return this.request<LabResultInspection>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/results`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/release-results
   * DH-EX-012/ADR-044, extended by DH-LAB-004 §57-62: the sole supported production way to move a
   * LabOrder from IN_PROGRESS to COMPLETED. `expected_result_fingerprint` is required -- the
   * exact value from a `getLabOrderResults` call the caller must have actually just made; a
   * mismatch (an intervening correction changed the effective set) is refused as a controlled
   * conflict, never releasing a result set the releaser did not actually see.
   */
  async releaseLabOrderResults(
    labId: string,
    orderId: string,
    body: { expected_result_fingerprint: string },
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabResultReleaseOutcome> {
    return this.request<LabResultReleaseOutcome>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/release-results`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: options.signal,
      },
      options.token,
    )
  }

  /**
   * POST .../lab-orders/{order_id}/items/{item_id}/observations/{observation_id}/native-correct
   * DH-LAB-005 §8-16: the Native controlled pre-release correction -- submits only the new value.
   * Every structural field (source_code/display_label/result_type/numeric_unit/reference ranges/
   * source_system/template definition identity) is derived server-side from the predecessor's own
   * pinned template definition, never sent by the browser.
   */
  async correctNativeObservation(
    labId: string,
    orderId: string,
    itemId: string,
    observationId: string,
    body: NativeCorrectionRequest,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabObservation> {
    return this.request<LabObservation>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/items/${encodeURIComponent(itemId)}/observations/${encodeURIComponent(observationId)}/native-correct`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: options.signal,
      },
      options.token,
    )
  }

  /**
   * GET .../lab-orders/{order_id}/items/{item_id}/observations/history
   * DH-EX-013: every observation that has ever existed under this item's container, superseded
   * and effective alike -- the one supported way to reconstruct a correction chain.
   */
  async getLabObservationHistory(
    labId: string,
    orderId: string,
    itemId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabObservationHistory> {
    return this.request<LabObservationHistory>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/items/${encodeURIComponent(itemId)}/observations/history`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST .../lab-orders/{order_id}/amendments
   * DH-EX-014B/ADR-048: starts a new amendment against a COMPLETED order's latest release
   * version. LAB_TECH only. No request body -- actor/order/lab all come from the URL/auth.
   */
  async startLabOrderAmendment(
    labId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<Amendment> {
    return this.request<Amendment>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/amendments`,
      { method: 'POST', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET .../lab-orders/{order_id}/amendments
   * DH-LAB-005 §27-28: lets the UI rediscover a PREPARING amendment (or see the full amendment
   * history) after a page refresh/browser close -- never dependent on browser memory.
   */
  async listLabOrderAmendments(
    labId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<AmendmentListResponse> {
    return this.request<AmendmentListResponse>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/amendments`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET .../lab-orders/{order_id}/amendments/{amendment_id}
   * DH-EX-014B: either LAB_TECH or LAB_RESULT_RELEASER may inspect -- "a releaser must not be
   * forced to release an amendment they cannot inspect." Carries `result_fingerprint`, the exact
   * value an amendment release request must echo back.
   */
  async getLabOrderAmendment(
    labId: string,
    orderId: string,
    amendmentId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<AmendmentInspection> {
    return this.request<AmendmentInspection>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/amendments/${encodeURIComponent(amendmentId)}`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST .../amendments/{amendment_id}/items/{item_id}/observations/{observation_id}/native-correct
   * DH-LAB-005 §30-35: the Native controlled counterpart of `correctNativeObservation` for an
   * amendment in progress -- browser submits only the new value.
   */
  async correctAmendmentNativeObservation(
    labId: string,
    orderId: string,
    amendmentId: string,
    itemId: string,
    observationId: string,
    body: NativeCorrectionRequest,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabObservation> {
    return this.request<LabObservation>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/amendments/${encodeURIComponent(amendmentId)}/items/${encodeURIComponent(itemId)}/observations/${encodeURIComponent(observationId)}/native-correct`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: options.signal,
      },
      options.token,
    )
  }

  /**
   * POST .../amendments/{amendment_id}/release
   * DH-LAB-005 §37-43: releases a PREPARING amendment, gated on `expected_result_fingerprint`
   * exactly like the original release -- a stale fingerprint (an intervening correction) is
   * refused as a controlled conflict, never releasing a result set the releaser did not actually
   * see. A replay of an already-RELEASED amendment is idempotent regardless of the fingerprint
   * given (mirrors the original release's own replay discipline).
   */
  async releaseLabOrderAmendment(
    labId: string,
    orderId: string,
    amendmentId: string,
    body: AmendmentReleaseRequest,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabReleaseVersionSummary> {
    return this.request<LabReleaseVersionSummary>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/amendments/${encodeURIComponent(amendmentId)}/release`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: options.signal,
      },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{lab_id}/amendment-release-queue
   * DH-LAB-005 §46-47: this lab's own dedicated, least-privilege amendment release queue --
   * LAB_RESULT_RELEASER only, never the LAB_TECH work queue broadened. Never lists an amendment
   * with zero revisions.
   */
  async getAmendmentReleaseQueue(
    labId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<AmendmentQueue> {
    return this.request<AmendmentQueue>(
      `/organizations/${encodeURIComponent(labId)}/amendment-release-queue`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET .../lab-orders/{order_id}/release-versions
   * DH-EX-014B/ADR-048: lab-side Version 1 / Version 2 / Version 3+ history metadata -- either
   * LAB_TECH or LAB_RESULT_RELEASER, exactly mirroring the pre-release inspection read's own
   * authorization.
   */
  async getLabOrderReleaseVersions(
    labId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabReleaseVersionHistory> {
    return this.request<LabReleaseVersionHistory>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/release-versions`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET .../lab-orders/{order_id}/release-versions/{release_version_id}
   * DH-LAB-005 §52-54: the exact, immutable content of one specific released version -- lab-side
   * sibling of `getReleaseVersion` (provider-facing). Reconstructed from the immutable manifest
   * only, never from live current-effective state; Version 1's content stays byte-for-byte
   * identical after Version 2/3 exist.
   */
  async getLabOrderReleaseVersionContent(
    labId: string,
    orderId: string,
    releaseVersionId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<LabReleaseVersionContent> {
    return this.request<LabReleaseVersionContent>(
      `/organizations/${encodeURIComponent(labId)}/lab-orders/${encodeURIComponent(orderId)}/release-versions/${encodeURIComponent(releaseVersionId)}`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/organizations/{provider_id}/orders/{order_id}/patient-access
   * DH-PATIENT-001 §8/§9: is a patient access link currently outstanding for this order?
   * Metadata only -- the raw bearer secret is never retrievable after issuance (§10), so a `true`
   * here means "a link exists somewhere in the patient's possession", never "the link can be
   * shown again".
   */
  async getPatientAccessMetadata(
    providerId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<PatientAccessMetadata> {
    return this.request<PatientAccessMetadata>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/patient-access`,
      { method: 'GET', signal: options.signal },
      options.token,
    )
  }

  /**
   * POST /exchange/v1/organizations/{provider_id}/orders/{order_id}/patient-access
   * DH-EX-005/DH-PATIENT-001 §12: issues a new order-scoped grant. The returned `access_token` is
   * the raw secret in its **only** appearance anywhere in this system -- hold it in component
   * memory, render it only as a QR/fragment URL, never store or log it.
   */
  async issuePatientAccess(
    providerId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<PatientAccessIssueOutcome> {
    return this.request<PatientAccessIssueOutcome>(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/patient-access`,
      { method: 'POST', signal: options.signal },
      options.token,
    )
  }

  /**
   * DELETE /exchange/v1/organizations/{provider_id}/orders/{order_id}/patient-access
   * DH-EX-005/DH-PATIENT-001 §19: revokes the outstanding grant. Idempotent server-side (revoking
   * an order with no active grant is a safe no-op, not an error).
   */
  async revokePatientAccess(
    providerId: string,
    orderId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<void> {
    await this.requestNoContent(
      `/organizations/${encodeURIComponent(providerId)}/orders/${encodeURIComponent(orderId)}/patient-access`,
      { method: 'DELETE', signal: options.signal },
      options.token,
    )
  }

  // ── DH-MVP-ONBOARD-001: the platform control plane ─────────────────────────────────────────

  /**
   * GET /exchange/v1/platform/facilities -- every enrolled facility on the network.
   * Platform-admin only server-side; this client method does not and cannot make that true.
   */
  async listFacilities(
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<FacilityEnrollment[]> {
    return this.request<FacilityEnrollment[]>(
      "/platform/facilities",
      { method: "GET", signal: options.signal },
      options.token,
    )
  }

  /** POST /exchange/v1/platform/facilities -- organization and enrollment, one transaction. */
  async enrolFacility(
    body: { name: string; organization_type: "PROVIDER" | "LAB"; participation_mode: ParticipationMode },
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<FacilityEnrollment> {
    return this.request<FacilityEnrollment>(
      "/platform/facilities",
      { method: "POST", body: JSON.stringify(body), signal: options.signal },
      options.token,
    )
  }

  async getFacilityEnrollment(
    organizationId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<FacilityEnrollment> {
    return this.request<FacilityEnrollment>(
      `/organizations/${encodeURIComponent(organizationId)}/enrollment`,
      { method: "GET", signal: options.signal },
      options.token,
    )
  }

  /**
   * GET .../enrollment/readiness -- server-derived, never recomputed here.
   * The UI formats these findings; it does not decide them.
   */
  async getFacilityReadiness(
    organizationId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<FacilityReadiness> {
    return this.request<FacilityReadiness>(
      `/organizations/${encodeURIComponent(organizationId)}/enrollment/readiness`,
      { method: "GET", signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/platform/facilities/{providerId}/lab-routes
   *
   * Every route configured for this provider, active or not. Deliberately not
   * `listProviderLabRoutes`, which requires CLINICIAN at the provider and returns
   * only active routes: a platform operator holds no membership and is looking at
   * the relationships themselves, including the ones switched off.
   */
  async listPlatformProviderLabRoutes(
    providerId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<ProviderLabRoute[]> {
    return this.request<ProviderLabRoute[]>(
      `/platform/facilities/${encodeURIComponent(providerId)}/lab-routes`,
      { method: "GET", signal: options.signal },
      options.token,
    )
  }

  /**
   * PUT /exchange/v1/organizations/{providerId}/lab-routes/{labId}
   *
   * Activation is readiness-gated on the server and may be refused with 409 even
   * when this client was told moments ago that it would be allowed. That refusal
   * is authoritative: readiness is evaluated from live rows inside the writing
   * transaction, and the caller's job is to show the reasons, never to retry past
   * them. Deactivation is never gated.
   */
  async setProviderLabRoute(
    providerId: string,
    labId: string,
    active: boolean,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<ProviderLabRoute> {
    return this.request<ProviderLabRoute>(
      `/organizations/${encodeURIComponent(providerId)}/lab-routes/${encodeURIComponent(labId)}`,
      { method: "PUT", body: JSON.stringify({ active }), signal: options.signal },
      options.token,
    )
  }

  async setParticipationMode(
    organizationId: string,
    participationMode: ParticipationMode,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<FacilityEnrollment> {
    return this.request<FacilityEnrollment>(
      `/organizations/${encodeURIComponent(organizationId)}/enrollment/participation-mode`,
      {
        method: "PUT",
        body: JSON.stringify({ participation_mode: participationMode }),
        signal: options.signal,
      },
      options.token,
    )
  }

  async setOnboardingStatus(
    organizationId: string,
    onboardingStatus: OnboardingStatus,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<FacilityEnrollment> {
    return this.request<FacilityEnrollment>(
      `/organizations/${encodeURIComponent(organizationId)}/enrollment/status`,
      {
        method: "PUT",
        body: JSON.stringify({ onboarding_status: onboardingStatus }),
        signal: options.signal,
      },
      options.token,
    )
  }

  /** PUT .../integration-profile -- CONNECT technical discovery. Never carries a credential. */
  async updateIntegrationProfile(
    organizationId: string,
    body: Record<string, string | boolean | null>,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<FacilityEnrollment> {
    return this.request<FacilityEnrollment>(
      `/organizations/${encodeURIComponent(organizationId)}/integration-profile`,
      { method: "PUT", body: JSON.stringify(body), signal: options.signal },
      options.token,
    )
  }

  /**
   * PUT .../integration-profile/interfaces/{type} -- what DHealth knows about one interface.
   * Recording VERIFIED says the interface exists as documented. It is not a connector, and the
   * server changes no onboarding status and unlocks no routing in response to it.
   */
  async setInterfaceCapability(
    organizationId: string,
    interfaceType: InterfaceType,
    body: { verification_state: InterfaceVerificationState; detail?: string | null },
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<FacilityEnrollment> {
    return this.request<FacilityEnrollment>(
      `/organizations/${encodeURIComponent(organizationId)}/integration-profile/interfaces/${encodeURIComponent(interfaceType)}`,
      { method: "PUT", body: JSON.stringify(body), signal: options.signal },
      options.token,
    )
  }

  async addEnrollmentBlocker(
    organizationId: string,
    body: { code: EnrollmentBlockerCode; note?: string | null },
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<FacilityEnrollment> {
    return this.request<FacilityEnrollment>(
      `/organizations/${encodeURIComponent(organizationId)}/enrollment/blockers`,
      { method: "POST", body: JSON.stringify(body), signal: options.signal },
      options.token,
    )
  }

  async resolveEnrollmentBlocker(
    organizationId: string,
    blockerId: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<FacilityEnrollment> {
    return this.request<FacilityEnrollment>(
      `/organizations/${encodeURIComponent(organizationId)}/enrollment/blockers/${encodeURIComponent(blockerId)}`,
      { method: "DELETE", signal: options.signal },
      options.token,
    )
  }

  /**
   * GET /exchange/v1/platform/identities -- find somebody who has already signed in.
   * A match grants nothing; creating the membership is a separate, explicit call.
   */
  async lookupIdentities(
    query: string,
    options: { token?: string; signal?: AbortSignal } = {},
  ): Promise<IdentityLookupResult[]> {
    const params = new URLSearchParams({ q: query })
    return this.request<IdentityLookupResult[]>(
      `/platform/identities?${params.toString()}`,
      { method: "GET", signal: options.signal },
      options.token,
    )
  }
}


export const exchangeClient = new ExchangeClient()
