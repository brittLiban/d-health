"""DH-EX-002: the Exchange's explicit authorization layer.

Deliberately the only place organization-scoped authorization decisions are made -- API routes
call `require_*` methods here rather than comparing roles inline, so the anti-enumeration and
role-matrix rules exist in exactly one place, are independently testable, and cannot silently
diverge between endpoints.

Two exception types, mapped to two different HTTP statuses at the API layer, on purpose:

- `ResourceNotFound` (404): the identity has *no relationship at all* to the organization -- an
  org that does not exist and an org the caller is not a member of are indistinguishable
  responses, by design (DH-EX-002's anti-enumeration policy: never disclose a cross-tenant
  resource's existence to someone with no claim to it).
- `NotAuthorized` (403): the identity *does* legitimately belong to the organization (or is the
  platform admin) but lacks the specific permission being checked (e.g. a CLINICIAN calling an
  ORG_ADMIN-only action, or any member of a legitimately-INACTIVE organization).

Platform admin bypasses both checks entirely (per DH-EX-002: "platform admin may still inspect/
manage" an organization, including an inactive one) but still gets `ResourceNotFound` for an
organization that genuinely does not exist.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from daher_adapter.exchange.membership import (
    MembershipRecord,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import OrganizationRole, OrganizationStatus
from daher_adapter.exchange.organizations import (
    OrganizationNotFound,
    OrganizationRecord,
    OrganizationRepository,
)


class NotAuthorized(RuntimeError):
    """The identity has a legitimate relationship to the organization but lacks the specific
    permission required. Maps to HTTP 403."""


class ResourceNotFound(RuntimeError):
    """The identity has no relationship to this organization -- deliberately indistinguishable
    from the organization not existing at all. Maps to HTTP 404."""


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    identity_id: uuid.UUID
    issuer: str
    subject: str
    platform_admin: bool


@dataclass(frozen=True, slots=True)
class OrganizationAccessContext:
    organization: OrganizationRecord
    membership: MembershipRecord | None
    via_platform_admin: bool


class AuthorizationService:
    def __init__(
        self,
        organizations: OrganizationRepository,
        memberships: OrganizationMembershipRepository,
        roles: OrganizationRoleAssignmentRepository,
    ) -> None:
        self._organizations = organizations
        self._memberships = memberships
        self._roles = roles

    def require_platform_admin(self, principal: AuthenticatedPrincipal) -> None:
        if not principal.platform_admin:
            raise NotAuthorized("platform admin required")

    def require_membership(
        self, principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        if principal.platform_admin:
            organization = self._get_organization_or_404(organization_id)
            return OrganizationAccessContext(
                organization=organization, membership=None, via_platform_admin=True
            )

        membership = self._memberships.find_active(
            organization_id=organization_id, identity_id=principal.identity_id
        )
        if membership is None:
            raise ResourceNotFound(f"organization {organization_id} is not accessible")

        # A membership row can only exist for an organization that exists (FK), so this lookup
        # cannot itself 404 here -- but it can still find the organization INACTIVE, which is a
        # 403 (the caller legitimately belongs; the organization is disabled), never a 404.
        organization = self._organizations.get(organization_id)
        if organization.status != OrganizationStatus.ACTIVE.value:
            raise NotAuthorized("organization is inactive")

        return OrganizationAccessContext(
            organization=organization, membership=membership, via_platform_admin=False
        )

    def require_role(
        self,
        principal: AuthenticatedPrincipal,
        organization_id: uuid.UUID,
        role: OrganizationRole,
    ) -> OrganizationAccessContext:
        context = self.require_membership(principal, organization_id)
        if context.via_platform_admin:
            return context
        if context.membership is None:
            raise RuntimeError(
                "require_membership returned a non-platform-admin context with no membership"
            )
        held_roles = self._roles.list_for_membership(context.membership.id)
        if role.value not in held_roles:
            raise NotAuthorized(f"role {role.value!r} required")
        return context

    def require_org_admin(
        self, principal: AuthenticatedPrincipal, organization_id: uuid.UUID
    ) -> OrganizationAccessContext:
        return self.require_role(principal, organization_id, OrganizationRole.ORG_ADMIN)

    def _get_organization_or_404(self, organization_id: uuid.UUID) -> OrganizationRecord:
        try:
            return self._organizations.get(organization_id)
        except OrganizationNotFound as error:
            raise ResourceNotFound(f"organization {organization_id} is not accessible") from error
