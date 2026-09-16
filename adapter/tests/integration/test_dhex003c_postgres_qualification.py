"""DH-CLINIC-003C real-PostgreSQL row-locking, idempotency, and concurrent commit qualification.

Opt-in only. The target must be a disposable database migrated through
``0020_exchange_order_intents``; never point these tests at a facility database.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import LabOrder, LabOrderIntent
from daher_adapter.exchange.order_intents import LabOrderIntentRepository
from daher_adapter.exchange.orders import LabOrderRepository
from daher_adapter.exchange.organizations import OrganizationRepository

PG_HOST = os.environ.get("DHEX003C_PG_HOST") or os.environ.get("DHEX014B_PG_HOST")
PG_PORT = os.environ.get("DHEX003C_PG_PORT") or os.environ.get("DHEX014B_PG_PORT")
PG_USER = os.environ.get("DHEX003C_PG_USER") or os.environ.get("DHEX014B_PG_USER")
PG_PASSWORD = os.environ.get("DHEX003C_PG_PASSWORD") or os.environ.get("DHEX014B_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX003C_PG_DATABASE") or os.environ.get("DHEX014B_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason=(
        "requires a disposable PostgreSQL database (DHEX003C_PG_* or DHEX014B_PG_*) -- never in CI"
    ),
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def test_concurrent_intent_commits_converge_to_single_order(sessions: sessionmaker) -> None:
    """5 concurrent threads attempt to commit the same intent simultaneously under Postgres.

    Row-level locking via `.with_for_update()` guarantees:
    - Exactly one thread creates the LabOrder (created=True)
    - All other threads return the exact same LabOrder (created=False)
    - Exactly one LabOrder row exists in the database
    - intent.committed_lab_order_id points to this LabOrder
    """
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    intents = LabOrderIntentRepository(sessions, organizations, catalog)

    prefix = f"pg003c-{uuid.uuid4().hex[:8]}"
    provider = organizations.create(org_type="PROVIDER", name=f"{prefix}-clinic")
    entry, _ = catalog.create_entry(code=f"{prefix}-TEST", display_name=f"Synthetic test {prefix}")
    patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="pg003c-qualification",
        source_identity_system=f"urn:{prefix}:patient",
        source_identity_value="P-1",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="pg003c-qualification",
        source_identity_system=f"urn:{prefix}:practitioner",
        source_identity_value="D-1",
    )

    intent_res = intents.create(
        idempotency_key=f"{prefix}-key",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        catalog_entry_ids=[entry.id],
    )
    intent_id = intent_res.intent.id

    results = []
    barrier = threading.Barrier(5)

    def worker():
        barrier.wait()
        with sessions() as session:
            res = intents.commit(provider.id, intent_id, orders, session=session)
            session.commit()
            results.append(res)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 5
    created_count = sum(1 for r in results if r.created)
    replayed_count = sum(1 for r in results if not r.created)
    assert created_count == 1
    assert replayed_count == 4

    order_ids = {r.order.id for r in results}
    assert len(order_ids) == 1
    single_order_id = list(order_ids)[0]

    with sessions() as session:
        orders_in_db = session.scalars(
            select(LabOrder).where(LabOrder.idempotency_key == f"{prefix}-key")
        ).all()
        assert len(orders_in_db) == 1
        assert orders_in_db[0].id == single_order_id

        intent_in_db = session.scalar(select(LabOrderIntent).where(LabOrderIntent.id == intent_id))
        assert intent_in_db.committed_lab_order_id == single_order_id
