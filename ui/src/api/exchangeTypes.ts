/**
 * DHealth Exchange API typed contracts (DH-CLINIC-001).
 *
 * Strictly models transaction-scoped Exchange contracts (ADR-042, ADR-043,
 * docs/MVP-1.0.md). Does NOT import or mix with OpenMRS API types.
 */

export interface ExchangeIdentity {
  identity_id: string
  issuer: string
  subject: string
  display_name: string | null
  email: string | null
  platform_admin: boolean
}

export interface ExchangeMembershipSummary {
  organization_id: string
  organization_name: string
  organization_type: 'PROVIDER' | 'LAB' | string
  organization_status: 'ACTIVE' | 'SUSPENDED' | string
  membership_status: 'ACTIVE' | 'INVITED' | 'REVOKED' | string
  roles: string[]
}

export interface ExchangeMeResponse {
  identity_id: string
  issuer: string
  subject: string
  display_name: string | null
  email: string | null
  platform_admin: boolean
  memberships: ExchangeMembershipSummary[]
}

export type LabOrderStatus =
  | 'AWAITING_LAB_SELECTION'
  | 'SELECTED'
  | 'ACCEPTED'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'REJECTED'
  | 'CANCELLED'

export type DoctorReviewStatus =
  | 'PENDING'
  | 'NO_FOLLOW_UP_REQUIRED'
  | 'FOLLOW_UP_REQUIRED'

export interface LabOrderItem {
  catalog_entry_id: string
  catalog_code_snapshot: string
  catalog_display_name_snapshot: string
}

/**
 * DH-CLINIC-005 §5/§38/§39: the minimal, reusable human-identification projection the backend
 * embeds in every order/result read response -- name/DOB/contact only, resolved server-side
 * through `patient_reference_id -> Patient`. Distinct from `PatientDirectoryEntry` (directory-
 * specific fields this projection omits on purpose) and never fabricated client-side: a `null`
 * field here means the value is genuinely unknown, never inferred or defaulted.
 */
export interface PatientIdentitySummary {
  patient_id: string
  display_name: string | null
  date_of_birth: string | null
  contact_channel_type: string | null
  contact_value: string | null
}

/**
 * DH-CLINIC-006 §22-24: the destination-lab projection embedded on an order read. `null` means
 * the order has not yet been routed to a lab -- a real, valid state, never fabricated. Carries no
 * price field of any kind, and no claim about *who* made the assignment (a historical patient
 * selection and a provider `assign-lab` call are represented identically here).
 */
export interface LabAssignmentSummary {
  lab_organization_id: string
  lab_organization_name: string
  selected_at: string
}

export interface LabOrderSummary {
  id: string
  provider_organization_id: string
  patient_reference_id: string
  practitioner_reference_id: string
  patient: PatientIdentitySummary
  status: LabOrderStatus
  created_at: string
  item_count: int_number
  lab_assignment: LabAssignmentSummary | null
}

// Helper alias for integer representation in types
type int_number = number

export interface LabOrder {
  id: string
  provider_organization_id: string
  patient_reference_id: string
  practitioner_reference_id: string
  patient: PatientIdentitySummary
  status: LabOrderStatus
  created_at: string
  items: LabOrderItem[]
  lab_assignment: LabAssignmentSummary | null
}

export type LabOrderResponse = LabOrder
export type LabOrderItemResponse = LabOrderItem

export interface CatalogEntry {
  id: string
  code: string
  display_name: string
  version: number
  active: boolean
  is_synthetic: boolean
}

export type CatalogEntryResponse = CatalogEntry

export interface LabOrderCreateRequest {
  patient_reference_id: string
  catalog_entry_ids: string[]
}

export interface LabOrderCreateOutcome {
  order: LabOrder
  created: boolean
}

export interface LabOrderIntentItem {
  id: string
  catalog_entry_id: string
  catalog_code_snapshot: string
  catalog_display_name_snapshot: string
}

export interface LabOrderIntent {
  id: string
  idempotency_key: string
  provider_organization_id: string
  patient_reference_id: string
  practitioner_reference_id: string
  committed_lab_order_id: string | null
  created_at: string
  items: LabOrderIntentItem[]
}

export type LabOrderIntentResponse = LabOrderIntent
export type LabOrderIntentItemResponse = LabOrderIntentItem

export interface LabOrderIntentCreateRequest {
  patient_reference_id: string
  catalog_entry_ids: string[]
}

export interface LabOrderIntentCreateOutcome {
  intent: LabOrderIntent
  created: boolean
}

// -- DH-CLINIC-006: provider-assigned laboratory routing --------------------------------------

/**
 * DH-CLINIC-006 §13/§32: one currently-eligible destination lab for a specific order -- the
 * provider is explicitly routed to it (an active `ProviderLabRoute`), and it can perform every
 * ordered test. Deliberately carries no price, ranking, "best"/cheapest, star-rating, distance,
 * or sponsored-placement field of any kind: this is operational routing, not a marketplace.
 */
