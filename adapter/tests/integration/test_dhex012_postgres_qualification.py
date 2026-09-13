"""DH-EX-012/ADR-044: real PostgreSQL qualification of explicit order-level lab-result release --
role-constraint width (``LAB_RESULT_RELEASER`` is 19 characters, exceeding the pre-0015
``VARCHAR(16)``), release uniqueness enforced by the database itself (not just application logic),
completeness gating, release atomicity under a real transactional engine, and the three genuinely
concurrent scenarios this ticket introduces (10-way concurrent release; final-result-vs-release
race with both valid orderings; two-different-releasers race). SQLite's threading model is not
sufficient evidence for production PostgreSQL row-locking behavior -- matching the reasoning
``test_dhex011_postgres_qualification.py`` already established, applied here to this ticket's own
new repository and invariant.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHEX012_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHEX012_PG_HOST=127.0.0.1 \\
    DHEX012_PG_PORT=5432 \\
    DHEX012_PG_USER=dhex012 \\
    DHEX012_PG_PASSWORD=dhex012 \\
    DHEX012_PG_DATABASE=dhex012_qual \\
        pytest adapter/tests/integration/test_dhex012_postgres_qualification.py -v

The target database must already have migrations applied through ``0015_exchange_result_release``
(``alembic upgrade head``) before running this file -- it does not run migrations itself. Migration
upgrade/downgrade itself is qualified via the `alembic` CLI directly (see PROJECT_STATE.md's
DH-EX-012 entry for that evidence), not by this pytest file, matching every prior ticket's own
downgrade-guard qualification method in this repository.
"""

from __future__ import annotations

import os
import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import LabOrder, LabOrderStatus, LabResultRelease
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    LabResultReleaseRepository,
    LabResultRepository,
    LegacyCompletedOrderHasNoRelease,
    ResultsNotReadyForRelease,
)
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHEX012_PG_HOST")
PG_PORT = os.environ.get("DHEX012_PG_PORT")
PG_USER = os.environ.get("DHEX012_PG_USER")
PG_PASSWORD = os.environ.get("DHEX012_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX012_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHEX012_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed_in_progress_order(sessions: sessionmaker, *, key_prefix: str, item_count: int = 1):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)
    workflow = LabWorkflowRepository(sessions, organizations)
    results = LabResultRepository(sessions)
    releases = LabResultReleaseRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)

    provider = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")
    lab = organizations.create(org_type="LAB", name=f"{key_prefix}-lab")
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    entries = []
    for i in range(item_count):
        entry, _ = catalog.create_entry(
            code=f"{key_prefix}-TEST-{i}", display_name="Qualification Test"
        )
        catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        entries.append(entry)
    patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-1",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:practitioner",
        source_identity_value="D-1",
    )
    order = orders.create(
        idempotency_key=key_prefix,
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id) for entry in entries],
    ).order

    selections.assign_by_provider(
        lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
    )
    workflow.accept(order_id=order.id, lab_organization_id=lab.id)
    workflow.record_collection(order_id=order.id, lab_organization_id=lab.id)
    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)

    return {
        "provider": provider,
        "lab": lab,
        "order": orders.get(order.id),
        "orders": orders,
        "results": results,
        "releases": releases,
        "identities": identities,
    }


def _new_releaser(seed: dict, *, name: str):
    identity, _ = seed["identities"].get_or_create(
        issuer="https://pg-qualification.example", subject=name
    )
    return identity


def _new_labtech(seed: dict, *, name: str):
    identity, _ = seed["identities"].get_or_create(
        issuer="https://pg-qualification.example", subject=name
    )
    return identity


# -- role width / constraint ---------------------------------------------------------------------


