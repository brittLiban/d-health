"""DHEALTH-ACCESS-CAPABILITIES-001: real PostgreSQL qualification of capability administration.

The SQLite suites prove every *rule* the access model has. They cannot prove the one property the
last-administrator invariant actually depends on, and they cannot prove it for a specific reason:

**SQLAlchemy's SQLite dialect omits `FOR UPDATE` entirely.** `staff_access.py` locks the
organization row precisely so an organization's whole access lifecycle serializes against itself —
two administrators removing *each other's* authority touch two different membership rows, both pass
a pre-flight check, and the organization ends with zero. On SQLite that lock is a silent no-op and
the tests pass for an unrelated reason (its own writer serialization), which is the "green for the
wrong reason" shape this repository has been burned by before.

§22 made the invariant harder, not easier: it now counts *effective* authority, which is an
`EXISTS`/`NOT EXISTS` query over role assignments and capability overrides rather than a count of
role rows. That query has to give the right answer inside a concurrent transaction, and this is the
only place that is demonstrated.

What is qualified here and nowhere else:

- **Two administrators denying each other `staff.access.manage` at the same instant.** The
  capability-era version of the classic last-admin race. Exactly one must succeed.
- **Removing a role and denying the capability simultaneously**, on two different administrators —
  the mixed-pathway race, which only exists because authority now has two sources.
- **A grant and a deny of the same capability to the same member, concurrently.** One row, one
  opinion: whichever commits last decides, and there is never a member holding both.
- **Deactivating a membership while a capability is granted to it.** The grant must not resurrect
  authority the deactivation removed.
- **Duplicate concurrent grants**: one row, no unique-violation reaching a caller.
- The **unique constraint** and the **effect CHECK**, against a real backend.

**The proof is the barrier plus the final state, not an inspection of the lock.** Nothing here
reads `pg_locks`.

**Manual / opt-in only**, like every other live-infra test in this directory: skipped unless every
`DHCAP_PG_*` variable is set. Never point it at a real facility database.

Usage::

    DHCAP_PG_HOST=127.0.0.1 DHCAP_PG_PORT=5432 DHCAP_PG_USER=adapter \\
    DHCAP_PG_PASSWORD=... DHCAP_PG_DATABASE=dhcap_qual \\
        pytest adapter/tests/integration/test_dhealthaccesscap001_postgres_qualification.py -v

The target database must already be migrated through `0036_capability_overrides`.

All data here is synthetic. No real patient data is used.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.capabilities import Capability, OverrideEffect
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    MembershipCapabilityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    MembershipCapabilityOverride,
    OrganizationRole,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
from daher_adapter.exchange.staff_access import (
    LastAdministratorProtected,
    StaffAccessAuthorityLost,
    StaffAccessService,
)

PG_HOST = os.environ.get("DHCAP_PG_HOST")
PG_PORT = os.environ.get("DHCAP_PG_PORT")
PG_USER = os.environ.get("DHCAP_PG_USER")
PG_PASSWORD = os.environ.get("DHCAP_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHCAP_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHCAP_PG_* env vars) -- never in CI",
)

ISSUER = "https://pg-qualification.invalid/realms/dhealth-exchange"


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    # A pool large enough for every concurrent thread to hold its own real connection: a thread
    # waiting on the *pool* rather than on the row lock would serialize the race this file exists
    # to create, and the test would pass without exercising the lock at all.
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
        capabilities=MembershipCapabilityRepository(sessions),
        security_audit=ExchangeSecurityAuditRepository(),
    )


def _principal(identity_id: uuid.UUID) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        identity_id=identity_id, issuer=ISSUER, subject=str(identity_id), platform_admin=False
    )


def _seed(sessions: sessionmaker, *, prefix: str, admins: int = 2, clinicians: int = 0):
    """A provider organization with `admins` administrators and `clinicians` clinicians.

    Built through the same repositories `exchange/api.py` uses, so every uniqueness, role-matrix
    and tenant rule a real request is subject to applies here too."""
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    memberships = OrganizationMembershipRepository(sessions)
    roles = OrganizationRoleAssignmentRepository(sessions)

    organization = organizations.create(org_type="PROVIDER", name=f"{prefix}-clinic")

    def member(subject: str, role: OrganizationRole):
        identity, _ = identities.get_or_create(issuer=ISSUER, subject=subject, display_name=subject)
        membership, _ = memberships.create(organization_id=organization.id, identity_id=identity.id)
        roles.assign(membership_id=membership.id, role=role)
        return identity.id, membership.id

    admin_ids = [member(f"{prefix}-admin-{i}", OrganizationRole.ORG_ADMIN) for i in range(admins)]
    clinician_ids = [
        member(f"{prefix}-clin-{i}", OrganizationRole.CLINICIAN) for i in range(clinicians)
    ]
    return organization, admin_ids, clinician_ids


def _run_concurrently(operations):
    """Each callable on its own thread, released simultaneously by a barrier.

    A barrier maximizes overlap; it cannot *guarantee* the transactions interleave at the worst
    instant, which is why every assertion below is about final state rather than timing."""
    barrier = threading.Barrier(len(operations))
    outcomes: list[tuple[int, object]] = []
    lock = threading.Lock()

    def worker(index: int, operation):
        barrier.wait()
        try:
            result = operation()
        except Exception as error:  # noqa: BLE001 -- the exception *is* the observation
            result = error
        with lock:
            outcomes.append((index, result))

    threads = [
        threading.Thread(target=worker, args=(index, operation))
        for index, operation in enumerate(operations)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "a worker deadlocked"
    return [result for _, result in sorted(outcomes)]


def _effective_admins(sessions, organization_id) -> int:
    """Counted through the product's own query, in a fresh session -- the committed truth."""
    with sessions() as session:
        return MembershipCapabilityRepository(sessions).count_active_with_capability_in_session(
            session,
            organization_id=organization_id,
            capability=Capability.STAFF_ACCESS_MANAGE,
        )


