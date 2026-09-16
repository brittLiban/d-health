"""DH-EX-002: the minimal ``/exchange/v1`` HTTP API -- organization onboarding, membership, and
role assignment, sitting on top of ``identity_auth``, ``membership``, and ``authorization``.

Deliberately five endpoints only, per the ticket's explicit scope boundary -- no lab-order API, no
portal workflow, no self-service signup:

- ``GET  /exchange/v1/me``
- ``POST /exchange/v1/organizations``                       (platform admin only)
- ``GET  /exchange/v1/organizations/{id}``
- ``GET  /exchange/v1/organizations/{id}/memberships``       (org admin, own org only)
- ``POST /exchange/v1/organizations/{id}/memberships``       (org admin, own org only)

**Membership attachment is lookup-only, by ``identity_id``.** ``POST .../memberships`` accepts
an existing ``ExchangeIdentity`` UUID, never a raw ``(issuer, subject)`` pair -- that row is
created in exactly one place (``get_principal`` below, from a validated token), never from
admin-supplied input. This is a known MVP workflow limitation, not an oversight: the target
person must authenticate once (e.g. call ``GET /me``) and share their ``identity_id``
out-of-band before an org admin can attach them. Building an invitation/lookup-by-email flow
is explicitly out of scope for this ticket.

Every ``require_*`` authorization decision is delegated to ``AuthorizationService`` -- this module
never compares roles or organization ids inline. ``TokenValidationError`` always maps to a generic
401 (never leaking *why* a token failed); ``ResourceNotFound``/``NotAuthorized`` map to 404/403
per the anti-enumeration policy documented on ``authorization.py``.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.audit import ExchangeAuditEvent, ExchangeAuditWriter
from daher_adapter.exchange.authorization import (
    AuthenticatedPrincipal,
    AuthorizationService,
    NotAuthorized,
    OrganizationAccessContext,
    ResourceNotFound,
)
from daher_adapter.exchange.catalog import (
    CatalogEntryConflict,
    CatalogEntryNotFound,
    CatalogEntryRecord,
    CatalogRepository,
    LabOfferingRecord,
    LabPriceRecord,
    OfferingConflict,
    OfferingInactiveConflict,
    OfferingNotFound,
    PriceConflict,
)
from daher_adapter.exchange.directory import (
    LabDirectoryListing,
    LabDirectoryProfileRecord,
    LabDirectoryProfileRepository,
)
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.identity_auth import TokenValidationError, TokenValidator
from daher_adapter.exchange.lab_workflow import (
    InvalidLabWorkflowTransition,
    LabOrderNotAssigned,
    LabWorkflowOrderRecord,
    LabWorkflowRepository,
)
from daher_adapter.exchange.membership import (
    ALLOWED_ROLES_BY_ORG_TYPE,
    ExchangeIdentityRepository,
    IdentityNotFound,
    InvalidRoleForOrganizationType,
    MembershipInactive,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    DoctorReviewStatus,
    LabOrderStatus,
    LabResultType,
    OrganizationMembershipStatus,
    OrganizationRole,
    OrganizationType,
    PatientAccessMode,
)
from daher_adapter.exchange.order_intents import (
    LabOrderIntentRecord,
    LabOrderIntentRepository,
    OrderIntentNotFound,
)
from daher_adapter.exchange.orders import (
    EmptyOrder,
    IdempotencyKeyConflict,
    InvalidOrderTransition,
    LabOrderRecord,
    LabOrderRepository,
    OrderItemInput,
)
from daher_adapter.exchange.orders import OrderNotFound as LabOrderNotFound
from daher_adapter.exchange.organizations import InvalidOrganizationType, OrganizationRepository
from daher_adapter.exchange.patient_access import (
    ActiveGrantAlreadyExists,
    OrderNotIssuable,
    PatientAccessGrantRepository,
)
from daher_adapter.exchange.patient_access import OrderNotFound as GrantOrderNotFound
from daher_adapter.exchange.results import (
    AmendmentAlreadyInProgress,
    AmendmentBaseVersionSuperseded,
    AmendmentInspectionRecord,
    AmendmentNotFound,
    AmendmentNotPreparing,
    AmendmentNotReadyForRelease,
    AmendmentReleaseIntegrityError,
    DoctorReviewRecord,
    DoctorReviewRepository,
    DuplicateResult,
    EntryAlreadyCompleted,
    EntryNotReadyForCompletion,
    ExplicitReviewVersionRequired,
    InvalidResultSubmissionState,
    LabResultAmendmentRecord,
    LabResultAmendmentRepository,
    LabResultInspectionRecord,
    LabResultReleaseRepository,
    LabResultReleaseVersionRecord,
    LabResultRepository,
    LegacyCompletedOrderHasNoRelease,
    ObservationNotEligibleForAmendment,
    ObservationNotFound,
    OrderItemNotFound,
    OrderNotCompletedForAmendment,
    ResultsNotReadyForRelease,
    ResultSubmissionOutcome,
    ReviewAlreadyFinalized,
    ReviewNotAllowedYet,
    ReviewNotFound,
    StaleObservationVersion,
)
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
from daher_adapter.exchange.selection import (
    DifferentLabAlreadySelected,
    LabAlreadyAssignedByProvider,
    LabCannotFulfillOrder,
    LabSelectionRepository,
    PatientAccessGrantInvalidated,
    PatientAccessGrantLacksSelectionAuthority,
    StalePriceSelection,
)

# DH-EX-004R: the fixed, server-derived source-identity namespace for a DHealth-native ordering
# clinician's `PractitionerReference` -- never a caller-supplied practitioner_reference_id (see
# `create_lab_order` below). `source_identity_value` is always the authenticated
# `AuthenticatedPrincipal.identity_id`, never an email/name/phone/role.
#
# `source_system` deliberately does NOT say "provider-portal" (the DH-EX-004 handoff's original
# choice, corrected here): what this binds is "the DHealth Exchange itself asserts this
# practitioner identity", not "the web Provider Portal UI specifically asserted it". The fact
# being recorded -- an authenticated Exchange identity ordering clinically inside this provider
# organization -- does not change if the web frontend is renamed, replaced by a native/mobile
# client, or if a future non-portal API consumer submits the same request. A source-identity
# namespace is meant to outlive any one client; naming it after today's UI would have made that
# UI's name part of a durable database value for no benefit.
_CLINICIAN_PRACTITIONER_SOURCE_SYSTEM = "dhealth-exchange"
_CLINICIAN_PRACTITIONER_SOURCE_IDENTITY_SYSTEM = "urn:dhealth:exchange-identity-id"

# DH-EX-004R: canonical-only UUIDv4 textual form (RFC 4122 §3), lowercase hex, no braces/prefix.
_CANONICAL_UUID4_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)

# DH-EX-005R: `secrets.token_urlsafe(32)` produces exactly 43 characters -- generously bounded so
# an attacker-controlled Authorization header cannot force unbounded hashing/DB-lookup work.
_MAX_PATIENT_BEARER_TOKEN_LENGTH = 512


class _InactiveMembershipReplay(Exception):
    """Internal-only signal: a `POST .../memberships` replay found an existing but INACTIVE
    membership. Raised inside the mutation transaction purely to trigger its rollback (nothing
    was actually committed on this path in the first place -- the insert attempt already resolved
    to the existing row via `OrganizationMembershipRepository.create`'s own SAVEPOINT), then caught
    outside the transaction and turned into a 409. Never a DB audit row: refusing to reactivate a
    membership is not a mutation, so it gets best-effort file telemetry instead, per DH-EX-002R's
    explicit "refusal is not a mutation" instruction."""

    def __init__(self, membership_id: uuid.UUID) -> None:
        self.membership_id = membership_id


_ROLE_LITERAL = Literal["ORG_ADMIN", "CLINICIAN", "LAB_TECH", "LAB_RESULT_RELEASER"]


class OrganizationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    organization_type: Literal["PROVIDER", "LAB"]


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    name: str
    organization_type: str
    status: str


class MembershipCreateRequest(BaseModel):
    """DH-EX-002R Part I: `roles` is an **ensure/add** list, never a replace-with list -- POSTing
    `["CLINICIAN"]` against a membership that already holds `["ORG_ADMIN"]` grants CLINICIAN
    *in addition*, it never removes ORG_ADMIN for having been omitted. There is no endpoint in
    this ticket that removes a role or membership; omission from a request body is never
    interpreted as a removal request anywhere in this API."""

    model_config = ConfigDict(extra="forbid")

    identity_id: uuid.UUID
    roles: list[_ROLE_LITERAL] = Field(default_factory=list)


class MembershipResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_id: uuid.UUID
    identity_id: uuid.UUID
    display_name: str | None
    email: str | None
    status: str
    roles: list[str]


class MembershipSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: uuid.UUID
    organization_name: str
    organization_type: str
    organization_status: str
    membership_status: str
    roles: list[str]


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_id: uuid.UUID
    issuer: str
    subject: str
    display_name: str | None
    email: str | None
    platform_admin: bool
    memberships: list[MembershipSummary]


# -- DH-EX-003: catalog, lab directory, offerings, pricing, marketplace --------------------------


class CatalogEntryCreateRequest(BaseModel):
    """No ``is_synthetic`` field, deliberately -- DH-EX-003's explicit instruction is that no
    client request may submit a non-synthetic catalog entry through this API; the server always
    forces ``is_synthetic=True`` (see ``create_catalog_test`` below), never trusting a client-sent
    value for it."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=256)
    version: int = Field(default=1, ge=1)


class CatalogEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    code: str
    version: int
    display_name: str
    active: bool
    is_synthetic: bool


class LabDirectoryProfileRequest(BaseModel):
    """PUT-shaped full-replace: every field reflects the complete desired state, including
    ``listed``/``accepting_orders`` -- omitting them resets to the safe `False` default rather
    than mysteriously preserving whatever was there before."""

    model_config = ConfigDict(extra="forbid")

    public_address: str = Field(min_length=1, max_length=512)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    public_phone: str | None = Field(default=None, max_length=64)
    listed: bool = False
    accepting_orders: bool = False

    @model_validator(mode="after")
    def _validate_coordinates(self) -> "LabDirectoryProfileRequest":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must both be present or both be absent")
        if self.latitude is not None and not (Decimal("-90") <= self.latitude <= Decimal("90")):
            raise ValueError("latitude must be between -90 and 90")
        if self.longitude is not None and not (Decimal("-180") <= self.longitude <= Decimal("180")):
            raise ValueError("longitude must be between -180 and 180")
        return self

    @model_validator(mode="after")
    def _validate_address_not_blank(self) -> "LabDirectoryProfileRequest":
        # DH-EX-003R: `min_length=1` alone accepts a single space -- unusable as a marketplace-
        # visible address whether or not `listed` happens to be true on this particular call, and
        # `public_address` is a mandatory, non-nullable field with no "draft, no address yet"
        # state modeled anywhere in this schema. Matches the `code.strip()`/`display_name.strip()`
        # non-blank convention already used elsewhere in this package (`exchange/catalog.py`).
        if not self.public_address.strip():
            raise ValueError("public_address must not be blank")
        return self


class LabDirectoryProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    lab_organization_id: uuid.UUID
    listed: bool
    accepting_orders: bool
    public_address: str
    latitude: Decimal | None
    longitude: Decimal | None
    public_phone: str | None
    created_at: datetime
    updated_at: datetime


class LabDirectoryListingResponse(BaseModel):
    """Directory browse/detail shape -- `GET /labs` and `GET /labs/{id}`. Never returned for an
    unlisted lab except to a platform admin (see `get_lab` below)."""

    model_config = ConfigDict(extra="forbid")

    lab_organization_id: uuid.UUID
    lab_organization_name: str
    listed: bool
    accepting_orders: bool
    public_address: str
    latitude: Decimal | None
    longitude: Decimal | None
    public_phone: str | None


class LabOfferingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_entry_id: uuid.UUID
    local_test_code: str | None = Field(default=None, max_length=128)


class LabOfferingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    lab_organization_id: uuid.UUID
    catalog_entry_id: uuid.UUID
    local_test_code: str | None
    active: bool


class LabOfferingStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


class LabPriceSetRequest(BaseModel):
    """``expected_current_price_id`` has no default -- the caller must always state their
    optimistic-concurrency expectation explicitly. `None` means "I expect no current price exists
    yet"; a UUID means "I expect that exact row to still be current". DH-EX-003's Djibouti-MVP
    scope: `currency` must be exactly `"DJF"`, enforced by the `Literal` type itself -- anything
    else is a 422 from schema validation before any application code runs.

    ``amount``'s ``max_digits``/``decimal_places`` mirror the database column exactly
    (``exchange_lab_prices.amount`` is ``Numeric(12, 2)``) -- DH-EX-003R: without this, a value
    with more than 2 decimal places or more than 10 integer digits reached the database rather
    than being refused here, and PostgreSQL's behavior for the two cases differs in a way an API
    caller should never have to discover by trial and error (silently rounding excess scale,
    but raising a raw, unfiltered `numeric field overflow` for excess precision). Both now fail
    the same, clean way: 422, at the schema boundary, before any application code runs."""

    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: Literal["DJF"]
    expected_current_price_id: uuid.UUID | None


class LabPriceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    lab_offering_id: uuid.UUID
    amount: Decimal
    currency: str
    effective_at: datetime
    superseded_at: datetime | None


class MarketplaceLabOptionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_entry_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _no_duplicate_entries(self) -> "MarketplaceLabOptionsRequest":
        if len(set(self.catalog_entry_ids)) != len(self.catalog_entry_ids):
            raise ValueError("catalog_entry_ids must not contain duplicates")
        return self


class MarketplacePriceLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_entry_id: uuid.UUID
    catalog_code: str
    display_name: str
    offering_id: uuid.UUID
    current_price_id: uuid.UUID
    amount: Decimal
    currency: str


class MarketplaceLabIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    name: str


class MarketplaceLabDirectory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_address: str
    latitude: Decimal | None
    longitude: Decimal | None
    public_phone: str | None


class MarketplaceLabOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lab: MarketplaceLabIdentity
    directory: MarketplaceLabDirectory
    prices: list[MarketplacePriceLine]
    total_amount: Decimal
    currency: str


class MarketplaceLabOptionsResponse(BaseModel):
    """``as_of`` is one timestamp for the whole response, not per-lab -- every lab option in a
    single response was computed from the same query instant, so a per-lab timestamp would only
    ever repeat the same value under a different name."""

    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    options: list[MarketplaceLabOption]


# -- DH-EX-009: provider-scoped patient-reference binding ------------------------------------------


class PatientReferenceBindRequest(BaseModel):
    """Source-owned reference information only -- deliberately no ``source_organization_id`` field:
    it comes from the URL's authorized ``provider_id``, never trusted from the request body (a
    caller could otherwise bind a reference under an organization it has no relationship to simply
    by naming it in JSON). No demographic-matching input either (no national ID, no DOB) -- the
    authoritative identifier at this layer is the exact ``source_system``/
    ``source_identity_system``/``source_identity_value`` tuple the source application already
    assigned; DHealth Exchange never decides two records "probably" belong to the same person."""

    model_config = ConfigDict(extra="forbid")

    source_system: str = Field(min_length=1, max_length=64)
    source_identity_system: str = Field(min_length=1, max_length=256)
    source_identity_value: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=256)
    contact_channel_type: str | None = Field(default=None, max_length=32)
    contact_value: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _validate_identity_not_blank(self) -> "PatientReferenceBindRequest":
        # DH-EX-007A/DH-EX-003R lesson applied here too: `min_length=1` alone accepts a single
        # space. These three fields are the exact identity this binding creates -- a whitespace-
        # only value is not a real source identity, and must never reach the repository as one.
        for name, value in (
            ("source_system", self.source_system),
            ("source_identity_system", self.source_identity_system),
            ("source_identity_value", self.source_identity_value),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        return self


class PatientReferenceResponse(BaseModel):
    """Deliberately minimal -- DH-EX-009's explicit data-minimization instruction: only what a
    caller needs to bind a future Exchange transaction (an order). No ``display_name``/contact
    snapshot, no ``created_at`` -- this is not a patient-profile representation."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    source_organization_id: uuid.UUID
    source_system: str
    source_identity_system: str
    source_identity_value: str


class ProviderPatientReferenceSummary(BaseModel):
    """Minimal provider-local selection view, not a patient demographic profile.

    Source-system and identity-namespace internals are deliberately omitted. ``local_reference``
    is the source's stable opaque value and is exposed only so a clinician can truthfully identify
    a nameless reference and a native client can reconcile an ambiguous create response.
    """

    model_config = ConfigDict(extra="forbid")

    patient_reference_id: uuid.UUID
    display_name: str | None
    contact_channel_type: str | None
    contact_value: str | None
    local_reference: str


class ProviderPatientReferenceSearchRequest(BaseModel):
    """Search body kept out of the URL so patient lookup text is absent from access logs."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def _validate_query_not_blank(self) -> "ProviderPatientReferenceSearchRequest":
        if not self.query.strip():
            raise ValueError("query must not be blank")
        return self


# -- DH-EX-004: provider lab-order creation -------------------------------------------------------


class LabOrderCreateRequest(BaseModel):
    """No ``provider_organization_id`` (comes from the path), no ``practitioner_reference_id``
    (server-derived from the authenticated clinician -- see ``create_lab_order``), no lab id, no
    price, no status. ``catalog_entry_ids`` duplicates are rejected outright, never silently
    deduplicated -- a caller sending ``[A, A, B]`` almost certainly made a mistake, and silently
    treating it as ``[A, B]`` would both hide that mistake and create idempotency ambiguity."""

    model_config = ConfigDict(extra="forbid")

    patient_reference_id: uuid.UUID
    catalog_entry_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _no_duplicate_entries(self) -> "LabOrderCreateRequest":
        if len(set(self.catalog_entry_ids)) != len(self.catalog_entry_ids):
            raise ValueError("catalog_entry_ids must not contain duplicates")
        return self


class LabOrderItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_entry_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str


class LabOrderResponse(BaseModel):
    """Deliberately excludes ``selected_price_amount``/``selected_price_currency`` -- this ticket
    never selects a lab, so both are always ``NULL`` on every item; omitting them from the response
    entirely rather than returning them as ``null`` keeps the contract honest about what this
    endpoint does and does not do yet."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    provider_organization_id: uuid.UUID
    patient_reference_id: uuid.UUID
    practitioner_reference_id: uuid.UUID
    status: str
    created_at: datetime
    items: list[LabOrderItemResponse]


class LabOrderSummaryResponse(BaseModel):
    """The list-endpoint shape -- an ``item_count`` rather than the full item array, so a bounded
    list of up to 100 orders never has to inline a proportionally larger amount of item data a
    caller almost certainly does not need for a list view."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    provider_organization_id: uuid.UUID
    patient_reference_id: uuid.UUID
    practitioner_reference_id: uuid.UUID
    status: str
    created_at: datetime
    item_count: int


# -- DH-CLINIC-003C: backend-owned durable canonical order intent ---------------------------------


class LabOrderIntentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_reference_id: uuid.UUID
    catalog_entry_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _no_duplicate_entries(self) -> "LabOrderIntentCreateRequest":
        if len(set(self.catalog_entry_ids)) != len(self.catalog_entry_ids):
            raise ValueError("catalog_entry_ids must not contain duplicates")
        return self


class LabOrderIntentItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_entry_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str


class LabOrderIntentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    idempotency_key: str
    provider_organization_id: uuid.UUID
    patient_reference_id: uuid.UUID
    practitioner_reference_id: uuid.UUID
    committed_lab_order_id: uuid.UUID | None
    created_at: datetime
    items: list[LabOrderIntentItemResponse]


def _to_intent_response(intent: LabOrderIntentRecord) -> LabOrderIntentResponse:
    return LabOrderIntentResponse(
        id=intent.id,
        idempotency_key=intent.idempotency_key,
        provider_organization_id=intent.provider_organization_id,
        patient_reference_id=intent.patient_reference_id,
        practitioner_reference_id=intent.practitioner_reference_id,
        committed_lab_order_id=intent.committed_lab_order_id,
        created_at=intent.created_at,
        items=[
            LabOrderIntentItemResponse(
                catalog_entry_id=item.catalog_entry_id,
                catalog_code_snapshot=item.catalog_code_snapshot,
                catalog_display_name_snapshot=item.catalog_display_name_snapshot,
            )
            for item in intent.items
        ],
    )


# -- DH-EX-010: provider/facility-assigned lab routing (ADR-043 launch option (a)) ---------------


class AssignLabRequest(BaseModel):
    """Deliberately nothing else in this body -- no price ids, no patient access grant, no
    ``source_organization_id``. ``provider_id``/``order_id`` come from the URL; the acting
    clinician comes from authentication."""

    model_config = ConfigDict(extra="forbid")

    lab_organization_id: uuid.UUID


class LabAssignmentResponse(BaseModel):
    """Deliberately minimal, and deliberately excludes any price field -- provider assignment
    never selects or freezes a price, so there is nothing honest to report here about one."""

    model_config = ConfigDict(extra="forbid")

    lab_order_id: uuid.UUID
    lab_organization_id: uuid.UUID
    selected_at: datetime


# -- DH-EX-005: secure patient order access + patient lab selection ------------------------------


class PatientAccessIssueResponse(BaseModel):
    """The raw bearer secret appears here, and *only* here, in this whole API -- never again in
    any subsequent response, log, or database row (only its SHA-256 digest is persisted). No
    canonical patient-frontend URL exists in this repository yet, so this deliberately returns the
    bare token + expiry rather than inventing a fabricated production hostname; a future
    messaging/UI layer constructs the actual `#access=<token>` fragment URL from this.

    ``access_mode`` (DH-EX-010A) tells the issuing clinician what they just handed the patient --
    ``LAB_SELECTION`` (may choose a lab) or ``ORDER_READ`` (read-only; a lab is already settled)."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    expires_at: datetime
    access_mode: Literal["LAB_SELECTION", "ORDER_READ"]


class PatientOrderItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_code_snapshot: str
    catalog_display_name_snapshot: str


class PatientSelectionReceiptItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_code_snapshot: str
    catalog_display_name_snapshot: str
    selected_price_amount: Decimal
    selected_price_currency: str


class PatientSelectionReceiptResponse(BaseModel):
    """Built entirely from the frozen `LabOrderItem` snapshots and the immutable `LabSelection`
    row -- never from a live re-read of `LabPrice`, so a lab changing or deactivating afterward
    can never alter what this receipt shows.

    DH-EX-010: ``assignment_source`` names which workflow produced this selection --
    ``"PATIENT"`` when `LabSelection.selected_via_patient_access_grant_id` is set (DH-EX-005's
    price-consent path), ``"PROVIDER"`` when it is not (ADR-043 launch option (a), no price
    step). ``total_amount``/``currency`` are nullable specifically for the ``"PROVIDER"`` case,
    where every item's price snapshot is genuinely `NULL` -- meaning "price was not part of this
    transaction," which this response must represent honestly as `null`, never as an implied
    `0 DJF` (a real bug this ticket found and fixed: summing an all-`None` generator with
    `start=Decimal("0")` silently produced exactly that)."""

    model_config = ConfigDict(extra="forbid")

    lab_organization_id: uuid.UUID
    lab_organization_name: str
    public_address: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    public_phone: str | None
    selected_at: datetime
    assignment_source: Literal["PROVIDER", "PATIENT"]
    items: list[PatientSelectionReceiptItemResponse]
    total_amount: Decimal | None
    currency: str | None


class PatientOrderResponse(BaseModel):
    """The patient-facing order view -- deliberately excludes every internal identifier
    (`PatientReference`/`PractitionerReference`/`ExchangeIdentity`/membership/audit ids), contact
    data, and any result value. `selection` is `None` before a lab is chosen."""

    model_config = ConfigDict(extra="forbid")

    status: str
    requested_tests: list[PatientOrderItemResponse]
    ordering_provider_name: str
    selection: PatientSelectionReceiptResponse | None


class PatientSelectionRequest(BaseModel):
    """No ``lab_order_id`` -- the bearer grant alone determines which order this request applies
    to (see ``get_patient_access`` below); accepting one in the body would reopen exactly the IDOR
    class this design otherwise removes structurally. ``expected_price_ids`` is the patient's
    explicit consent to the *exact* current prices they were shown -- required, no default,
    duplicates rejected before any comparison."""

    model_config = ConfigDict(extra="forbid")

    lab_organization_id: uuid.UUID
    expected_price_ids: list[uuid.UUID] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicate_price_ids(self) -> "PatientSelectionRequest":
        if len(set(self.expected_price_ids)) != len(self.expected_price_ids):
            raise ValueError("expected_price_ids must not contain duplicates")
        return self


@dataclass(frozen=True, slots=True)
class AuthenticatedPatientAccess:
    grant_id: uuid.UUID
    lab_order_id: uuid.UUID
    access_mode: str
    """DH-EX-010A: the grant's own persisted `PatientAccessMode` value, fixed at issuance and
    read verbatim from the database on every request -- never re-derived from the order's current
    status, so an already-delivered `LAB_SELECTION` link keeps meaning that even after a later
    provider assignment (see `PatientAccessMode`'s own docstring)."""


# -- DH-EX-006: selected-lab assigned-order workflow ----------------------------------------------


class LabWorkflowOrderItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_code_snapshot: str
    catalog_display_name_snapshot: str
    selected_price_amount: Decimal | None
    selected_price_currency: str | None


class LabWorkflowOrderResponse(BaseModel):
    """The selected lab's own narrow clinical-work view -- deliberately excludes
    `PatientReference`/`ExchangeIdentity`/membership/audit internal ids, patient contact data, and
    any Provider-side detail beyond a safe organization display name. `patient_display_name` is
    whatever transaction-scoped snapshot DH-EX-001 recorded, or `None` if it was never supplied --
    never fabricated, never used for anything beyond operational display (see
    `exchange/lab_workflow.py`'s module docstring)."""

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    status: str
    selected_at: datetime
    updated_at: datetime
    patient_display_name: str | None
    ordering_provider_name: str
    requested_tests: list[LabWorkflowOrderItemResponse]
    collection_recorded_at: datetime | None
    """DH-EX-011: ``null`` means no sample/specimen collection milestone has been recorded in
    DHealth for this order -- honestly "unrecorded" for a legacy order, never a claim that
    collection did not happen (see ``LabSampleCollection``'s own docstring in ``models.py``)."""


class LabCollectionResponse(BaseModel):
    """DH-EX-011: the response to recording (or idempotently replaying) the order-level
    sample/specimen collection workflow milestone -- deliberately narrow, exactly the one fact
    this ticket records. No specimen type/tube/volume/handling field exists anywhere in this
    shape because none is modeled."""

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    recorded_at: datetime


# -- DH-EX-007: lab result recording, order completion, provider result access, doctor review ----


class LabResultSubmitRequest(BaseModel):
    """No ``order_id``/``item_id`` (both come from the path), no ``lab_organization_id`` (comes
    from the authenticated principal via ``_require_lab_tech``). Exactly one of the NUMERIC or
    TEXT value shapes must be present, matching ``LabResult``'s own DB CHECK constraint -- this
    validator exists so a malformed request is refused at the API boundary with a clear 422,
    never reaching the repository layer to fail there instead. ``numeric_unit``/``text_value``
    carry ``max_length`` matching ``LabResult.numeric_unit`` (``String(32)``) and
    ``LabResult.text_value`` (``String(2048)``) exactly -- without it, an oversized value passes
    this model and reaches PostgreSQL's real ``VARCHAR(n)`` column, which raises a
    ``StringDataRightTruncation`` translated by SQLAlchemy to ``DataError``, not
    ``IntegrityError`` -- the one exception type `_submit_in_session` translates to a controlled
    ``DuplicateResult`` conflict. Uncaught, that reaches the caller as a raw 500, invisible to the
    SQLite-backed test suite (SQLite does not enforce ``VARCHAR`` length), and reproduced live
    against real PostgreSQL during review.

    ``numeric_value``/``reference_range_low``/``reference_range_high``'s ``max_digits``/
    ``decimal_places`` mirror ``LabResult``'s three ``Numeric(18, 4)`` columns exactly --
    DH-EX-007A, following the identical class of gap ``LabPriceSetRequest.amount`` already closed
    for DH-EX-003R (this docstring's own precedent). Reproduced live against a real, unmodified
    PostgreSQL 18.6 database before this fix: an over-scale value (``1.23456``) reached the
    database, which **silently rounded it to ``1.2346``** and returned `201` -- the caller believes
    one clinical value was recorded while a different one was actually stored, with no error at
    all. An over-precision value (``123456789012345.6789``, 15 integer digits) raised
    ``psycopg.errors.NumericValueOutOfRange`` ("numeric field overflow ... must round to an
    absolute value less than 10^14"), translated by SQLAlchemy to ``DataError`` -- uncaught by
    `_submit_in_session` (which only translates ``IntegrityError``), reaching the caller as a raw,
    unfiltered HTTP 500. Both now fail the same, clean way: 422, at the schema boundary, before any
    application code runs or any database write is attempted. Verified directly against Pydantic
    2.13.4's actual behavior (not assumed): trailing zeros beyond four decimal places (e.g.
    ``1.10000``) are accepted, matching `NUMERIC(18,4)`'s own lossless normalization; a genuinely
    significant fifth decimal digit (e.g. ``1.00001``) is rejected; the boundary value
    ``99999999999999.9999`` (14 integer digits + 4 decimal digits = 18 total) is accepted, and
    ``100000000000000.0000`` (15 integer digits) is refused for `decimal_whole_digits`, exactly
    matching `NUMERIC(18,4)`'s own representable domain. No sign, unit, or reference-range-ordering
    rule is added or implied by this constraint -- representation only, not clinical validation."""

    model_config = ConfigDict(extra="forbid")

    result_type: Literal["NUMERIC", "TEXT"]
    numeric_value: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    numeric_unit: str | None = Field(default=None, max_length=32)
    reference_range_low: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    reference_range_high: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    text_value: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _value_matches_declared_type(self) -> "LabResultSubmitRequest":
        if self.result_type == "NUMERIC":
            if self.numeric_value is None:
                raise ValueError("numeric_value is required when result_type is 'NUMERIC'")
            if self.text_value is not None:
                raise ValueError("text_value must not be set when result_type is 'NUMERIC'")
        else:
            if self.text_value is None or not self.text_value.strip():
                raise ValueError("text_value is required when result_type is 'TEXT'")
            if (
                self.numeric_value is not None
                or self.numeric_unit is not None
                or self.reference_range_low is not None
                or self.reference_range_high is not None
            ):
                raise ValueError("numeric fields must not be set when result_type is 'TEXT'")
        return self


class LabResultSubmitResponse(BaseModel):
    """Deliberately narrow -- confirms the mutation and the order's resulting status, nothing
    more. No internal auth/security-audit fields, no patient token/grant data, no re-statement of
    frozen catalog/price snapshots the caller already has from the work-queue read.

    DH-EX-012/ADR-044: ``order_status`` is truthfully always ``'IN_PROGRESS'`` now -- recording a
    result, even the last one, no longer completes the order. ``ready_for_release`` reports the
    same DB-grounded "every item now has a completed result entry" fact DH-EX-007's since-removed
    automatic completion used to act on; this response never acts on it, only reports it.

    DH-EX-012C/ADR-047: this is the backward-compatible single-observation convenience path's
    response. ``result_id`` keeps its name (the container id, for compatibility) and
    ``completed_at`` keeps its old meaning (when the one observation's value was entered) --
    ``observation_id``/``entry_completed_at`` are new, additive fields for the newly-relevant facts
    this call also atomically performs (recording the observation and completing the item's entry
    in one step)."""

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    item_id: uuid.UUID
    result_id: uuid.UUID
    observation_id: uuid.UUID
    result_type: str
    completed_at: datetime
    entry_completed_at: datetime
    order_status: str
    ready_for_release: bool


class LabObservationRecordRequest(BaseModel):
    """DH-EX-012C: one observation under an ordered item's result-entry container -- the granular,
    multi-observation-capable counterpart to ``LabResultSubmitRequest``. ``source_code``/
    ``display_label`` are caller-supplied and mandatory: ADR-047's hybrid-namespace identity design
    requires every observation to carry its own source/display identity, and DHealth never invents
    one on the caller's behalf (no LOINC dependency, no universal analyte ontology, no hard-coded
    panel content). ``source_system`` is deliberately not accepted here -- no external LIS/Connect
    source exists yet; every observation this route can produce is self-asserted by DHealth itself
    (see ``exchange/results.py``'s ``NATIVE_OBSERVATION_SOURCE_SYSTEM``), and a future Connect
    import path is free to introduce its own value once it exists. Value-shape validation mirrors
    ``LabResultSubmitRequest`` exactly -- see that model's own docstring for the full reasoning."""

    model_config = ConfigDict(extra="forbid")

    result_type: Literal["NUMERIC", "TEXT"]
    source_code: str = Field(min_length=1, max_length=64)
    display_label: str = Field(min_length=1, max_length=256)
    numeric_value: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    numeric_unit: str | None = Field(default=None, max_length=32)
    reference_range_low: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    reference_range_high: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    text_value: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _value_matches_declared_type(self) -> "LabObservationRecordRequest":
        if self.result_type == "NUMERIC":
            if self.numeric_value is None:
                raise ValueError("numeric_value is required when result_type is 'NUMERIC'")
            if self.text_value is not None:
                raise ValueError("text_value must not be set when result_type is 'NUMERIC'")
        else:
            if self.text_value is None or not self.text_value.strip():
                raise ValueError("text_value is required when result_type is 'TEXT'")
            if (
                self.numeric_value is not None
                or self.numeric_unit is not None
                or self.reference_range_low is not None
                or self.reference_range_high is not None
            ):
                raise ValueError("numeric fields must not be set when result_type is 'TEXT'")
        return self


class LabObservationResponse(BaseModel):
    """Shared by both the granular entry route and the DH-EX-013 correction route -- a freshly
    recorded observation always has ``supersedes_observation_id``/``superseded_at`` both ``None``;
    a correction's response has ``supersedes_observation_id`` set to the observation it replaced
    and ``superseded_at`` still ``None`` (it is itself effective, until a future correction of its
    own)."""

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    item_id: uuid.UUID
    result_id: uuid.UUID
    observation_id: uuid.UUID
    source_code: str
    source_system: str
    display_label_snapshot: str
    result_type: str
    numeric_value: Decimal | None
    numeric_unit: str | None
    reference_range_low: Decimal | None
    reference_range_high: Decimal | None
    text_value: str | None
    recorded_at: datetime
    recorded_by_identity_id: uuid.UUID | None
    supersedes_observation_id: uuid.UUID | None
    superseded_at: datetime | None


class LabObservationHistoryResponse(BaseModel):
    """DH-EX-013: every observation that has ever existed under this item's container, superseded
    and effective alike, ordered oldest to newest -- the one place a lab-authorized caller may see
    historical (non-effective) observations at all. Never returned by any provider-facing or
    release-facing route."""

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    item_id: uuid.UUID
    observations: list[LabObservationResponse]


class EntryCompletionResponse(BaseModel):
    """DH-EX-012C: the response to marking (or idempotently replaying) an ordered item's result
    entry complete -- deliberately narrow, matching `LabResultReleaseResponse`'s own precedent. No
    result values, no clinical content: confirms only that the workflow fact was recorded, by whom,
    and when."""

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    item_id: uuid.UUID
    entry_completed_at: datetime
    entry_completed_by_identity_id: uuid.UUID | None


class ProviderObservationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: uuid.UUID
    source_code: str
    source_system: str
    display_label_snapshot: str
    result_type: str
    numeric_value: Decimal | None
    numeric_unit: str | None
    reference_range_low: Decimal | None
    reference_range_high: Decimal | None
    text_value: str | None
    recorded_at: datetime


class ProviderResultItemResponse(BaseModel):
    """DH-EX-012C/ADR-047: nested, not flat -- one ordered item may now carry zero, one, or many
    observations, so a flat per-item scalar shape can no longer represent this honestly (ADR-047's
    own instruction against preserving a flat shape merely for compatibility once doing so makes
    panel results wrong). ``entry_completed_at`` is always non-``None`` here: this shape only ever
    appears inside ``ProviderOrderResultsResponse``, which is itself only ever returned once
    ``LabOrder.status == COMPLETED`` -- release (`LabResultReleaseRepository.release`) already
    proved every item's entry is complete before that transition is possible."""

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str
    entry_completed_at: datetime
    observations: list[ProviderObservationResponse]


class ProviderOrderResultsResponse(BaseModel):
    """Available only once ``LabOrder.status == COMPLETED`` -- ``get_lab_order_results`` refuses
    with a 409 before this is ever built for an order still in progress, so this shape never has
    to represent a partial-results state.

    **ADR-048:** ``release_version_id``/``release_version_number``/``is_amendment`` are all ``None``
    together for exactly one reason -- a grandfathered ``COMPLETED``-without-
    ``LabResultRelease`` order, which has no release version at all and is not amendment-eligible
    (Decision 6). For every other order they are populated from the latest immutable manifest;
    ``is_amendment`` is ``version_number > 1``. No clinical interpretation is added by any of the
    three."""

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    order_status: str
    release_version_id: uuid.UUID | None
    release_version_number: int | None
    is_amendment: bool | None
    results: list[ProviderResultItemResponse]


class LabResultInspectionObservationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: uuid.UUID
    source_code: str
    source_system: str
    display_label_snapshot: str
    result_type: str
    numeric_value: Decimal | None
    numeric_unit: str | None
    reference_range_low: Decimal | None
    reference_range_high: Decimal | None
    text_value: str | None
    recorded_at: datetime


class LabResultInspectionItemResponse(BaseModel):
    """DH-EX-012, reshaped by DH-EX-012C/DH-EX-013: this is the lab's own pre-release view, which
    must show what is still missing, not only what has already been entered -- ``entry_completed``/
    ``entry_completed_at`` are ``False``/``None`` and ``observations`` may be empty while entry is
    still in progress. ``observations`` is always the *effective* set only -- a superseded
    historical value never appears here; ``has_correction_history`` is a cheap boolean signal (not
    the full chain) that at least one correction has happened for this item -- fetch
    ``GET .../items/{item_id}/observations/history`` for the actual chain. No clinical
    interpretation: no normal/abnormal flag, no reference-range evaluation is computed here or
    anywhere in this codebase."""

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str
    entry_completed: bool
    entry_completed_at: datetime | None
    observations: list[LabResultInspectionObservationResponse]
    has_correction_history: bool


class LabResultInspectionResponse(BaseModel):
    """DH-EX-012: the selected lab's own operational read of an order's entered results, before
    release -- "a releaser must not be forced to release results they cannot inspect." Deliberately
    separate from provider delivery (`ProviderOrderResultsResponse`, gated on `COMPLETED`) and from
    the DH-EX-006 order-detail view (`LabWorkflowOrderResponse`, which carries no result values at
    all). DH-EX-012C: ``all_results_entered`` is renamed ``all_items_entry_complete`` -- the
    underlying fact materially changed (from "has a result row" to "entry explicitly marked
    complete, backed by at least one observation"), so this field keeps its old name only in
    concept, not in name, per this ticket's own instruction against silently repointing a field's
    meaning."""

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    order_status: str
    all_items_entry_complete: bool
    released: bool
    released_at: datetime | None
    results: list[LabResultInspectionItemResponse]


class LabResultReleaseResponse(BaseModel):
    """DH-EX-012/ADR-044: the response to releasing (or idempotently replaying) an order's result
    set -- deliberately narrow, matching `LabCollectionResponse`'s own precedent. No result values,
    no clinical content: confirms only that the workflow-authorization action was recorded, by
    whom, and when."""

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    released_by_identity_id: uuid.UUID
    released_at: datetime


class LabResultReleaseVersionResponse(BaseModel):
    """ADR-048 release-version metadata; ``is_amendment`` is true after Version 1."""

    model_config = ConfigDict(extra="forbid")

    release_version_id: uuid.UUID
    version_number: int
    lab_result_release_id: uuid.UUID | None
    amendment_id: uuid.UUID | None
    created_at: datetime
    is_amendment: bool


class LabResultReleaseVersionHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    versions: list[LabResultReleaseVersionResponse]


class ProviderReleaseVersionContentResponse(BaseModel):
    """DH-EX-014B: the exact, immutable content of one specific released version, by its own
    immutable id -- never derived from live current-effective state, identical historical-
    reconstruction semantic to ``get_manifest_for_version``. A provider retrieves this for any
    version in its own order's history, not only the latest."""

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    release_version_id: uuid.UUID
    release_version_number: int
    is_amendment: bool
    review_status: str
    reviewed_at: datetime | None
    reviewed_by_practitioner_reference_id: uuid.UUID | None
    results: list[ProviderResultItemResponse]


class ProviderReleaseVersionSummaryResponse(BaseModel):
    """Provider-visible workflow metadata for one immutable released version and its review."""

    model_config = ConfigDict(extra="forbid")

    release_version_id: uuid.UUID
    release_version_number: int
    is_amendment: bool
    released_at: datetime
    review_status: str
    reviewed_at: datetime | None
    reviewed_by_practitioner_reference_id: uuid.UUID | None


class ProviderReleaseVersionHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    versions: list[ProviderReleaseVersionSummaryResponse]


class AmendmentResponse(BaseModel):
    """DH-EX-014B/ADR-048: one amendment episode. No clinical content of any kind -- purely
    workflow/version metadata, matching ``LabResultReleaseVersionResponse``'s own convention."""

    model_config = ConfigDict(extra="forbid")

    amendment_id: uuid.UUID
    lab_order_id: uuid.UUID
    amendment_number: int
    base_release_version_id: uuid.UUID
    status: str
    started_by_identity_id: uuid.UUID
    started_at: datetime
    released_by_identity_id: uuid.UUID | None
    released_at: datetime | None


class AmendmentInspectionObservationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: uuid.UUID
    source_code: str
    source_system: str
    display_label_snapshot: str
    result_type: str
    numeric_value: Decimal | None
    numeric_unit: str | None
    reference_range_low: Decimal | None
    reference_range_high: Decimal | None
    text_value: str | None
    recorded_at: datetime
    amendment_id: uuid.UUID | None


class AmendmentInspectionItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str
    entry_completed_at: datetime
    observations: list[AmendmentInspectionObservationResponse]
    changed_in_this_amendment: bool


class AmendmentInspectionResponse(BaseModel):
    """DH-EX-014B: the lab-authorized-only pre-release-of-amendment read -- ``LAB_TECH``/
    ``LAB_RESULT_RELEASER`` only; never reachable by a provider or patient-bearer credential (no
    route exposes this shape to either)."""

    model_config = ConfigDict(extra="forbid")

    amendment: AmendmentResponse
    items: list[AmendmentInspectionItemResponse]
    ready_to_release: bool


class DoctorReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    release_version_id: uuid.UUID | None
    status: str
    reviewed_at: datetime | None
    reviewed_by_practitioner_reference_id: uuid.UUID | None


def build_exchange_router(
    *,
    session_factory: sessionmaker[Session],
    token_validator: TokenValidator,
    audit_writer: ExchangeAuditWriter,
    platform_admin_role: str,
    patient_access_ttl_hours: int = 168,
) -> APIRouter:
    identities = ExchangeIdentityRepository(session_factory)
    organizations = OrganizationRepository(session_factory)
    memberships = OrganizationMembershipRepository(session_factory)
    role_assignments = OrganizationRoleAssignmentRepository(session_factory)
    authz = AuthorizationService(organizations, memberships, role_assignments)
    security_audit = ExchangeSecurityAuditRepository()
    catalog = CatalogRepository(session_factory, organizations)
    directory = LabDirectoryProfileRepository(session_factory, organizations)
    patients = PatientReferenceRepository(session_factory)
    practitioners = PractitionerReferenceRepository(session_factory)
    orders = LabOrderRepository(session_factory, organizations, catalog)
    order_intents = LabOrderIntentRepository(session_factory, organizations, catalog)
    selections = LabSelectionRepository(session_factory, organizations)
    patient_access = PatientAccessGrantRepository(
        session_factory, ttl_hours=patient_access_ttl_hours
    )
    lab_workflow = LabWorkflowRepository(session_factory, organizations)
    results = LabResultRepository(session_factory)
    releases = LabResultReleaseRepository(session_factory)
    reviews = DoctorReviewRepository(session_factory)
    amendments = LabResultAmendmentRepository(session_factory)

    router = APIRouter(prefix="/exchange/v1", tags=["exchange"])

    def get_principal(
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthenticatedPrincipal:
        try:
            token = token_validator.validate_header(authorization)
        except TokenValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing credentials"
            ) from error

        record, created = identities.get_or_create(
            issuer=token.issuer,
            subject=token.subject,
            display_name=token.display_name,
            email=token.email,
        )
        if created:
            audit_writer.append(
                ExchangeAuditEvent(
                    event_type="identity_first_seen",
                    actor_identity_id=str(record.id),
                    subject_identity_id=str(record.id),
                    outcome="created",
                )
            )
        # Identity creation grants zero organization access on its own -- `platform_admin` comes
        # only from the token's own realm roles, never from anything stored in the Exchange DB.
        return AuthenticatedPrincipal(
            identity_id=record.id,
            issuer=record.issuer,
            subject=record.subject,
            platform_admin=token.has_role(platform_admin_role),
        )

    def get_patient_access(
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthenticatedPatientAccess:
        # DH-EX-005: deliberately does NOT call `token_validator` (the OIDC path) at all -- a
        # completely separate authentication mechanism, per the ticket's explicit "must be
        # separate from OIDC" instruction. An OIDC JWT presented here simply will not match any
        # stored `token_hash` (a JWT is a three-part, dot-separated, ~200+ char structure; a SHA-256
        # digest lookup of it just misses), so it fails closed the same generic way as any other
        # unknown token, with no special-casing needed to keep the two paths from cross-
        # contaminating. Every failure mode -- missing header, malformed scheme, unknown token,
        # expired, revoked -- collapses to this same 401, never distinguishing *why* to an
        # unauthenticated caller.
        if not authorization:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing credentials"
            )
        scheme, _, raw_token = authorization.partition(" ")
        raw_token = raw_token.strip()
        # DH-EX-005R: a bound on the bearer value itself, not just a transport-layer header-size
        # limit -- `secrets.token_urlsafe(32)` produces exactly 43 characters, so this is
        # generously sized for any legitimate token while still refusing to feed an unbounded,
        # attacker-controlled multi-megabyte string into hashing/DB lookup. This is DoS hardening,
        # not secret validation by obscurity: the same generic 401 as every other malformed/
        # unknown/expired/revoked token, never a distinct error revealing the bound exists.
        if (
            scheme.lower() != "bearer"
            or not raw_token
            or len(raw_token) > _MAX_PATIENT_BEARER_TOKEN_LENGTH
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing credentials"
            )
        grant = patient_access.validate_token(raw_token)
        if grant is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing credentials"
            )
        return AuthenticatedPatientAccess(
            grant_id=grant.id,
            lab_order_id=grant.lab_order_id,
            access_mode=grant.access_mode.value,
        )

    def _require_lab_selection_capability(access: AuthenticatedPatientAccess) -> None:
        # DH-EX-010A: a capability check, independent of and prior to any workflow-state check --
        # an `ORDER_READ` grant is refused here even on an order whose status would otherwise
        # *also* refuse a selection action, so this layer is never masked by (and never relies on)
        # "the status check will probably catch it anyway".
        if access.access_mode != PatientAccessMode.LAB_SELECTION.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="this patient access grant may only read the order, not select a lab",
            )

    def _audit_refused(
        principal: AuthenticatedPrincipal, organization_id: uuid.UUID | None
    ) -> None:
        # Best-effort, deliberately: refusal telemetry, not an authoritative mutation record (see
        # `ExchangeSecurityAuditEvent`'s docstring). A failure here must never turn an intended
        # 403/404 into an accidental 500 -- and, more importantly, must never be able to *block* a
        # refusal from happening; authorization always fails closed first, independent of whether
        # this write succeeds.
        try:
            audit_writer.append(
                ExchangeAuditEvent(
                    event_type="authorization_refused",
                    actor_identity_id=str(principal.identity_id),
                    organization_id=str(organization_id) if organization_id else None,
                    outcome="refused",
                )
            )
        except Exception:  # noqa: S110, BLE001 -- best-effort telemetry, never masks the refusal
            pass

    def _require_membership(
        principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        try:
            return authz.require_membership(principal, organization_id)
        except ResourceNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="organization not found"
            ) from error
        except NotAuthorized as error:
            _audit_refused(principal, organization_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="not authorized"
            ) from error

    def _require_org_admin(
        principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        try:
            return authz.require_org_admin(principal, organization_id)
        except ResourceNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="organization not found"
            ) from error
        except NotAuthorized as error:
            _audit_refused(principal, organization_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="not authorized"
            ) from error

    def _require_lab_tech(
        principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        # DH-EX-006: deliberately `authz.require_lab_tech`, never `require_org_admin`/
        # `require_role` -- platform admin must NOT bypass this one, mirroring
        # `_require_clinician` exactly (see `AuthorizationService.require_lab_tech`'s docstring).
        try:
            return authz.require_lab_tech(principal, organization_id)
        except ResourceNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="organization not found"
            ) from error
        except NotAuthorized as error:
            _audit_refused(principal, organization_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="not authorized"
            ) from error

    def _require_lab_result_releaser(
        principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        # DH-EX-012/ADR-044: deliberately `authz.require_lab_result_releaser`, never
        # `require_org_admin`/`require_role` -- platform admin must NOT bypass this one, mirroring
        # `_require_lab_tech` exactly (see `AuthorizationService.require_lab_result_releaser`'s
        # docstring).
        try:
            return authz.require_lab_result_releaser(principal, organization_id)
        except ResourceNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="organization not found"
            ) from error
        except NotAuthorized as error:
            _audit_refused(principal, organization_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="not authorized"
            ) from error

    def _require_lab_tech_or_releaser(
        principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        # DH-EX-012: the pre-release inspection read -- either role suffices, platform admin does
        # not bypass (see `AuthorizationService.require_lab_tech_or_result_releaser`'s docstring).
        try:
            return authz.require_lab_tech_or_result_releaser(principal, organization_id)
        except ResourceNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="organization not found"
            ) from error
        except NotAuthorized as error:
            _audit_refused(principal, organization_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="not authorized"
            ) from error

    def _require_network_read_access(principal: AuthenticatedPrincipal) -> None:
        """DH-EX-003: the gate for catalog/directory/marketplace reads -- not scoped to any single
        organization (see `AuthorizationService.require_any_active_membership`'s own docstring for
        why). A valid identity with zero memberships may still call `/me`, but not this."""
        try:
            authz.require_any_active_membership(principal)
        except NotAuthorized as error:
            _audit_refused(principal, None)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="an active organization membership is required",
            ) from error

    def _require_clinician(
        principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        # DH-EX-004: deliberately `authz.require_clinician`, never `require_org_admin`/
        # `require_role` -- platform admin must NOT bypass this one (see its docstring).
        try:
            return authz.require_clinician(principal, organization_id)
        except ResourceNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="organization not found"
            ) from error
        except NotAuthorized as error:
            _audit_refused(principal, organization_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="not authorized"
            ) from error

    def _require_idempotency_key(
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> str:
        # DH-EX-004R: header, never the JSON body -- a canonical UUIDv4, one value per *intended*
        # submission, and "canonical" is enforced literally by this regex, not just "parseable by
        # Python's uuid.UUID". `uuid.UUID(...)` itself is deliberately lenient -- it also accepts
        # braces (`{...}`), a `urn:uuid:` prefix, and any letter case, silently normalizing all of
        # them to the same canonical string. That leniency is exactly wrong for a header whose own
        # docstring already promised "canonical" as the contract: this endpoint must reject a
        # noncanonical representation outright (422), never quietly accept and reformat it. The
        # pattern below is RFC 4122 §3's textual representation restricted to version 4 (the
        # literal `4` nibble) and the RFC 4122 variant (`8`/`9`/`a`/`b`) -- lowercase hex only, no
        # braces, no prefix, no surrounding whitespace, and it structurally rejects v1/v3/v5 UUIDs
        # too (a syntactically valid but wrong-version UUID is still not this contract's key).
        if idempotency_key is None or not idempotency_key.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Idempotency-Key header is required",
            )
        if not _CANONICAL_UUID4_PATTERN.fullmatch(idempotency_key):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "Idempotency-Key must be a canonical lowercase UUIDv4 "
                    "(no braces, no urn: prefix, no surrounding whitespace)"
                ),
            )
        return idempotency_key

    @router.get("/me", response_model=MeResponse)
    def get_me(principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)]) -> MeResponse:
        identity = identities.get(principal.identity_id)
        summaries = []
        for membership in memberships.list_for_identity(principal.identity_id):
            org = organizations.get(membership.organization_id)
            held_roles = sorted(role_assignments.list_for_membership(membership.id))
            summaries.append(
                MembershipSummary(
                    organization_id=org.id,
                    organization_name=org.name,
                    organization_type=org.org_type,
                    organization_status=org.status,
                    membership_status=membership.status,
                    roles=held_roles,
                )
            )
        return MeResponse(
            identity_id=identity.id,
            issuer=identity.issuer,
            subject=identity.subject,
            display_name=identity.display_name,
            email=identity.email,
            platform_admin=principal.platform_admin,
            memberships=summaries,
        )

    @router.post(
        "/organizations", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED
    )
    def create_organization(
        body: OrganizationCreateRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> OrganizationResponse:
        try:
            authz.require_platform_admin(principal)
        except NotAuthorized as error:
            _audit_refused(principal, None)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="platform admin required"
            ) from error

        # DH-EX-002R: organization creation and its authoritative audit row commit or roll back
        # together, in one database transaction -- `session_factory.begin()` commits on normal
        # exit and rolls back the whole thing if anything inside raises, including the security
        # audit insert itself. `InvalidOrganizationType`/`ValueError` raise before any `session.add`
        # happens (see `OrganizationRepository.create`), so the rollback there is a no-op.
        try:
            with session_factory.begin() as session:
                record = organizations.create(
                    org_type=body.organization_type, name=body.name, session=session
                )
                security_audit.record(
                    session,
                    event_type="organization_created",
                    actor_identity_id=principal.identity_id,
                    organization_id=record.id,
                    outcome="created",
                )
        except (InvalidOrganizationType, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error

        return OrganizationResponse(
            id=record.id, name=record.name, organization_type=record.org_type, status=record.status
        )

    @router.get("/organizations/{organization_id}", response_model=OrganizationResponse)
    def get_organization(
        organization_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> OrganizationResponse:
        context = _require_membership(principal, organization_id)
        org = context.organization
        return OrganizationResponse(
            id=org.id, name=org.name, organization_type=org.org_type, status=org.status
        )

    @router.get(
        "/organizations/{organization_id}/memberships", response_model=list[MembershipResponse]
    )
    def list_memberships(
        organization_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> list[MembershipResponse]:
        _require_org_admin(principal, organization_id)
        results = []
        for membership in memberships.list_for_organization(organization_id):
            identity = identities.get(membership.identity_id)
            held_roles = sorted(role_assignments.list_for_membership(membership.id))
            results.append(
                MembershipResponse(
                    membership_id=membership.id,
                    identity_id=identity.id,
                    display_name=identity.display_name,
                    email=identity.email,
                    status=membership.status,
                    roles=held_roles,
                )
            )
        return results

    @router.post("/organizations/{organization_id}/memberships", response_model=MembershipResponse)
    def create_membership(
        organization_id: uuid.UUID,
        body: MembershipCreateRequest,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> MembershipResponse:
        context = _require_org_admin(principal, organization_id)

        try:
            target_identity = identities.get(body.identity_id)
        except IdentityNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="identity has not authenticated yet",
            ) from error

        # Validate every requested role against the org-type matrix *before* creating anything,
        # so a bad role list in a multi-role request 422s cleanly with no membership or role
        # committed -- never a partial grant left behind by a mid-loop failure.
        org_type = OrganizationType(context.organization.org_type)
        allowed_roles = ALLOWED_ROLES_BY_ORG_TYPE[org_type]
        for role_name in body.roles:
            if OrganizationRole(role_name) not in allowed_roles:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"role {role_name!r} is not valid for a {org_type.value!r} organization",
                )

        # DH-EX-002R: membership creation, every role assignment, and their authoritative audit
        # rows all commit or roll back together as ONE database transaction. Any exception raised
        # inside -- including the security audit insert itself -- rolls back everything, so a
        # request can never leave a partial membership/role grant with no matching audit row, or
        # an audit row with no matching grant.
        try:
            with session_factory.begin() as session:
                membership, membership_created = memberships.create(
                    organization_id=organization_id, identity_id=target_identity.id, session=session
                )

                # An existing INACTIVE membership is never silently reactivated by a re-POST --
                # that is a distinct, explicit action this ticket's endpoint set does not
                # implement. This is a refusal, not a mutation: nothing new was actually inserted
                # (the attempted insert already resolved to the existing row via the repository's
                # own SAVEPOINT-scoped duplicate-key handling), so there is nothing to roll back
                # except an empty transaction, and no authoritative audit row is written for it --
                # only best-effort file telemetry, outside this transaction (see below).
                if (
                    not membership_created
                    and membership.status != OrganizationMembershipStatus.ACTIVE.value
                ):
                    raise _InactiveMembershipReplay(membership.id)

                security_audit.record(
                    session,
                    event_type="membership_created",
                    actor_identity_id=principal.identity_id,
                    organization_id=organization_id,
                    subject_identity_id=target_identity.id,
                    membership_id=membership.id,
                    outcome="created" if membership_created else "returned_existing",
                )

                for role_name in body.roles:
                    _, role_created = role_assignments.assign(
                        membership_id=membership.id,
                        role=OrganizationRole(role_name),
                        session=session,
                    )
                    security_audit.record(
                        session,
                        event_type="role_assigned",
                        actor_identity_id=principal.identity_id,
                        organization_id=organization_id,
                        subject_identity_id=target_identity.id,
                        membership_id=membership.id,
                        role=role_name,
                        outcome="created" if role_created else "returned_existing",
                    )
        except _InactiveMembershipReplay as error:
            try:  # best-effort telemetry, never allowed to turn this 409 into a 500
                audit_writer.append(
                    ExchangeAuditEvent(
                        event_type="membership_reactivation_refused",
                        actor_identity_id=str(principal.identity_id),
                        subject_identity_id=str(target_identity.id),
                        organization_id=str(organization_id),
                        outcome="refused",
                    )
                )
            except Exception:  # noqa: S110, BLE001 -- best-effort telemetry, never masks the 409
                pass
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="membership exists but is inactive; reactivation is not supported here",
            ) from error
        except (InvalidRoleForOrganizationType, MembershipInactive) as error:
            # Defense-in-depth only -- roles are pre-validated above and the membership was just
            # confirmed ACTIVE, so reaching here means a genuine race (e.g. concurrent
            # deactivation), not bad input. The whole transaction, including the membership
            # creation above, has already been rolled back by this point.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        response.status_code = status.HTTP_201_CREATED if membership_created else status.HTTP_200_OK
        held_roles = sorted(role_assignments.list_for_membership(membership.id))
        return MembershipResponse(
            membership_id=membership.id,
            identity_id=target_identity.id,
            display_name=target_identity.display_name,
            email=target_identity.email,
            status=membership.status,
            roles=held_roles,
        )

    # -- DH-EX-003: catalog -----------------------------------------------------------------------

    @router.get("/catalog/tests", response_model=list[CatalogEntryResponse])
    def list_catalog_tests(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> list[CatalogEntryResponse]:
        _require_network_read_access(principal)
        # DH-EX-003R: an ordinary network member sees ACTIVE + SYNTHETIC entries only -- an
        # inactive or non-synthetic row must never look selectable/approved merely because it
        # exists in the DB. Platform admin gets the unfiltered inspection view.
        entries = catalog.list_entries(active_synthetic_only=not principal.platform_admin)
        return [_to_catalog_response(entry) for entry in entries]

    @router.post(
        "/catalog/tests", response_model=CatalogEntryResponse, status_code=status.HTTP_201_CREATED
    )
    def create_catalog_test(
        body: CatalogEntryCreateRequest,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> CatalogEntryResponse:
        # Platform admin only -- creating a DHealth canonical catalog entry is platform work, not
        # something a lab admin does (a lab admin instead *offers* an existing catalog entry, via
        # POST .../offerings below).
        try:
            authz.require_platform_admin(principal)
        except NotAuthorized as error:
            _audit_refused(principal, None)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="platform admin required"
            ) from error

        try:
            with session_factory.begin() as session:
                # `is_synthetic=True` is hardcoded, never taken from the request body -- DH-EX-003
                # explicitly forbids a client submitting `is_synthetic=false` through this API.
                record, created = catalog.create_entry(
                    code=body.code,
                    display_name=body.display_name,
                    version=body.version,
                    is_synthetic=True,
                    session=session,
                )
                security_audit.record(
                    session,
                    event_type="catalog_entry_created",
                    actor_identity_id=principal.identity_id,
                    outcome="created" if created else "returned_existing",
                    resource_type="catalog_entry",
                    resource_id=record.id,
                )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        except CatalogEntryConflict as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return _to_catalog_response(record)

    # -- DH-EX-003: lab directory ---------------------------------------------------------------

    @router.get("/labs", response_model=list[LabDirectoryListingResponse])
    def list_labs(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> list[LabDirectoryListingResponse]:
        _require_network_read_access(principal)
        return [_to_listing_response(listing) for listing in directory.list_listed()]

    @router.get("/labs/{lab_id}", response_model=LabDirectoryListingResponse)
    def get_lab(
        lab_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabDirectoryListingResponse:
        _require_network_read_access(principal)
        # Platform admin may inspect an unlisted lab (onboarding/admin purposes); a normal member
        # never sees one through this endpoint -- an unlisted lab and a nonexistent one both 404.
        listing = directory.get_listing_for_lab(lab_id, include_unlisted=principal.platform_admin)
        if listing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lab not found")
        return _to_listing_response(listing)

    @router.put(
        "/organizations/{lab_id}/directory-profile", response_model=LabDirectoryProfileResponse
    )
    def put_directory_profile(
        lab_id: uuid.UUID,
        body: LabDirectoryProfileRequest,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabDirectoryProfileResponse:
        _require_org_admin(principal, lab_id)

        try:
            with session_factory.begin() as session:
                record, created, changed = directory.upsert(
                    lab_organization_id=lab_id,
                    public_address=body.public_address,
                    latitude=body.latitude,
                    longitude=body.longitude,
                    public_phone=body.public_phone,
                    listed=body.listed,
                    accepting_orders=body.accepting_orders,
                    session=session,
                )
                event_type = (
                    "lab_directory_profile_created" if created else "lab_directory_profile_updated"
                )
                outcome = "created" if created else ("updated" if changed else "unchanged")
                security_audit.record(
                    session,
                    event_type=event_type,
                    actor_identity_id=principal.identity_id,
                    organization_id=lab_id,
                    outcome=outcome,
                    resource_type="lab_directory_profile",
                    resource_id=record.id,
                )
        except InvalidOrganizationType as error:
            # `lab_id` is not an ACTIVE LAB organization -- e.g. a PROVIDER org's own admin
            # calling this on their own organization. A business-rule 422, not a 403: the caller
            # legitimately administers `lab_id`, the endpoint just does not apply to its type.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error

        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return _to_profile_response(record)

    # -- DH-EX-003: lab offerings ---------------------------------------------------------------

    @router.get("/organizations/{lab_id}/offerings", response_model=list[LabOfferingResponse])
    def list_lab_offerings(
        lab_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> list[LabOfferingResponse]:
        _require_org_admin(principal, lab_id)
        return [_to_offering_response(o) for o in catalog.list_offerings_for_lab(lab_id)]

    @router.post(
        "/organizations/{lab_id}/offerings",
        response_model=LabOfferingResponse,
    )
    def create_lab_offering(
        lab_id: uuid.UUID,
        body: LabOfferingCreateRequest,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabOfferingResponse:
        _require_org_admin(principal, lab_id)

        try:
            with session_factory.begin() as session:
                record, created = catalog.create_offering(
                    lab_organization_id=lab_id,
                    catalog_entry_id=body.catalog_entry_id,
                    local_test_code=body.local_test_code,
                    session=session,
                )
                security_audit.record(
                    session,
                    event_type="lab_offering_created",
                    actor_identity_id=principal.identity_id,
                    organization_id=lab_id,
                    outcome="created" if created else "returned_existing",
                    resource_type="lab_offering",
                    resource_id=record.id,
                )
        except InvalidOrganizationType as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        except CatalogEntryNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="catalog entry not found"
            ) from error
        except OfferingInactiveConflict as error:
            # Checked before the parent `OfferingConflict` -- this is a subclass, and except
            # clauses are matched in order, so the more specific one must come first.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="offering exists but is inactive; reactivate it explicitly instead",
            ) from error
        except OfferingConflict as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return _to_offering_response(record)

    @router.patch(
        "/organizations/{lab_id}/offerings/{offering_id}", response_model=LabOfferingResponse
    )
    def update_lab_offering_status(
        lab_id: uuid.UUID,
        offering_id: uuid.UUID,
        body: LabOfferingStatusUpdateRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabOfferingResponse:
        _require_org_admin(principal, lab_id)
        _existing_offering_for_lab_or_404(offering_id, lab_id)

        with session_factory.begin() as session:
            record, outcome = catalog.set_offering_active(offering_id, body.active, session=session)
            security_audit.record(
                session,
                event_type="lab_offering_status_changed",
                actor_identity_id=principal.identity_id,
                organization_id=lab_id,
                outcome=outcome,
                resource_type="lab_offering",
                resource_id=record.id,
            )
        return _to_offering_response(record)

    # -- DH-EX-003: pricing -----------------------------------------------------------------------

    @router.post(
        "/organizations/{lab_id}/offerings/{offering_id}/prices", response_model=LabPriceResponse
    )
    def set_lab_offering_price(
        lab_id: uuid.UUID,
        offering_id: uuid.UUID,
        body: LabPriceSetRequest,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabPriceResponse:
        _require_org_admin(principal, lab_id)
        _existing_offering_for_lab_or_404(offering_id, lab_id)

        try:
            with session_factory.begin() as session:
                record, outcome = catalog.set_price(
                    offering_id=offering_id,
                    amount=body.amount,
                    currency=body.currency,
                    expected_current_price_id=body.expected_current_price_id,
                    session=session,
                )
                security_audit.record(
                    session,
                    event_type="lab_price_changed",
                    actor_identity_id=principal.identity_id,
                    organization_id=lab_id,
                    outcome=outcome,
                    resource_type="lab_price",
                    resource_id=record.id,
                )
        except PriceConflict as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        response.status_code = (
            status.HTTP_201_CREATED if outcome == "created" else status.HTTP_200_OK
        )
        return _to_price_response(record)

    def _existing_offering_for_lab_or_404(offering_id: uuid.UUID, lab_id: uuid.UUID):
        # Never trust `offering_id` alone -- it must actually belong to `lab_id`, the tenant the
        # caller was just proven to administer. An offering belonging to a *different* lab looks
        # exactly like a nonexistent one from here, matching the anti-enumeration policy.
        try:
            existing = catalog.get_offering(offering_id)
        except OfferingNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="offering not found"
            ) from error
        if existing.lab_organization_id != lab_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="offering not found")
        return existing

    # -- DH-EX-003: marketplace (read-only query, never mutates order state) --------------------

    def _require_orderable_entries(catalog_entry_ids: list[uuid.UUID]) -> None:
        # Fail closed on the whole request rather than silently dropping one requested test --
        # every entry must exist, be ACTIVE, and (this pilot phase) be SYNTHETIC.
        for entry_id in catalog_entry_ids:
            try:
                entry = catalog.get_entry(entry_id)
            except CatalogEntryNotFound as error:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"catalog entry {entry_id} not found",
                ) from error
            if not entry.active:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"catalog entry {entry_id} is not active",
                )
            if not entry.is_synthetic:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"catalog entry {entry_id} is not a synthetic pilot-phase entry",
                )

    def _build_marketplace_response(
        catalog_entry_ids: list[uuid.UUID],
    ) -> MarketplaceLabOptionsResponse:
        labs = catalog.find_whole_order_eligible_labs(catalog_entry_ids)
        options = [
            MarketplaceLabOption(
                lab=MarketplaceLabIdentity(
                    id=lab.lab_organization_id, name=lab.lab_organization_name
                ),
                directory=MarketplaceLabDirectory(
                    public_address=lab.public_address,
                    latitude=lab.latitude,
                    longitude=lab.longitude,
                    public_phone=lab.public_phone,
                ),
                prices=[
                    MarketplacePriceLine(
                        catalog_entry_id=line.catalog_entry_id,
                        catalog_code=line.catalog_code,
                        display_name=line.display_name,
                        offering_id=line.offering_id,
                        current_price_id=line.current_price_id,
                        amount=line.amount,
                        currency=line.currency,
                    )
                    for line in lab.prices
                ],
                total_amount=lab.total_amount,
                currency=lab.currency,
            )
            for lab in labs
        ]
        return MarketplaceLabOptionsResponse(as_of=datetime.now(UTC), options=options)

    @router.post("/marketplace/lab-options", response_model=MarketplaceLabOptionsResponse)
    def marketplace_lab_options(
        body: MarketplaceLabOptionsRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> MarketplaceLabOptionsResponse:
        _require_network_read_access(principal)
        _require_orderable_entries(body.catalog_entry_ids)
        return _build_marketplace_response(body.catalog_entry_ids)

    # -- DH-EX-009: provider-scoped patient-reference binding ------------------------------------

    @router.post(
        "/organizations/{provider_id}/patient-references",
        response_model=PatientReferenceResponse,
    )
    def bind_patient_reference(
        provider_id: uuid.UUID,
        body: PatientReferenceBindRequest,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> PatientReferenceResponse:
        # CLINICIAN specifically, same gate and same reasoning as `create_lab_order` below --
        # binding the patient a clinician is about to order for is part of that same clinical
        # ordering authority, not a separate receptionist/integration role this ticket invents.
        _require_clinician(principal, provider_id)

        # Exact-identity, insert-first-wins binding, and its authoritative audit event, commit or
        # roll back together in one transaction -- the same pattern `create_lab_order` already
        # establishes for its own practitioner-reference get/create and order-creation audit.
        with session_factory.begin() as session:
            record, created = patients.get_or_create_with_outcome(
                source_organization_id=provider_id,
                source_system=body.source_system,
                source_identity_system=body.source_identity_system,
                source_identity_value=body.source_identity_value,
                display_name=body.display_name,
                contact_channel_type=body.contact_channel_type,
                contact_value=body.contact_value,
                session=session,
            )
            security_audit.record(
                session,
                event_type="patient_reference_bound",
                actor_identity_id=principal.identity_id,
                organization_id=provider_id,
                outcome="created" if created else "returned_existing",
                resource_type="patient_reference",
                resource_id=record.id,
            )

        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return PatientReferenceResponse(
            id=record.id,
            source_organization_id=record.source_organization_id,
            source_system=record.source_system,
            source_identity_system=record.source_identity_system,
            source_identity_value=record.source_identity_value,
        )

    # -- DH-CLINIC-002: provider-scoped patient lookup -------------------------------------------

    @router.get(
        "/organizations/{provider_id}/patient-references",
        response_model=list[ProviderPatientReferenceSummary],
    )
    def list_patient_references(
        provider_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[ProviderPatientReferenceSummary]:
        _require_clinician(principal, provider_id)
        records = patients.list_for_organization(provider_id, limit=limit)
        return [
            ProviderPatientReferenceSummary(
                patient_reference_id=rec.id,
                display_name=rec.display_name,
                contact_channel_type=rec.contact_channel_type,
                contact_value=rec.contact_value,
                local_reference=rec.source_identity_value,
            )
            for rec in records
        ]

    @router.post(
        "/organizations/{provider_id}/patient-references/search",
        response_model=list[ProviderPatientReferenceSummary],
    )
    def search_patient_references(
        provider_id: uuid.UUID,
        body: ProviderPatientReferenceSearchRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> list[ProviderPatientReferenceSummary]:
        _require_clinician(principal, provider_id)
        records = patients.list_for_organization(provider_id, query=body.query, limit=body.limit)
        return [
            ProviderPatientReferenceSummary(
                patient_reference_id=rec.id,
                display_name=rec.display_name,
                contact_channel_type=rec.contact_channel_type,
                contact_value=rec.contact_value,
                local_reference=rec.source_identity_value,
            )
            for rec in records
        ]

    @router.get(
        "/organizations/{provider_id}/patient-references/{reference_id}",
        response_model=ProviderPatientReferenceSummary,
    )
    def get_patient_reference(
        provider_id: uuid.UUID,
        reference_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> ProviderPatientReferenceSummary:
        _require_clinician(principal, provider_id)
        record = patients.get_for_organization(reference_id, provider_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="patient reference not found"
            )
        return ProviderPatientReferenceSummary(
            patient_reference_id=record.id,
            display_name=record.display_name,
            contact_channel_type=record.contact_channel_type,
            contact_value=record.contact_value,
            local_reference=record.source_identity_value,
        )

    # -- DH-EX-004: provider lab-order creation --------------------------------------------------

    @router.post(
        "/organizations/{provider_id}/orders",
        response_model=LabOrderResponse,
    )
    def create_lab_order(
        provider_id: uuid.UUID,
        body: LabOrderCreateRequest,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
        idempotency_key: Annotated[str, Depends(_require_idempotency_key)],
    ) -> LabOrderResponse:
        # CLINICIAN specifically -- an ORG_ADMIN who does not also hold CLINICIAN, a LAB_TECH, a
        # LAB org's ORG_ADMIN, and platform admin acting alone are all refused here, never just
        # untested (see `AuthorizationService.require_clinician`'s docstring for why platform
        # admin does not bypass this one check).
        _require_clinician(principal, provider_id)

        # Tenant-scoped, read-only pre-validation -- outside any write transaction, matching the
        # marketplace endpoint's own pattern -- before anything is mutated. A patient reference
        # that exists but belongs to a *different* provider looks exactly like a nonexistent one
        # (anti-enumeration; never discloses "that patient belongs to someone else").
        if patients.get_for_organization(body.patient_reference_id, provider_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="patient reference not found"
            )

        # Fail closed on the whole request rather than silently dropping one requested test --
        # every entry must exist, be ACTIVE, and (this pilot phase) be SYNTHETIC. Duplicate ids
        # are already rejected at the schema layer (`LabOrderCreateRequest._no_duplicate_entries`).
        for entry_id in body.catalog_entry_ids:
            try:
                entry = catalog.get_entry(entry_id)
            except CatalogEntryNotFound as error:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"catalog entry {entry_id} not found",
                ) from error
            if not entry.active:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"catalog entry {entry_id} is not active",
                )
            if not entry.is_synthetic:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"catalog entry {entry_id} is not a synthetic pilot-phase entry",
                )

        identity = identities.get(principal.identity_id)

        # DH-EX-004: the ordering practitioner-reference get/create, the LabOrder aggregate
        # (order + items + PENDING DoctorReview + PENDING ResultDelivery), and the authoritative
        # `lab_order_created` audit row all commit or roll back together, in one database
        # transaction. A duplicate idempotency-key race, an idempotency conflict, or an audit
        # failure all leave nothing behind -- including no orphaned practitioner reference, since
        # its own insert is part of this same transaction, not a separate prior commit.
        try:
            with session_factory.begin() as session:
                practitioner = practitioners.get_or_create(
                    source_organization_id=provider_id,
                    source_system=_CLINICIAN_PRACTITIONER_SOURCE_SYSTEM,
                    source_identity_system=_CLINICIAN_PRACTITIONER_SOURCE_IDENTITY_SYSTEM,
                    source_identity_value=str(principal.identity_id),
                    display_name=identity.display_name,
                    session=session,
                )
                # DH-CLINIC-003C: a prepared durable intent reserves this
                # provider/key.  The retained legacy endpoint may only consume
                # that exact canonical intent; it must never create different
                # clinical content under the same idempotency identity.
                reserved_intent = order_intents.get_by_idempotency_key(
                    provider_id, idempotency_key, session=session
                )
                if reserved_intent is not None:
                    if (
                        reserved_intent.patient_reference_id != body.patient_reference_id
                        or reserved_intent.practitioner_reference_id != practitioner.id
                        or {item.catalog_entry_id for item in reserved_intent.items}
                        != set(body.catalog_entry_ids)
                    ):
                        raise IdempotencyKeyConflict(
                            "idempotency key is reserved by an immutable order intent with "
                            "different canonical content"
                        )
                    result = order_intents.commit(
                        provider_id, reserved_intent.id, orders, session=session
                    )
                else:
                    # All new creation, including this legacy-compatible route,
                    # first acquires the single durable provider/key reservation.
                    # The intent table's unique constraint is therefore the one
                    # owner under concurrent legacy and native-Clinic requests.
                    reservation = order_intents.create(
                        idempotency_key=idempotency_key,
                        provider_organization_id=provider_id,
                        patient_reference_id=body.patient_reference_id,
                        practitioner_reference_id=practitioner.id,
                        catalog_entry_ids=body.catalog_entry_ids,
                        session=session,
                    )
                    result = order_intents.commit(
                        provider_id, reservation.intent.id, orders, session=session
                    )
                # Exact idempotent replay must never emit a second `lab_order_created` row -- the
                # original mutation was already audited.
                if result.created:
                    security_audit.record(
                        session,
                        event_type="lab_order_created",
                        actor_identity_id=principal.identity_id,
                        organization_id=provider_id,
                        outcome="created",
                        resource_type="lab_order",
                        resource_id=result.order.id,
                    )
        except (InvalidOrganizationType, ValueError, EmptyOrder) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        except IdempotencyKeyConflict as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
        return _to_order_response(result.order)

    @router.get("/organizations/{provider_id}/orders", response_model=list[LabOrderSummaryResponse])
    def list_lab_orders(
        provider_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[LabOrderSummaryResponse]:
        # DH-EX-004R: `limit` is a real, bounded query parameter -- `ge=1, le=100` rejects 0,
        # negative values, and anything above the documented maximum with a clean 422, rather than
        # silently ignoring an out-of-range value or passing it through to an unbounded query.
        _require_clinician(principal, provider_id)
        return [
            _to_order_summary_response(order)
            for order in orders.list_for_organization(provider_id, limit=limit)
        ]

    @router.get(
        "/organizations/{provider_id}/orders/by-idempotency-key/{idempotency_key}",
        response_model=LabOrderResponse,
    )
    def get_order_by_idempotency_key(
        provider_id: uuid.UUID,
        idempotency_key: Annotated[str, Path(min_length=1, max_length=256)],
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabOrderResponse:
        """Lookup an existing LabOrder by its client-supplied idempotency key.

        DH-CLINIC-003B: Clinician authorization is checked strictly before key lookup.
        Anti-enumeration: returns 404 when key is unknown or belongs to another provider.

        IMPORTANT SAFETY SEMANTICS (404 is not proof of absence):
        A 404 indicates only that no committed LabOrder was found for this
        (provider_id, idempotency_key) pair at the time of the query. It does NOT
        prove that an earlier in-flight POST request never reached the server or
        will never commit (e.g. concurrent transaction, network delay). Callers
        must not interpret a 404 as permission to abandon an unconfirmed attempt
        or mint a new idempotency key for the same diagnostic intent.
        """
        _require_clinician(principal, provider_id)
        try:
            order = orders.get_by_idempotency_key(provider_id, idempotency_key)
        except LabOrderNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        return _to_order_response(order)

    @router.get("/organizations/{provider_id}/orders/{order_id}", response_model=LabOrderResponse)
    def get_lab_order(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabOrderResponse:
        _require_clinician(principal, provider_id)
        order = orders.get_for_organization(order_id, provider_id)
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
        return _to_order_response(order)

    @router.post(
        "/organizations/{provider_id}/order-intents",
        response_model=LabOrderIntentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_order_intent(
        provider_id: uuid.UUID,
        body: LabOrderIntentCreateRequest,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
        idempotency_key: Annotated[str, Depends(_require_idempotency_key)],
    ) -> LabOrderIntentResponse:
        """DH-CLINIC-003C: Prepare and persist an immutable diagnostic order intent.

        Validates provider scope, patient reference existence within provider, active synthetic
        catalog entries, and derives practitioner from authenticated clinician.
        Enforces provider-scoped idempotency: exact match returns existing intent (200 OK),
        mismatched payload for same key returns 409 Conflict.
        """
        _require_clinician(principal, provider_id)

        if patients.get_for_organization(body.patient_reference_id, provider_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="patient reference not found"
            )

        for entry_id in body.catalog_entry_ids:
            try:
                entry = catalog.get_entry(entry_id)
            except CatalogEntryNotFound as error:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"catalog entry {entry_id} not found",
                ) from error
            if not entry.active:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"catalog entry {entry_id} is not active",
                )
            if not entry.is_synthetic:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"catalog entry {entry_id} is not a synthetic pilot-phase entry",
                )

        identity = identities.get(principal.identity_id)

        try:
            with session_factory.begin() as session:
                practitioner = practitioners.get_or_create(
                    source_organization_id=provider_id,
                    source_system=_CLINICIAN_PRACTITIONER_SOURCE_SYSTEM,
                    source_identity_system=_CLINICIAN_PRACTITIONER_SOURCE_IDENTITY_SYSTEM,
                    source_identity_value=str(principal.identity_id),
                    display_name=identity.display_name,
                    session=session,
                )
                result = order_intents.create(
                    idempotency_key=idempotency_key,
                    provider_organization_id=provider_id,
                    patient_reference_id=body.patient_reference_id,
                    practitioner_reference_id=practitioner.id,
                    catalog_entry_ids=body.catalog_entry_ids,
                    session=session,
                )
        except (InvalidOrganizationType, ValueError, EmptyOrder) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        except IdempotencyKeyConflict as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
        return _to_intent_response(result.intent)

    @router.get(
        "/organizations/{provider_id}/order-intents/by-idempotency-key/{idempotency_key}",
        response_model=LabOrderIntentResponse,
    )
    def get_order_intent_by_idempotency_key(
        provider_id: uuid.UUID,
        idempotency_key: Annotated[str, Path(min_length=1, max_length=256)],
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabOrderIntentResponse:
        """DH-CLINIC-003C: Lookup an existing LabOrderIntent by its idempotency key.

        Enforces clinician authorization, provider isolation, and anti-enumeration (404 on miss).
        """
        _require_clinician(principal, provider_id)
        intent = order_intents.get_by_idempotency_key(provider_id, idempotency_key)
        if intent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order intent not found"
            )
        caller = practitioners.get_or_create(
            source_organization_id=provider_id,
            source_system=_CLINICIAN_PRACTITIONER_SOURCE_SYSTEM,
            source_identity_system=_CLINICIAN_PRACTITIONER_SOURCE_IDENTITY_SYSTEM,
            source_identity_value=str(principal.identity_id),
            display_name=identities.get(principal.identity_id).display_name,
        )
        if caller.id != intent.practitioner_reference_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order intent not found")
        return _to_intent_response(intent)

    @router.get(
        "/organizations/{provider_id}/order-intents/{intent_id}",
        response_model=LabOrderIntentResponse,
    )
    def get_order_intent(
        provider_id: uuid.UUID,
        intent_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabOrderIntentResponse:
        """DH-CLINIC-003C: Lookup an existing LabOrderIntent by intent_id.

        Enforces clinician authorization, provider isolation, and anti-enumeration (404 on miss).
        """
        _require_clinician(principal, provider_id)
        intent = order_intents.get_for_organization(provider_id, intent_id)
        if intent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order intent not found"
            )
        caller = practitioners.get_or_create(
            source_organization_id=provider_id,
            source_system=_CLINICIAN_PRACTITIONER_SOURCE_SYSTEM,
            source_identity_system=_CLINICIAN_PRACTITIONER_SOURCE_IDENTITY_SYSTEM,
            source_identity_value=str(principal.identity_id),
            display_name=identities.get(principal.identity_id).display_name,
        )
        if caller.id != intent.practitioner_reference_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order intent not found")
        return _to_intent_response(intent)

    @router.post(
        "/organizations/{provider_id}/order-intents/{intent_id}/commit",
        response_model=LabOrderResponse,
    )
    def commit_order_intent(
        provider_id: uuid.UUID,
        intent_id: uuid.UUID,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabOrderResponse:
        """DH-CLINIC-003C: Commit an immutable backend LabOrderIntent into an actual LabOrder.

        Enforces:
        - Clinician authorization for provider
        - Intent must exist under provider
        - Caller must be the author clinician (practitioner reference matches)
        - Row-level locking ensures concurrent commits converge cleanly to exactly one LabOrder
        - Audits lab_order_created on creation
        """
        _require_clinician(principal, provider_id)
        identity = identities.get(principal.identity_id)

        try:
            with session_factory.begin() as session:
                intent_record = order_intents.get_for_organization(
                    provider_id, intent_id, session=session
                )
                if intent_record is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND, detail="order intent not found"
                    )

                caller_practitioner = practitioners.get_or_create(
                    source_organization_id=provider_id,
                    source_system=_CLINICIAN_PRACTITIONER_SOURCE_SYSTEM,
                    source_identity_system=_CLINICIAN_PRACTITIONER_SOURCE_IDENTITY_SYSTEM,
                    source_identity_value=str(principal.identity_id),
                    display_name=identity.display_name,
                    session=session,
                )
                if caller_practitioner.id != intent_record.practitioner_reference_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="only the ordering clinician who authored this intent may commit it",
                    )

                result = order_intents.commit(provider_id, intent_id, orders, session=session)
                if result.created:
                    security_audit.record(
                        session,
                        event_type="lab_order_created",
                        actor_identity_id=principal.identity_id,
                        organization_id=provider_id,
                        outcome="created",
                        resource_type="lab_order",
                        resource_id=result.order.id,
                    )
        except OrderIntentNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order intent not found"
            ) from error
        except IdempotencyKeyConflict as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
        return _to_order_response(result.order)

    @router.get(
        "/organizations/{provider_id}/patient-references/{patient_reference_id}/orders",
        response_model=list[LabOrderResponse],
    )
    def list_patient_orders(
        provider_id: uuid.UUID,
        patient_reference_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[LabOrderResponse]:
        # DH-CLINIC-003: clinician authorization checked strictly before patient lookup
        # to prevent leaking patient reference existence across tenant boundaries.
        _require_clinician(principal, provider_id)
        if patients.get_for_organization(patient_reference_id, provider_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="patient reference not found"
            )
        return [
            _to_order_response(order)
            for order in orders.list_for_patient(provider_id, patient_reference_id, limit=limit)
        ]

    # -- DH-EX-010: provider/facility-assigned lab routing (ADR-043 launch option (a)) -----------

    @router.post(
        "/organizations/{provider_id}/orders/{order_id}/assign-lab",
        response_model=LabAssignmentResponse,
    )
    def assign_lab_to_order(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        body: AssignLabRequest,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabAssignmentResponse:
        # CLINICIAN specifically -- the same gate and reasoning as `create_lab_order`: routing an
        # order to a lab is part of the same clinical ordering authority, not a separate
        # receptionist/dispatcher role this ticket invents.
        _require_clinician(principal, provider_id)

        # The provider's own tenant boundary is re-verified *inside* `assign_by_provider` itself
        # (against the locked order row), not only here -- see that method's own docstring for
        # why a pre-route read alone is not enough.
        try:
            with session_factory.begin() as session:
                record, created = selections.assign_by_provider(
                    lab_order_id=order_id,
                    lab_organization_id=body.lab_organization_id,
                    provider_organization_id=provider_id,
                    session=session,
                )
                # Idempotent replay (already assigned to this same lab, by either workflow): no
                # mutation happened, so no second authoritative audit row -- matching DH-EX-006's
                # accept/reject/start-processing convention for this exact situation.
                if created:
                    security_audit.record(
                        session,
                        event_type="lab_order_assigned",
                        actor_identity_id=principal.identity_id,
                        organization_id=provider_id,
                        outcome="updated",
                        resource_type="lab_order",
                        resource_id=order_id,
                    )
        except LabOrderNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except InvalidOrderTransition as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except DifferentLabAlreadySelected as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except LabCannotFulfillOrder as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return LabAssignmentResponse(
            lab_order_id=order_id,
            lab_organization_id=record.lab_organization_id,
            selected_at=record.selected_at,
        )

    # -- DH-EX-005: provider-issued secure patient order access ----------------------------------

    def _existing_order_for_provider_or_404(order_id: uuid.UUID, provider_id: uuid.UUID):
        order = orders.get_for_organization(order_id, provider_id)
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
        return order

    @router.post(
        "/organizations/{provider_id}/orders/{order_id}/patient-access",
        response_model=PatientAccessIssueResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def issue_patient_access(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> PatientAccessIssueResponse:
        # Patient access is clinical order access -- the exact same CLINICIAN boundary as
        # creating/reading the order itself, never platform-admin-or-ORG_ADMIN-alone.
        response.headers["Cache-Control"] = "no-store"
        _require_clinician(principal, provider_id)
        _existing_order_for_provider_or_404(order_id, provider_id)

        try:
            with session_factory.begin() as session:
                issued = patient_access.issue(lab_order_id=order_id, session=session)
                security_audit.record(
                    session,
                    event_type="patient_access_issued",
                    actor_identity_id=principal.identity_id,
                    organization_id=provider_id,
                    outcome="created",
                    resource_type="patient_access_grant",
                    resource_id=issued.id,
                )
        except GrantOrderNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except OrderNotIssuable as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ActiveGrantAlreadyExists as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        return PatientAccessIssueResponse(
            access_token=issued.raw_token,
            expires_at=issued.expires_at,
            access_mode=issued.access_mode.value,
        )

    @router.delete(
        "/organizations/{provider_id}/orders/{order_id}/patient-access",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def revoke_patient_access(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> None:
        _require_clinician(principal, provider_id)
        _existing_order_for_provider_or_404(order_id, provider_id)

        try:
            with session_factory.begin() as session:
                outcome, grant_id = patient_access.revoke(lab_order_id=order_id, session=session)
                # Idempotent: revoking an order with no active grant is a safe no-op -- never a
                # second `patient_access_revoked` audit row for the same underlying fact.
                if outcome == "revoked":
                    security_audit.record(
                        session,
                        event_type="patient_access_revoked",
                        actor_identity_id=principal.identity_id,
                        organization_id=provider_id,
                        outcome="updated",
                        resource_type="patient_access_grant",
                        resource_id=grant_id,
                    )
        except GrantOrderNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        return None

    # -- DH-EX-005: patient API -- order-scoped by the bearer grant alone, never a path/body id ----

    def _patient_order_response(lab_order_id: uuid.UUID) -> PatientOrderResponse:
        order = orders.get(lab_order_id)
        provider = organizations.get(order.provider_organization_id)
        selection_record = selections.get_for_order(lab_order_id)
        selection_response: PatientSelectionReceiptResponse | None = None
        if selection_record is not None:
            lab = organizations.get(selection_record.lab_organization_id)
            listing = directory.get_listing_for_lab(
                selection_record.lab_organization_id, include_unlisted=True
            )
            # DH-EX-010: provenance -- not price presence -- is the authoritative signal for
            # which workflow produced this selection. A provider assignment never writes a price
            # snapshot, so `total_amount`/`currency` must stay `null`, never an implied `0 DJF`.
            is_patient_driven = selection_record.selected_via_patient_access_grant_id is not None
            priced_items = [
                item
                for item in order.items
                if item.selected_price_amount is not None
                and item.selected_price_currency is not None
            ]
            if is_patient_driven:
                total = sum(
                    (item.selected_price_amount for item in priced_items), start=Decimal("0")
                )
                currency = priced_items[0].selected_price_currency if priced_items else None
            else:
                # A non-patient-driven selection reports no items either, even on the
                # (HTTP-unreachable today) legacy/internal-caller row shape that has real frozen
                # prices but no grant -- `items`/`total_amount`/`currency` must agree with each
                # other, never itemized prices next to a null total.
                total = None
                currency = None
                priced_items = []
            selection_response = PatientSelectionReceiptResponse(
                lab_organization_id=lab.id,
                lab_organization_name=lab.name,
                public_address=listing.public_address if listing else None,
                latitude=listing.latitude if listing else None,
                longitude=listing.longitude if listing else None,
                public_phone=listing.public_phone if listing else None,
                selected_at=selection_record.selected_at,
                assignment_source="PATIENT" if is_patient_driven else "PROVIDER",
                items=[
                    PatientSelectionReceiptItemResponse(
                        catalog_code_snapshot=item.catalog_code_snapshot,
                        catalog_display_name_snapshot=item.catalog_display_name_snapshot,
                        selected_price_amount=item.selected_price_amount,
                        selected_price_currency=item.selected_price_currency,
                    )
                    for item in priced_items
                ],
                total_amount=total,
                currency=currency,
            )
        return PatientOrderResponse(
            status=order.status,
            requested_tests=[
                PatientOrderItemResponse(
                    catalog_code_snapshot=item.catalog_code_snapshot,
                    catalog_display_name_snapshot=item.catalog_display_name_snapshot,
                )
                for item in order.items
            ],
            ordering_provider_name=provider.name,
            selection=selection_response,
        )

    @router.get("/patient/order", response_model=PatientOrderResponse)
    def get_patient_order(
        response: Response,
        access: Annotated[AuthenticatedPatientAccess, Depends(get_patient_access)],
    ) -> PatientOrderResponse:
        response.headers["Cache-Control"] = "no-store"
        return _patient_order_response(access.lab_order_id)

    @router.get("/patient/order/lab-options", response_model=MarketplaceLabOptionsResponse)
    def get_patient_lab_options(
        response: Response,
        access: Annotated[AuthenticatedPatientAccess, Depends(get_patient_access)],
    ) -> MarketplaceLabOptionsResponse:
        response.headers["Cache-Control"] = "no-store"
        _require_lab_selection_capability(access)
        order = orders.get(access.lab_order_id)
        if order.status != LabOrderStatus.AWAITING_LAB_SELECTION.value:
            # Fails closed for every later status too (ACCEPTED/IN_PROGRESS/COMPLETED/REJECTED/
            # CANCELLED) -- no selection is possible once the order has moved past this stage, and
            # a SELECTED order's real answer lives in the frozen receipt, not a fresh marketplace
            # read.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="order is not awaiting lab selection",
            )
        # The exact requested catalog set is derived from the order itself -- the patient grant
        # can never be used to browse the network or query an arbitrary test set.
        entry_ids = [item.catalog_entry_id for item in order.items]
        _require_orderable_entries(entry_ids)
        return _build_marketplace_response(entry_ids)

    @router.post("/patient/order/selection", response_model=PatientOrderResponse)
    def create_patient_selection(
        body: PatientSelectionRequest,
        response: Response,
        access: Annotated[AuthenticatedPatientAccess, Depends(get_patient_access)],
    ) -> PatientOrderResponse:
        response.headers["Cache-Control"] = "no-store"
        _require_lab_selection_capability(access)
        try:
            _record, created = selections.select(
                lab_order_id=access.lab_order_id,
                lab_organization_id=body.lab_organization_id,
                expected_price_ids=frozenset(body.expected_price_ids),
                patient_access_grant_id=access.grant_id,
            )
        except LabOrderNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except InvalidOrderTransition as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except DifferentLabAlreadySelected as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except LabAlreadyAssignedByProvider as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except PatientAccessGrantLacksSelectionAuthority as error:
            # Defense-in-depth only: `_require_lab_selection_capability` above already refuses
            # this on the normal HTTP path before `selections.select()` is ever called. Reachable
            # only if a future caller of `select()` skips that route-level check.
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        except LabCannotFulfillOrder as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except StalePriceSelection as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except PatientAccessGrantInvalidated as error:
            # Discovered *inside* the mutation transaction, after the `get_patient_access`
            # dependency's own (already-closed) check passed -- same generic 401 as every other
            # invalid-grant response, never distinguishing "it was fine a moment ago" from any
            # other unauthenticated-caller-facing failure mode.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing credentials"
            ) from error

        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return _patient_order_response(access.lab_order_id)

    # -- DH-EX-006: selected-lab assigned-order workflow ------------------------------------------

    def _lab_workflow_order_response(record: LabWorkflowOrderRecord) -> LabWorkflowOrderResponse:
        provider = organizations.get(record.provider_organization_id)
        patient = patients.get(record.patient_reference_id)
        return LabWorkflowOrderResponse(
            order_id=record.id,
            status=record.status,
            selected_at=record.selected_at,
            updated_at=record.updated_at,
            patient_display_name=patient.display_name,
            ordering_provider_name=provider.name,
            requested_tests=[
                LabWorkflowOrderItemResponse(
                    catalog_code_snapshot=item.catalog_code_snapshot,
                    catalog_display_name_snapshot=item.catalog_display_name_snapshot,
                    selected_price_amount=item.selected_price_amount,
                    selected_price_currency=item.selected_price_currency,
                )
                for item in record.items
            ],
            collection_recorded_at=record.collection_recorded_at,
        )

    @router.get("/organizations/{lab_id}/lab-orders", response_model=list[LabWorkflowOrderResponse])
    def list_lab_workflow_orders(
        lab_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        order_status: Annotated[LabOrderStatus | None, Query(alias="status")] = None,
    ) -> list[LabWorkflowOrderResponse]:
        _require_lab_tech(principal, lab_id)
        records = lab_workflow.list_for_lab(lab_id, limit=limit, status_filter=order_status)
        return [_lab_workflow_order_response(record) for record in records]

    @router.get(
        "/organizations/{lab_id}/lab-orders/{order_id}", response_model=LabWorkflowOrderResponse
    )
    def get_lab_workflow_order(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabWorkflowOrderResponse:
        _require_lab_tech(principal, lab_id)
        record = lab_workflow.get_for_lab(order_id, lab_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
        return _lab_workflow_order_response(record)

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/accept",
        response_model=LabWorkflowOrderResponse,
    )
    def accept_lab_order(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabWorkflowOrderResponse:
        _require_lab_tech(principal, lab_id)
        try:
            with session_factory.begin() as session:
                record, transitioned = lab_workflow.accept(
                    order_id=order_id, lab_organization_id=lab_id, session=session
                )
                # Idempotent replay (already ACCEPTED): no mutation happened, so no second
                # authoritative audit row -- one real transition, one event, matching every other
                # replay-safe mutation in this package (issue/revoke/selection).
                if transitioned:
                    security_audit.record(
                        session,
                        event_type="lab_order_accepted",
                        actor_identity_id=principal.identity_id,
                        organization_id=lab_id,
                        outcome="updated",
                        resource_type="lab_order",
                        resource_id=order_id,
                    )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except InvalidLabWorkflowTransition as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return _lab_workflow_order_response(record)

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/reject",
        response_model=LabWorkflowOrderResponse,
    )
    def reject_lab_order(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabWorkflowOrderResponse:
        _require_lab_tech(principal, lab_id)
        try:
            with session_factory.begin() as session:
                record, transitioned = lab_workflow.reject(
                    order_id=order_id, lab_organization_id=lab_id, session=session
                )
                if transitioned:
                    security_audit.record(
                        session,
                        event_type="lab_order_rejected",
                        actor_identity_id=principal.identity_id,
                        organization_id=lab_id,
                        outcome="updated",
                        resource_type="lab_order",
                        resource_id=order_id,
                    )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except InvalidLabWorkflowTransition as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return _lab_workflow_order_response(record)

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/start-processing",
        response_model=LabWorkflowOrderResponse,
    )
    def start_processing_lab_order(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabWorkflowOrderResponse:
        _require_lab_tech(principal, lab_id)
        try:
            with session_factory.begin() as session:
                record, transitioned = lab_workflow.start_processing(
                    order_id=order_id, lab_organization_id=lab_id, session=session
                )
                if transitioned:
                    security_audit.record(
                        session,
                        event_type="lab_order_processing_started",
                        actor_identity_id=principal.identity_id,
                        organization_id=lab_id,
                        outcome="updated",
                        resource_type="lab_order",
                        resource_id=order_id,
                    )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except InvalidLabWorkflowTransition as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return _lab_workflow_order_response(record)

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/record-collection",
        response_model=LabCollectionResponse,
    )
    def record_lab_order_collection(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabCollectionResponse:
        # DH-EX-011: records exactly one fact -- this LAB_TECH explicitly advanced the
        # order-level sample/specimen collection workflow milestone. No request body: the order
        # comes from the URL, the lab from the URL/auth context, the actor from authentication,
        # and the timestamp from the server clock -- never a caller-supplied value.
        _require_lab_tech(principal, lab_id)
        try:
            with session_factory.begin() as session:
                record, created = lab_workflow.record_collection(
                    order_id=order_id, lab_organization_id=lab_id, session=session
                )
                if created:
                    security_audit.record(
                        session,
                        event_type="lab_sample_collection_recorded",
                        actor_identity_id=principal.identity_id,
                        organization_id=lab_id,
                        outcome="created",
                        resource_type="lab_order",
                        resource_id=order_id,
                    )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except InvalidLabWorkflowTransition as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return LabCollectionResponse(order_id=record.lab_order_id, recorded_at=record.recorded_at)

    # -- DH-EX-007: lab result recording, order completion, provider result access, doctor review

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}/result",
        response_model=LabResultSubmitResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def submit_lab_result(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        item_id: uuid.UUID,
        body: LabResultSubmitRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabResultSubmitResponse:
        # DH-EX-012C: the backward-compatible single-observation convenience path -- atomically
        # creates the container, records exactly one observation, and completes the item's entry,
        # in one call. Refuses outright (`DuplicateResult`) if the item already has any
        # observation/completion content; use the granular observation/completion endpoints below
        # for anything beyond the simple one-observation case. Does NOT auto-release the order --
        # ADR-044's explicit release step is unaffected.
        _require_lab_tech(principal, lab_id)
        try:
            with session_factory.begin() as session:
                outcome: ResultSubmissionOutcome
                if body.result_type == "NUMERIC":
                    outcome = results.record_numeric(
                        order_id=order_id,
                        lab_order_item_id=item_id,
                        lab_organization_id=lab_id,
                        value=body.numeric_value,
                        recorded_by_identity_id=principal.identity_id,
                        unit=body.numeric_unit,
                        reference_range_low=body.reference_range_low,
                        reference_range_high=body.reference_range_high,
                        session=session,
                    )
                else:
                    outcome = results.record_text(
                        order_id=order_id,
                        lab_order_item_id=item_id,
                        lab_organization_id=lab_id,
                        value=body.text_value or "",
                        recorded_by_identity_id=principal.identity_id,
                        session=session,
                    )
                # Both events commit or roll back together with the observation/container insert
                # and the completion write -- added to the identical `session` this transaction
                # already owns, never a separate commit. DH-EX-012/ADR-044: `lab_order_completed`
                # is still not written here -- recording/completing a result entry never completes
                # the order by itself; see `release_lab_order_results` below for where
                # `lab_order_completed` fires, atomically with the release itself.
                security_audit.record(
                    session,
                    event_type="lab_observation_recorded",
                    actor_identity_id=principal.identity_id,
                    organization_id=lab_id,
                    outcome="created",
                    resource_type="lab_order",
                    resource_id=order_id,
                )
                security_audit.record(
                    session,
                    event_type="lab_result_entry_completed",
                    actor_identity_id=principal.identity_id,
                    organization_id=lab_id,
                    outcome="created",
                    resource_type="lab_order",
                    resource_id=order_id,
                )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except InvalidResultSubmissionState as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except OrderItemNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order item not found"
            ) from error
        except DuplicateResult as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        return LabResultSubmitResponse(
            order_id=order_id,
            item_id=item_id,
            result_id=outcome.observation.lab_result_id,
            observation_id=outcome.observation.id,
            result_type=outcome.observation.result_type,
            completed_at=outcome.observation.recorded_at,
            entry_completed_at=outcome.entry_completed_at,
            order_status=outcome.order_status,
            ready_for_release=outcome.ready_for_release,
        )

    def _observation_response(
        order_id: uuid.UUID, item_id: uuid.UUID, observation
    ) -> LabObservationResponse:
        return LabObservationResponse(
            order_id=order_id,
            item_id=item_id,
            result_id=observation.lab_result_id,
            observation_id=observation.id,
            source_code=observation.source_code,
            source_system=observation.source_system,
            display_label_snapshot=observation.display_label_snapshot,
            result_type=observation.result_type,
            numeric_value=observation.numeric_value,
            numeric_unit=observation.numeric_unit,
            reference_range_low=observation.reference_range_low,
            reference_range_high=observation.reference_range_high,
            text_value=observation.text_value,
            recorded_at=observation.recorded_at,
            recorded_by_identity_id=observation.recorded_by_identity_id,
            supersedes_observation_id=observation.supersedes_observation_id,
            superseded_at=observation.superseded_at,
        )

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}/observations",
        response_model=LabObservationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def record_lab_observation(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        item_id: uuid.UUID,
        body: LabObservationRecordRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabObservationResponse:
        # DH-EX-012C/ADR-047: the granular, multi-observation-capable entry path -- may be called
        # any number of times for the same item while its entry is not yet completed. Never
        # completes anything itself; see `complete_lab_result_entry` below for the one place
        # `entry_completed_at` is ever set.
        _require_lab_tech(principal, lab_id)
        try:
            with session_factory.begin() as session:
                observation = results.record_observation(
                    order_id=order_id,
                    lab_order_item_id=item_id,
                    lab_organization_id=lab_id,
                    result_type=LabResultType(body.result_type),
                    source_code=body.source_code,
                    display_label_snapshot=body.display_label,
                    recorded_by_identity_id=principal.identity_id,
                    numeric_value=body.numeric_value,
                    numeric_unit=body.numeric_unit,
                    reference_range_low=body.reference_range_low,
                    reference_range_high=body.reference_range_high,
                    text_value=body.text_value,
                    session=session,
                )
                security_audit.record(
                    session,
                    event_type="lab_observation_recorded",
                    actor_identity_id=principal.identity_id,
                    organization_id=lab_id,
                    outcome="created",
                    resource_type="lab_order",
                    resource_id=order_id,
                )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except InvalidResultSubmissionState as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except OrderItemNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order item not found"
            ) from error
        except EntryAlreadyCompleted as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        return _observation_response(order_id, item_id, observation)

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}/complete-result-entry",
        response_model=EntryCompletionResponse,
    )
    def complete_lab_result_entry(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        item_id: uuid.UUID,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> EntryCompletionResponse:
        # DH-EX-012C/ADR-047: the single explicit signal that this item's result entry is
        # finished -- never inferred from observation count. LAB_TECH only, deliberately not
        # LAB_RESULT_RELEASER -- entry completion and release are separate authorities, exactly
        # mirroring ADR-044's own "entry and release are separate" framing at the item level
        # instead of the order level. No request body: actor from authentication, item/order/lab
        # from the URL, time from the server clock. Idempotent: a retry after the first successful
        # completion returns the identical original fact, never overwriting it.
        _require_lab_tech(principal, lab_id)
        try:
            with session_factory.begin() as session:
                record, created = results.complete_entry(
                    order_id=order_id,
                    lab_order_item_id=item_id,
                    lab_organization_id=lab_id,
                    completed_by_identity_id=principal.identity_id,
                    session=session,
                )
                if created:
                    security_audit.record(
                        session,
                        event_type="lab_result_entry_completed",
                        actor_identity_id=principal.identity_id,
                        organization_id=lab_id,
                        outcome="created",
                        resource_type="lab_order",
                        resource_id=order_id,
                    )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except InvalidResultSubmissionState as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except OrderItemNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order item not found"
            ) from error
        except EntryNotReadyForCompletion as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return EntryCompletionResponse(
            order_id=order_id,
            item_id=item_id,
            entry_completed_at=record.entry_completed_at,
            entry_completed_by_identity_id=record.entry_completed_by_identity_id,
        )

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}"
        "/observations/{observation_id}/correct",
        response_model=LabObservationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def correct_lab_observation(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        item_id: uuid.UUID,
        observation_id: uuid.UUID,
        body: LabObservationRecordRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabObservationResponse:
        # DH-EX-013/ADR-045 (pre-release correction only): supersedes the currently effective
        # `observation_id` with one new immutable observation -- never mutates the original row's
        # value. LAB_TECH only, matching entry authority exactly (ADR-045's Decision 2 --
        # LAB_RESULT_RELEASER is not required merely to correct an unreleased observation). No
        # reason field accepted (ADR-045's Decision 3 -- no mandatory correction reason for MVP,
        # and no invented closed reason codes). Permitted whether or not the item's entry is
        # already complete; refused outright once the order is released -- see
        # `results.correct_observation`'s own docstring for why the same LabOrder-locked
        # IN_PROGRESS check already used by every other entry mutation is what enforces this.
        _require_lab_tech(principal, lab_id)
        try:
            with session_factory.begin() as session:
                successor = results.correct_observation(
                    order_id=order_id,
                    lab_order_item_id=item_id,
                    lab_organization_id=lab_id,
                    observation_id=observation_id,
                    result_type=LabResultType(body.result_type),
                    source_code=body.source_code,
                    display_label_snapshot=body.display_label,
                    corrected_by_identity_id=principal.identity_id,
                    numeric_value=body.numeric_value,
                    numeric_unit=body.numeric_unit,
                    reference_range_low=body.reference_range_low,
                    reference_range_high=body.reference_range_high,
                    text_value=body.text_value,
                    session=session,
                )
                # Atomic with the correction/supersession write -- same transaction, same
                # `session_factory.begin()`, matching `lab_result_entry_completed`'s own
                # established atomicity discipline exactly.
                security_audit.record(
                    session,
                    event_type="lab_observation_corrected",
                    actor_identity_id=principal.identity_id,
                    organization_id=lab_id,
                    outcome="created",
                    resource_type="lab_order",
                    resource_id=order_id,
                )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except InvalidResultSubmissionState as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ObservationNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="observation not found"
            ) from error
        except StaleObservationVersion as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        return _observation_response(order_id, item_id, successor)

    @router.get(
        "/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}/observations/history",
        response_model=LabObservationHistoryResponse,
    )
    def get_lab_observation_history(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        item_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabObservationHistoryResponse:
        # DH-EX-013: the one supported way to reconstruct a correction chain -- every observation
        # ever recorded under this item, superseded and effective alike. Same auth as the ordinary
        # pre-release inspection read (either LAB_TECH or LAB_RESULT_RELEASER); tenant ownership is
        # proven the same way `get_lab_order_results_for_release` does, by requiring the item's own
        # order to belong to this exact lab before returning anything.
        _require_lab_tech_or_releaser(principal, lab_id)
        inspection = results.get_for_lab(order_id, lab_id)
        if inspection is None or not any(item.item_id == item_id for item in inspection.items):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
        history = results.get_correction_history(item_id)
        if history is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order item not found"
            )
        return LabObservationHistoryResponse(
            order_id=order_id,
            item_id=item_id,
            observations=[
                _observation_response(order_id, item_id, observation)
                for observation in history.observations
            ],
        )

    @router.get(
        "/organizations/{lab_id}/lab-orders/{order_id}/results",
        response_model=LabResultInspectionResponse,
    )
    def get_lab_order_results_for_release(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabResultInspectionResponse:
        # DH-EX-012: the selected lab's own pre-release operational read -- NOT provider delivery
        # (that is `get_lab_order_results` below, gated on COMPLETED). Either LAB_TECH or
        # LAB_RESULT_RELEASER may inspect: a releaser must be able to see what they are about to
        # release, and the tech who entered results must be able to see their own work.
        _require_lab_tech_or_releaser(principal, lab_id)
        record: LabResultInspectionRecord | None = results.get_for_lab(order_id, lab_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
        return LabResultInspectionResponse(
            order_id=record.order_id,
            order_status=record.order_status,
            all_items_entry_complete=record.all_items_entry_complete,
            released=record.released,
            released_at=record.released_at,
            results=[
                LabResultInspectionItemResponse(
                    item_id=item.item_id,
                    catalog_code_snapshot=item.catalog_code_snapshot,
                    catalog_display_name_snapshot=item.catalog_display_name_snapshot,
                    entry_completed=item.entry_completed_at is not None,
                    entry_completed_at=item.entry_completed_at,
                    has_correction_history=item.has_correction_history,
                    observations=[
                        LabResultInspectionObservationResponse(
                            observation_id=observation.id,
                            source_code=observation.source_code,
                            source_system=observation.source_system,
                            display_label_snapshot=observation.display_label_snapshot,
                            result_type=observation.result_type,
                            numeric_value=observation.numeric_value,
                            numeric_unit=observation.numeric_unit,
                            reference_range_low=observation.reference_range_low,
                            reference_range_high=observation.reference_range_high,
                            text_value=observation.text_value,
                            recorded_at=observation.recorded_at,
                        )
                        for observation in item.observations
                    ],
                )
                for item in record.items
            ],
        )

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/release-results",
        response_model=LabResultReleaseResponse,
    )
    def release_lab_order_results(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabResultReleaseResponse:
        # DH-EX-012/ADR-044: the sole supported production way to move a LabOrder from
        # IN_PROGRESS to COMPLETED. No request body: actor from authentication, lab/order from the
        # URL, time from the server clock. LAB_RESULT_RELEASER only -- LAB_TECH alone (without
        # also holding LAB_RESULT_RELEASER) is refused, matching ADR-044's explicit "entry and
        # release are separate authorities" binding decision.
        _require_lab_result_releaser(principal, lab_id)
        try:
            with session_factory.begin() as session:
                record, created = releases.release(
                    order_id=order_id,
                    lab_organization_id=lab_id,
                    released_by_identity_id=principal.identity_id,
                    session=session,
                )
                # `lab_results_released` and `lab_order_completed` commit or roll back together
                # with the release insert and the order's own status write, all in the identical
                # `session` this transaction already owns. Actor is the releaser -- a semantic
                # change from DH-EX-007, where `lab_order_completed`'s actor was whoever submitted
                # the final result (see PROJECT_STATE.md's DH-EX-012 entry).
                if created:
                    security_audit.record(
                        session,
                        event_type="lab_results_released",
                        actor_identity_id=principal.identity_id,
                        organization_id=lab_id,
                        outcome="created",
                        resource_type="lab_order",
                        resource_id=order_id,
                    )
                    security_audit.record(
                        session,
                        event_type="lab_order_completed",
                        actor_identity_id=principal.identity_id,
                        organization_id=lab_id,
                        outcome="updated",
                        resource_type="lab_order",
                        resource_id=order_id,
                    )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except LegacyCompletedOrderHasNoRelease as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ResultsNotReadyForRelease as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return LabResultReleaseResponse(
            order_id=record.lab_order_id,
            released_by_identity_id=record.released_by_identity_id,
            released_at=record.released_at,
        )

    def _release_version_response(
        version: LabResultReleaseVersionRecord,
    ) -> LabResultReleaseVersionResponse:
        return LabResultReleaseVersionResponse(
            release_version_id=version.id,
            version_number=version.version_number,
            lab_result_release_id=version.lab_result_release_id,
            amendment_id=version.amendment_id,
            created_at=version.created_at,
            is_amendment=version.version_number > 1,
        )

    @router.get(
        "/organizations/{lab_id}/lab-orders/{order_id}/release-versions",
        response_model=LabResultReleaseVersionHistoryResponse,
    )
    def get_lab_order_release_version_history(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabResultReleaseVersionHistoryResponse:
        # DH-EX-014B/ADR-048: lab-side Version 1 / Version 2 / Version 3 history metadata.
        # `LAB_TECH` or `LAB_RESULT_RELEASER`, exactly mirroring the pre-release inspection
        # read's own authorization; `get_for_lab` is reused purely for its existing non-enumerating
        # tenant check (order doesn't exist vs. belongs to a different lab are never
        # distinguished), not for its own result content.
        _require_lab_tech_or_releaser(principal, lab_id)
        inspection = results.get_for_lab(order_id, lab_id)
        if inspection is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
        history = releases.get_release_version_history(order_id)
        return LabResultReleaseVersionHistoryResponse(
            order_id=order_id,
            versions=[_release_version_response(version) for version in history],
        )

    def _amendment_response(record: LabResultAmendmentRecord) -> AmendmentResponse:
        return AmendmentResponse(
            amendment_id=record.id,
            lab_order_id=record.lab_order_id,
            amendment_number=record.amendment_number,
            base_release_version_id=record.base_release_version_id,
            status=record.status,
            started_by_identity_id=record.started_by_identity_id,
            started_at=record.started_at,
            released_by_identity_id=record.released_by_identity_id,
            released_at=record.released_at,
        )

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/amendments",
        response_model=AmendmentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def start_lab_order_amendment(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> AmendmentResponse:
        # DH-EX-014B/ADR-048 Model 3/§8: LAB_TECH only, matching entry/pre-release-correction
        # authority exactly. No request body: actor from authentication, order/lab from the URL,
        # amendment number/base version/time all server-assigned, never caller-supplied. Starting
        # an amendment never changes provider-visible results, never creates a release version,
        # and never creates a DoctorReview row -- all of that happens only at release.
        _require_lab_tech(principal, lab_id)
        try:
            with session_factory.begin() as session:
                record = amendments.start_amendment(
                    order_id=order_id,
                    lab_organization_id=lab_id,
                    started_by_identity_id=principal.identity_id,
                    session=session,
                )
                security_audit.record(
                    session,
                    event_type="lab_result_amendment_started",
                    actor_identity_id=principal.identity_id,
                    organization_id=lab_id,
                    outcome="created",
                    resource_type="lab_order",
                    resource_id=order_id,
                )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except OrderNotCompletedForAmendment as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except LegacyCompletedOrderHasNoRelease as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except AmendmentAlreadyInProgress as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return _amendment_response(record)

    @router.get(
        "/organizations/{lab_id}/lab-orders/{order_id}/amendments/{amendment_id}",
        response_model=AmendmentInspectionResponse,
    )
    def get_lab_order_amendment(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        amendment_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> AmendmentInspectionResponse:
        # DH-EX-014B: either LAB_TECH or LAB_RESULT_RELEASER may inspect -- "a releaser must not
        # be forced to release an amendment they cannot inspect," identical framing to the
        # original pre-release inspection read. `get_for_lab` reused purely for its existing
        # non-enumerating tenant check, matching `get_lab_order_release_version_history`'s own
        # convention. Provider and patient-bearer credentials have no route to this shape at all.
        _require_lab_tech_or_releaser(principal, lab_id)
        tenant_check = results.get_for_lab(order_id, lab_id)
        if tenant_check is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
        try:
            inspection: AmendmentInspectionRecord | None = amendments.get_amendment_inspection(
                amendment_id
            )
        except AmendmentNotReadyForRelease as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        if inspection is None or inspection.amendment.lab_order_id != order_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="amendment not found")
        return AmendmentInspectionResponse(
            amendment=_amendment_response(inspection.amendment),
            items=[
                AmendmentInspectionItemResponse(
                    item_id=item.item_id,
                    catalog_code_snapshot=item.catalog_code_snapshot,
                    catalog_display_name_snapshot=item.catalog_display_name_snapshot,
                    entry_completed_at=item.entry_completed_at,
                    observations=[
                        AmendmentInspectionObservationResponse(
                            observation_id=observation.id,
                            source_code=observation.source_code,
                            source_system=observation.source_system,
                            display_label_snapshot=observation.display_label_snapshot,
                            result_type=observation.result_type,
                            numeric_value=observation.numeric_value,
                            numeric_unit=observation.numeric_unit,
                            reference_range_low=observation.reference_range_low,
                            reference_range_high=observation.reference_range_high,
                            text_value=observation.text_value,
                            recorded_at=observation.recorded_at,
                            amendment_id=observation.amendment_id,
                        )
                        for observation in item.observations
                    ],
                    changed_in_this_amendment=item.changed_in_this_amendment,
                )
                for item in inspection.items
            ],
            ready_to_release=inspection.ready_to_release,
        )

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/amendments/{amendment_id}"
        "/items/{item_id}/observations/{observation_id}/correct",
        response_model=LabObservationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def correct_lab_order_amendment_observation(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        amendment_id: uuid.UUID,
        item_id: uuid.UUID,
        observation_id: uuid.UUID,
        body: LabObservationRecordRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabObservationResponse:
        # DH-EX-014B/ADR-048 Decision 4: immediate/permanent revision -- LAB_TECH only, no reason
        # field anywhere (Decision 5). Never mutates the predecessor's value; supersedes it with
        # one new immutable observation carrying this exact amendment's id. This is explicitly NOT
        # the DH-EX-013 pre-release correction endpoint -- that one's IN_PROGRESS gate is
        # completely untouched and unaffected by this one's existence.
        _require_lab_tech(principal, lab_id)
        try:
            with session_factory.begin() as session:
                successor = amendments.correct_observation(
                    amendment_id=amendment_id,
                    order_id=order_id,
                    lab_order_item_id=item_id,
                    lab_organization_id=lab_id,
                    observation_id=observation_id,
                    result_type=LabResultType(body.result_type),
                    source_code=body.source_code,
                    display_label_snapshot=body.display_label,
                    corrected_by_identity_id=principal.identity_id,
                    numeric_value=body.numeric_value,
                    numeric_unit=body.numeric_unit,
                    reference_range_low=body.reference_range_low,
                    reference_range_high=body.reference_range_high,
                    text_value=body.text_value,
                    session=session,
                )
                security_audit.record(
                    session,
                    event_type="lab_observation_amended",
                    actor_identity_id=principal.identity_id,
                    organization_id=lab_id,
                    outcome="created",
                    resource_type="lab_order",
                    resource_id=order_id,
                )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except OrderNotCompletedForAmendment as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except AmendmentNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="amendment not found"
            ) from error
        except AmendmentNotPreparing as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except OrderItemNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order item not found"
            ) from error
        except ObservationNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="observation not found"
            ) from error
        except StaleObservationVersion as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ObservationNotEligibleForAmendment as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return _observation_response(order_id, item_id, successor)

    @router.post(
        "/organizations/{lab_id}/lab-orders/{order_id}/amendments/{amendment_id}/release",
        response_model=LabResultReleaseVersionResponse,
    )
    def release_lab_order_amendment(
        lab_id: uuid.UUID,
        order_id: uuid.UUID,
        amendment_id: uuid.UUID,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> LabResultReleaseVersionResponse:
        # DH-EX-014B/ADR-048 §8: LAB_RESULT_RELEASER only -- an amendment changes content a
        # provider and clinician may have already acted on, a materially higher-consequence event
        # than a pre-release fix, exactly mirroring the original release's own separate release
        # authority. Same human may hold both roles, unaffected by this ticket.
        _require_lab_result_releaser(principal, lab_id)
        try:
            with session_factory.begin() as session:
                version_record, created = amendments.release_amendment(
                    amendment_id=amendment_id,
                    order_id=order_id,
                    lab_organization_id=lab_id,
                    released_by_identity_id=principal.identity_id,
                    session=session,
                )
                # Only audited on a genuine new release -- a lost-response retry that returns the
                # existing version (`created=False`) writes no second audit row, mirroring
                # `release_lab_order_results`'s own identical `if created:` discipline.
                if created:
                    security_audit.record(
                        session,
                        event_type="lab_result_amendment_released",
                        actor_identity_id=principal.identity_id,
                        organization_id=lab_id,
                        outcome="created",
                        resource_type="lab_order",
                        resource_id=order_id,
                    )
        except LabOrderNotAssigned as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error
        except AmendmentNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="amendment not found"
            ) from error
        except OrderNotCompletedForAmendment as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except AmendmentNotReadyForRelease as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except AmendmentReleaseIntegrityError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except AmendmentBaseVersionSuperseded as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ResultsNotReadyForRelease as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return _release_version_response(version_record)

    def _provider_manifest_results(manifest) -> list[ProviderResultItemResponse]:
        return [
            ProviderResultItemResponse(
                item_id=item.item_id,
                catalog_code_snapshot=item.catalog_code_snapshot,
                catalog_display_name_snapshot=item.catalog_display_name_snapshot,
                entry_completed_at=item.entry_completed_at,
                observations=[
                    ProviderObservationResponse(
                        observation_id=observation.id,
                        source_code=observation.source_code,
                        source_system=observation.source_system,
                        display_label_snapshot=observation.display_label_snapshot,
                        result_type=observation.result_type,
                        numeric_value=observation.numeric_value,
                        numeric_unit=observation.numeric_unit,
                        reference_range_low=observation.reference_range_low,
                        reference_range_high=observation.reference_range_high,
                        text_value=observation.text_value,
                        recorded_at=observation.recorded_at,
                    )
                    for observation in item.observations
                ],
            )
            for item in manifest.items
        ]

    def _provider_review_for_version_or_conflict(
        release_version_id: uuid.UUID,
    ) -> DoctorReviewRecord:
        review = reviews.get_for_release_version(release_version_id)
        if review is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="release version has no clinician review obligation",
            )
        return review

    @router.get(
        "/organizations/{provider_id}/orders/{order_id}/release-versions",
        response_model=ProviderReleaseVersionHistoryResponse,
    )
    def get_provider_release_version_history(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> ProviderReleaseVersionHistoryResponse:
        _require_clinician(principal, provider_id)
        order = _existing_order_for_provider_or_404(order_id, provider_id)
        if order.status != LabOrderStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="order results are not yet available",
            )
        history_with_reviews = releases.get_release_version_history_with_reviews(order_id)
        versions: list[ProviderReleaseVersionSummaryResponse] = []
        for version, review in history_with_reviews:
            if review is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="release version has no clinician review obligation",
                )
            versions.append(
                ProviderReleaseVersionSummaryResponse(
                    release_version_id=version.id,
                    release_version_number=version.version_number,
                    is_amendment=version.version_number > 1,
                    released_at=version.created_at,
                    review_status=review.status,
                    reviewed_at=review.reviewed_at,
                    reviewed_by_practitioner_reference_id=(
                        review.reviewed_by_practitioner_reference_id
                    ),
                )
            )
        return ProviderReleaseVersionHistoryResponse(
            order_id=order_id,
            versions=versions,
        )

    @router.get(
        "/organizations/{provider_id}/orders/{order_id}/release-versions/{release_version_id}",
        response_model=ProviderReleaseVersionContentResponse,
    )
    def get_provider_release_version_content(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        release_version_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> ProviderReleaseVersionContentResponse:
        _require_clinician(principal, provider_id)
        order = _existing_order_for_provider_or_404(order_id, provider_id)
        if order.status != LabOrderStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="order results are not yet available",
            )
        version = releases.get_release_version(release_version_id)
        if version is None or version.lab_order_id != order_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="release version not found",
            )
        manifest = releases.get_manifest_for_version(release_version_id)
        if manifest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="release version not found",
            )
        review = _provider_review_for_version_or_conflict(release_version_id)
        return ProviderReleaseVersionContentResponse(
            order_id=order_id,
            release_version_id=version.id,
            release_version_number=version.version_number,
            is_amendment=version.version_number > 1,
            review_status=review.status,
            reviewed_at=review.reviewed_at,
            reviewed_by_practitioner_reference_id=review.reviewed_by_practitioner_reference_id,
            results=_provider_manifest_results(manifest),
        )

    @router.get(
        "/organizations/{provider_id}/orders/{order_id}/results",
        response_model=ProviderOrderResultsResponse,
    )
    def get_lab_order_results(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> ProviderOrderResultsResponse:
        _require_clinician(principal, provider_id)
        order = _existing_order_for_provider_or_404(order_id, provider_id)
        if order.status != LabOrderStatus.COMPLETED.value:
            # The order genuinely exists and genuinely belongs to this provider -- a bare 404
            # here would misrepresent that fact (matching ticket guidance: distinguish "not
            # ready yet" from "not yours"). No partial/clinical result value is ever included.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"order results are not yet available -- order is {order.status!r}, "
                f"not 'COMPLETED'",
            )
        item_by_id = {item.id: item for item in order.items}

        # DH-EX-014A/ADR-048: an order with a release version reads its exact, immutable manifest
        # -- never live "current effective" state (that live-read behavior is exactly the naive
        # post-release-mutation risk ADR-048 identified). A grandfathered COMPLETED-without-
        # LabResultRelease order has no release version by construction (Decision 6, not
        # amendment-eligible) and falls through to the unchanged legacy branch below -- the two
        # branches are deliberately kept separate, not merged into one conditional expression.
        manifest = releases.get_manifest_for_order(order_id)
        if manifest is not None:
            return ProviderOrderResultsResponse(
                order_id=order.id,
                order_status=order.status,
                release_version_id=manifest.version.id,
                release_version_number=manifest.version.version_number,
                is_amendment=manifest.version.version_number > 1,
                results=[
                    ProviderResultItemResponse(
                        item_id=item_record.item_id,
                        catalog_code_snapshot=item_record.catalog_code_snapshot,
                        catalog_display_name_snapshot=item_record.catalog_display_name_snapshot,
                        entry_completed_at=item_record.entry_completed_at,
                        observations=[
                            ProviderObservationResponse(
                                observation_id=observation.id,
                                source_code=observation.source_code,
                                source_system=observation.source_system,
                                display_label_snapshot=observation.display_label_snapshot,
                                result_type=observation.result_type,
                                numeric_value=observation.numeric_value,
                                numeric_unit=observation.numeric_unit,
                                reference_range_low=observation.reference_range_low,
                                reference_range_high=observation.reference_range_high,
                                text_value=observation.text_value,
                                recorded_at=observation.recorded_at,
                            )
                            for observation in item_record.observations
                        ],
                    )
                    for item_record in manifest.items
                    if item_record.item_id in item_by_id
                ],
            )

        # Legacy/grandfathered branch, unchanged from before this ticket -- a COMPLETED order
        # with no LabResultRelease predates DH-EX-012's explicit release step entirely. Reads
        # live current-effective observations exactly as it always has; this is deliberately NOT
        # a versioned read, since no version can exist for such an order (Decision 6).
        return ProviderOrderResultsResponse(
            order_id=order.id,
            order_status=order.status,
            release_version_id=None,
            release_version_number=None,
            is_amendment=None,
            results=[
                ProviderResultItemResponse(
                    item_id=record.lab_order_item_id,
                    catalog_code_snapshot=item_by_id[
                        record.lab_order_item_id
                    ].catalog_code_snapshot,
                    catalog_display_name_snapshot=item_by_id[
                        record.lab_order_item_id
                    ].catalog_display_name_snapshot,
                    # `entry_completed_at` is never None here -- release already proved every
                    # item's entry is complete before COMPLETED was reachable at all (see
                    # `ProviderResultItemResponse`'s own docstring).
                    entry_completed_at=record.entry_completed_at,  # type: ignore[arg-type]
                    observations=[
                        ProviderObservationResponse(
                            observation_id=observation.id,
                            source_code=observation.source_code,
                            source_system=observation.source_system,
                            display_label_snapshot=observation.display_label_snapshot,
                            result_type=observation.result_type,
                            numeric_value=observation.numeric_value,
                            numeric_unit=observation.numeric_unit,
                            reference_range_low=observation.reference_range_low,
                            reference_range_high=observation.reference_range_high,
                            text_value=observation.text_value,
                            recorded_at=observation.recorded_at,
                        )
                        for observation in record.observations
                    ],
                )
                for record in results.list_for_order(order_id)
                if record.lab_order_item_id in item_by_id
            ],
        )

    def _doctor_review_response(
        order_id: uuid.UUID, record: DoctorReviewRecord
    ) -> DoctorReviewResponse:
        return DoctorReviewResponse(
            order_id=order_id,
            release_version_id=record.release_version_id,
            status=record.status,
            reviewed_at=record.reviewed_at,
            reviewed_by_practitioner_reference_id=record.reviewed_by_practitioner_reference_id,
        )

    def _submit_doctor_review(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: AuthenticatedPrincipal,
        target_status: DoctorReviewStatus,
        release_version_id: uuid.UUID | None = None,
    ) -> DoctorReviewResponse:
        _require_clinician(principal, provider_id)
        order = _existing_order_for_provider_or_404(order_id, provider_id)
        identity = identities.get(principal.identity_id)
        try:
            with session_factory.begin() as session:
                # Exact same DH-EX-004 identity-backed binding used for the ordering clinician --
                # the *reviewing* clinician may be a different person than whoever created the
                # order, so this is resolved fresh here, never carried over from order creation.
                practitioner = practitioners.get_or_create(
                    source_organization_id=provider_id,
                    source_system=_CLINICIAN_PRACTITIONER_SOURCE_SYSTEM,
                    source_identity_system=_CLINICIAN_PRACTITIONER_SOURCE_IDENTITY_SYSTEM,
                    source_identity_value=str(principal.identity_id),
                    display_name=identity.display_name,
                    session=session,
                )
                if target_status == DoctorReviewStatus.NO_FOLLOW_UP_REQUIRED:
                    if release_version_id is None:
                        record = reviews.mark_no_follow_up_required(
                            order.id,
                            reviewed_by_practitioner_reference_id=practitioner.id,
                            session=session,
                        )
                    else:
                        record = reviews.mark_no_follow_up_required_for_release_version(
                            order.id,
                            release_version_id,
                            reviewed_by_practitioner_reference_id=practitioner.id,
                            session=session,
                        )
                else:
                    if release_version_id is None:
                        record = reviews.mark_follow_up_required(
                            order.id,
                            reviewed_by_practitioner_reference_id=practitioner.id,
                            session=session,
                        )
                    else:
                        record = reviews.mark_follow_up_required_for_release_version(
                            order.id,
                            release_version_id,
                            reviewed_by_practitioner_reference_id=practitioner.id,
                            session=session,
                        )
                security_audit.record(
                    session,
                    event_type="doctor_review_recorded",
                    actor_identity_id=principal.identity_id,
                    organization_id=provider_id,
                    outcome="updated",
                    resource_type="lab_order",
                    resource_id=order.id,
                )
        except ReviewNotAllowedYet as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ReviewAlreadyFinalized as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ExplicitReviewVersionRequired as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ReviewNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            ) from error

        return _doctor_review_response(order.id, record)

    @router.post(
        "/organizations/{provider_id}/orders/{order_id}/review/no-follow-up-required",
        response_model=DoctorReviewResponse,
    )
    def review_no_follow_up_required(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> DoctorReviewResponse:
        return _submit_doctor_review(
            provider_id, order_id, principal, DoctorReviewStatus.NO_FOLLOW_UP_REQUIRED
        )

    @router.post(
        "/organizations/{provider_id}/orders/{order_id}/review/follow-up-required",
        response_model=DoctorReviewResponse,
    )
    def review_follow_up_required(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> DoctorReviewResponse:
        return _submit_doctor_review(
            provider_id, order_id, principal, DoctorReviewStatus.FOLLOW_UP_REQUIRED
        )

    @router.post(
        "/organizations/{provider_id}/orders/{order_id}/release-versions/{release_version_id}"
        "/review/no-follow-up-required",
        response_model=DoctorReviewResponse,
    )
    def review_release_version_no_follow_up_required(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        release_version_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> DoctorReviewResponse:
        return _submit_doctor_review(
            provider_id,
            order_id,
            principal,
            DoctorReviewStatus.NO_FOLLOW_UP_REQUIRED,
            release_version_id,
        )

    @router.post(
        "/organizations/{provider_id}/orders/{order_id}/release-versions/{release_version_id}"
        "/review/follow-up-required",
        response_model=DoctorReviewResponse,
    )
    def review_release_version_follow_up_required(
        provider_id: uuid.UUID,
        order_id: uuid.UUID,
        release_version_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> DoctorReviewResponse:
        return _submit_doctor_review(
            provider_id,
            order_id,
            principal,
            DoctorReviewStatus.FOLLOW_UP_REQUIRED,
            release_version_id,
        )

    return router


def _to_catalog_response(entry: CatalogEntryRecord) -> CatalogEntryResponse:
    return CatalogEntryResponse(
        id=entry.id,
        code=entry.code,
        version=entry.version,
        display_name=entry.display_name,
        active=entry.active,
        is_synthetic=entry.is_synthetic,
    )


def _to_offering_response(offering: LabOfferingRecord) -> LabOfferingResponse:
    return LabOfferingResponse(
        id=offering.id,
        lab_organization_id=offering.lab_organization_id,
        catalog_entry_id=offering.catalog_entry_id,
        local_test_code=offering.local_test_code,
        active=offering.active,
    )


def _to_price_response(price: LabPriceRecord) -> LabPriceResponse:
    return LabPriceResponse(
        id=price.id,
        lab_offering_id=price.lab_offering_id,
        amount=price.amount,
        currency=price.currency,
        effective_at=price.effective_at,
        superseded_at=price.superseded_at,
    )


def _to_profile_response(profile: LabDirectoryProfileRecord) -> LabDirectoryProfileResponse:
    return LabDirectoryProfileResponse(
        id=profile.id,
        lab_organization_id=profile.lab_organization_id,
        listed=profile.listed,
        accepting_orders=profile.accepting_orders,
        public_address=profile.public_address,
        latitude=profile.latitude,
        longitude=profile.longitude,
        public_phone=profile.public_phone,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _to_listing_response(listing: LabDirectoryListing) -> LabDirectoryListingResponse:
    return LabDirectoryListingResponse(
        lab_organization_id=listing.lab_organization_id,
        lab_organization_name=listing.lab_organization_name,
        listed=listing.listed,
        accepting_orders=listing.accepting_orders,
        public_address=listing.public_address,
        latitude=listing.latitude,
        longitude=listing.longitude,
        public_phone=listing.public_phone,
    )


def _to_order_response(order: LabOrderRecord) -> LabOrderResponse:
    return LabOrderResponse(
        id=order.id,
        provider_organization_id=order.provider_organization_id,
        patient_reference_id=order.patient_reference_id,
        practitioner_reference_id=order.practitioner_reference_id,
        status=order.status,
        created_at=order.created_at,
        items=[
            LabOrderItemResponse(
                catalog_entry_id=item.catalog_entry_id,
                catalog_code_snapshot=item.catalog_code_snapshot,
                catalog_display_name_snapshot=item.catalog_display_name_snapshot,
            )
            for item in order.items
        ],
    )


def _to_order_summary_response(order: LabOrderRecord) -> LabOrderSummaryResponse:
    return LabOrderSummaryResponse(
        id=order.id,
        provider_organization_id=order.provider_organization_id,
        patient_reference_id=order.patient_reference_id,
        practitioner_reference_id=order.practitioner_reference_id,
        status=order.status,
        created_at=order.created_at,
        item_count=len(order.items),
    )
