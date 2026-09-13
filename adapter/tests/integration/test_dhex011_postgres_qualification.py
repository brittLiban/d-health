"""DH-EX-011: real PostgreSQL qualification of the order-level sample/specimen collection
workflow milestone -- persistence/idempotency, no-retroactive-fabrication, the start-processing
gate enforced from both ``LabWorkflowRepository.start_processing`` and the generic
``LabOrderRepository.transition()`` path, and the two genuinely concurrent scenarios this ticket
introduces (10-way concurrent collection recording; collection racing start-processing for the
same order). SQLite's threading model is not sufficient evidence for production PostgreSQL
row-locking behavior -- matching the reasoning ``test_dhex010a_postgres_qualification.py`` already
established, applied here to this ticket's own new repository methods and invariant.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHEX011_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHEX011_PG_HOST=127.0.0.1 \\
    DHEX011_PG_PORT=5432 \\
    DHEX011_PG_USER=dhex011 \\
    DHEX011_PG_PASSWORD=dhex011 \\
    DHEX011_PG_DATABASE=dhex011_qual \\
        pytest adapter/tests/integration/test_dhex011_postgres_qualification.py -v

The target database must already have migrations applied through ``0014_exchange_collection``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import InvalidLabWorkflowTransition, LabWorkflowRepository
from daher_adapter.exchange.models import LabOrder, LabOrderStatus, LabSampleCollection
from daher_adapter.exchange.orders import InvalidOrderTransition, LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHEX011_PG_HOST")
PG_PORT = os.environ.get("DHEX011_PG_PORT")
PG_USER = os.environ.get("DHEX011_PG_USER")
PG_PASSWORD = os.environ.get("DHEX011_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX011_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHEX011_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed_accepted_order(sessions: sessionmaker, *, key_prefix: str):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    workflow = LabWorkflowRepository(sessions, organizations)

    provider = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")
    lab = organizations.create(org_type="LAB", name=f"{key_prefix}-lab")
    entry, _ = catalog.create_entry(code=f"{key_prefix}-TEST", display_name="Qualification Test")
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
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
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order

    selections = LabSelectionRepository(sessions, organizations)
    selections.assign_by_provider(
        lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
    )
    workflow.accept(order_id=order.id, lab_organization_id=lab.id)
    return provider, lab, order, workflow, orders


def test_collection_persists_and_replays_idempotently(sessions: sessionmaker) -> None:
    """A recorded milestone survives a fresh read, and a second call against the same order --
    even after it has progressed to IN_PROGRESS -- returns the identical row rather than a
    second one or a rewritten timestamp."""
    _provider, lab, order, workflow, _orders = _seed_accepted_order(
        sessions, key_prefix=f"persist-{uuid.uuid4().hex[:8]}"
    )

    first, created_first = workflow.record_collection(order_id=order.id, lab_organization_id=lab.id)
    assert created_first is True

    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)

    second, created_second = workflow.record_collection(
        order_id=order.id, lab_organization_id=lab.id
    )
    assert created_second is False
    assert second.id == first.id
    assert second.recorded_at == first.recorded_at

    with sessions() as session:
        row_count = session.scalar(
            select(func.count())
            .select_from(LabSampleCollection)
            .where(LabSampleCollection.lab_order_id == order.id)
        )
    assert row_count == 1


def test_no_retroactive_fabrication_for_legacy_in_progress_order(sessions: sessionmaker) -> None:
    """A row moved to IN_PROGRESS by a raw UPDATE -- simulating a genuine pre-DH-EX-011 legacy
    order, since both ``LabWorkflowRepository.start_processing`` and the generic
    ``LabOrderRepository.transition()`` now refuse this move without a milestone (proven by the
    two ``test_start_processing_gate_enforced_via_*`` tests above) -- has no collection milestone.
    A fresh ``record_collection`` attempt against it must fail closed rather than inventing history
    DHealth never observed, and the legacy order's own repeated start_processing retry must remain
    a safe no-op."""
    _provider, lab, order, workflow, _orders = _seed_accepted_order(
        sessions, key_prefix=f"legacy-{uuid.uuid4().hex[:8]}"
    )

    with sessions() as session:
        session.execute(
            LabOrder.__table__.update()
            .where(LabOrder.id == order.id)
            .values(status=LabOrderStatus.IN_PROGRESS.value)
        )
        session.commit()

    with sessions() as session:
        row_count = session.scalar(
            select(func.count())
            .select_from(LabSampleCollection)
            .where(LabSampleCollection.lab_order_id == order.id)
        )
    assert row_count == 0

    with pytest.raises(InvalidLabWorkflowTransition):
        workflow.record_collection(order_id=order.id, lab_organization_id=lab.id)

    record, transitioned = workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)
    assert transitioned is False
    assert record.collection_recorded_at is None


def test_start_processing_gate_enforced_via_workflow_repository(sessions: sessionmaker) -> None:
    """``LabWorkflowRepository.start_processing`` refuses ACCEPTED -> IN_PROGRESS with no
    milestone, and succeeds once one is recorded."""
    _provider, lab, order, workflow, _orders = _seed_accepted_order(
        sessions, key_prefix=f"gate-wf-{uuid.uuid4().hex[:8]}"
    )

    with pytest.raises(InvalidLabWorkflowTransition):
        workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)

    workflow.record_collection(order_id=order.id, lab_organization_id=lab.id)
    _record, transitioned = workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)
    assert transitioned is True


def test_start_processing_gate_enforced_via_generic_transition_path(sessions: sessionmaker) -> None:
    """``LabOrderRepository.transition()`` -- a second, independent path capable of producing
    IN_PROGRESS -- enforces the identical invariant, proving the API path is not the only place
    this is checked."""
    _provider, lab, order, _workflow, orders = _seed_accepted_order(
        sessions, key_prefix=f"gate-tx-{uuid.uuid4().hex[:8]}"
    )

    with pytest.raises(InvalidOrderTransition):
        orders.transition(order.id, LabOrderStatus.IN_PROGRESS)

    with sessions() as session:
        session.add(LabSampleCollection(lab_order_id=order.id, lab_organization_id=lab.id))
        session.commit()

    record = orders.transition(order.id, LabOrderStatus.IN_PROGRESS)
    assert record.status == LabOrderStatus.IN_PROGRESS.value


def test_10way_concurrent_collection_recording_yields_one_row(sessions: sessionmaker) -> None:
    """10 threads call ``record_collection`` for the same order simultaneously against real
    PostgreSQL row-locking. Exactly one row must be created; every other caller observes a safe
    idempotent replay of that same row -- never a duplicate, never a UNIQUE-constraint crash
    surfaced to a caller."""
    _provider, lab, order, workflow, _orders = _seed_accepted_order(
        sessions, key_prefix=f"tenway-{uuid.uuid4().hex[:8]}"
    )

    results: list[tuple[str, object]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def attempt() -> None:
        try:
            barrier.wait(timeout=15)
            record, created = workflow.record_collection(
                order_id=order.id, lab_organization_id=lab.id
            )
            with lock:
                results.append(("ok", (record.id, created)))
        except BaseException as error:  # noqa: BLE001
            with lock:
                results.append(("error", type(error).__name__))

    threads = [threading.Thread(target=attempt) for _ in range(10)]
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
            .select_from(LabSampleCollection)
            .where(LabSampleCollection.lab_order_id == order.id)
        )
    assert row_count == 1


def _run_collection_vs_start_once(
    sessions: sessionmaker, workflow: LabWorkflowRepository, *, lab, order
) -> dict[str, object]:
    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def collect_attempt() -> None:
        try:
            barrier.wait(timeout=15)
            _record, created = workflow.record_collection(
                order_id=order.id, lab_organization_id=lab.id
            )
            results["collect"] = ("ok", created)
        except BaseException as error:  # noqa: BLE001
            results["collect"] = ("error", type(error).__name__)

    def start_attempt() -> None:
        try:
            barrier.wait(timeout=15)
            _record, transitioned = workflow.start_processing(
                order_id=order.id, lab_organization_id=lab.id
            )
            results["start"] = ("ok", transitioned)
        except InvalidLabWorkflowTransition:
            results["start"] = ("conflict", None)
        except BaseException as error:  # noqa: BLE001
            results["start"] = ("error", type(error).__name__)

    t_collect = threading.Thread(target=collect_attempt)
    t_start = threading.Thread(target=start_attempt)
    t_collect.start()
    t_start.start()
    t_collect.join(timeout=30)
    t_start.join(timeout=30)

    with sessions() as session:
        results["final_status"] = session.scalar(
            select(LabOrder.status).where(LabOrder.id == order.id)
        )
        results["collection_count"] = session.scalar(
            select(func.count())
            .select_from(LabSampleCollection)
            .where(LabSampleCollection.lab_order_id == order.id)
        )
    return results


def test_collection_vs_start_processing_race_yields_coherent_outcome(
    sessions: sessionmaker,
) -> None:
    """Racing ``record_collection`` against ``start_processing`` for the same ACCEPTED order must
    never allow processing to begin without the milestone existing at the moment IN_PROGRESS is
    committed. Both of exactly two outcomes are valid: collection commits first and
    start_processing then succeeds, or start_processing loses (409-equivalent
    ``InvalidLabWorkflowTransition``) and a caller's retry succeeds after collection has landed.
    Run 5 times, matching this ticket's own PROJECT_STATE evidence discipline."""
    for attempt in range(5):
        _provider, lab, order, workflow, _orders = _seed_accepted_order(
            sessions, key_prefix=f"race-{uuid.uuid4().hex[:8]}"
        )

        results = _run_collection_vs_start_once(sessions, workflow, lab=lab, order=order)

        assert results["collect"][0] == "ok", (attempt, results)
        assert results["collection_count"] == 1, (attempt, results)

        if results["final_status"] == LabOrderStatus.IN_PROGRESS.value:
            assert results["start"][0] == "ok", (
                attempt,
                results,
                "IN_PROGRESS requires a milestone",
            )
        else:
            assert results["final_status"] == LabOrderStatus.ACCEPTED.value, (attempt, results)
            assert results["start"][0] in ("conflict", "ok"), (attempt, results)