def test_lab_result_releaser_role_persists_within_widened_column(sessions: sessionmaker) -> None:
    """`LAB_RESULT_RELEASER` is 19 characters -- the pre-0015 `VARCHAR(16)` could not hold it.
    Confirms the CHECK constraint and the widened column accept it against real PostgreSQL, not
    just SQLite (which never enforces VARCHAR length), on BOTH columns 0015 widened:
    `exchange_organization_role_assignments.role` (via the real role-assignment repository) and
    `exchange_security_audit_events.role` (via a real `role_assigned` audit write) -- the second
    is not otherwise exercised by any test that inserts a 19-character role value against real
    PostgreSQL specifically (the SQLite HTTP suite does write this column, but SQLite never
    enforces VARCHAR length)."""
    from daher_adapter.exchange.membership import (
        OrganizationMembershipRepository,
        OrganizationRoleAssignmentRepository,
    )
    from daher_adapter.exchange.models import OrganizationRole

    seed = _seed_in_progress_order(sessions, key_prefix=f"rolewidth-{uuid.uuid4().hex[:8]}")
    memberships = OrganizationMembershipRepository(sessions)
    role_assignments = OrganizationRoleAssignmentRepository(sessions)
    identity = _new_releaser(seed, name="role-width-releaser")
    membership, _created = memberships.create(
        organization_id=seed["lab"].id, identity_id=identity.id
    )
    record, _created = role_assignments.assign(
        membership_id=membership.id, role=OrganizationRole.LAB_RESULT_RELEASER
    )
    assert record.role == "LAB_RESULT_RELEASER"

    from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository

    audit = ExchangeSecurityAuditRepository()
    with sessions.begin() as session:
        audit.record(
            session,
            event_type="role_assigned",
            actor_identity_id=identity.id,
            organization_id=seed["lab"].id,
            membership_id=membership.id,
            role="LAB_RESULT_RELEASER",
            outcome="created",
        )
    with sessions() as session:
        audit_role_value = session.execute(
            text(
                "SELECT role FROM exchange_security_audit_events "
                "WHERE membership_id = :mid AND role = 'LAB_RESULT_RELEASER'"
            ),
            {"mid": str(membership.id)},
        ).scalar_one()
    assert audit_role_value == "LAB_RESULT_RELEASER"

    with sessions() as session:
        role_value = session.execute(
            text(
                "SELECT role FROM exchange_organization_role_assignments "
                "WHERE membership_id = :mid AND role = 'LAB_RESULT_RELEASER'"
            ),
            {"mid": str(membership.id)},
        ).scalar_one()
    assert role_value == "LAB_RESULT_RELEASER"


# -- release uniqueness enforced by the database itself ------------------------------------------


def test_database_rejects_a_second_release_row_for_the_same_order(sessions: sessionmaker) -> None:
    """The UNIQUE constraint on `lab_order_id` is a real database-level guarantee, not merely an
    application-level check -- proven by bypassing the repository's own idempotency check and
    inserting a second row directly."""
    seed = _seed_in_progress_order(sessions, key_prefix=f"uniq-{uuid.uuid4().hex[:8]}")
    order = seed["order"]
    seed["results"].record_numeric(
        recorded_by_identity_id=_new_labtech(seed, name=f"labtech-1-{uuid.uuid4().hex[:6]}").id,
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=seed["lab"].id,
        value=Decimal("1"),
    )
    releaser_a = _new_releaser(seed, name="uniq-releaser-a")
    seed["releases"].release(
        order_id=order.id, lab_organization_id=seed["lab"].id, released_by_identity_id=releaser_a.id
    )
    releaser_b = _new_releaser(seed, name="uniq-releaser-b")

    with sessions() as session, pytest.raises(IntegrityError):
        session.add(
            LabResultRelease(
                lab_order_id=order.id,
                lab_organization_id=seed["lab"].id,
                released_by_identity_id=releaser_b.id,
            )
        )
        session.commit()


# -- completeness gating ---------------------------------------------------------------------


