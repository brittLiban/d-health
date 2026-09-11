"""DH-EX-007R: real PostgreSQL qualification of the results-workflow's concurrency and atomicity
claims. SQLite is not sufficient evidence for production PostgreSQL row-locking behavior --
matching the reasoning ``test_dhex001r_postgres_qualification.py`` already established for the
Exchange core, applied here to ``exchange/results.py``'s ``LabOrder FOR UPDATE`` discipline.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHEX007_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHEX007_PG_HOST=127.0.0.1 \\
    DHEX007_PG_PORT=5432 \\
    DHEX007_PG_USER=dhex007 \\
    DHEX007_PG_PASSWORD=dhex007 \\
    DHEX007_PG_DATABASE=dhex007_qual \\
        pytest adapter/tests/integration/test_dhex007_postgres_qualification.py -v

The target database must already have migrations applied through ``0010_exchange_results_workflow``
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
from daher_adapter.exchange.models import (
    DoctorReview,
    ExchangeSecurityAuditEvent,
    LabOrder,
    LabPrice,
    LabResult,
    ResultDelivery,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    DuplicateResult,
    InvalidResultSubmissionState,
    LabResultRepository,
)
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHEX007_PG_HOST")
PG_PORT = os.environ.get("DHEX007_PG_PORT")
PG_USER = os.environ.get("DHEX007_PG_USER")
PG_PASSWORD = os.environ.get("DHEX007_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX007_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHEX007_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed_in_progress_order(sessions: sessionmaker, *, key_prefix: str, item_count: int = 1):
    """One provider, one lab, an `item_count`-item order, selected and driven to IN_PROGRESS via
    the real API-equivalent repository calls. Returns (order_id, [item_id, ...])."""
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
            code=f"{key_prefix}-TEST-{i}", display_name=f"DH-EX-007R test {i}"
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
    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)

    order = orders.get(order.id)
    return lab.id, order.id, [item.id for item in order.items]


def test_concurrent_same_item_submission_yields_exactly_one_result(sessions: sessionmaker) -> None:
    """Single-item order: the LabOrder FOR UPDATE lock serializes every concurrent submission for
    this order down to one winner. Because this is a *one-item* order, that single winner also
    completes it (same invariant `test_second_submission_on_a_completed_one_item_order_hits_the_
    state_gate_first` pins down at the unit level) -- so the nine losers correctly observe
    `InvalidResultSubmissionState` (order already COMPLETED), never `DuplicateResult`, matching
    the ticket's required check order (lifecycle gate before the duplicate check). Both are
    controlled conflicts; neither is a raw DB error."""
    key_prefix = f"pg7sameitem-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(sessions, key_prefix=key_prefix)
    item_id = item_ids[0]
    results = LabResultRepository(sessions)

    winners: list[int] = []
    losers: list[int] = []
    unexpected_errors: list[BaseException] = []
    lock = threading.Lock()
    n = 10
    barrier = threading.Barrier(n)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            results.record_numeric(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                value=Decimal(i + 1),
            )
            with lock:
                winners.append(i)
        except (DuplicateResult, InvalidResultSubmissionState):
            with lock:
                losers.append(i)
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                unexpected_errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not unexpected_errors, (
        f"no raw DB error may leak to a caller -- got: {unexpected_errors!r}"
    )
    assert len(winners) == 1, f"exactly one winner required, got {winners!r}"
    assert len(losers) == n - 1

    with sessions() as session:
        result_count = session.scalar(
            select(func.count())
            .select_from(LabResult)
            .where(LabResult.lab_order_item_id == item_id)
        )
        assert result_count == 1
        recorded_events_for_this_order = session.scalar(
            select(func.count())
            .select_from(ExchangeSecurityAuditEvent)
            .where(
                ExchangeSecurityAuditEvent.event_type == "lab_result_recorded",
                ExchangeSecurityAuditEvent.resource_id == order_id,
            )
        )
        # This test calls the repository directly (no API layer, no audit call) -- 0 is expected
        # for *this order* here; audit-count correctness under concurrency is proven at the API
        # layer in test_exchange_results_api.py's own (non-concurrent) audit-atomicity tests.
        # Scoped by resource_id, not a bare global count -- this scratch database is reused
        # across this session's qualification steps and may hold unrelated orders' audit rows.
        assert recorded_events_for_this_order == 0
        order = session.get(LabOrder, order_id)
        assert order.status == "COMPLETED"  # one-item order: the single winner completed it


def test_concurrent_same_item_submission_on_still_in_progress_order_yields_duplicate_result(
    sessions: sessionmaker,
) -> None:
    """Companion to the one-item test above, isolating `DuplicateResult` specifically: a two-item
    order racing on only one of its items never completes (the second item has no result), so
    every loser observes the real `DuplicateResult` conflict, not the lifecycle gate."""
    key_prefix = f"pg7sameitem2-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(
        sessions, key_prefix=key_prefix, item_count=2
    )
    item_id = item_ids[0]
    results = LabResultRepository(sessions)

    winners: list[int] = []
    losers: list[int] = []
    unexpected_errors: list[BaseException] = []
    lock = threading.Lock()
    n = 10
    barrier = threading.Barrier(n)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            results.record_numeric(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                value=Decimal(i + 1),
            )
            with lock:
                winners.append(i)
        except DuplicateResult:
            with lock:
                losers.append(i)
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                unexpected_errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not unexpected_errors, (
        f"no raw DB error may leak to a caller -- got: {unexpected_errors!r}"
    )
    assert len(winners) == 1
    assert len(losers) == n - 1

    with sessions() as session:
        result_count = session.scalar(
            select(func.count())
            .select_from(LabResult)
            .where(LabResult.lab_order_item_id == item_id)
        )
        assert result_count == 1
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"  # second item still has no result


def test_concurrent_final_two_results_converge_to_completed_with_no_lost_write(
    sessions: sessionmaker,
) -> None:
    key_prefix = f"pg7finaltwo-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(
        sessions, key_prefix=key_prefix, item_count=2
    )
    results = LabResultRepository(sessions)

    outcomes: dict[int, bool] = {}
    unexpected_errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def attempt(i: int, item_id) -> None:
        try:
            barrier.wait(timeout=15)
            outcome = results.record_numeric(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                value=Decimal(i + 1),
            )
            with lock:
                outcomes[i] = outcome.order_completed
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                unexpected_errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i, item_ids[i])) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not unexpected_errors, f"no lost write / no raw DB error: {unexpected_errors!r}"
    assert len(outcomes) == 2, "both concurrent submissions must succeed -- no lost write"
    assert sorted(outcomes.values()) == [False, True], (
        "exactly one of the two must be the order-completing submission"
    )

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "COMPLETED"
        result_count = session.scalar(
            select(func.count())
            .select_from(LabResult)
            .where(LabResult.lab_order_item_id.in_(item_ids))
        )
        assert result_count == 2, "both results must persist -- no lost write"
        review = session.scalar(select(DoctorReview).where(DoctorReview.lab_order_id == order_id))
        assert review.status == "PENDING"
        delivery = session.scalar(
            select(ResultDelivery).where(ResultDelivery.lab_order_id == order_id)
        )
        assert delivery.status == "PENDING"
