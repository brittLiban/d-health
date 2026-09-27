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
  /** DHEALTH-CLINIC-VISIT-001: the clinic visit this order was raised during, or `null` for a
   * standalone order and for every order that predates visits. */
  visit_id: string | null
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
  /** DHEALTH-CLINIC-VISIT-001: the clinic visit this order was raised during, or `null`. Set only
   * by the backend, copied from the committing intent — never sent by this client. */
  visit_id: string | null
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
  /** DHEALTH-CLINIC-VISIT-001: the visit this intent was raised inside, if any. Frozen on the
   * intent at creation and copied to the order by commit, so a retry can never move the order to a
   * different visit. */
  visit_id: string | null
}

export type LabOrderIntentResponse = LabOrderIntent
export type LabOrderIntentItemResponse = LabOrderIntentItem

export interface LabOrderIntentCreateRequest {
  patient_reference_id: string
  catalog_entry_ids: string[]
  /**
   * DHEALTH-CLINIC-VISIT-001: the clinic visit this order is being raised during.
   *
   * Explicit and optional. Omitting it means "a standalone order with no clinic contact", which is
   * permanently supported. It is part of the backend's idempotency comparison, so a retry of the
   * same key must present the *same* visit — which is why the stored pending-order record carries
   * it across a page reload.
   */
  visit_id?: string | null
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

// -- DH-CLINIC-007: released-result review + follow-up UX -------------------------------------

/**
 * The response to a review disposition mutation (version-specific or legacy order-only) --
 * deliberately no result/observation value, matching `LabAssignmentResponse`'s own convention:
 * confirms only that the workflow decision was recorded, never renders it as clinical content.
 */
export interface DoctorReviewResponse {
  order_id: string
  release_version_id: string | null
  status: DoctorReviewStatus | string
  reviewed_at: string | null
  reviewed_by_practitioner_reference_id: string | null
}

/**
 * DH-CLINIC-007 §10/§12/§27: one review *obligation* -- never one row per order. An order with
 * two independently-pending amendment releases surfaces as two separate entries. Deliberately
 * carries no result/observation value of any kind -- metadata only, enough to decide what to
 * open next. `legacy_unversioned=true` marks the one grandfathered shape explicitly, rather than
 * requiring the UI to infer that meaning from three simultaneous `null`s.
 */
export interface ProviderResultReviewQueueEntry {
  review_id: string
  order_id: string
  release_version_id: string | null
  release_version_number: number | null
  is_amendment: boolean
  released_at: string | null
  legacy_unversioned: boolean
  review_status: DoctorReviewStatus | string
  reviewed_at: string | null
  patient: PatientIdentitySummary
  order_created_at: string
}

export interface ProviderResultReviewQueueResponse {
  provider_organization_id: string
  entries: ProviderResultReviewQueueEntry[]
}

export type ReviewQueueStatusFilter =
  | 'PENDING'
  | 'NO_FOLLOW_UP_REQUIRED'
  | 'FOLLOW_UP_REQUIRED'
  | 'ALL'

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

/**
 * DH-LAB-001 §16/§17: the minimal, lab-safe human-identity projection -- name and exact date of
 * birth only, deliberately never `PatientIdentitySummary`'s own `contact_channel_type`/
 * `contact_value` fields. Native Lab staff need enough identity to safely distinguish one patient
 * from another, not the provider's full directory entry.
 */
export interface LabWorkflowPatientIdentity {
  patient_id: string
  display_name: string | null
  date_of_birth: string | null
}

export interface LabWorkflowOrderItem {
  catalog_code_snapshot: string
  catalog_display_name_snapshot: string
  selected_price_amount: string | null
  selected_price_currency: string | null
}

/**
 * DH-LAB-001 §22: the selected lab's own narrow clinical-work view of an assigned order.
 * `patient_display_name` is the legacy `PatientReference` snapshot, kept only for API
 * compatibility -- `patient` (§16/§17) is the authoritative field Native Lab actually renders
 * identity from. `collection_recorded_at: null` means "not recorded in DHealth," never a claim
 * that collection did not happen (a legacy order may predate DH-EX-011).
 */
export interface LabWorkflowOrder {
  order_id: string
  status: LabOrderStatus
  selected_at: string
  updated_at: string
  patient_display_name: string | null
  patient: LabWorkflowPatientIdentity
  ordering_provider_name: string
  requested_tests: LabWorkflowOrderItem[]
  collection_recorded_at: string | null
}

/**
 * DH-LAB-002 §22: the response to recording (or idempotently replaying) the order-level
 * sample/specimen collection workflow milestone -- deliberately narrow, mirroring the backend's
 * own `LabCollectionResponse` exactly (`adapter/src/daher_adapter/exchange/api.py`). No specimen
 * type/tube/barcode/free-text field exists here because none is modeled.
 */
export interface LabCollectionResponse {
  order_id: string
  recorded_at: string
}

/**
 * DH-LAB-003 §36: one controlled observation definition within the currently active
 * `LabResultTemplate` for an ordered item's offering -- pure structure, never a Patient value.
 * Mirrors the backend's own `ResultEntryPlanObservationResponse` exactly.
 */
export interface ResultEntryPlanObservation {
  definition_id: string
  source_code: string
  display_label: string
  result_type: 'NUMERIC' | 'TEXT'
  numeric_unit: string | null
  required: boolean
  sort_order: number
}

export interface ResultEntryPlanTemplate {
  template_id: string
  version: number
  is_synthetic: boolean
  observations: ResultEntryPlanObservation[]
}

/**
 * DH-LAB-003 §37: `template: null` is a valid, honestly represented read state -- no active
 * `LabResultTemplate` exists yet for this item's offering. Never fabricated, never a fallback to
 * free-form structure.
 */
export interface ResultEntryPlanItem {
  item_id: string
  catalog_entry_id: string
  catalog_code_snapshot: string
  catalog_display_name_snapshot: string
  template: ResultEntryPlanTemplate | null
}

/**
 * DH-LAB-003 §34-§36: the order-scoped, read-only result-entry-plan -- never a mutation, never a
 * Patient-result write. Exposed only while the order is `IN_PROGRESS` (the backend returns 409
 * otherwise).
 */
export interface ResultEntryPlan {
  order_id: string
  order_status: LabOrderStatus
  all_items_configured: boolean
  items: ResultEntryPlanItem[]
}

/**
 * DH-LAB-004: the outcome of one submitted value within a Native batch write -- `created: false`
 * means this exact value already existed and was returned as a safe idempotent replay, never a
 * fabricated "created" outcome for a no-op.
 */
export interface NativeResultValue {
  definition_id: string
  observation_id: string
  numeric_value: string | null
  text_value: string | null
  recorded_at: string
  created: boolean
}

export interface NativeResultValueBatchResponse {
  order_id: string
  item_id: string
  result_id: string
  template_id: string
  values: NativeResultValue[]
}

export interface ResultEntryWorkspaceEnteredValue {
  definition_id: string | null
  observation_id: string
  numeric_value: string | null
  text_value: string | null
  recorded_at: string
}

/**
 * DH-LAB-004 §46-48: one ordered item's Native result-entry state. `template: null` with
 * `entry_mode: 'UNSTARTED_NO_TEMPLATE'` means Native entry is blocked -- no free-form fallback.
 * `entry_mode: 'LEGACY_OR_EXTERNAL'` means a container already exists but was never
 * Native-template-bound -- represented truthfully, never silently adopted. `template_pinned:
 * true` means `template` is this container's own exact pinned version, which may no longer be
 * the offering's currently active template.
 */
export interface ResultEntryWorkspaceItem {
  item_id: string
  catalog_code_snapshot: string
  catalog_display_name_snapshot: string
  template: ResultEntryPlanTemplate | null
  template_pinned: boolean
  entry_mode: 'NATIVE_TEMPLATE' | 'LEGACY_OR_EXTERNAL' | 'UNSTARTED_NO_TEMPLATE'
  entry_completed: boolean
  entry_completed_at: string | null
  entered_values: ResultEntryWorkspaceEnteredValue[]
}

export interface ResultEntryWorkspace {
  order_id: string
  order_status: LabOrderStatus
  items: ResultEntryWorkspaceItem[]
}

/**
 * DH-EX-012C: the response to marking (or idempotently replaying) an ordered item's result entry
 * complete -- no result values, no clinical content.
 */
export interface EntryCompletionResponse {
  order_id: string
  item_id: string
  entry_completed_at: string
  entry_completed_by_identity_id: string | null
}

/**
 * DH-LAB-004 §49-50: one order ready for this lab's own `LAB_RESULT_RELEASER` to inspect and
 * release -- assigned to this lab, `IN_PROGRESS`, every item's entry complete, not already
 * released. Deliberately least-privilege and never UUID-first.
 */
export interface ReleaseQueueEntry {
  order_id: string
  patient: LabWorkflowPatientIdentity
  ordering_provider_name: string
  requested_tests: LabWorkflowOrderItem[]
  updated_at: string
}

export interface ReleaseQueue {
  lab_organization_id: string
  orders: ReleaseQueueEntry[]
}

export interface LabResultInspectionObservation {
  observation_id: string
  source_code: string
  source_system: string
  display_label_snapshot: string
  result_type: 'NUMERIC' | 'TEXT'
  numeric_value: string | null
  numeric_unit: string | null
  reference_range_low: string | null
  reference_range_high: string | null
  text_value: string | null
  recorded_at: string
}

export interface LabResultInspectionItem {
  item_id: string
  catalog_code_snapshot: string
  catalog_display_name_snapshot: string
  entry_completed: boolean
  entry_completed_at: string | null
  observations: LabResultInspectionObservation[]
  has_correction_history: boolean
}

/**
 * DH-LAB-004 §51-56: the selected lab's own pre-release read -- "a releaser must not be forced
 * to release results they cannot inspect." `result_fingerprint` is the exact value a release
 * request must echo back as `expected_result_fingerprint`; it changes the instant any effective
 * observation in this order is added or corrected.
 */
export interface LabResultInspection {
  order_id: string
  order_status: LabOrderStatus
  all_items_entry_complete: boolean
  released: boolean
  released_at: string | null
  results: LabResultInspectionItem[]
  patient: LabWorkflowPatientIdentity
  ordering_provider_name: string
  result_fingerprint: string
}

export interface LabResultReleaseOutcome {
  order_id: string
  released_by_identity_id: string
  released_at: string
}

// -- DH-LAB-005: template-aware pre-release correction, and the amendment lifecycle -----------

/**
 * DH-LAB-005 §8-16: one observation as returned by either the pre-release or amendment Native
 * controlled correction endpoint. `template_observation_definition_id` non-null is the frontend's
 * own signal that the Native controlled correction control applies to this observation (never the
 * raw free-structure one, which now refuses a template-bound predecessor outright).
 * `supersedes_observation_id` set + `superseded_at: null` marks a correction's own successor.
 */
export interface LabObservation {
  order_id: string
  item_id: string
  result_id: string
  observation_id: string
  source_code: string
  source_system: string
  display_label_snapshot: string
  result_type: 'NUMERIC' | 'TEXT'
  numeric_value: string | null
  numeric_unit: string | null
  reference_range_low: string | null
  reference_range_high: string | null
  text_value: string | null
  recorded_at: string
  recorded_by_identity_id: string | null
  supersedes_observation_id: string | null
  superseded_at: string | null
  template_observation_definition_id: string | null
}

export interface NativeCorrectionRequest {
  numeric_value?: string
  text_value?: string
}

/**
 * DH-EX-013: every observation that has ever existed under this item's container, superseded and
 * effective alike, ordered oldest to newest -- `superseded_at !== null` must always be rendered
 * as historical/replaced, never as equally current alongside the one effective (`superseded_at
 * === null`) entry.
 */
export interface LabObservationHistory {
  order_id: string
  item_id: string
  observations: LabObservation[]
}

export type AmendmentStatus = 'PREPARING' | 'RELEASED'

/**
 * DH-EX-014B/ADR-048: one amendment episode. `PREPARING`/`RELEASED` are the only two statuses
 * that exist in this version of DHealth -- there is no CANCELLED/DISCARDED state, and no UI may
 * imply one exists.
 */
export interface Amendment {
  amendment_id: string
  lab_order_id: string
  amendment_number: number
  base_release_version_id: string
  status: AmendmentStatus
  started_by_identity_id: string
  started_at: string
  released_by_identity_id: string | null
  released_at: string | null
}

export interface AmendmentListResponse {
  order_id: string
  amendments: Amendment[]
}

export interface AmendmentInspectionObservation extends LabResultInspectionObservation {
  amendment_id: string | null
  template_observation_definition_id: string | null
}

/**
 * DH-EX-014B: `changed_in_this_amendment` is a DB-grounded fact (at least one of this item's
 * current observations carries this exact amendment's id) -- the frontend's own signal for
 * "Modifié dans cet amendement", never re-derived client-side.
 */
export interface AmendmentInspectionItem {
  item_id: string
  catalog_code_snapshot: string
  catalog_display_name_snapshot: string
  entry_completed_at: string
  observations: AmendmentInspectionObservation[]
  changed_in_this_amendment: boolean
}

/**
 * DH-LAB-005 §37-40: `result_fingerprint` is the exact digest of this amendment's proposed
 * (PREPARING) or released (historical) effective observation set -- the value a release request
 * must echo back as `expected_result_fingerprint`. `ready_to_release` is a DB-grounded fact (at
 * least one revision exists) -- no-empty-amendment-release is enforced server-side, and the
 * release queue never lists an amendment with none.
 */
export interface AmendmentInspection {
  amendment: Amendment
  items: AmendmentInspectionItem[]
  ready_to_release: boolean
  result_fingerprint: string
}

export interface AmendmentReleaseRequest {
  expected_result_fingerprint: string
}

/**
 * DH-LAB-005 §46-47: one PREPARING amendment ready for this lab's own LAB_RESULT_RELEASER to
 * inspect and release -- mirrors `ReleaseQueueEntry`'s own least-privilege, never-the-work-queue-
 * broadened convention. An amendment with zero revisions never appears here.
 */
export interface AmendmentQueueEntry {
  order_id: string
  amendment_id: string
  amendment_number: number
  patient: LabWorkflowPatientIdentity
  ordering_provider_name: string
  requested_tests: LabWorkflowOrderItem[]
  started_at: string
}

export interface AmendmentQueue {
  lab_organization_id: string
  amendments: AmendmentQueueEntry[]
}

/**
 * DH-EX-014B/ADR-048: lab-side Version 1 / Version 2 / Version 3+ history metadata --
 * `version_number` (not `release_version_number`, unlike the provider-facing equivalent).
 */
export interface LabReleaseVersionSummary {
  release_version_id: string
  version_number: number
  lab_result_release_id: string | null
  amendment_id: string | null
  created_at: string
  is_amendment: boolean
}

export interface LabReleaseVersionHistory {
  order_id: string
  versions: LabReleaseVersionSummary[]
}

/**
 * DH-LAB-005 §52-54: the lab-side sibling of the provider-facing exact-version-content read --
 * reconstructed from the immutable manifest only, never from live current-effective state.
 * Version 1's content here must stay byte-for-byte identical after Version 2/3 exist.
 */
export interface LabReleaseVersionItem {
  item_id: string
  catalog_code_snapshot: string
  catalog_display_name_snapshot: string
  entry_completed_at: string
  observations: LabResultInspectionObservation[]
}

export interface LabReleaseVersionContent {
  order_id: string
  release_version_id: string
  release_version_number: number
  is_amendment: boolean
  patient: LabWorkflowPatientIdentity
  ordering_provider_name: string
  results: LabReleaseVersionItem[]
}

export type ExchangeErrorCode =
  | 'UNAUTHORIZED'
  | 'FORBIDDEN'
  | 'NOT_FOUND'
  | 'CONFLICT'
  | 'VALIDATION_ERROR'
  | 'SERVER_ERROR'
  | 'NETWORK_ERROR'

/**
 * DH-PATIENT-001 §5/§7/§58: the provider-side patient-access surface.
 *
 * `PatientAccessIssueOutcome.access_token` is the **raw opaque bearer secret**, returned exactly
 * once by the issuance endpoint and unrecoverable from the backend afterward by design (§10). It
 * must never be persisted in browser storage, written into a query string or path, logged, or
 * rendered as visible text -- see `features/patient/patientAccessUrl.ts` for the one legitimate
 * transport (a URL fragment) and `PatientAccessHandoff` for the one legitimate rendering (a
 * locally-generated QR code).
 */
export type PatientAccessMode = 'LAB_SELECTION' | 'ORDER_READ'

export interface PatientAccessIssueOutcome {
  access_token: string
  expires_at: string
  access_mode: PatientAccessMode
}

/**
 * DH-PATIENT-001 §9: metadata only. `expires_at`/`access_mode` are `null` whenever `active` is
 * `false` -- an expired or revoked grant discloses no expiry instant. There is deliberately no
 * field here from which the raw token could be reconstructed.
 */
export interface PatientAccessMetadata {
  active: boolean
  expires_at: string | null
  access_mode: PatientAccessMode | null
}

/**
 * DH-NOTIFY-001 §5: what a clinician must supply to have DHealth message a patient.
 *
 * `patient_consent_confirmed` is typed `true`, not `boolean`, mirroring the backend's
 * `Literal[True]`. A request that does not affirm consent cannot be constructed in TypeScript and
 * is a 422 at the server's schema boundary if one is sent anyway -- the checkbox is not the
 * control, the type and the schema are.
 *
 * `destination` is as the clinician typed it. The **server** normalizes to E.164 via
 * libphonenumber and refuses anything it cannot parse; the browser deliberately does no
 * country-code guessing of its own, because two validators that disagree is how a number gets
 * accepted here and rejected there.
 */
export interface PatientNotificationEnrollmentRequest {
  channel: NotificationChannel
  destination: string
  patient_consent_confirmed: true
}

export type NotificationChannel = 'SMS' | 'WHATSAPP'

export type NotificationEventType =
  | 'ACCESS_LINK_ISSUED'
  | 'RESULT_RELEASED'
  | 'AMENDMENT_RELEASED'

/**
 * DH-NOTIFY-001 §7/§18: what a message's state means, stated so the UI cannot overclaim.
 *
 * `SENT` means the transport provider's API accepted the request -- nothing about a handset.
 * `DELIVERED` means the provider sent an authenticated receipt saying a handset accepted it. It
 * does **not** mean the patient read it, opened the link, understood it, or that anybody has
 * reviewed a result. `patientNotificationWording.ts` is the only place these are turned into
 * French, precisely so the distinction survives translation.
 */
export type NotificationState = 'PENDING' | 'SENDING' | 'SENT' | 'DELIVERED' | 'FAILED'

/**
 * DH-NOTIFY-001 §18: one message's operational state, for the clinician who sent it.
 *
 * Carries no message body, no secure URL and no provider message id -- a clinician needs to know
 * whether the message got out and what to do if it did not, and a vendor's identifier answers
 * neither. `retryable` is server-derived: it is false for every `ACCESS_LINK_ISSUED` row, whose
 * secure link cannot be reconstructed, so the UI offers revoke-and-reissue instead of a retry that
 * would send a message with no link in it.
 */
export interface PatientNotificationSummary {
  id: string
  event_type: NotificationEventType
  channel: NotificationChannel
  destination: string
  state: NotificationState
  failure_category: string | null
  attempt_count: number
  retryable: boolean
  created_at: string
  sent_at: string | null
  delivered_at: string | null
}

export interface PatientNotificationStatus {
  enrolled: boolean
  channel: NotificationChannel | null
  destination: string | null
  notifications: PatientNotificationSummary[]
  /**
   * DH-NOTIFY-META-001 §6/§20: the channels this deployment can **actually** send on, derived
   * server-side from the configured transport.
   *
   * The frontend must never decide this. A hard-coded `['WHATSAPP', 'SMS']` is a UI that keeps
   * offering SMS forever after the provider stops supporting it, which is exactly the false
   * advertisement §6 forbids -- a clinician would collect a patient's consent for a message
   * nothing can deliver. Empty means notifications are off entirely.
   *
   * Today, with the direct Meta WhatsApp transport and no SMS provider selected, this is
   * `['WHATSAPP']`.
   */
  available_channels: NotificationChannel[]
}

/**
 * DH-PATIENT-001 §26/§27: the server-owned patient result/follow-up projection. The frontend
 * never derives this by combining order status, release existence, and review disposition -- it
 * renders exactly what the Exchange says, and has no code path that could compute it otherwise.
 */
export type PatientResultStatus =
  | 'NOT_READY'
  | 'AWAITING_CLINICIAN_REVIEW'
  | 'NO_FOLLOW_UP_REQUIRED'
  | 'FOLLOW_UP_REQUIRED'

export interface PatientOrderTest {
  catalog_code_snapshot: string
  catalog_display_name_snapshot: string
}

/**
 * The patient-visible selection receipt, typed exactly as the Exchange serializes it (DH-EX-005 /
 * DH-EX-010) -- including the price fields that belong to the *preserved* patient-marketplace
 * flow, which for a provider-assigned order are always `null`/empty (`assignment_source:
 * 'PROVIDER'`).
 *
 * DH-PATIENT-001 deliberately types the real wire shape rather than a convenient subset: a type
 * that pretended these fields did not exist would be a lie about the response, and would make a
 * future accidental render of one a *type-checked* operation. The launch patient UI instead
 * narrows this through `toPatientLab()`, which is the one function allowed to decide what a
 * patient sees (§34: name, public address, public phone -- never a price, never an internal id).
 */
export interface PatientOrderSelection {
  lab_organization_id: string
  lab_organization_name: string
  public_address: string | null
  latitude: string | null
  longitude: string | null
  public_phone: string | null
  selected_at: string
  assignment_source: 'PROVIDER' | 'PATIENT'
  items: {
    catalog_code_snapshot: string
    catalog_display_name_snapshot: string
    selected_price_amount: string
    selected_price_currency: string
  }[]
  total_amount: string | null
  currency: string | null
}

/**
 * DH-PATIENT-001 §34: the narrowed laboratory projection the patient surface actually renders.
 */
export interface PatientOrderLab {
  lab_organization_name: string
  public_address: string | null
  public_phone: string | null
}

/**
 * DH-PATIENT-001 §30/§36: status only, never result content. No numeric value, text value,
 * reference range, source code, observation, release manifest, or reviewer identity exists on
 * this type -- and the backend regression tests assert the same against the serialized JSON.
 */
export interface PatientOrder {
  status: LabOrderStatus
  requested_tests: PatientOrderTest[]
  ordering_provider_name: string
  selection: PatientOrderSelection | null
  result_status: PatientResultStatus
}

// ── DH-MVP-ONBOARD-001: facility enrollment and integration readiness ────────────────────────

export type ParticipationMode = 'NATIVE' | 'CONNECT'

export type OnboardingStatus =
  | 'DRAFT'
  | 'FACILITY_VERIFIED'
  | 'ORGANIZATION_READY'
  | 'CONFIGURATION_IN_PROGRESS'
  | 'TECHNICAL_DISCOVERY'
  | 'AWAITING_CONNECTOR'
  | 'READY_FOR_QUALIFICATION'
  | 'READY_FOR_ROUTING'
  | 'BLOCKED'

export type IntegrationFeasibility =
  | 'DISCOVERY_REQUIRED'
  | 'CONNECTABLE'
  | 'CONNECTOR_DEVELOPMENT_REQUIRED'
  | 'NOT_SAFELY_INTEGRABLE'

export type HostingModel = 'ON_PREMISE' | 'CLOUD' | 'HYBRID' | 'UNKNOWN'

export type InterfaceType =
  | 'REST_API'
  | 'FHIR'
  | 'HL7_V2'
  | 'WEBHOOK'
  | 'SFTP'
  | 'FILE_IMPORT_EXPORT'
  | 'DATABASE_READ'
  | 'OTHER'
  | 'UNKNOWN'

/**
 * What DHealth actually knows about a reported interface. The gap between
 * REPORTED and VERIFIED is the most load-bearing distinction in Connect
 * onboarding, and neither of them means a connector exists.
 */
export type InterfaceVerificationState = 'REPORTED' | 'DOCUMENTED' | 'VERIFIED' | 'UNSUPPORTED'

export type EnrollmentBlockerCode =
  | 'VENDOR_DOCUMENTATION_UNAVAILABLE'
  | 'VENDOR_CONTACT_REQUIRED'
  | 'REQUIRED_INTERFACE_UNAVAILABLE'
  | 'MISSING_RESULT_RELEASE_EVENT'
  | 'UNKNOWN_PATIENT_IDENTIFIER_BEHAVIOR'
  | 'CONNECTOR_NOT_IMPLEMENTED'
  | 'FACILITY_CONFIGURATION_INCOMPLETE'
  | 'CLINICAL_TEMPLATE_SIGNOFF_REQUIRED'

export interface FacilityInterfaceCapability {
  interface_type: InterfaceType
  verification_state: InterfaceVerificationState
  detail: string | null
}

export interface FacilityEnrollmentBlocker {
  id: string
  code: EnrollmentBlockerCode
  note: string | null
  created_at: string
}

export interface FacilityEnrollment {
  organization_id: string
  organization_name: string
  organization_type: string
  organization_status: string
  participation_mode: ParticipationMode
  onboarding_status: OnboardingStatus
  integration_feasibility: IntegrationFeasibility | null
  existing_system_name: string | null
  existing_system_vendor: string | null
  existing_system_version: string | null
  hosting_model: HostingModel | null
  technical_contact: string | null
  vendor_contact: string | null
  documentation_reference: string | null
  patient_identifier_namespace: string | null
  order_identifier_concept: string | null
  report_identifier_concept: string | null
  specimen_identifier_concept: string | null
  exposes_source_event_timestamps: boolean | null
  exposes_professional_release_event: boolean | null
  notes: string | null
  interfaces: FacilityInterfaceCapability[]
  open_blockers: FacilityEnrollmentBlocker[]
}

/**
 * One server-derived fact about whether this facility can take production work.
 * The frontend renders these; it never computes its own version of them.
 */
export interface ReadinessFinding {
  code: string
  satisfied: boolean
  blocking: boolean
  detail: string | null
}

export interface FacilityReadiness {
  organization_id: string
  participation_mode: ParticipationMode
  onboarding_status: OnboardingStatus
  production_routing_allowed: boolean
  findings: ReadinessFinding[]
}

/**
 * What the server actually persisted for a new facility's first administrator.
 *
 * Present only when a membership was really created. The wizard reports
 * attachment from this field and never from its own selection state: the two can
 * disagree, and only one of them is true.
 */
export interface FirstAdministrator {
  identity_id: string
  membership_id: string
  display_name: string | null
  roles: string[]
}

/** The create-facility response. Only this endpoint carries `first_administrator`. */
export interface FacilityEnrollmentCreated extends FacilityEnrollment {
  first_administrator: FirstAdministrator | null
}

/**
 * The body of a 409 from `PUT .../lab-routes/{labId}`.
 *
 * Each reason names the side and the code separately. The side is a field rather
 * than a prefix on the code precisely so this client never has to recover it by
 * stripping a string.
 */
export interface RouteActivationRefusal {
  message: string
  blocking_reasons: { side: 'provider' | 'lab' | null; code: string }[]
}

export interface IdentityLookupResult {
  identity_id: string
  display_name: string | null
  email: string | null
}

/**
 * One person's place in one organization, as `GET .../memberships` returns it.
 *
 * `roles` is what the server holds for this membership right now -- the complete
 * set, not a delta. `status` is the membership's own status (`ACTIVE`,
 * `INACTIVE`, `REVOKED`): a row can be listed and still grant nothing, which is
 * why the status travels with it instead of being inferred from presence.
 */
export interface OrganizationMembership {
  membership_id: string
  identity_id: string
  display_name: string | null
  email: string | null
  status: string
  roles: string[]
}

/**
 * The body of `POST .../memberships`.
 *
 * `roles` is an ensure/add list, never a replace-with list: omitting a role the
 * person already holds does not remove it, and no endpoint in this API removes
 * one. Anything sending this must not present it as "the roles they will have".
 */
export interface OrganizationMembershipCreateRequest {
  identity_id: string
  roles: string[]
}

/**
 * Whether the POST created a membership (201) or found one already there and
 * added roles to it (200). Two different sentences on screen, one endpoint.
 */
export interface CreateMembershipOutcome {
  membership: OrganizationMembership
  created: boolean
}

/**
 * DH-CONNECT-CONTRACT-001 — a connector installation, and whether it qualified.
 *
 * Three facts that look like one and are not, kept as three fields on purpose:
 * an installation exists, a qualification applies to it right now, and a runtime
 * is reporting in. Only the third would make a facility routable, and in this
 * release it is false everywhere because no runtime exists.
 */
export interface ConnectorInstallation {
  installation_id: string
  organization_id: string
  connector_key: string
  connector_version: string
  contract_version: string
  source_system: string
  source_system_version: string | null
  interface_type: string
  /** A SHA-256 digest. Never the configuration itself -- DHealth never holds it. */
  configuration_fingerprint: string
  mapping_fingerprint: string
  status: string
  /** The bound OIDC client, or `null` while no service identity has been issued. Not a secret. */
  service_client_id: string | null
  created_at: string
  updated_at: string
}

/** How strongly a capability row is evidenced. `passed` alone does not gate production. */
export type ConnectorEvidenceLevel = 'DECLARED' | 'EXERCISED'

export interface ConnectorCapabilityOutcome {
  capability: string
  passed: boolean
  /**
   * `DECLARED` — the connector says it implements this, and nothing drove it.
   * `EXERCISED` — DHealth drove it and observed the required effect.
   *
   * A required capability satisfies production qualification only when `EXERCISED`. A declared
   * one must never be given the same visual treatment as an exercised one.
   */
  evidence_level: ConnectorEvidenceLevel
  detail: string | null
}

/**
 * One completed qualification attempt. Append-only: a failure is never replaced
 * by a later success, and the fields here are copies taken when the run finished,
 * so the row keeps describing what it was actually run against.
 */
export interface ConnectorQualificationRecord {
  qualification_id: string
  result: string
  suite_version: string
  started_at: string
  completed_at: string
  contract_version: string
  connector_version: string
  configuration_fingerprint: string
  mapping_fingerprint: string
  evidence_summary: string | null
  capabilities: ConnectorCapabilityOutcome[]
}

/**
 * Derived by the server on every read. There is no stored "qualified" column and
 * no endpoint that sets one; `gap` names the single reason when it is false.
 */
export interface ConnectorQualificationVerdict {
  qualified: boolean
  gap: string | null
  detail: string
  qualification_id: string | null
  completed_at: string | null
  /** Required capabilities the applicable qualification did not cover at all. */
  missing_capabilities: string[]
  /**
   * Required capabilities the connector declares and nothing has driven end to end.
   *
   * Kept separate from `missing_capabilities` because they are different work: one is "build it",
   * the other is "we have no way yet to prove the thing you built". Today the second is DHealth's
   * own gap, not the partner's, and telling an operator otherwise would send them to file a bad
   * bug report against a partner.
   */
  unexercised_capabilities: string[]
  installed_contract_version: string | null
  current_contract_version: string
  current_suite_version: string
}

/**
 * What DHealth has observed about this connector's agent. Observations, never a verdict.
 *
 * There is nothing writable here and no endpoint that sets health -- the state is derived from
 * these fields server-side, and the absence of a setter is how that rule is kept rather than
 * merely stated. `reasons` is the derivation's own explanation, in French, composed by the server
 * so the sentence and the state cannot drift apart.
 *
 * No patient data: counts, versions, timestamps and short sentences only.
 */
export interface ConnectorRuntime {
  /** `null` when no agent has ever reported in for this installation. */
  runtime_instance_id: string | null
  agent_version: string | null
  /** DHealth's own receive time, never the agent's clock. */
  last_seen_at: string | null
  adapter_reachable: boolean | null
  pending_deliveries: number
  dead_letters: number
  oldest_pending_seconds: number | null
  reasons: string[]
}

export interface FacilityConnector {
  organization_id: string
  participation_mode: string
  installation: ConnectorInstallation | null
  installations: ConnectorInstallation[]
  verdict: ConnectorQualificationVerdict
  /** UNKNOWN | HEALTHY | DEGRADED | UNAVAILABLE, derived from `runtime`. */
  health_state: string
  /**
   * Whether a live runtime is carrying this facility's traffic right now. `HEALTHY` and nothing
   * else -- DEGRADED is deliberately not operational.
   *
   * Reachable as of DH-CONNECT-AGENT-001, and still not the same question as "may this facility
   * take real clinical work": that also needs a qualified *vendor* adapter, which does not exist.
   */
  operational: boolean
  /** True since DH-CONNECT-AGENT-001: DHealth has a delivery runtime. A fact about DHealth, not
   * about this facility. */
  transport_available: boolean
  runtime: ConnectorRuntime
  qualification_history: ConnectorQualificationRecord[]
}

/** The body of `PUT .../connector/{id}/service-identity`. An identifier, never a secret. */
export interface ConnectorServiceIdentityRequest {
  /** The OIDC client the agent authenticates as, or `null` to clear the binding. */
  service_client_id: string | null
}

/** The body of `POST /platform/facilities/{id}/connector`. No credential fields, by design. */
export interface ConnectorRegistrationRequest {
  connector_key: string
  connector_version: string
  source_system: string
  source_system_version?: string | null
  interface_type: string
  configuration_fingerprint: string
  mapping_fingerprint: string
  contract_version?: string
}

// -- DHEALTH-CLINIC-VISIT-001: the native clinic visit ------------------------------------------

/**
 * The four states a visit can actually be in.
 *
 * `PLANNED` and `NO_SHOW` are deliberately absent: both need a calendar to be reachable, no
 * appointment surface exists, and a state nothing can enter is decoration
 * (`docs/NATIVE-OPERATING-PRODUCT.md` §12). The backend's CHECK constraint enumerates the same
 * four, so this is the whole set rather than the subset this build happens to render.
 */
export type VisitStatus = 'CHECKED_IN' | 'IN_PROGRESS' | 'COMPLETED' | 'CANCELLED'

export interface Visit {
  id: string
  provider_organization_id: string
  /** A native `Patient`, never a `PatientReference` — a visit is about a person in this
   * organization's own directory. */
  patient_id: string
  patient_display_name: string | null
  /** Optional, and never invented: nothing in DHealth substitutes a placeholder birthday
   * (ADR-051). A worklist row must render without one. */
  patient_date_of_birth: string | null
  /** `null` while `CHECKED_IN` — at check-in, who will see the patient is not yet known. */
  clinician_identity_id: string | null
  status: VisitStatus
  reason_for_visit: string
  created_at: string
  started_at: string | null
  completed_at: string | null
  cancelled_at: string | null
}

/** One append-only revision of a visit's clinical note. The highest `revision_number` is current;
 * earlier ones are kept and shown, because a corrected note's history is part of the record
 * (ADR-045/048's principle). */
export interface VisitNote {
  id: string
  revision_number: number
  author_identity_id: string
  body: string
  created_at: string
}

/** One visit with its note history and the orders raised during it.
 *
 * `orders` carries no result values by design: results are opened from the order, through the read
 * paths that already apply ADR-044's entry-is-not-release filtering. */
export interface VisitDetail {
  visit: Visit
  notes: VisitNote[]
  orders: LabOrderSummary[]
}

export interface VisitCreateRequest {
  patient_id: string
  reason_for_visit: string
}

export interface VisitCreateOutcome {
  visit: Visit
  /** `false` when an existing visit for this idempotency key was returned — the retry or
   * double-click case, not an error. */
  created: boolean
}

export interface VisitNoteCreateRequest {
  body: string
}

export interface VisitReasonUpdateRequest {
  reason_for_visit: string
}
