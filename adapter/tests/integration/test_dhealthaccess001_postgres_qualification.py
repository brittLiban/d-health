"""DHEALTH-NATIVE-ACCESS-001: real PostgreSQL qualification of the staff access lifecycle.

The SQLite unit suite (`tests/test_dhealthaccess001_staff_access.py`) proves every rule this
lifecycle has. It cannot prove the one property that is *load-bearing under concurrency*, and it
cannot prove it for a specific, stated reason: **SQLAlchemy's SQLite dialect omits `FOR UPDATE`
entirely**, so `OrganizationRepository.lock_in_session` -- the organization-row lock that serializes
an organization's whole access lifecycle -- is silently a no-op there. SQLite's own writer
serialization then makes the tests pass anyway, for a reason unrelated to the lock being taken. That
is precisely the shape of "green for the wrong reason" this repository has been burned by before, so
the concurrency claim is qualified only here.

What is qualified in this file, and nowhere else:

- **§25-C, the race that matters most.** Two administrators, each removing the *other's* `ORG_ADMIN`
  at the same instant. Without the organization-level lock both read "there are two administrators",
  both pass, and the organization ends with **zero** -- locked out, with no way back in except
  platform intervention. A per-membership lock does not help, because the two operations touch two
  different rows. Exactly one must be refused.
- **§25-A**, two administrators removing the same role simultaneously: one real removal, one
  truthful "did not hold it" conflict, never two successes.
- **§25-B**, deactivation racing a role mutation on the same membership.
- **§25-D**, reactivate racing deactivate: a deterministic final state, not a coin toss.
- **Transactional audit coupling.** A refused last-administrator removal flushes the delete *before*
  the invariant is evaluated, so PostgreSQL must roll back both the delete and its audit row. SQLite
  cannot stand in for that either, because the audit row and the mutation are only meaningfully
  coupled by a real transaction.
- **The unique role assignment constraint**, enforced by the database rather than by application
  logic, under genuine concurrency.

**The proof is the barrier plus the final count, not an inspection of the lock.** If the lock were
not held, the observable outcome would be two successes and zero administrators -- which is what
these assertions catch. Nothing here reads `pg_locks` or otherwise asserts on the mechanism.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHACCESS_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHACCESS_PG_HOST=127.0.0.1 \\
    DHACCESS_PG_PORT=5432 \\
    DHACCESS_PG_USER=adapter \\
    DHACCESS_PG_PASSWORD=... \\
    DHACCESS_PG_DATABASE=dhaccess_qual \\
        pytest adapter/tests/integration/test_dhealthaccess001_postgres_qualification.py -v

The target database must already have migrations applied through
``0033_staff_access_lifecycle`` (``alembic upgrade head``) before running this file -- it does not
run migrations itself.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    OrganizationMembership,
    OrganizationMembershipStatus,
    OrganizationRole,
    OrganizationRoleAssignment,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
from daher_adapter.exchange.staff_access import (
    LastAdministratorProtected,
    StaffAccessAuthorityLost,
    StaffAccessService,
    StaffAccessStateUnchanged,
)

PG_HOST = os.environ.get("DHACCESS_PG_HOST")
PG_PORT = os.environ.get("DHACCESS_PG_PORT")
PG_USER = os.environ.get("DHACCESS_PG_USER")
PG_PASSWORD = os.environ.get("DHACCESS_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHACCESS_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHACCESS_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    # A pool large enough for every concurrent thread below to hold its own real connection. A
    # thread waiting on the *pool* rather than on the database's row lock would serialize the race
    # this file exists to create, and the test would pass without ever exercising the lock.
    engine = create_engine(url, pool_pre_ping=True, pool_size=12, max_overflow=8)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _service(sessions: sessionmaker) -> StaffAccessService:
    return StaffAccessService(
        session_factory=sessions,
        organizations=OrganizationRepository(sessions),
        memberships=OrganizationMembershipRepository(sessions),
        roles=OrganizationRoleAssignmentRepository(sessions),
        security_audit=ExchangeSecurityAuditRepository(),
    )


def _principal(identity_id: uuid.UUID) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        identity_id=identity_id,
        issuer="https://pg-qualification.invalid/realms/dhealth-exchange",
        subject=str(identity_id),
        platform_admin=False,
    )


def _seed(sessions: sessionmaker, *, key_prefix: str, admins: int = 2, members: int = 0):
    """A provider organization with `admins` ORG_ADMIN memberships and `members` CLINICIAN ones.

    Built through the same repositories `exchange/api.py` uses, so every uniqueness, role-matrix and
    tenant rule a real request is subject to applies here too.
    """
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    memberships = OrganizationMembershipRepository(sessions)
    roles = OrganizationRoleAssignmentRepository(sessions)

    organization = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")

    def attach(label: str, role: OrganizationRole):
        identity, _ = identities.get_or_create(
            issuer="https://pg-qualification.invalid/realms/dhealth-exchange",
            subject=f"{key_prefix}-{label}",
            display_name=f"{key_prefix} {label}",
        )
        membership, _ = memberships.create(organization_id=organization.id, identity_id=identity.id)
        roles.assign(membership_id=membership.id, role=role)
        return identity.id, membership.id

    admin_pairs = [attach(f"admin-{i}", OrganizationRole.ORG_ADMIN) for i in range(admins)]
    member_pairs = [attach(f"member-{i}", OrganizationRole.CLINICIAN) for i in range(members)]
    return organization, admin_pairs, member_pairs


def _active_admin_count(sessions: sessionmaker, organization_id: uuid.UUID) -> int:
    with sessions() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(OrganizationMembership)
                .join(
                    OrganizationRoleAssignment,
                    OrganizationRoleAssignment.membership_id == OrganizationMembership.id,
                )
                .where(
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationMembership.status == OrganizationMembershipStatus.ACTIVE.value,
                    OrganizationRoleAssignment.role == OrganizationRole.ORG_ADMIN.value,
                )
            )
            or 0
        )


def _run_concurrently(operations):
    """Runs each callable on its own thread, released simultaneously by a barrier.

    Returns a list of `(index, result_or_exception)`. A barrier maximizes overlap; it cannot
    *guarantee* the two transactions interleave at the worst possible instant, which is why the
    assertions below are about the invariant's outcome rather than about timing.
    """
    barrier = threading.Barrier(len(operations))
    outcomes: list[tuple[int, object]] = []
    lock = threading.Lock()

    def worker(index: int, operation):
        barrier.wait()
        try:
            result = operation()
        except Exception as error:  # noqa: BLE001 -- the exception *is* the observation here
            result = error
        with lock:
            outcomes.append((index, result))

    threads = [threading.Thread(target=worker, args=(i, op)) for i, op in enumerate(operations)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    for thread in threads:
        assert not thread.is_alive(), (
            "a staff-access transaction never completed -- possible deadlock"
        )
    return outcomes


# -- §25-C: the lockout race -----------------------------------------------------------------------


def test_pg1_two_admins_removing_each_others_org_admin_cannot_reach_zero(
    sessions: sessionmaker,
) -> None:
    """The single most important assertion in this file.

    Two administrators, two *different* membership rows, each removing the other's ORG_ADMIN at the
    same instant. Both would pass a "there are two administrators right now" pre-flight check, and
    the organization would end with none -- administratively locked out. The organization-level
    `FOR UPDATE` lock is what makes exactly one of them lose.
    """
    organization, admins, _ = _seed(sessions, key_prefix=f"pg1-{uuid.uuid4().hex[:8]}", admins=2)
    (identity_a, membership_a), (identity_b, membership_b) = admins
    assert _active_admin_count(sessions, organization.id) == 2

    service = _service(sessions)

    outcomes = _run_concurrently(
        [
            # A removes B's ORG_ADMIN.
            lambda: service.remove_role(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=membership_b,
                role_name="ORG_ADMIN",
            ),
            # B removes A's ORG_ADMIN, simultaneously.
            lambda: service.remove_role(
                principal=_principal(identity_b),
                organization_id=organization.id,
                membership_id=membership_a,
                role_name="ORG_ADMIN",
            ),
        ]
    )

    # **Either refusal is correct here, and which one fires depends on the lock order.** Observed
    # empirically on the first real-PostgreSQL run, and worth stating because the first draft
    # of this test demanded `LastAdministratorProtected` specifically and went red on the
    # *stronger* answer:
    #
    #   - If the loser still holds ORG_ADMIN when it acquires the lock, the last-administrator
    #     invariant refuses it (`LastAdministratorProtected`).
    #   - If the winner removed the loser's OWN ORG_ADMIN first, the loser's in-lock authority
    #     re-check refuses it before the invariant is ever reached (`StaffAccessAuthorityLost`) --
    #     §14 firing, and a strictly earlier refusal.
    #
    # In the mutual-removal case the two guards overlap, and the second subsumes the first. The
    # assertion that matters is the invariant's outcome, not which guard produced it.
    succeeded = [r for _, r in outcomes if not isinstance(r, Exception)]
    refused = [
        r
        for _, r in outcomes
        if isinstance(r, (LastAdministratorProtected, StaffAccessAuthorityLost))
    ]
    other_errors = [
        r
        for _, r in outcomes
        if isinstance(r, Exception)
        and not isinstance(r, (LastAdministratorProtected, StaffAccessAuthorityLost))
    ]

    assert other_errors == [], f"unexpected failures: {other_errors}"
    assert len(succeeded) == 1, "exactly one removal must be allowed to commit"
    assert len(refused) == 1, f"the second must be refused, by either guard: {outcomes}"

    # The invariant, stated as the thing that actually matters.
    assert _active_admin_count(sessions, organization.id) == 1, (
        "the organization was left with no active administrator -- "
        "this is the lockout the invariant exists to prevent"
    )

    # The loser's ORG_ADMIN genuinely survives: a rolled-back transaction, not a half-applied one.
    surviving = [
        m
        for m in (membership_a, membership_b)
        if OrganizationRole.ORG_ADMIN.value
        in OrganizationRoleAssignmentRepository(sessions).list_for_membership(m)
    ]
    assert len(surviving) == 1


def test_pg2_two_admins_deactivating_each_other_cannot_reach_zero(sessions: sessionmaker) -> None:
    """The same race through the other operation. Deactivation is the other way to zero."""
    organization, admins, _ = _seed(sessions, key_prefix=f"pg2-{uuid.uuid4().hex[:8]}", admins=2)
    (identity_a, membership_a), (identity_b, membership_b) = admins
    service = _service(sessions)

    outcomes = _run_concurrently(
        [
            lambda: service.deactivate_membership(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=membership_b,
            ),
            lambda: service.deactivate_membership(
                principal=_principal(identity_b),
                organization_id=organization.id,
                membership_id=membership_a,
            ),
        ]
    )

    refused = [r for _, r in outcomes if isinstance(r, LastAdministratorProtected)]
    succeeded = [r for _, r in outcomes if not isinstance(r, Exception)]
    # The loser may alternatively find its own authority gone, if it was the one deactivated first
    # -- that is `StaffAccessAuthorityLost`, and it is an equally correct refusal. `pg1` records why
    # the two guards overlap in a mutual-removal race.
    lost = [r for _, r in outcomes if isinstance(r, StaffAccessAuthorityLost)]

    assert len(succeeded) == 1, f"exactly one deactivation must commit, got {outcomes}"
    assert len(refused) + len(lost) == 1, f"the other must be refused, got {outcomes}"
    assert _active_admin_count(sessions, organization.id) == 1


def test_pg3_many_admins_racing_to_the_floor_still_leaves_one(sessions: sessionmaker) -> None:
    """Six administrators, six simultaneous self-removals.

    A stronger form of the same invariant: whatever order the lock grants, five may succeed and the
    sixth must not. This is also the shape a real "everyone resigns at once" mistake would take.
    """
    organization, admins, _ = _seed(sessions, key_prefix=f"pg3-{uuid.uuid4().hex[:8]}", admins=6)
    service = _service(sessions)

    outcomes = _run_concurrently(
        [
            (
                lambda identity=identity, membership=membership: service.remove_role(
                    principal=_principal(identity),
                    organization_id=organization.id,
                    membership_id=membership,
                    role_name="ORG_ADMIN",
                )
            )
            for identity, membership in admins
        ]
    )

    succeeded = [r for _, r in outcomes if not isinstance(r, Exception)]
    refused = [r for _, r in outcomes if isinstance(r, LastAdministratorProtected)]
    assert len(succeeded) == 5
    assert len(refused) == 1
    assert _active_admin_count(sessions, organization.id) == 1


# -- §25-A: the same role, twice, at once ----------------------------------------------------------


def test_pg4_two_admins_removing_the_same_role_produce_one_removal_and_one_conflict(
    sessions: sessionmaker,
) -> None:
    organization, admins, members = _seed(
        sessions, key_prefix=f"pg4-{uuid.uuid4().hex[:8]}", admins=2, members=1
    )
    (identity_a, _), (identity_b, _) = admins
    _, target_membership = members[0]
    service = _service(sessions)

    outcomes = _run_concurrently(
        [
            lambda: service.remove_role(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=target_membership,
                role_name="CLINICIAN",
            ),
            lambda: service.remove_role(
                principal=_principal(identity_b),
                organization_id=organization.id,
                membership_id=target_membership,
                role_name="CLINICIAN",
            ),
        ]
    )

    succeeded = [r for _, r in outcomes if not isinstance(r, Exception)]
    conflicts = [r for _, r in outcomes if isinstance(r, StaffAccessStateUnchanged)]
    assert len(succeeded) == 1, f"one real removal, never two: {outcomes}"
    assert len(conflicts) == 1, "the loser must be told it did not hold the role, not told it won"
    assert (
        OrganizationRoleAssignmentRepository(sessions).list_for_membership(target_membership)
        == frozenset()
    )
    # Exactly one audit row, because exactly one thing happened.
    with sessions() as session:
        rows = int(
            session.scalar(
                select(func.count())
                .select_from(ExchangeSecurityAuditEvent)
                .where(
                    ExchangeSecurityAuditEvent.event_type == "role_removed",
                    ExchangeSecurityAuditEvent.membership_id == target_membership,
                )
            )
            or 0
        )
    assert rows == 1


def test_pg5_two_admins_adding_the_same_role_converge_without_a_duplicate(
    sessions: sessionmaker,
) -> None:
    """The unique `(membership_id, role)` constraint, enforced by the database under real
    concurrency rather than by whichever caller checked first."""
    organization, admins, members = _seed(
        sessions, key_prefix=f"pg5-{uuid.uuid4().hex[:8]}", admins=2, members=1
    )
    (identity_a, _), (identity_b, _) = admins
    _, target_membership = members[0]
    service = _service(sessions)

    outcomes = _run_concurrently(
        [
            lambda: service.add_role(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=target_membership,
                role_name="ORG_ADMIN",
            ),
            lambda: service.add_role(
                principal=_principal(identity_b),
                organization_id=organization.id,
                membership_id=target_membership,
                role_name="ORG_ADMIN",
            ),
        ]
    )

    errors = [r for _, r in outcomes if isinstance(r, Exception)]
    assert errors == [], f"an idempotent grant must never fail: {errors}"
    with sessions() as session:
        duplicates = int(
            session.scalar(
                select(func.count())
                .select_from(OrganizationRoleAssignment)
                .where(
                    OrganizationRoleAssignment.membership_id == target_membership,
                    OrganizationRoleAssignment.role == OrganizationRole.ORG_ADMIN.value,
                )
            )
            or 0
        )
    assert duplicates == 1, "the database must hold exactly one row for this (membership, role)"
    # One real grant, one replay: exactly one `created` audit row.
    changed = [r for _, r in outcomes if getattr(r, "changed", False)]
    assert len(changed) == 1


# -- §25-B and §25-D: status racing roles, and reactivate racing deactivate -----------------


def test_pg6_deactivation_racing_a_role_grant_never_leaves_an_active_role_on_a_dead_membership(
    sessions: sessionmaker,
) -> None:
    """Whichever order the lock grants, the outcome must be coherent.

    If the deactivation wins, the grant is refused (`MembershipInactive` -- an inactive membership
    receives nothing). If the grant wins, both commit and the membership ends inactive holding the
    role, which is the documented model: roles survive deactivation and are what reactivation
    restores. What must never happen is a *third* outcome.
    """
    from daher_adapter.exchange.membership import MembershipInactive

    organization, admins, members = _seed(
        sessions, key_prefix=f"pg6-{uuid.uuid4().hex[:8]}", admins=2, members=1
    )
    (identity_a, _), (identity_b, _) = admins
    _, target_membership = members[0]
    service = _service(sessions)

    outcomes = _run_concurrently(
        [
            lambda: service.deactivate_membership(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=target_membership,
            ),
            lambda: service.add_role(
                principal=_principal(identity_b),
                organization_id=organization.id,
                membership_id=target_membership,
                role_name="ORG_ADMIN",
            ),
        ]
    )

    by_index = dict(outcomes)
    deactivation, grant = by_index[0], by_index[1]

    assert not isinstance(deactivation, Exception), f"the deactivation must succeed: {deactivation}"
    with sessions() as session:
        status = session.get(OrganizationMembership, target_membership).status
    assert status == OrganizationMembershipStatus.INACTIVE.value

    if isinstance(grant, Exception):
        assert isinstance(grant, MembershipInactive), f"unexpected grant failure: {grant!r}"
        assert OrganizationRole.ORG_ADMIN.value not in OrganizationRoleAssignmentRepository(
            sessions
        ).list_for_membership(target_membership)
    else:
        # The grant won the lock. The role is attached to an inactive membership, which grants
        # nothing (every `require_*` filters on ACTIVE first) and is exactly what reactivation would
        # restore -- the model, not a defect.
        assert OrganizationRole.ORG_ADMIN.value in OrganizationRoleAssignmentRepository(
            sessions
        ).list_for_membership(target_membership)

    # Either way: an inactive membership holding ORG_ADMIN must not count as an administrator.
    assert _active_admin_count(sessions, organization.id) == 2


def test_pg7_reactivate_racing_deactivate_reaches_a_deterministic_state(
    sessions: sessionmaker,
) -> None:
    """One of the two is a no-op conflict, and the final status is whichever one committed -- never
    a row left in some third state, and never both reported as successes."""
    organization, admins, members = _seed(
        sessions, key_prefix=f"pg7-{uuid.uuid4().hex[:8]}", admins=2, members=1
    )
    (identity_a, _), (identity_b, _) = admins
    _, target_membership = members[0]
    service = _service(sessions)

    # Start from INACTIVE, so both operations are meaningful requests.
    service.deactivate_membership(
        principal=_principal(identity_a),
        organization_id=organization.id,
        membership_id=target_membership,
    )

    outcomes = _run_concurrently(
        [
            lambda: service.reactivate_membership(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=target_membership,
            ),
            lambda: service.deactivate_membership(
                principal=_principal(identity_b),
                organization_id=organization.id,
                membership_id=target_membership,
            ),
        ]
    )

    succeeded = [(i, r) for i, r in outcomes if not isinstance(r, Exception)]
    conflicts = [r for _, r in outcomes if isinstance(r, StaffAccessStateUnchanged)]

    assert len(succeeded) == 1, f"exactly one transition can commit: {outcomes}"
    assert len(conflicts) == 1, "the other must be a truthful conflict, not a silent success"

    with sessions() as session:
        status = session.get(OrganizationMembership, target_membership).status
    winner_index = succeeded[0][0]
    expected = (
        OrganizationMembershipStatus.ACTIVE.value
        if winner_index == 0
        else OrganizationMembershipStatus.INACTIVE.value
    )
    assert status == expected, "the committed state must be the one the winner asked for"


# -- transactional audit coupling ------------------------------------------------------------------


def test_pg8_a_refused_last_admin_removal_rolls_back_both_the_delete_and_its_audit_row(
    sessions: sessionmaker,
) -> None:
    """The invariant is evaluated *after* the delete has been flushed, so a refusal depends on
    a real transaction rolling both back. This is the property SQLite cannot stand in for."""
    organization, admins, _ = _seed(sessions, key_prefix=f"pg8-{uuid.uuid4().hex[:8]}", admins=1)
    (identity, membership) = admins[0]
    service = _service(sessions)

    with sessions() as session:
        audit_before = int(
            session.scalar(
                select(func.count())
                .select_from(ExchangeSecurityAuditEvent)
                .where(ExchangeSecurityAuditEvent.membership_id == membership)
            )
            or 0
        )

    with pytest.raises(LastAdministratorProtected):
        service.remove_role(
            principal=_principal(identity),
            organization_id=organization.id,
            membership_id=membership,
            role_name="ORG_ADMIN",
        )

    assert OrganizationRole.ORG_ADMIN.value in OrganizationRoleAssignmentRepository(
        sessions
    ).list_for_membership(membership), "the flushed delete must have been rolled back"

    with sessions() as session:
        audit_after = int(
            session.scalar(
                select(func.count())
                .select_from(ExchangeSecurityAuditEvent)
                .where(ExchangeSecurityAuditEvent.membership_id == membership)
            )
            or 0
        )
    assert audit_after == audit_before, "a refused mutation must leave no audit row behind"


def test_pg9_every_committed_mutation_carries_exactly_one_audit_row(
    sessions: sessionmaker,
) -> None:
    organization, admins, members = _seed(
        sessions, key_prefix=f"pg9-{uuid.uuid4().hex[:8]}", admins=1, members=1
    )
    (actor_identity, _) = admins[0]
    target_identity, target_membership = members[0]
    service = _service(sessions)
    principal = _principal(actor_identity)

    service.add_role(
        principal=principal,
        organization_id=organization.id,
        membership_id=target_membership,
        role_name="ORG_ADMIN",
    )
    service.remove_role(
        principal=principal,
        organization_id=organization.id,
        membership_id=target_membership,
        role_name="ORG_ADMIN",
    )
    service.deactivate_membership(
        principal=principal,
        organization_id=organization.id,
        membership_id=target_membership,
    )
    service.reactivate_membership(
        principal=principal,
        organization_id=organization.id,
        membership_id=target_membership,
    )

    with sessions() as session:
        rows = session.scalars(
            select(ExchangeSecurityAuditEvent)
            .where(ExchangeSecurityAuditEvent.membership_id == target_membership)
            .order_by(ExchangeSecurityAuditEvent.created_at)
        ).all()

    # The `role_assigned` row from `_seed`'s own CLINICIAN grant is not here: that grant goes
    # through the repository directly, which writes no audit row. Every row below came from
    # the service.
    observed = [(row.event_type, row.role) for row in rows]
    assert observed == [
        ("role_assigned", "ORG_ADMIN"),
        ("role_removed", "ORG_ADMIN"),
        ("membership_deactivated", None),
        ("membership_reactivated", None),
    ]
    for row in rows:
        assert row.actor_identity_id == actor_identity
        assert row.organization_id == organization.id
        assert row.subject_identity_id == target_identity
        assert row.created_at is not None
        assert row.outcome in ("created", "updated")


def test_pg10_platform_admin_role_is_refused_by_the_database_as_well(
    sessions: sessionmaker,
) -> None:
    """Defence in depth, stated rather than assumed.

    The service refuses `PLATFORM_ADMIN` before any transaction opens. The database refuses it too:
    `ck_exchange_org_role_assignments_role` and `ck_exchange_security_audit_events_role` both
    enumerate the four real organization roles. This matters because it is what makes NC-ACCESS-5
    (the negative control that removes the service check) fail *closed* rather than succeed -- the
    control turns the clean 422 into a 500, which is still a refusal.

    Only PostgreSQL enforces this: SQLite does not check `VARCHAR` length and honours CHECK
    constraints only when they are created, which `create_all` does -- but the audit-table
    constraint in particular is only meaningful against the migrated schema this file runs on.
    """
    from sqlalchemy.exc import IntegrityError

    organization, admins, _ = _seed(sessions, key_prefix=f"pg10-{uuid.uuid4().hex[:8]}", admins=1)
    (_, membership) = admins[0]

    with pytest.raises(IntegrityError):
        with sessions() as session, session.begin():
            session.add(OrganizationRoleAssignment(membership_id=membership, role="PLATFORM_ADMIN"))
