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

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
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
from daher_adapter.exchange.identity_auth import TokenValidationError, TokenValidator
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
    OrganizationMembershipStatus,
    OrganizationRole,
    OrganizationType,
)
from daher_adapter.exchange.organizations import InvalidOrganizationType, OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository


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


_ROLE_LITERAL = Literal["ORG_ADMIN", "CLINICIAN", "LAB_TECH"]


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


def build_exchange_router(
    *,
    session_factory: sessionmaker[Session],
    token_validator: TokenValidator,
    audit_writer: ExchangeAuditWriter,
    platform_admin_role: str,
) -> APIRouter:
    identities = ExchangeIdentityRepository(session_factory)
    organizations = OrganizationRepository(session_factory)
    memberships = OrganizationMembershipRepository(session_factory)
    role_assignments = OrganizationRoleAssignmentRepository(session_factory)
    authz = AuthorizationService(organizations, memberships, role_assignments)
    security_audit = ExchangeSecurityAuditRepository()
    catalog = CatalogRepository(session_factory, organizations)
    directory = LabDirectoryProfileRepository(session_factory, organizations)

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

    @router.post("/marketplace/lab-options", response_model=MarketplaceLabOptionsResponse)
    def marketplace_lab_options(
        body: MarketplaceLabOptionsRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    ) -> MarketplaceLabOptionsResponse:
        _require_network_read_access(principal)

        # Fail closed on the whole request rather than silently dropping one requested test --
        # every entry must exist, be ACTIVE, and (this pilot phase) be SYNTHETIC.
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

        labs = catalog.find_whole_order_eligible_labs(body.catalog_entry_ids)
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
