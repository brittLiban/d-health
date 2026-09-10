"""DH-EX-001's required concurrency proof: two genuinely concurrent attempts to create a LabOrder
with the same idempotency key must be resolved by the database's own UNIQUE constraint, not by
any check-then-insert race window in application code -- the same insert-first-wins discipline
`daher_adapter.ledger.LedgerRepository.claim` already relies on for message identity.

Uses its own SQLite engine (not the shared `engine`/`sessions` fixtures from ``conftest.py``) so
this file controls `connect_args={"timeout": ...}` -- SQLite serializes concurrent writers via a
whole-database lock, and a short default busy-timeout can otherwise surface as
`OperationalError: database is locked` rather than exercising the code path this test exists to
prove. A `threading.Barrier` maximizes (never fully guarantees, since SQLite still serializes the
actual write) the chance both threads reach the write at the same instant; the assertions below
hold regardless of which thread the database lets go first.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from pathlib import Path

from exchange_support import (
    make_lab,
    make_patient_reference,
    make_practitioner_reference,
    make_provider,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import Base, DoctorReview, LabOrder, ResultDelivery
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository


def test_concurrent_duplicate_claim_has_a_database_level_final_authority(tmp_path: Path) -> None:
    database = tmp_path / "exchange-concurrency-test.sqlite"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)

    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DH-CONCURRENCY-1", display_name="Concurrency test item")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("100"),
        currency="DJF",
        expected_current_price_id=None,
    )
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)

    idempotency_key = "req-concurrent-1"
    barrier = threading.Barrier(2)
    outcomes: dict[str, bool] = {}
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt(name: str) -> None:
        try:
            barrier.wait(timeout=10)
            result = orders.create(
                idempotency_key=idempotency_key,
                provider_organization_id=provider.id,
                patient_reference_id=patient.id,
                practitioner_reference_id=practitioner.id,
                items=[OrderItemInput(catalog_entry_id=entry.id)],
            )
            with lock:
                outcomes[name] = result.created
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion, not swallowed
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(f"thread-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"unexpected exceptions from concurrent create() calls: {errors!r}"
    assert len(outcomes) == 2
    assert sorted(outcomes.values()) == [False, True], (
        "exactly one concurrent attempt must have created the order and the other must have "
        f"received the existing one, got {outcomes!r}"
    )

    with sessions() as session:
        order_count = session.scalar(
            select(func.count())
            .select_from(LabOrder)
            .where(LabOrder.idempotency_key == idempotency_key)
        )
        review_count = session.scalar(select(func.count()).select_from(DoctorReview))
        delivery_count = session.scalar(select(func.count()).select_from(ResultDelivery))

    assert order_count == 1, "the DB unique constraint must permit exactly one LabOrder row"
    assert review_count == 1, "the losing attempt must not have created a second DoctorReview"
    assert delivery_count == 1, "the losing attempt must not have created a second ResultDelivery"

    engine.dispose()
