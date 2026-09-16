"""DH-CLINIC-003C real-PostgreSQL row-locking, idempotency, and concurrent commit qualification.

Opt-in only. The target must be a disposable database migrated through
``0020_exchange_order_intents``; never point these tests at a facility database.
"""

from __future__ import annotations

import os
import tempfile
import threading
import uuid
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import LabOrder, LabOrderIntent, LabOrderItem, LabOrderIntentItem
from daher_adapter.exchange.order_intents import LabOrderIntentRepository
from daher_adapter.exchange.orders import LabOrderRepository
from daher_adapter.exchange.organizations import OrganizationRepository


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _race_clients(sessions: sessionmaker):
    private_key, public_key = generate_keypair()
    app = FastAPI()
    app.include_router(
        build_exchange_router(
            session_factory=sessions,
            token_validator=build_validator(public_key),
            audit_writer=ExchangeAuditWriter(Path(tempfile.mkdtemp()) / "audit.jsonl"),
            platform_admin_role=PLATFORM_ADMIN_ROLE,
        )
    )
    return app, private_key


def _race_clinician(app: FastAPI, private_key, organizations, provider):
    token = make_token(private_key, subject=f"pg003c-{provider.id}")
    with TestClient(app, raise_server_exceptions=False) as client:
        identity = client.get("/exchange/v1/me", headers=_auth(token)).json()["identity_id"]
        admin = make_token(private_key, subject=f"admin-{provider.id}", roles=[PLATFORM_ADMIN_ROLE])
        response = client.post(
            f"/exchange/v1/organizations/{provider.id}/memberships",
            headers=_auth(admin),
            json={"identity_id": identity, "roles": ["CLINICIAN"]},
        )
        assert response.status_code in (200, 201), response.text
    return token


def _run_race(label: str, workers: dict[str, object]):
    barrier = threading.Barrier(len(workers))
    results, exceptions = {}, {}
    def run(name, operation):
        try:
            barrier.wait(timeout=10)
            results[name] = operation()
        except BaseException as error:
            exceptions[name] = error
    threads = [threading.Thread(target=run, args=(name, operation)) for name, operation in workers.items()]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), f"{label} deadlocked"
    assert exceptions == {}, f"{label} worker exceptions: {exceptions!r}"
    return results

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
    exceptions = []
    barrier = threading.Barrier(5)

    def worker():
        try:
            barrier.wait(timeout=10)
            with sessions() as session:
                res = intents.commit(provider.id, intent_id, orders, session=session)
                session.commit()
                results.append(res)
        except BaseException as error:  # thread failures must fail this qualification explicitly
            exceptions.append(error)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "concurrent intent commit deadlocked"
    assert exceptions == []

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
        assert orders_in_db[0].intent_id == intent_id


@pytest.mark.parametrize("pre_registered,mismatch", [(False, False), (False, True), (True, False), (True, True)])
def test_legacy_and_intent_paths_converge_under_real_postgres(
    sessions: sessionmaker, pre_registered: bool, mismatch: bool
) -> None:
    """PG3–PG6: actual HTTP registration/legacy/commit operations share one reservation."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    prefix = f"pg003c-race-{uuid.uuid4().hex[:8]}"
    provider = organizations.create(org_type="PROVIDER", name=prefix)
    entry, _ = catalog.create_entry(code=f"{prefix}-T", display_name=prefix)
    patient_a = patients.get_or_create(source_organization_id=provider.id, source_system=prefix, source_identity_system=f"urn:{prefix}", source_identity_value="A")
    patient_b = patients.get_or_create(source_organization_id=provider.id, source_system=prefix, source_identity_system=f"urn:{prefix}", source_identity_value="B")
    app, private_key = _race_clients(sessions)
    token = _race_clinician(app, private_key, organizations, provider)
    key = f"{prefix}-key"
    headers = {**_auth(token), "Idempotency-Key": key}
    intent_url = f"/exchange/v1/organizations/{provider.id}/order-intents"
    order_url = f"/exchange/v1/organizations/{provider.id}/orders"
    payload_a = {"patient_reference_id": str(patient_a.id), "catalog_entry_ids": [str(entry.id)]}
    payload_b = {"patient_reference_id": str(patient_b.id), "catalog_entry_ids": [str(entry.id)]}
    intent_id = None
    if pre_registered:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(intent_url, headers=headers, json=payload_a)
            assert response.status_code == 201, response.text
            intent_id = response.json()["id"]
    def register():
        with TestClient(app, raise_server_exceptions=False) as client:
            return client.post(intent_url, headers=headers, json=payload_a)
    def legacy():
        with TestClient(app, raise_server_exceptions=False) as client:
            return client.post(order_url, headers=headers, json=payload_b if mismatch else payload_a)
    def commit():
        with TestClient(app, raise_server_exceptions=False) as client:
            return client.post(f"{intent_url}/{intent_id}/commit", headers=_auth(token))
    results = _run_race("PG3-PG6", {"native": commit if pre_registered else register, "legacy": legacy})
    statuses = {result.status_code for result in results.values()}
    assert statuses <= {200, 201, 409}, statuses
    if mismatch: assert 409 in statuses
    else: assert statuses <= {200, 201}
    with sessions() as session:
        intents = session.scalars(select(LabOrderIntent).where(LabOrderIntent.provider_organization_id == provider.id, LabOrderIntent.idempotency_key == key)).all()
        orders = session.scalars(select(LabOrder).where(LabOrder.provider_organization_id == provider.id, LabOrder.idempotency_key == key)).all()
        assert len(intents) == 1
        intent = intents[0]
        assert intent.patient_reference_id in {patient_a.id, patient_b.id} if mismatch and not pre_registered else intent.patient_reference_id == patient_a.id
        assert len(orders) == 1
        assert orders[0].intent_id == intent.id
        assert intent.committed_lab_order_id == orders[0].id
        assert len(session.scalars(select(LabOrderItem).where(LabOrderItem.lab_order_id == orders[0].id)).all()) == 1
        assert len(session.scalars(select(LabOrderIntentItem).where(LabOrderIntentItem.intent_id == intent.id)).all()) == 1