def test_release_refused_against_real_postgres_when_a_result_is_missing(
    sessions: sessionmaker,
) -> None:
    seed = _seed_in_progress_order(
        sessions, key_prefix=f"incomplete-{uuid.uuid4().hex[:8]}", item_count=2
    )
    order = seed["order"]
    seed["results"].record_numeric(
        recorded_by_identity_id=_new_labtech(seed, name=f"labtech-2-{uuid.uuid4().hex[:6]}").id,
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=seed["lab"].id,
        value=Decimal("1"),
    )
    releaser = _new_releaser(seed, name="incomplete-releaser")

    with pytest.raises(ResultsNotReadyForRelease):
        seed["releases"].release(
            order_id=order.id,
            lab_organization_id=seed["lab"].id,
            released_by_identity_id=releaser.id,
        )
    with sessions() as session:
        status = session.scalar(select(LabOrder.status).where(LabOrder.id == order.id))
    assert status == "IN_PROGRESS"


# -- legacy grandfathered order -----------------------------------------------------------------


def test_legacy_completed_order_with_no_release_row_refuses_against_real_postgres(
    sessions: sessionmaker,
) -> None:
    seed = _seed_in_progress_order(sessions, key_prefix=f"legacy-{uuid.uuid4().hex[:8]}")
    order = seed["order"]
    seed["results"].record_numeric(
        recorded_by_identity_id=_new_labtech(seed, name=f"labtech-3-{uuid.uuid4().hex[:6]}").id,
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=seed["lab"].id,
        value=Decimal("1"),
    )
    with sessions() as session:
        session.execute(
            LabOrder.__table__.update()
            .where(LabOrder.id == order.id)
            .values(status=LabOrderStatus.COMPLETED.value)
        )
        session.commit()

    releaser = _new_releaser(seed, name="legacy-releaser")
    with pytest.raises(LegacyCompletedOrderHasNoRelease):
        seed["releases"].release(
            order_id=order.id,
            lab_organization_id=seed["lab"].id,
            released_by_identity_id=releaser.id,
        )
    assert seed["releases"].get_for_order(order.id) is None


# -- atomicity under a real transactional engine ---------------------------------------------


def test_release_insert_and_status_write_roll_back_together_on_real_postgres(
    sessions: sessionmaker,
) -> None:
    """A failure injected between the release insert and the transaction's commit must leave no
    partial state -- neither a `LabResultRelease` row nor a `COMPLETED` status survives -- proving
    PostgreSQL's own transactional guarantee backs `LabResultReleaseRepository`'s single-transaction
    design, not just SQLite's simulated one."""
    seed = _seed_in_progress_order(sessions, key_prefix=f"atomic-{uuid.uuid4().hex[:8]}")
    order = seed["order"]
    seed["results"].record_numeric(
        recorded_by_identity_id=_new_labtech(seed, name=f"labtech-4-{uuid.uuid4().hex[:6]}").id,
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=seed["lab"].id,
        value=Decimal("1"),
    )
    releaser = _new_releaser(seed, name="atomic-releaser")

    class _SimulatedFailure(RuntimeError):
        pass

    with pytest.raises(_SimulatedFailure), sessions.begin() as session:
        seed["releases"].release(
            order_id=order.id,
            lab_organization_id=seed["lab"].id,
            released_by_identity_id=releaser.id,
            session=session,
        )
        raise _SimulatedFailure("simulated failure before commit")

    with sessions() as session:
        status = session.scalar(select(LabOrder.status).where(LabOrder.id == order.id))
        release_count = session.scalar(
            select(func.count())
            .select_from(LabResultRelease)
            .where(LabResultRelease.lab_order_id == order.id)
        )
    assert status == "IN_PROGRESS"
    assert release_count == 0


# -- concurrency ------------------------------------------------------------------------------


