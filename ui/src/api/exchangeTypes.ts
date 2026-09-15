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
  | 'PENDING_SELECTION'
  | 'LAB_ASSIGNED'
  | 'SUBMITTED'
  | 'ACCEPTED'
  | 'REJECTED'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'CANCELLED'

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

export interface ProviderResultObservation {
  id: string
  observation_code: string
  display_name: string
  value_numeric: number | null
  value_text: string | null
  value_boolean: boolean | null
  value_coded: string | null
  unit: string | null
  reference_range_low: number | null
  reference_range_high: number | null
  abnormal_flag: string | null
  status: string
}

export interface ProviderResultItem {
  order_item_id: string
  catalog_code: string
  catalog_display_name: string
  observations: ProviderResultObservation[]
}

export interface ProviderOrderResults {
  order_id: string
  release_version_id: string
  release_version_number: number
  is_amendment: boolean
  review_status: 'PENDING_REVIEW' | 'NO_FOLLOW_UP_REQUIRED' | 'FOLLOW_UP_REQUIRED' | string
  reviewed_at: string | null
  reviewed_by_practitioner_reference_id: string | null
  results: ProviderResultItem[]
}

export interface ProviderReleaseVersionSummary {
  release_version_id: string
  release_version_number: number
  is_amendment: boolean
  released_at: string
  review_status: string
  reviewed_at: string | null
  reviewed_by_practitioner_reference_id: string | null
}

export interface ProviderReleaseVersionHistory {
  order_id: string
  versions: ProviderReleaseVersionSummary[]
}

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

export type ExchangeErrorCode =
  | 'UNAUTHORIZED'
  | 'FORBIDDEN'
  | 'NOT_FOUND'
  | 'CONFLICT'
  | 'VALIDATION_ERROR'
  | 'SERVER_ERROR'
  | 'NETWORK_ERROR'