# == the last-administrator races =================================================================


def test_capqual1_two_administrators_cannot_deny_each_other_simultaneously(sessions):
    """The classic race, in its capability-era form.

    Two administrators, each denying the other `staff.access.manage` at the same instant. Without
    the organization lock both read "there are two administrators", both write, and the
    organization is left with none -- unadministrable, and recoverable only by a platform
    administrator. Exactly one must succeed.

    **The loser may be refused by either of two protections, and which one fires is a real race.**
    This surprised the first version of this test, which insisted on `LastAdministratorProtected`:

    * if the loser's transaction reaches the invariant, it is `LastAdministratorProtected` -- the
      count of effective administrators would hit zero;
    * if the *winner* commits first, the loser's own live re-authorization
      (`_require_current_admin_authority`, re-read inside the lock) finds it has just been denied
      `staff.access.manage` and raises `StaffAccessAuthorityLost` before the invariant is even
      consulted. An administrator stripped a moment ago cannot finish administering, which is
      DHEALTH-NATIVE-ACCESS-001 §14 doing its job.

    Both are correct refusals and both leave the organization administrable, so the assertion is
    about the *outcome* -- one success, one refusal, one administrator standing -- rather than
    about which guard happened to get there first. Insisting on one exception type would have been
    a test asserting a scheduling accident."""
    prefix = f"capqual1-{uuid.uuid4().hex[:8]}"
    organization, admins, _ = _seed(sessions, prefix=prefix)
    (identity_a, membership_a), (identity_b, membership_b) = admins
    service = _service(sessions)

    results = _run_concurrently(
        [
            lambda: service.set_capability_override(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=membership_b,
                capability_key=Capability.STAFF_ACCESS_MANAGE.value,
                effect=OverrideEffect.DENY,
            ),
            lambda: service.set_capability_override(
                principal=_principal(identity_b),
                organization_id=organization.id,
                membership_id=membership_a,
                capability_key=Capability.STAFF_ACCESS_MANAGE.value,
                effect=OverrideEffect.DENY,
            ),
        ]
    )

    succeeded = [r for r in results if not isinstance(r, Exception)]
    refused = [
        r for r in results if isinstance(r, (LastAdministratorProtected, StaffAccessAuthorityLost))
    ]
    assert len(succeeded) == 1, (
        f"both denials committed -- the organization has no admins: {results}"
    )
    assert len(refused) == 1, f"the loser was not refused by either guard: {results}"
    assert _effective_admins(sessions, organization.id) == 1


