"""DH-EX-012C real PostgreSQL qualification: multi-observation result domain concurrency and
atomicity claims. SQLite is not sufficient evidence for production PostgreSQL row-locking behavior
-- matching the reasoning ``test_dhex007_postgres_qualification.py``/``test_dhex001r_postgres_
qualification.py`` already established, applied here to ``exchange/results.py``'s new
``LabOrder FOR UPDATE`` discipline shared by ``record_observation``/``complete_entry``/
``record_numeric``/``record_text``/``LabResultReleaseRepository.release``.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHEX012C_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHEX012C_PG_HOST=127.0.0.1 \\
    DHEX012C_PG_PORT=5432 \\
    DHEX012C_PG_USER=dhex012c \\
    DHEX012C_PG_PASSWORD=dhex012c \\
    DHEX012C_PG_DATABASE=dhex012c_qual \\
        pytest adapter/tests/integration/test_dhex012c_postgres_qualification.py -v

The target database must already have migrations applied through ``0016_exchange_observations``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.
"""

from __future__ import annotations

import os
import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    LabObservation,
    LabOrder,
    LabPrice,
    LabResult,
    LabResultType,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    EntryAlreadyCompleted,
    EntryNotReadyForCompletion,
    LabResultReleaseRepository,
    LabResultRepository,
    ResultsNotReadyForRelease,
)
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHEX012C_PG_HOST")
PG_PORT = os.environ.get("DHEX012C_PG_PORT")
PG_USER = os.environ.get("DHEX012C_PG_USER")
PG_PASSWORD = os.environ.get("DHEX012C_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX012C_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHEX012C_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _labtech_identity_id(sessions: sessionmaker, subject: str):
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer="https://pg12c-qualification.example", subject=subject
    )
    return identity.id


