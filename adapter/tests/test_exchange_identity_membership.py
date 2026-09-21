"""DH-EX-002's identity/membership/role repository tests: bootstrap idempotency, exact-identity
scoping (issuer+subject, never email), the provider/lab role matrix, inactive-membership refusal,
and the required concurrency proof for both identity bootstrap and membership creation.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from exchange_support import make_lab, make_provider
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    IdentityNotFound,
    InvalidRoleForOrganizationType,
    MembershipInactive,
    MembershipNotFound,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    Base,
    ExchangeIdentity,
    OrganizationMembership,
    OrganizationRole,
)
from daher_adapter.exchange.organizations import OrganizationRepository


@pytest.fixture
def identities(sessions: sessionmaker[Session]) -> ExchangeIdentityRepository:
    return ExchangeIdentityRepository(sessions)


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


@pytest.fixture
def memberships(sessions: sessionmaker[Session]) -> OrganizationMembershipRepository:
    return OrganizationMembershipRepository(sessions)


@pytest.fixture
def role_assignments(sessions: sessionmaker[Session]) -> OrganizationRoleAssignmentRepository:
    return OrganizationRoleAssignmentRepository(sessions)


# -- identity bootstrap ------------------------------------------------------------------------


def test_first_use_creates_an_identity(identities: ExchangeIdentityRepository) -> None:
    record, created = identities.get_or_create(
        issuer="https://idp.test/realms/dhealth", subject="sub-1", email="a@test.invalid"
    )
    assert created is True
    fetched = identities.get(record.id)
    assert fetched.issuer == "https://idp.test/realms/dhealth"
    assert fetched.subject == "sub-1"


def test_replay_resolves_to_the_same_identity(identities: ExchangeIdentityRepository) -> None:
    first, first_created = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    second, second_created = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    assert first_created is True
    assert second_created is False
    assert first.id == second.id


def test_same_email_different_subject_are_distinct_identities(
    identities: ExchangeIdentityRepository,
) -> None:
    a, _ = identities.get_or_create(
        issuer="https://idp.test", subject="sub-a", email="shared@test.invalid"
    )
    b, _ = identities.get_or_create(
        issuer="https://idp.test", subject="sub-b", email="shared@test.invalid"
    )
    assert a.id != b.id


def test_same_subject_different_issuer_are_distinct_identities(
    identities: ExchangeIdentityRepository,
) -> None:
    a, _ = identities.get_or_create(issuer="https://idp-one.test", subject="sub-1")
    b, _ = identities.get_or_create(issuer="https://idp-two.test", subject="sub-1")
    assert a.id != b.id


def test_get_unknown_identity_raises(identities: ExchangeIdentityRepository) -> None:
    import uuid

    with pytest.raises(IdentityNotFound):
        identities.get(uuid.uuid4())


def test_blank_issuer_or_subject_is_rejected(identities: ExchangeIdentityRepository) -> None:
    with pytest.raises(ValueError, match="issuer and subject"):
        identities.get_or_create(issuer="  ", subject="sub-1")
    with pytest.raises(ValueError, match="issuer and subject"):
        identities.get_or_create(issuer="https://idp.test", subject=" ")


# -- membership ---------------------------------------------------------------------------------


def test_membership_create_is_idempotent(
    organizations: OrganizationRepository,
    identities: ExchangeIdentityRepository,
    memberships: OrganizationMembershipRepository,
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")

    first, first_created = memberships.create(organization_id=org.id, identity_id=identity.id)
    second, second_created = memberships.create(organization_id=org.id, identity_id=identity.id)

    assert first_created is True
    assert second_created is False
    assert first.id == second.id


def test_find_active_is_none_without_a_membership(
    organizations: OrganizationRepository,
    identities: ExchangeIdentityRepository,
    memberships: OrganizationMembershipRepository,
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    assert memberships.find_active(organization_id=org.id, identity_id=identity.id) is None


def test_set_inactive_removes_it_from_find_active(
    organizations: OrganizationRepository,
    identities: ExchangeIdentityRepository,
    memberships: OrganizationMembershipRepository,
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    membership, _ = memberships.create(organization_id=org.id, identity_id=identity.id)

    memberships.set_inactive(membership.id)

    assert memberships.find_active(organization_id=org.id, identity_id=identity.id) is None


def test_set_inactive_unknown_membership_raises(
    memberships: OrganizationMembershipRepository,
) -> None:
    import uuid

    with pytest.raises(MembershipNotFound):
        memberships.set_inactive(uuid.uuid4())


def test_list_for_organization_and_list_for_identity(
    organizations: OrganizationRepository,
    identities: ExchangeIdentityRepository,
    memberships: OrganizationMembershipRepository,
) -> None:
    org_a = make_provider(organizations, name="Clinic A", operational=False)
    org_b = make_provider(organizations, name="Clinic B", operational=False)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    memberships.create(organization_id=org_a.id, identity_id=identity.id)
    memberships.create(organization_id=org_b.id, identity_id=identity.id)

    for_identity = memberships.list_for_identity(identity.id)
    assert {m.organization_id for m in for_identity} == {org_a.id, org_b.id}

    for_org_a = memberships.list_for_organization(org_a.id)
    assert len(for_org_a) == 1
    assert for_org_a[0].identity_id == identity.id


# -- role assignment matrix ----------------------------------------------------------------------


def test_provider_org_allows_org_admin_and_clinician_not_lab_tech(
    organizations: OrganizationRepository,
    identities: ExchangeIdentityRepository,
    memberships: OrganizationMembershipRepository,
    role_assignments: OrganizationRoleAssignmentRepository,
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    membership, _ = memberships.create(organization_id=org.id, identity_id=identity.id)

    role_assignments.assign(membership_id=membership.id, role=OrganizationRole.ORG_ADMIN)
    role_assignments.assign(membership_id=membership.id, role=OrganizationRole.CLINICIAN)
    with pytest.raises(InvalidRoleForOrganizationType):
        role_assignments.assign(membership_id=membership.id, role=OrganizationRole.LAB_TECH)


def test_lab_org_allows_org_admin_and_lab_tech_not_clinician(
    organizations: OrganizationRepository,
    identities: ExchangeIdentityRepository,
    memberships: OrganizationMembershipRepository,
    role_assignments: OrganizationRoleAssignmentRepository,
) -> None:
    org = make_lab(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    membership, _ = memberships.create(organization_id=org.id, identity_id=identity.id)

    role_assignments.assign(membership_id=membership.id, role=OrganizationRole.ORG_ADMIN)
    role_assignments.assign(membership_id=membership.id, role=OrganizationRole.LAB_TECH)
    with pytest.raises(InvalidRoleForOrganizationType):
        role_assignments.assign(membership_id=membership.id, role=OrganizationRole.CLINICIAN)


def test_role_assignment_is_idempotent(
    organizations: OrganizationRepository,
    identities: ExchangeIdentityRepository,
    memberships: OrganizationMembershipRepository,
    role_assignments: OrganizationRoleAssignmentRepository,
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    membership, _ = memberships.create(organization_id=org.id, identity_id=identity.id)

    first, first_created = role_assignments.assign(
        membership_id=membership.id, role=OrganizationRole.ORG_ADMIN
    )
    second, second_created = role_assignments.assign(
        membership_id=membership.id, role=OrganizationRole.ORG_ADMIN
    )
    assert first_created is True
    assert second_created is False
    assert first.id == second.id
    assert role_assignments.list_for_membership(membership.id) == frozenset({"ORG_ADMIN"})


def test_assign_role_to_unknown_membership_raises(
    role_assignments: OrganizationRoleAssignmentRepository,
) -> None:
    import uuid

    with pytest.raises(MembershipNotFound):
        role_assignments.assign(membership_id=uuid.uuid4(), role=OrganizationRole.ORG_ADMIN)


def test_assign_role_to_inactive_membership_is_refused(
    organizations: OrganizationRepository,
    identities: ExchangeIdentityRepository,
    memberships: OrganizationMembershipRepository,
    role_assignments: OrganizationRoleAssignmentRepository,
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    membership, _ = memberships.create(organization_id=org.id, identity_id=identity.id)
    memberships.set_inactive(membership.id)

    with pytest.raises(MembershipInactive):
        role_assignments.assign(membership_id=membership.id, role=OrganizationRole.ORG_ADMIN)


def test_remove_role_assignment(
    organizations: OrganizationRepository,
    identities: ExchangeIdentityRepository,
    memberships: OrganizationMembershipRepository,
    role_assignments: OrganizationRoleAssignmentRepository,
) -> None:
    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    membership, _ = memberships.create(organization_id=org.id, identity_id=identity.id)
    role_assignments.assign(membership_id=membership.id, role=OrganizationRole.CLINICIAN)

    role_assignments.remove(membership_id=membership.id, role=OrganizationRole.CLINICIAN)

    assert role_assignments.list_for_membership(membership.id) == frozenset()
    # Removing an already-absent assignment is a no-op, not an error.
    role_assignments.remove(membership_id=membership.id, role=OrganizationRole.CLINICIAN)


# -- concurrency (required proof) -----------------------------------------------------------------


def test_concurrent_identity_first_seen_creates_exactly_one_identity(tmp_path: Path) -> None:
    database = tmp_path / "exchange-identity-concurrency.sqlite"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    identities = ExchangeIdentityRepository(sessions)

    barrier = threading.Barrier(10)
    outcomes: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            barrier.wait(timeout=10)
            _, created = identities.get_or_create(
                issuer="https://idp.test", subject="concurrent-subject"
            )
            with lock:
                outcomes.append(created)
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"unexpected exceptions from concurrent get_or_create() calls: {errors!r}"
    assert len(outcomes) == 10
    assert outcomes.count(True) == 1, "exactly one concurrent attempt must have created the row"

    with sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(ExchangeIdentity)
            .where(ExchangeIdentity.subject == "concurrent-subject")
        )
    assert count == 1, "the DB unique constraint must permit exactly one identity row"
    engine.dispose()


def test_concurrent_membership_creation_creates_exactly_one_membership(tmp_path: Path) -> None:
    database = tmp_path / "exchange-membership-concurrency.sqlite"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    memberships = OrganizationMembershipRepository(sessions)

    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")

    barrier = threading.Barrier(10)
    outcomes: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            barrier.wait(timeout=10)
            _, created = memberships.create(organization_id=org.id, identity_id=identity.id)
            with lock:
                outcomes.append(created)
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"unexpected exceptions from concurrent create() calls: {errors!r}"
    assert len(outcomes) == 10
    assert outcomes.count(True) == 1, "exactly one concurrent attempt must have created the row"

    with sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == org.id,
                OrganizationMembership.identity_id == identity.id,
            )
        )
    assert count == 1, "the DB unique constraint must permit exactly one membership row"
    engine.dispose()


def test_concurrent_role_assignment_creates_exactly_one_role_row(tmp_path: Path) -> None:
    database = tmp_path / "exchange-role-concurrency.sqlite"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    memberships = OrganizationMembershipRepository(sessions)
    role_assignments = OrganizationRoleAssignmentRepository(sessions)

    org = make_provider(organizations)
    identity, _ = identities.get_or_create(issuer="https://idp.test", subject="sub-1")
    membership, _ = memberships.create(organization_id=org.id, identity_id=identity.id)

    barrier = threading.Barrier(10)
    outcomes: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            barrier.wait(timeout=10)
            _, created = role_assignments.assign(
                membership_id=membership.id, role=OrganizationRole.CLINICIAN
            )
            with lock:
                outcomes.append(created)
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"unexpected exceptions from concurrent assign() calls: {errors!r}"
    assert len(outcomes) == 10
    assert outcomes.count(True) == 1, "exactly one concurrent attempt must have created the row"
    assert role_assignments.list_for_membership(membership.id) == frozenset({"CLINICIAN"})
    engine.dispose()
