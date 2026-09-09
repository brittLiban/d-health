"""DH-EX-001R: real PostgreSQL qualification of the Exchange core's concurrency and atomicity
claims. SQLite (``test_exchange_idempotency_concurrency.py``) is not sufficient evidence for
production PostgreSQL behavior -- this file exists because that distinction was raised explicitly
in review and needed real proof, not reasoning by analogy.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHEX001R_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHEX001R_PG_HOST=127.0.0.1 \\
    DHEX001R_PG_PORT=5432 \\
    DHEX001R_PG_USER=adapter \\
    DHEX001R_PG_PASSWORD=adapter \\
    DHEX001R_PG_DATABASE=adapter \\
        pytest adapter/tests/integration/test_dhex001r_postgres_qualification.py -v

The target database must already have migrations applied through ``0004_exchange_core``
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
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import (
    DoctorReview,
    LabOrder,
    LabOrderItem,
    LabOrderStatus,
    LabPrice,
    ResultDelivery,
)
from daher_adapter.exchange.orders import InvalidOrderTransition, LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHEX001R_PG_HOST")
PG_PORT = os.environ.get("DHEX001R_PG_PORT")
PG_USER = os.environ.get("DHEX001R_PG_USER")
PG_PASSWORD = os.environ.get("DHEX001R_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX001R_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHEX001R_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed(sessions: sessionmaker, *, key_prefix: str):
    """One provider, one lab, one priced catalog entry -- unique names per call so tests never
    collide with each other or with prior runs against the same live database."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)

    provider = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")
    lab = organizations.create(org_type="LAB", name=f"{key_prefix}-lab")
    entry = catalog.create_entry(code=f"{key_prefix}-TEST", display_name="PG qualification test")
    offering = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(offering_id=offering.id, amount=Decimal("100.00"))
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
    return (
        organizations,
        catalog,
        orders,
        selections,
        provider,
        lab,
        entry,
        offering,
        patient,
        practitioner,
    )


def test_ten_concurrent_creates_same_idempotency_key_yield_exactly_one_order(
    sessions: sessionmaker,
) -> None:
    key_prefix = f"pg10same-{uuid.uuid4().hex[:8]}"
    _org, _cat, orders, _sel, provider, _lab, entry, _off, patient, practitioner = _seed(
        sessions, key_prefix=key_prefix
    )
    idempotency_key = f"{key_prefix}-order"

    outcomes: dict[int, bool] = {}
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            result = orders.create(
                idempotency_key=idempotency_key,
                provider_organization_id=provider.id,
                patient_reference_id=patient.id,
                practitioner_reference_id=practitioner.id,
                items=[OrderItemInput(catalog_entry_id=entry.id)],
            )
            with lock:
                outcomes[i] = result.created
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"unexpected exceptions: {errors!r}"
    assert len(outcomes) == 10
    assert sorted(outcomes.values()) == [False] * 9 + [True]

    with sessions() as session:
        order_count = session.scalar(
            select(func.count())
            .select_from(LabOrder)
            .where(LabOrder.idempotency_key == idempotency_key)
        )
        order_id = session.scalar(
            select(LabOrder.id).where(LabOrder.idempotency_key == idempotency_key)
        )
        item_count = session.scalar(
            select(func.count())
            .select_from(LabOrderItem)
            .where(LabOrderItem.lab_order_id == order_id)
        )
        review_count = session.scalar(
            select(func.count())
            .select_from(DoctorReview)
            .where(DoctorReview.lab_order_id == order_id)
        )
        delivery_count = session.scalar(
            select(func.count())
            .select_from(ResultDelivery)
            .where(ResultDelivery.lab_order_id == order_id)
        )

    assert order_count == 1, "real PostgreSQL UNIQUE(idempotency_key) must permit exactly one row"
    assert item_count == 1, "no duplicate LabOrderItem from the nine losing attempts"
    assert review_count == 1, "no duplicate DoctorReview from the nine losing attempts"
    assert delivery_count == 1, "no duplicate ResultDelivery from the nine losing attempts"


def test_ten_concurrent_creates_different_keys_same_payload_yield_ten_orders(
    sessions: sessionmaker,
) -> None:
    key_prefix = f"pg10diff-{uuid.uuid4().hex[:8]}"
    _org, _cat, orders, _sel, provider, _lab, entry, _off, patient, practitioner = _seed(
        sessions, key_prefix=key_prefix
    )

    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            orders.create(
                idempotency_key=f"{key_prefix}-order-{i}",
                provider_organization_id=provider.id,
                patient_reference_id=patient.id,
                practitioner_reference_id=practitioner.id,
                items=[OrderItemInput(catalog_entry_id=entry.id)],
            )
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"unexpected exceptions: {errors!r}"

    with sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(LabOrder)
            .where(LabOrder.idempotency_key.like(f"{key_prefix}-order-%"))
        )
    assert count == 10, "ten distinct intentional submissions must yield ten distinct orders"


def test_concurrent_price_updates_preserve_exactly_one_current_price(
    sessions: sessionmaker,
) -> None:
    key_prefix = f"pgprice-{uuid.uuid4().hex[:8]}"
    _org, catalog, _orders, _sel, _provider, _lab, _entry, offering, _pat, _prac = _seed(
        sessions, key_prefix=key_prefix
    )

    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            catalog.set_price(offering_id=offering.id, amount=Decimal(f"{100 + i}.00"))
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    with sessions() as session:
        current_count = session.scalar(
            select(func.count())
            .select_from(LabPrice)
            .where(LabPrice.lab_offering_id == offering.id, LabPrice.superseded_at.is_(None))
        )
        total_count = session.scalar(
            select(func.count())
            .select_from(LabPrice)
            .where(LabPrice.lab_offering_id == offering.id)
        )

    assert not errors, (
        f"concurrent set_price must not raise -- either the DB serializes cleanly or the "
        f"repository must retry/absorb the race; got {errors!r}"
    )
    assert current_count == 1, "the partial unique index must leave exactly one current price"
    # 1 initial (from _seed) + 8 concurrent attempts = 9 rows total, all but one superseded.
    assert total_count == 9


def test_invalid_transition_refused_under_real_row_lock(sessions: sessionmaker) -> None:
    key_prefix = f"pgstate-{uuid.uuid4().hex[:8]}"
    _org, _cat, orders, _sel, provider, _lab, entry, _off, patient, practitioner = _seed(
        sessions, key_prefix=key_prefix
    )
    created = orders.create(
        idempotency_key=f"{key_prefix}-order",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order

    with pytest.raises(InvalidOrderTransition):
        orders.transition(created.id, LabOrderStatus.COMPLETED)

    with sessions() as session:
        status = session.scalar(select(LabOrder.status).where(LabOrder.id == created.id))
    assert status == LabOrderStatus.AWAITING_LAB_SELECTION.value, (
        "a refused transition must never partially apply under a real row lock"
    )