def _seed_in_progress_order(sessions: sessionmaker, *, key_prefix: str, item_count: int = 1):
    """Identical shape to `test_dhex007_postgres_qualification.py`'s own fixture -- one provider,
    one lab, an `item_count`-item order, selected and driven to IN_PROGRESS (including the DH-EX-011
    collection milestone)."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)
    workflow = LabWorkflowRepository(sessions, organizations)

    provider = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")
    lab = organizations.create(org_type="LAB", name=f"{key_prefix}-lab")
    entries = []
    offering_ids = []
    for i in range(item_count):
        entry, _ = catalog.create_entry(
            code=f"{key_prefix}-TEST-{i}", display_name=f"DH-EX-012C test {i}"
        )
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("100.00") * (i + 1),
            currency="DJF",
            expected_current_price_id=None,
        )
        entries.append(entry)
        offering_ids.append(offering.id)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="addr",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="pg12c-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-1",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="pg12c-qualification",
        source_identity_system=f"urn:{key_prefix}:practitioner",
        source_identity_value="D-1",
    )
    order = orders.create(
        idempotency_key=f"{key_prefix}-order",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=e.id) for e in entries],
    ).order

    with sessions() as session:
        price_ids = session.scalars(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id.in_(offering_ids), LabPrice.superseded_at.is_(None)
            )
        ).all()

    selections.select(
        lab_order_id=order.id,
        lab_organization_id=lab.id,
        expected_price_ids=frozenset(price_ids),
    )
    workflow.accept(order_id=order.id, lab_organization_id=lab.id)
    workflow.record_collection(order_id=order.id, lab_organization_id=lab.id)
    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)

    order = orders.get(order.id)
    return lab.id, order.id, [item.id for item in order.items]


def test_concurrent_observation_inserts_do_not_corrupt_the_container(
    sessions: sessionmaker,
) -> None:
    """Ten concurrent `record_observation` calls for the same item must all succeed and all
    persist under the identical container -- the LabOrder FOR UPDATE lock serializes them into one
    coherent sequence rather than corrupting or losing any of them (unlike the single-observation
    convenience path, there is no winner/loser split here -- every call is a legitimate append)."""
    key_prefix = f"pg12c-multiobs-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(sessions, key_prefix=key_prefix)
    item_id = item_ids[0]
    results = LabResultRepository(sessions)

    n = 10
    unexpected_errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            results.record_observation(
                recorded_by_identity_id=_labtech_identity_id(
                    sessions, f"pg12c-actor-1-{uuid.uuid4().hex[:6]}"
                ),
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                result_type=LabResultType.NUMERIC,
                source_code=f"OBS-{i}",
                display_label_snapshot=f"Observation {i}",
                numeric_value=Decimal(i + 1),
            )
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                unexpected_errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not unexpected_errors, f"every concurrent append must succeed: {unexpected_errors!r}"

    with sessions() as session:
        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item_id))
        observation_count = session.scalar(
            select(func.count())
            .select_from(LabObservation)
            .where(LabObservation.lab_result_id == container.id)
        )
        assert observation_count == n, "no lost write -- all ten observations must persist"
        assert container.entry_completed_at is None, "recording observations never auto-completes"


def test_observation_never_sneaks_in_after_completion_wins_the_race(
    sessions: sessionmaker,
) -> None:
    """An observation insert racing `complete_entry` on the same item: the shared LabOrder FOR
    UPDATE lock means exactly one of the two genuinely runs first. If completion wins, the late
    observation must be refused (`EntryAlreadyCompleted`), never silently accepted after the fact --
    the container's observation count must match exactly what was true at the moment completion
    committed. If the observation wins instead, completion must still succeed afterward (a real
    observation existed before completion ran)."""
    key_prefix = f"pg12c-obsvscomplete-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(sessions, key_prefix=key_prefix)
    item_id = item_ids[0]
    results = LabResultRepository(sessions)
    # Seed one observation up front so completion is always valid to attempt regardless of race
    # outcome (EntryNotReadyForCompletion would otherwise conflate with the property under test).
    results.record_observation(
        recorded_by_identity_id=_labtech_identity_id(
            sessions, f"pg12c-actor-2-{uuid.uuid4().hex[:6]}"
        ),
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        result_type=LabResultType.NUMERIC,
        source_code="SEED",
        display_label_snapshot="Seed observation",
        numeric_value=Decimal("1"),
    )

    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def do_observe() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["observe"] = results.record_observation(
                recorded_by_identity_id=_labtech_identity_id(
                    sessions, f"pg12c-actor-3-{uuid.uuid4().hex[:6]}"
                ),
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                result_type=LabResultType.NUMERIC,
                source_code="RACE",
                display_label_snapshot="Race observation",
                numeric_value=Decimal("2"),
            )
        except EntryAlreadyCompleted as refused:
            outcomes["observe"] = refused
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    def do_complete() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["complete"] = results.complete_entry(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                completed_by_identity_id=_labtech_identity_id(sessions, "pg12c-completer"),
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    t1 = threading.Thread(target=do_observe)
    t2 = threading.Thread(target=do_complete)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, errors
    assert "observe" in outcomes and "complete" in outcomes

    with sessions() as session:
        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item_id))
        observation_count = session.scalar(
            select(func.count())
            .select_from(LabObservation)
            .where(LabObservation.lab_result_id == container.id)
        )
        assert container.entry_completed_at is not None, "completion always succeeds here"
        if isinstance(outcomes["observe"], EntryAlreadyCompleted):
            # Completion won -- the late observation must never have landed.
            assert observation_count == 1, "refused observation must not persist"
        else:
            # The observation won -- it landed before completion locked the container.
            assert observation_count == 2


def test_completion_cannot_succeed_before_release_and_release_waits_for_completion(
    sessions: sessionmaker,
) -> None:
    """Release cannot succeed until completion is committed: races `complete_entry` against
    `LabResultReleaseRepository.release` on a two-item order whose first item is already complete.
    Exactly one ordering is possible per run because the shared LabOrder lock fully serializes the
    two calls -- either release runs first (while item two is still incomplete, refused with
    `ResultsNotReadyForRelease`) or completion runs first (release then succeeds). Neither call may
    corrupt the other or produce a split-brain state (a released order with an incomplete item, or
    a completed item with no committed fact)."""
    key_prefix = f"pg12c-completevsrelease-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(
        sessions, key_prefix=key_prefix, item_count=2
    )
    results = LabResultRepository(sessions)
    releases = LabResultReleaseRepository(sessions)
    for item_id in item_ids:
        results.record_observation(
            recorded_by_identity_id=_labtech_identity_id(
                sessions, f"pg12c-actor-4-{uuid.uuid4().hex[:6]}"
            ),
            order_id=order_id,
            lab_order_item_id=item_id,
            lab_organization_id=lab_id,
            result_type=LabResultType.NUMERIC,
            source_code="OBS",
            display_label_snapshot="Observation",
            numeric_value=Decimal("1"),
        )
    # Item 0 is completed up front; item 1's completion races the release attempt below.
    results.complete_entry(
        order_id=order_id,
        lab_order_item_id=item_ids[0],
        lab_organization_id=lab_id,
        completed_by_identity_id=_labtech_identity_id(sessions, "pg12c-cvr-tech"),
    )
    releaser_id = _labtech_identity_id(sessions, "pg12c-cvr-releaser")

    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def do_complete_second_item() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["complete"] = results.complete_entry(
                order_id=order_id,
                lab_order_item_id=item_ids[1],
                lab_organization_id=lab_id,
                completed_by_identity_id=_labtech_identity_id(sessions, "pg12c-cvr-tech"),
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    def do_release() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["release"] = releases.release(
                order_id=order_id, lab_organization_id=lab_id, released_by_identity_id=releaser_id
            )
        except ResultsNotReadyForRelease as refused:
            outcomes["release"] = refused
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    t1 = threading.Thread(target=do_complete_second_item)
    t2 = threading.Thread(target=do_release)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, errors
    assert "complete" in outcomes and "release" in outcomes

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        if isinstance(outcomes["release"], ResultsNotReadyForRelease):
            assert order.status == "IN_PROGRESS"
            assert releases.get_for_order(order_id) is None
        else:
            assert order.status == "COMPLETED"
            assert releases.get_for_order(order_id) is not None


def test_concurrent_completion_calls_converge_to_one_provenance(sessions: sessionmaker) -> None:
    """Ten concurrent `complete_entry` calls for the same already-observed item: exactly one may
    create the completion fact; every other call must observe that identical fact back (same
    actor, same time), never overwrite it and never create a second one."""
    key_prefix = f"pg12c-completerace-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(sessions, key_prefix=key_prefix)
    item_id = item_ids[0]
    results = LabResultRepository(sessions)
    results.record_observation(
        recorded_by_identity_id=_labtech_identity_id(
            sessions, f"pg12c-actor-5-{uuid.uuid4().hex[:6]}"
        ),
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        result_type=LabResultType.NUMERIC,
        source_code="OBS",
        display_label_snapshot="Observation",
        numeric_value=Decimal("1"),
    )

    n = 10
    outcomes: list[tuple] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def attempt(i: int) -> None:
        try:
            actor = _labtech_identity_id(sessions, f"pg12c-completer-{i}")
            barrier.wait(timeout=15)
            record, created = results.complete_entry(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                completed_by_identity_id=actor,
            )
            with lock:
                outcomes.append((record, created))
        except (EntryAlreadyCompleted, EntryNotReadyForCompletion) as error:
            with lock:
                errors.append(error)
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"complete_entry must never raise under a same-item race: {errors!r}"
    assert len(outcomes) == n
    created_flags = [created for _record, created in outcomes]
    assert created_flags.count(True) == 1, "exactly one call may create the completion fact"
    winning_actor = next(
        record.entry_completed_by_identity_id for record, created in outcomes if created
    )
    for record, _created in outcomes:
        assert record.entry_completed_by_identity_id == winning_actor
        assert record.id == outcomes[0][0].id