def test_10way_concurrent_release_converges_to_one_record(sessions: sessionmaker) -> None:
    """10 threads call `release` for the same ready order simultaneously against real PostgreSQL
    row-locking. Exactly one must actually create the row; every other caller observes a safe
    idempotent replay of that same row."""
    seed = _seed_in_progress_order(sessions, key_prefix=f"tenway-{uuid.uuid4().hex[:8]}")
    order = seed["order"]
    seed["results"].record_numeric(
        recorded_by_identity_id=_new_labtech(seed, name=f"labtech-5-{uuid.uuid4().hex[:6]}").id,
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=seed["lab"].id,
        value=Decimal("1"),
    )

    results: list[tuple[str, object]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def attempt(i: int) -> None:
        try:
            releaser = _new_releaser(seed, name=f"tenway-releaser-{i}")
            barrier.wait(timeout=15)
            record, created = seed["releases"].release(
                order_id=order.id,
                lab_organization_id=seed["lab"].id,
                released_by_identity_id=releaser.id,
            )
            with lock:
                results.append(("ok", (record.id, created)))
        except BaseException as error:  # noqa: BLE001
            with lock:
                results.append(("error", type(error).__name__))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 10
    assert all(outcome == "ok" for outcome, _ in results), results
    ids = {payload[0] for _outcome, payload in results}
    assert len(ids) == 1
    created_flags = [payload[1] for _outcome, payload in results]
    assert created_flags.count(True) == 1

    with sessions() as session:
        row_count = session.scalar(
            select(func.count())
            .select_from(LabResultRelease)
            .where(LabResultRelease.lab_order_id == order.id)
        )
    assert row_count == 1


def _run_final_result_vs_release_once(sessions: sessionmaker, i: int) -> dict[str, object]:
    seed = _seed_in_progress_order(
        sessions, key_prefix=f"fvr-{i}-{uuid.uuid4().hex[:8]}", item_count=2
    )
    order = seed["order"]
    seed["results"].record_numeric(
        recorded_by_identity_id=_new_labtech(seed, name=f"labtech-6-{uuid.uuid4().hex[:6]}").id,
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=seed["lab"].id,
        value=Decimal("1"),
    )
    releaser = _new_releaser(seed, name=f"fvr-releaser-{i}")
    submit_actor = _new_labtech(seed, name=f"labtech-7-{uuid.uuid4().hex[:6]}")

    outcomes: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def do_submit() -> None:
        try:
            barrier.wait(timeout=15)
            seed["results"].record_numeric(
                recorded_by_identity_id=submit_actor.id,
                order_id=order.id,
                lab_order_item_id=order.items[1].id,
                lab_organization_id=seed["lab"].id,
                value=Decimal("2"),
            )
            outcomes["submit"] = "ok"
        except BaseException as error:  # noqa: BLE001
            outcomes["submit"] = ("error", type(error).__name__)

    def do_release() -> None:
        try:
            barrier.wait(timeout=15)
            _record, created = seed["releases"].release(
                order_id=order.id,
                lab_organization_id=seed["lab"].id,
                released_by_identity_id=releaser.id,
            )
            outcomes["release"] = ("ok", created)
        except ResultsNotReadyForRelease:
            outcomes["release"] = ("refused", None)
        except BaseException as error:  # noqa: BLE001
            outcomes["release"] = ("error", type(error).__name__)

    # Alternating which thread's `.start()` call comes first matters: with a fixed start order,
    # the first-started thread wins the row-lock acquisition almost every time (thread-launch
    # overhead dwarfs the barrier's synchronization window), which would silently make this test
    # exercise only one of its two claimed orderings -- confirmed directly against SQLite in
    # `test_exchange_results.py`, where a fixed order produced the same ordering 20/20 times.
    t1 = threading.Thread(target=do_submit)
    t2 = threading.Thread(target=do_release)
    if i % 2 == 0:
        t1.start()
        t2.start()
    else:
        t2.start()
        t1.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    with sessions() as session:
        outcomes["final_status"] = session.scalar(
            select(LabOrder.status).where(LabOrder.id == order.id)
        )
        outcomes["release_count"] = session.scalar(
            select(func.count())
            .select_from(LabResultRelease)
            .where(LabResultRelease.lab_order_id == order.id)
        )
    return outcomes


def test_final_result_vs_release_race_both_valid_orderings(sessions: sessionmaker) -> None:
    """Racing the last missing result's submission against a release attempt: either ordering is
    valid, but each must be internally coherent -- a release that ran before the final result
    landed is refused (never completes a half-resulted order); one that ran after succeeds. The
    submission itself always succeeds regardless of which ordering occurred. Run enough repetitions
    to actually observe both orderings, not merely tolerate either one -- a run that happened to
    take the same branch every time would not be evidence the other branch is handled correctly."""
    observed: list[str] = []
    for i in range(10):
        outcomes = _run_final_result_vs_release_once(sessions, i)
        assert outcomes["submit"] == "ok", (i, outcomes)

        if outcomes["final_status"] == LabOrderStatus.COMPLETED.value:
            assert outcomes["release"][0] == "ok", (i, outcomes)
            assert outcomes["release_count"] == 1, (i, outcomes)
            observed.append("release_second")
        else:
            assert outcomes["final_status"] == LabOrderStatus.IN_PROGRESS.value, (i, outcomes)
            assert outcomes["release"][0] == "refused", (i, outcomes)
            assert outcomes["release_count"] == 0, (i, outcomes)
            observed.append("release_first")

    assert set(observed) == {"release_first", "release_second"}, (
        f"expected both orderings across 10 repetitions, observed only: {set(observed)} -- "
        f"distribution: {observed}"
    )


def test_two_different_releasers_race_no_overwrite(sessions: sessionmaker) -> None:
    """Two distinct, equally-authorized releasers call `release` concurrently on the same ready
    order -- exactly one may create the row; the other observes that same row back unchanged."""
    seed = _seed_in_progress_order(sessions, key_prefix=f"tworel-{uuid.uuid4().hex[:8]}")
    order = seed["order"]
    seed["results"].record_numeric(
        recorded_by_identity_id=_new_labtech(seed, name=f"labtech-8-{uuid.uuid4().hex[:6]}").id,
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=seed["lab"].id,
        value=Decimal("1"),
    )
    releaser_a = _new_releaser(seed, name="tworel-a")
    releaser_b = _new_releaser(seed, name="tworel-b")

    results: list[tuple] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def attempt(identity_id) -> None:
        barrier.wait(timeout=15)
        record, created = seed["releases"].release(
            order_id=order.id,
            lab_organization_id=seed["lab"].id,
            released_by_identity_id=identity_id,
        )
        with lock:
            results.append((record, created))

    t1 = threading.Thread(target=attempt, args=(releaser_a.id,))
    t2 = threading.Thread(target=attempt, args=(releaser_b.id,))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert len(results) == 2
    created_flags = [created for _record, created in results]
    assert created_flags.count(True) == 1
    winning_identity = next(
        record.released_by_identity_id for record, created in results if created
    )
    for record, _created in results:
        assert record.released_by_identity_id == winning_identity
        assert record.id == results[0][0].id


# -- provider-visibility transition -----------------------------------------------------------


def test_provider_visibility_gate_flips_on_release_against_real_postgres(
    sessions: sessionmaker,
) -> None:
    seed = _seed_in_progress_order(sessions, key_prefix=f"visibility-{uuid.uuid4().hex[:8]}")
    order = seed["order"]
    seed["results"].record_numeric(
        recorded_by_identity_id=_new_labtech(seed, name=f"labtech-9-{uuid.uuid4().hex[:6]}").id,
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=seed["lab"].id,
        value=Decimal("1"),
    )
    with sessions() as session:
        before = session.scalar(select(LabOrder.status).where(LabOrder.id == order.id))
    assert before == "IN_PROGRESS"

    releaser = _new_releaser(seed, name="visibility-releaser")
    seed["releases"].release(
        order_id=order.id, lab_organization_id=seed["lab"].id, released_by_identity_id=releaser.id
    )

    with sessions() as session:
        after = session.scalar(select(LabOrder.status).where(LabOrder.id == order.id))
    assert after == "COMPLETED"