def test_capqual2_a_role_removal_and_a_capability_deny_cannot_both_win(sessions):
    """The **mixed-pathway** race, which exists only because authority now has two sources.

    One administrator removes the other's `ORG_ADMIN` role while the other denies the first's
    `staff.access.manage` capability. Both pathways reach zero, and neither knows about the other
    unless the invariant is computed over effective authority inside one lock -- which is exactly
    what §22 rebuilt and what NC-CAP-4 breaks."""
    prefix = f"capqual2-{uuid.uuid4().hex[:8]}"
    organization, admins, _ = _seed(sessions, prefix=prefix)
    (identity_a, membership_a), (identity_b, membership_b) = admins
    service = _service(sessions)

    results = _run_concurrently(
        [
            lambda: service.remove_role(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=membership_b,
                role_name=OrganizationRole.ORG_ADMIN.value,
            ),
            lambda: service.set_capability_override(
                principal=_principal(identity_b),
                organization_id=organization.id,
                membership_id=membership_a,
                capability_key=Capability.STAFF_ACCESS_MANAGE.value,
                effect=OverrideEffect.DENY,
            ),
        ]
    )

    succeeded = [r for r in results if not isinstance(r, Exception)]
    assert len(succeeded) == 1, f"two different pathways both reached zero: {results}"
    assert _effective_admins(sessions, organization.id) == 1


def test_capqual3_a_grant_keeps_the_organization_administrable_under_a_race(sessions):
    """The recovery direction stays open, which is what stops the invariant being a trap.

    One administrator grants a clinician `staff.access.manage` while the other steps down. Granting
    is never blocked by the invariant, so whichever order they commit in the organization always
    has at least one administrator -- and both operations may legitimately succeed here, unlike the
    races above."""
    prefix = f"capqual3-{uuid.uuid4().hex[:8]}"
    organization, admins, clinicians = _seed(sessions, prefix=prefix, admins=1, clinicians=1)
    (identity_a, membership_a) = admins[0]
    (_, clinician_membership) = clinicians[0]
    service = _service(sessions)

    results = _run_concurrently(
        [
            lambda: service.set_capability_override(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=clinician_membership,
                capability_key=Capability.STAFF_ACCESS_MANAGE.value,
                effect=OverrideEffect.GRANT,
            ),
            lambda: service.set_capability_override(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=membership_a,
                capability_key=Capability.STAFF_ACCESS_MANAGE.value,
                effect=OverrideEffect.DENY,
            ),
        ]
    )

    # The deny may legitimately lose if it commits first (there was only one administrator then).
    # What must never happen is zero administrators.
    assert _effective_admins(sessions, organization.id) >= 1, results


# == override races ===============================================================================


def test_capqual4_a_grant_and_a_deny_of_the_same_capability_leave_one_row(sessions):
    """One member, one capability, two administrators disagreeing at the same instant.

    The model holds exactly one opinion per (membership, capability) and the unique constraint
    enforces it. Whichever transaction commits last decides; what must never exist is a member who
    is both granted and denied, because the resolver would then need an ordering rule for a state
    that should be unreachable."""
    prefix = f"capqual4-{uuid.uuid4().hex[:8]}"
    organization, admins, clinicians = _seed(sessions, prefix=prefix, admins=2, clinicians=1)
    (identity_a, _), (identity_b, _) = admins
    (_, target) = clinicians[0]
    service = _service(sessions)

    results = _run_concurrently(
        [
            lambda: service.set_capability_override(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=target,
                capability_key=Capability.BILLING_OPERATE.value,
                effect=OverrideEffect.GRANT,
            ),
            lambda: service.set_capability_override(
                principal=_principal(identity_b),
                organization_id=organization.id,
                membership_id=target,
                capability_key=Capability.BILLING_OPERATE.value,
                effect=OverrideEffect.DENY,
            ),
        ]
    )
    assert all(not isinstance(r, Exception) for r in results), results

    with sessions() as session:
        rows = session.scalars(
            select(MembershipCapabilityOverride).where(
                MembershipCapabilityOverride.membership_id == target,
                MembershipCapabilityOverride.capability == Capability.BILLING_OPERATE.value,
            )
        ).all()
    assert len(rows) == 1, f"a member holds two opinions about one capability: {rows}"
    assert rows[0].effect in (OverrideEffect.GRANT.value, OverrideEffect.DENY.value)


