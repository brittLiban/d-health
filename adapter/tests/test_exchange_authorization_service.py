"""DH-EX-002's `AuthorizationService` tests: the anti-enumeration 404-vs-403 policy, the role
matrix, cross-tenant isolation, and the platform-admin bypass -- all at the service level, isolated
from HTTP status-code mapping (covered separately in `test_exchange_api.py`).
"""

from __future__ import annotations

import uuid

import pytest
from exchange_support import make_lab, make_provider
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.authorization import (
    AuthenticatedPrincipal,
    AuthorizationService,
    NotAuthorized,
    ResourceNotFound,
)
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import Organization, OrganizationRole, OrganizationStatus
from daher_adapter.exchange.organizations import OrganizationRepository


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


@pytest.fixture
def identities(sessions: sessionmaker[Session]) -> ExchangeIdentityRepository:
    return ExchangeIdentityRepository(sessions)


@pytest.fixture
def memberships(sessions: sessionmaker[Session]) -> OrganizationMembershipRepository:
    return OrganizationMembershipRepository(sessions)


@pytest.fixture
def role_assignments(sessions: sessionmaker[Session]) -> OrganizationRoleAssignmentRepository:
    return OrganizationRoleAssignmentRepository(sessions)


@pytest.fixture
def authz(
    organizations: OrganizationRepository,
    memberships: OrganizationMembershipRepository,
    role_assignments: OrganizationRoleAssignmentRepository,
) -> AuthorizationService:
    return AuthorizationService(organizations, memberships, role_assignments)


def _principal(identity_id: uuid.UUID, *, platform_admin: bool = False) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        identity_id=identity_id,
        issuer="https://idp.test",
        subject=str(identity_id),
        platform_admin=platform_admin,
    )


def _deactivate(sessions: sessionmaker[Session], organization_id: uuid.UUID) -> None:
    with sessions() as session:
        session.execute(
            update(Organization)
            .where(Organization.id == organization_id)
            .values(status=OrganizationStatus.INACTIVE.value)
        )
        session.commit()


def test_no_relationship_raises_resource_not_found(
    authz: AuthorizationService, organizations: OrganizationRepository, identities
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    with pytest.raises(ResourceNotFound):
        authz.require_membership(_principal(identity.id), org.id)


def test_nonexistent_org_raises_the_same_resource_not_found(
    authz: AuthorizationService, identities
) -> None:
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    with pytest.raises(ResourceNotFound):
        authz.require_membership(_principal(identity.id), uuid.uuid4())


def test_active_membership_returns_context(
    authz: AuthorizationService,
    organizations: OrganizationRepository,
    identities,
    memberships: OrganizationMembershipRepository,
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    memberships.create(organization_id=org.id, identity_id=identity.id)

    context = authz.require_membership(_principal(identity.id), org.id)

    assert context.organization.id == org.id
    assert context.via_platform_admin is False
    assert context.membership is not None


def test_inactive_organization_raises_not_authorized_for_a_member(
    authz: AuthorizationService,
    organizations: OrganizationRepository,
    identities,
    memberships: OrganizationMembershipRepository,
    sessions: sessionmaker[Session],
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    memberships.create(organization_id=org.id, identity_id=identity.id)
    _deactivate(sessions, org.id)

    with pytest.raises(NotAuthorized):
        authz.require_membership(_principal(identity.id), org.id)


def test_cross_tenant_membership_does_not_grant_access(
    authz: AuthorizationService,
    organizations: OrganizationRepository,
    identities,
    memberships: OrganizationMembershipRepository,
) -> None:
    org_a = make_provider(organizations, name="Clinic A")
    org_b = make_provider(organizations, name="Clinic B")
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    memberships.create(organization_id=org_a.id, identity_id=identity.id)

    with pytest.raises(ResourceNotFound):
        authz.require_membership(_principal(identity.id), org_b.id)


def test_require_role_without_the_role_raises_not_authorized(
    authz: AuthorizationService,
    organizations: OrganizationRepository,
    identities,
    memberships: OrganizationMembershipRepository,
    role_assignments: OrganizationRoleAssignmentRepository,
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    membership, _ = memberships.create(organization_id=org.id, identity_id=identity.id)
    role_assignments.assign(membership_id=membership.id, role=OrganizationRole.CLINICIAN)

    with pytest.raises(NotAuthorized):
        authz.require_role(_principal(identity.id), org.id, OrganizationRole.ORG_ADMIN)


def test_require_role_with_the_role_returns_context(
    authz: AuthorizationService,
    organizations: OrganizationRepository,
    identities,
    memberships: OrganizationMembershipRepository,
    role_assignments: OrganizationRoleAssignmentRepository,
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    membership, _ = memberships.create(organization_id=org.id, identity_id=identity.id)
    role_assignments.assign(membership_id=membership.id, role=OrganizationRole.ORG_ADMIN)

    context = authz.require_org_admin(_principal(identity.id), org.id)
    assert context.organization.id == org.id


def test_platform_admin_bypasses_membership_for_an_existing_org(
    authz: AuthorizationService, organizations: OrganizationRepository, identities
) -> None:
    org = make_lab(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="admin-1")

    context = authz.require_membership(_principal(identity.id, platform_admin=True), org.id)

    assert context.via_platform_admin is True
    assert context.membership is None


def test_platform_admin_still_gets_resource_not_found_for_a_nonexistent_org(
    authz: AuthorizationService, identities
) -> None:
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="admin-1")
    with pytest.raises(ResourceNotFound):
        authz.require_membership(_principal(identity.id, platform_admin=True), uuid.uuid4())


def test_platform_admin_bypasses_role_check(
    authz: AuthorizationService, organizations: OrganizationRepository, identities
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="admin-1")
    context = authz.require_org_admin(_principal(identity.id, platform_admin=True), org.id)
    assert context.via_platform_admin is True


def test_require_platform_admin_rejects_a_non_admin(
    authz: AuthorizationService, identities
) -> None:
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    with pytest.raises(NotAuthorized):
        authz.require_platform_admin(_principal(identity.id, platform_admin=False))