export interface ProviderEligibleLab {
  lab_organization_id: string
  lab_organization_name: string
  public_address: string
  public_phone: string | null
}

export interface AssignLabRequest {
  lab_organization_id: string
}

/**
 * DH-CLINIC-006 §30: deliberately does NOT carry `lab_organization_name` -- the backend response
 * this mirrors has only these three fields. After a successful `assignLab` call, the UI must
 * re-read `getOrder()` for the authoritative, named `LabAssignmentSummary` rather than rendering a
 * name from this response (which has none).
 */
export interface LabAssignmentResponse {
  lab_order_id: string
  lab_organization_id: string
  selected_at: string
}

/**
 * DH-CLINIC-006 §12: one provider's own routing-directory entry. Never exposed cross-provider,
 * never includes price.
 */
export interface ProviderLabRoute {
  id: string
  provider_organization_id: string
  lab_organization_id: string
  lab_organization_name: string
  active: boolean
  created_at: string
  updated_at: string
}

export interface ProviderObservationResponse {
  observation_id: string
  source_code: string
  source_system: string
  display_label_snapshot: string
  result_type: string
  // FastAPI/Pydantic serializes Decimal response values as JSON strings.
  numeric_value: string | null
  numeric_unit: string | null
  reference_range_low: string | null
  reference_range_high: string | null
  text_value: string | null
  recorded_at: string
}

export interface ProviderResultItemResponse {
  item_id: string
  catalog_code_snapshot: string
  catalog_display_name_snapshot: string
  entry_completed_at: string
  observations: ProviderObservationResponse[]
}

export interface ProviderOrderResultsResponse {
  order_id: string
  order_status: string
  patient: PatientIdentitySummary
  release_version_id: string | null
  release_version_number: number | null
  is_amendment: boolean | null
  results: ProviderResultItemResponse[]
}

export interface ProviderReleaseVersionSummaryResponse {
  release_version_id: string
  release_version_number: number
  is_amendment: boolean
  released_at: string
  review_status: DoctorReviewStatus | string
  reviewed_at: string | null
  reviewed_by_practitioner_reference_id: string | null
}

export interface ProviderReleaseVersionHistoryResponse {
  order_id: string
  versions: ProviderReleaseVersionSummaryResponse[]
}

export interface ProviderReleaseVersionContentResponse {
  order_id: string
  release_version_id: string
  release_version_number: number
  is_amendment: boolean
  patient: PatientIdentitySummary
  review_status: DoctorReviewStatus | string
  reviewed_at: string | null
  reviewed_by_practitioner_reference_id: string | null
  results: ProviderResultItemResponse[]
}

// Compatibility aliases
export type ProviderOrderResults = ProviderOrderResultsResponse
export type ProviderResultItem = ProviderResultItemResponse
export type ProviderResultObservation = ProviderObservationResponse
export type ProviderReleaseVersionSummary = ProviderReleaseVersionSummaryResponse
export type ProviderReleaseVersionHistory = ProviderReleaseVersionHistoryResponse
export type ProviderReleaseVersionContent = ProviderReleaseVersionContentResponse

export interface PatientReference {
  id: string
  source_organization_id: string
  source_system: string
  source_identity_system: string
  source_identity_value: string
}

export interface PatientReferenceBindRequest {
  source_system: string
  source_identity_system: string
  source_identity_value: string
  display_name?: string | null
  contact_channel_type?: string | null
  contact_value?: string | null
}

export interface ClinicPatientSummary {
  patient_reference_id: string
  display_name: string | null
  contact_channel_type: string | null
  contact_value: string | null
  local_reference: string
}

export interface BindPatientReferenceOutcome {
  reference: PatientReference
  created: boolean
}

/**
 * DH-IDENTITY-001: the native, provider-scoped Patient directory record -- distinct from
 * ClinicPatientSummary/PatientReference above, which stay exactly what they were (the legacy
 * exact source-identity binding view). `primary_patient_reference_id` is a compatibility bridge
 * only: existing LabOrder APIs still take a patient_reference_id, not a patient_id.
 */
export interface PatientDirectoryEntry {
  patient_id: string
  provider_organization_id: string
  display_name: string | null
  date_of_birth: string | null
  contact_channel_type: string | null
  contact_value: string | null
  primary_patient_reference_id: string
  created_at: string
}

export interface PatientCreateRequest {
  display_name: string
  date_of_birth?: string | null
  contact_channel_type?: string | null
  contact_value?: string | null
}

export interface PatientCreateOutcome {
  patient: PatientDirectoryEntry
  created: boolean
}

export type ExchangeErrorCode =
  | 'UNAUTHORIZED'
  | 'FORBIDDEN'
  | 'NOT_FOUND'
  | 'CONFLICT'
  | 'VALIDATION_ERROR'
  | 'SERVER_ERROR'
  | 'NETWORK_ERROR'
