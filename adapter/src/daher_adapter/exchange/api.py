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
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.audit import ExchangeAuditEvent, ExchangeAuditWriter
from daher_adapter.exchange.authorization import (
    AuthenticatedPrincipal,
    AuthorizationService,
    NotAuthorized,
    OrganizationAccessContext,
    ResourceNotFound,
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

    return router
