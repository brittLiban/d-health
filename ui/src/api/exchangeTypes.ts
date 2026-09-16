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

export interface LabOrderSummary {
  id: string
  provider_organization_id: string
  patient_reference_id: string
  practitioner_reference_id: string
  status: LabOrderStatus
  created_at: string
  item_count: int_number
}

// Helper alias for integer representation in types
type int_number = number

export interface LabOrder {
  id: string
  provider_organization_id: string
  patient_reference_id: string
  practitioner_reference_id: string
  status: LabOrderStatus
  created_at: string
  items: LabOrderItem[]
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

export type ExchangeErrorCode =
  | 'UNAUTHORIZED'
  | 'FORBIDDEN'
  | 'NOT_FOUND'
  | 'CONFLICT'
  | 'VALIDATION_ERROR'
  | 'SERVER_ERROR'
  | 'NETWORK_ERROR'