def test_capqual5_two_identical_grants_produce_one_row_and_no_error(sessions):
    """A double-submitted grant. Both callers are served, one row exists, and neither sees a
    constraint violation -- the unique index is the second layer, not the error message."""
    prefix = f"capqual5-{uuid.uuid4().hex[:8]}"
    organization, admins, clinicians = _seed(sessions, prefix=prefix, admins=1, clinicians=1)
    (identity_a, _) = admins[0]
    (_, target) = clinicians[0]
    service = _service(sessions)

    def grant():
        return service.set_capability_override(
            principal=_principal(identity_a),
            organization_id=organization.id,
            membership_id=target,
            capability_key=Capability.BILLING_OPERATE.value,
            effect=OverrideEffect.GRANT,
        )

    results = _run_concurrently([grant, grant])
    assert all(not isinstance(r, Exception) for r in results), results

    with sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(MembershipCapabilityOverride)
            .where(MembershipCapabilityOverride.membership_id == target)
        )
    assert count == 1


def test_capqual6_deactivation_wins_over_a_concurrent_grant(sessions):
    """A capability granted to somebody being deactivated must not resurrect their authority.

    Both operations may commit -- they touch different rows and neither reaches zero
    administrators. The invariant that matters is the *resolved* one: an inactive membership holds
    nothing, whatever override rows exist beside it."""
    prefix = f"capqual6-{uuid.uuid4().hex[:8]}"
    organization, admins, clinicians = _seed(sessions, prefix=prefix, admins=2, clinicians=1)
    (identity_a, _), (identity_b, _) = admins
    (_, target) = clinicians[0]
    service = _service(sessions)
    repository = MembershipCapabilityRepository(sessions)

    _run_concurrently(
        [
            lambda: service.deactivate_membership(
                principal=_principal(identity_a),
                organization_id=organization.id,
                membership_id=target,
            ),
            lambda: service.set_capability_override(
                principal=_principal(identity_b),
                organization_id=organization.id,
                membership_id=target,
                capability_key=Capability.BILLING_OPERATE.value,
                effect=OverrideEffect.GRANT,
            ),
        ]
    )

    memberships = OrganizationMembershipRepository(sessions)
    membership = memberships.get(target)
    effective = repository.effective_for_membership(
        membership_id=membership.id,
        membership_status=membership.status,
        organization_type=organization.org_type,
        organization_status=organization.status,
    )
    if membership.status != "ACTIVE":
        assert effective == frozenset(), (
            "an inactive membership resolved to real authority -- a grant outlived the "
            f"deactivation: {sorted(effective)}"
        )


# == the schema itself ============================================================================


def test_capqual7_a_member_cannot_hold_two_opinions_about_one_capability(sessions):
    """The unique constraint, with the application bypassed.

    This is the layer that survives an application bug, and it is unenforced under SQLite because
    that engine has foreign keys and much of this discipline switched off in the unit suite."""
    prefix = f"capqual7-{uuid.uuid4().hex[:8]}"
    organization, admins, clinicians = _seed(sessions, prefix=prefix, admins=1, clinicians=1)
    (identity_a, _) = admins[0]
    (_, target) = clinicians[0]

    with sessions.begin() as session:
        session.add(
            MembershipCapabilityOverride(
                membership_id=target,
                capability=Capability.BILLING_OPERATE.value,
                effect=OverrideEffect.GRANT.value,
                created_by_identity_id=identity_a,
            )
        )

    with pytest.raises((IntegrityError, DBAPIError)):
        with sessions.begin() as session:
            session.add(
                MembershipCapabilityOverride(
                    membership_id=target,
                    capability=Capability.BILLING_OPERATE.value,
                    effect=OverrideEffect.DENY.value,
                    created_by_identity_id=identity_a,
                )
            )
    del organization


def test_capqual8_an_effect_outside_grant_and_deny_is_refused_by_the_schema(sessions):
    """There is no third effect, and the database says so.

    A row with `effect = 'MAYBE'` would be honoured by neither branch of the resolver and would
    simply make its capability disappear -- an invisible authority loss, which is exactly the kind
    of state a CHECK constraint exists to make unrepresentable."""
    prefix = f"capqual8-{uuid.uuid4().hex[:8]}"
    _organization, admins, clinicians = _seed(sessions, prefix=prefix, admins=1, clinicians=1)
    (identity_a, _) = admins[0]
    (_, target) = clinicians[0]

    with pytest.raises((IntegrityError, DBAPIError)):
        with sessions.begin() as session:
            session.add(
                MembershipCapabilityOverride(
                    membership_id=target,
                    capability=Capability.BILLING_OPERATE.value,
                    effect="MAYBE",
                    created_by_identity_id=identity_a,
                )
            )
