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
  AssignLabRequest,
  BindPatientReferenceOutcome,
  CatalogEntry,
  ClinicPatientSummary,
  DoctorReviewResponse,
  ExchangeErrorCode,
  ExchangeMeResponse,
  LabAssignmentResponse,
  LabOrder,
  LabOrderCreateOutcome,
  LabOrderCreateRequest,
  LabOrderIntent,
  LabOrderIntentCreateOutcome,
  LabOrderIntentCreateRequest,
  LabOrderSummary,
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

  constructor(status: number, code: ExchangeErrorCode, detail: string) {
    super(detail)
    this.name = 'ExchangeApiError'
    this.status = status
    this.code = code
    this.detail = detail
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
    try {
      const body = await response.json()
      errorDetail = parseFastApiDetail(body, errorDetail)
    } catch {
      // Body not json, retain statusText
    }

    let code: ExchangeErrorCode = 'SERVER_ERROR'
    if (response.status === 400 || response.status === 422) {
      code = 'VALIDATION_ERROR'
    } else if (response.status === 401) {
      code = 'UNAUTHORIZED'
    } else if (response.status === 403) {
      code = 'FORBIDDEN'
    } else if (response.status === 404) {
      code = 'NOT_FOUND'
    } else if (response.status === 409) {
      code = 'CONFLICT'
    } else if (response.status >= 500) {
      code = 'SERVER_ERROR'
    }

    throw new ExchangeApiError(response.status, code, errorDetail)
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
}


export const exchangeClient = new ExchangeClient()
