"""DH-EX-005: secure patient order access + atomic patient lab selection.

Same harness pattern as ``test_exchange_orders_api.py``: a bare ``FastAPI()`` app with
``build_exchange_router`` mounted directly, a ``StaticKeySource``-backed ``TokenValidator`` (local
RSA keypair, zero network) for the provider/OIDC side, plus the entirely separate opaque
bearer-token path for patient routes.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    LabOrder,
    LabSelection,
    PatientAccessGrant,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository

TTL_HOURS = 168


@pytest.fixture
def keypair():
    return generate_keypair()


def _build_repo_harness(tmp_path: Path, *, db_name: str):
    """Repository-direct harness for real-thread concurrency proofs -- matches
    ``test_exchange_catalog_dh_ex_003.py``'s and ``test_exchange_idempotency_concurrency.py``'s
    own established pattern exactly (dedicated engine, ``BEGIN IMMEDIATE``, 30s busy-timeout,
    direct repository calls). No existing concurrency test in this codebase drives real threads
    through the full HTTP/TestClient stack -- FastAPI's own thread pool plus a deferred-BEGIN
    default-5s-timeout SQLite connection compounds badly under genuine multi-thread write
    contention (observed directly: reliably timing out even with BEGIN IMMEDIATE + 30s once
    routed through TestClient). The auth/HTTP boundary this would otherwise exercise is already
    covered by this file's non-concurrent HTTP tests; this harness isolates exactly the race this
    test exists to prove -- the domain-layer transaction, locking, and DB constraints."""
    from sqlalchemy import create_engine, event

    from daher_adapter.exchange.models import Base
    from daher_adapter.exchange.orders import LabOrderRepository
    from daher_adapter.exchange.patient_access import PatientAccessGrantRepository
    from daher_adapter.exchange.selection import LabSelectionRepository

    database = tmp_path / f"{db_name}.sqlite"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _disable(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin_immediate(connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    Base.metadata.create_all(engine)
    local_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    local_organizations = OrganizationRepository(local_sessions)
    local_catalog = CatalogRepository(local_sessions, local_organizations)
    local_directory = LabDirectoryProfileRepository(local_sessions, local_organizations)
    local_patients = PatientReferenceRepository(local_sessions)
    local_orders = LabOrderRepository(local_sessions, local_organizations, local_catalog)
    local_selections = LabSelectionRepository(local_sessions, local_organizations)
    local_access = PatientAccessGrantRepository(local_sessions, ttl_hours=TTL_HOURS)
    return {
        "sessions": local_sessions,
        "organizations": local_organizations,
        "catalog": local_catalog,
        "directory": local_directory,
        "patients": local_patients,
        "orders": local_orders,
        "selections": local_selections,
        "access": local_access,
    }


@pytest.fixture
def client(sessions: sessionmaker[Session], keypair, tmp_path: Path) -> TestClient:
    _, public_key = keypair
    validator = build_validator(public_key)
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=validator,
        audit_writer=ExchangeAuditWriter(tmp_path / "exchange-audit.jsonl"),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
        patient_access_ttl_hours=TTL_HOURS,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


@pytest.fixture
def catalog(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> CatalogRepository:
    return CatalogRepository(sessions, organizations)


@pytest.fixture
def directory(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> LabDirectoryProfileRepository:
    return LabDirectoryProfileRepository(sessions, organizations)


@pytest.fixture
def patients(sessions: sessionmaker[Session]) -> PatientReferenceRepository:
    return PatientReferenceRepository(sessions)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bootstrap_identity(client: TestClient, private_key, subject: str, **token_kwargs) -> str:
    token = make_token(private_key, subject=subject, **token_kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"]


def _grant_roles(
    client: TestClient, private_key, org_id, identity_id, subject: str, roles: list[str]
) -> None:
    platform_admin_token = make_token(
        private_key, subject=f"setup-admin-{subject}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(platform_admin_token),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text


def _setup_clinician(client: TestClient, private_key, organizations, name: str, *, roles=None):
    provider = make_provider(organizations, name=name)
    identity_id = _bootstrap_identity(client, private_key, f"clinician-{name}")
    _grant_roles(
        client, private_key, provider.id, identity_id, f"clinician-{name}", roles or ["CLINICIAN"]
    )
    token = make_token(private_key, subject=f"clinician-{name}")
    return provider, token


def _create_patient(client, private_key, patients, provider_id, name: str) -> uuid.UUID:
    record = patients.get_or_create(
        source_organization_id=provider_id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-PATIENT-{name}",
        display_name="Synthetic Patient",
    )
    return record.id


def _new_key() -> str:
    return str(uuid.uuid4())


def _setup_lab(
    catalog, directory, organizations, *, name: str, tests: list[tuple[uuid.UUID, Decimal]]
):
    lab = make_lab(organizations, name=name)
    for entry_id, amount in tests:
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_id)
        catalog.set_price(
            offering_id=offering.id, amount=amount, currency="DJF", expected_current_price_id=None
        )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address=f"{name} Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    return lab


def _create_order_awaiting_selection(
    client, private_key, organizations, catalog, patients, *, clinician_name="A", test_count=2
):
    provider, token = _setup_clinician(client, private_key, organizations, clinician_name)
    patient_id = _create_patient(client, private_key, patients, provider.id, clinician_name)
    entry_ids = []
    for i in range(test_count):
        entry, _ = catalog.create_entry(
            code=f"DHEALTH-PA-DEMO-{clinician_name}-{i}", display_name=f"Demo Test {i}"
        )
        entry_ids.append(entry.id)
    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(e) for e in entry_ids],
        },
    )
    assert response.status_code == 201, response.text
    order_id = uuid.UUID(response.json()["id"])
    return provider, token, order_id, entry_ids


def _issue_access(client, private_key, provider_id, order_id, token) -> tuple[str, str]:
    response = client.post(
        f"/exchange/v1/organizations/{provider_id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], body["expires_at"]


def _audit_events(sessions: sessionmaker[Session], event_type: str) -> list[dict]:
    with sessions() as session:
        rows = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == event_type
            )
        ).all()
        return [
            {
                "outcome": row.outcome,
                "resource_type": row.resource_type,
                "resource_id": str(row.resource_id) if row.resource_id else None,
                "organization_id": str(row.organization_id) if row.organization_id else None,
            }
            for row in rows
        ]


# -- golden backend test (Part "GOLDEN BACKEND TEST") ----------------------------------------------


def test_golden_backend_journey(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token = _setup_clinician(client, private_key, organizations, "Golden")
    patient_id = _create_patient(client, private_key, patients, provider.id, "Golden")
    entry_a, _ = catalog.create_entry(code="DHEALTH-GOLDEN-A", display_name="Demo Test A")
    entry_b, _ = catalog.create_entry(code="DHEALTH-GOLDEN-B", display_name="Demo Test B")

    lab_a = _setup_lab(
        catalog,
        directory,
        organizations,
        name="DHealth Demo Lab A",
        tests=[(entry_a.id, Decimal("1000")), (entry_b.id, Decimal("2000"))],
    )
    _setup_lab(
        catalog,
        directory,
        organizations,
        name="DHealth Demo Lab B",
        tests=[(entry_a.id, Decimal("1200")), (entry_b.id, Decimal("2300"))],
    )

    # 1. clinician creates order
    create_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(entry_a.id), str(entry_b.id)],
        },
    )
    assert create_response.status_code == 201
    order_id = create_response.json()["id"]
    assert create_response.json()["status"] == "AWAITING_LAB_SELECTION"

    # 2. clinician issues access
    with sessions() as session:
        before_orders = session.scalar(select(func.count()).select_from(LabOrder))
    access_token, expires_at = _issue_access(
        client, private_key, provider.id, order_id, clinician_token
    )
    assert access_token
    assert expires_at
    with sessions() as session:
        after_orders = session.scalar(select(func.count()).select_from(LabOrder))
        grant = session.scalar(select(PatientAccessGrant))
        assert grant.token_hash != access_token
        issued_grant_id = grant.id
    assert after_orders == before_orders

    # 3. patient GET order
    order_view = client.get("/exchange/v1/patient/order", headers=_auth(access_token))
    assert order_view.status_code == 200
    assert order_view.json()["status"] == "AWAITING_LAB_SELECTION"
    assert order_view.json()["selection"] is None
    assert len(order_view.json()["requested_tests"]) == 2

    # 4. patient GET lab-options
    options_response = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    )
    assert options_response.status_code == 200
    options = options_response.json()["options"]
    assert len(options) == 2
    assert options[0]["lab"]["id"] == str(lab_a.id)  # cheaper total sorts first
    assert Decimal(options[0]["total_amount"]) == Decimal("3000")
    price_ids_a = [line["current_price_id"] for line in options[0]["prices"]]

    # 5. patient selects Lab A with exact expected price ids
    selection_response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab_a.id), "expected_price_ids": price_ids_a},
    )
    assert selection_response.status_code == 201, selection_response.text
    body = selection_response.json()
    assert body["status"] == "SELECTED"
    assert body["selection"]["lab_organization_id"] == str(lab_a.id)
    assert Decimal(body["selection"]["total_amount"]) == Decimal("3000")

    with sessions() as session:
        selection_count = session.scalar(select(func.count()).select_from(LabSelection))
        selection_row = session.scalar(select(LabSelection))
        assert selection_row.selected_via_patient_access_grant_id == issued_grant_id
    assert selection_count == 1

    # 6. change Lab A prices afterward -- receipt must remain 3000
    offering_a_a, _ = catalog.create_offering(
        lab_organization_id=lab_a.id, catalog_entry_id=entry_a.id
    )
    with sessions() as session:
        from daher_adapter.exchange.models import LabOffering, LabPrice

        offering_id = session.scalar(
            select(LabOffering.id).where(
                LabOffering.lab_organization_id == lab_a.id,
                LabOffering.catalog_entry_id == entry_a.id,
            )
        )
        current_price_id = session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering_id, LabPrice.superseded_at.is_(None)
            )
        )
    catalog.set_price(
        offering_id=offering_id,
        amount=Decimal("9999"),
        currency="DJF",
        expected_current_price_id=current_price_id,
    )
    receipt_after = client.get("/exchange/v1/patient/order", headers=_auth(access_token))
    assert Decimal(receipt_after.json()["selection"]["total_amount"]) == Decimal("3000")


# -- stale price golden negative ---------------------------------------------------------------


def test_stale_price_golden_negative(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="Stale", test_count=1
    )
    lab = _setup_lab(
        catalog, directory, organizations, name="Stale Lab", tests=[(entry_ids[0], Decimal("1000"))]
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)

    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    old_price_id = options["options"][0]["prices"][0]["current_price_id"]

    # Lab admin changes the price before the patient clicks select.
    with sessions() as session:
        from daher_adapter.exchange.models import LabOffering

        offering_id = session.scalar(
            select(LabOffering.id).where(
                LabOffering.lab_organization_id == lab.id,
                LabOffering.catalog_entry_id == entry_ids[0],
            )
        )
    catalog.set_price(
        offering_id=offering_id,
        amount=Decimal("5000"),
        currency="DJF",
        expected_current_price_id=uuid.UUID(old_price_id),
    )

    stale_response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [old_price_id]},
    )
    assert stale_response.status_code == 409

    with sessions() as session:
        selection_count = session.scalar(select(func.count()).select_from(LabSelection))
    assert selection_count == 0
    order_view = client.get("/exchange/v1/patient/order", headers=_auth(access_token))
    assert order_view.json()["status"] == "AWAITING_LAB_SELECTION"

    # Refresh options, select with the new price -- succeeds.
    refreshed = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    new_price_id = refreshed["options"][0]["prices"][0]["current_price_id"]
    assert new_price_id != old_price_id
    success = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [new_price_id]},
    )
    assert success.status_code == 201


def test_lab_becomes_unavailable_negative(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="Unavail",
        test_count=1,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="Unavail Lab",
        tests=[(entry_ids[0], Decimal("1000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_id = options["options"][0]["prices"][0]["current_price_id"]

    directory.upsert(
        lab_organization_id=lab.id,
        public_address="Unavail Lab Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=False,
    )

    response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [price_id]},
    )
    assert response.status_code == 409
    with sessions() as session:
        selection_count = session.scalar(select(func.count()).select_from(LabSelection))
    assert selection_count == 0


# -- concurrency ----------------------------------------------------------------------------------


def test_concurrent_double_click_same_lab_produces_one_selection(tmp_path: Path) -> None:
    import threading

    from daher_adapter.exchange.orders import OrderItemInput
    from daher_adapter.exchange.selection import DifferentLabAlreadySelected

    h = _build_repo_harness(tmp_path, db_name="double-click")
    provider = make_provider(h["organizations"], name="DoubleClickProvider")
    entry, _ = h["catalog"].create_entry(code="DHEALTH-DBLCLICK", display_name="Double Click Test")
    lab = _setup_lab(
        h["catalog"],
        h["directory"],
        h["organizations"],
        name="DoubleClick Lab",
        tests=[(entry.id, Decimal("1000"))],
    )
    patient = h["patients"].get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="SYNTH-PATIENT-DBLCLICK",
        display_name="Synthetic Patient",
    )
    from daher_adapter.exchange.identity import PractitionerReferenceRepository

    practitioners = PractitionerReferenceRepository(h["sessions"])
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-practitioner-id",
        source_identity_value="SYNTH-DOCTOR-DBLCLICK",
        display_name="Dr Synthetic",
    )
    order = (
        h["orders"]
        .create(
            idempotency_key="double-click-1",
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            items=[OrderItemInput(catalog_entry_id=entry.id)],
        )
        .order
    )
    with h["sessions"]() as session:
        from daher_adapter.exchange.models import LabOffering, LabPrice

        offering_id = session.scalar(
            select(LabOffering.id).where(
                LabOffering.lab_organization_id == lab.id, LabOffering.catalog_entry_id == entry.id
            )
        )
        price_id = session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering_id, LabPrice.superseded_at.is_(None)
            )
        )

    outcomes: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    thread_count = 10
    barrier = threading.Barrier(thread_count)

    def attempt():
        try:
            barrier.wait(timeout=10)
            _, created = h["selections"].select(
                lab_order_id=order.id,
                lab_organization_id=lab.id,
                expected_price_ids=frozenset({price_id}),
            )
            with lock:
                outcomes.append(created)
        except DifferentLabAlreadySelected as error:
            with lock:
                errors.append(error)
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"unexpected exceptions/refusals for a same-lab replay: {errors!r}"
    assert len(outcomes) == thread_count
    assert outcomes.count(True) == 1
    with h["sessions"]() as session:
        selection_count = session.scalar(select(func.count()).select_from(LabSelection))
    assert selection_count == 1


def test_concurrent_two_lab_race_produces_one_coherent_winner(tmp_path: Path) -> None:
    import threading

    from daher_adapter.exchange.orders import OrderItemInput
    from daher_adapter.exchange.selection import DifferentLabAlreadySelected

    h = _build_repo_harness(tmp_path, db_name="two-lab-race")
    provider = make_provider(h["organizations"], name="TwoLabProvider")
    entry, _ = h["catalog"].create_entry(code="DHEALTH-TWOLAB", display_name="Two Lab Race Test")
    lab_a = _setup_lab(
        h["catalog"],
        h["directory"],
        h["organizations"],
        name="TwoLab A",
        tests=[(entry.id, Decimal("1000"))],
    )
    lab_b = _setup_lab(
        h["catalog"],
        h["directory"],
        h["organizations"],
        name="TwoLab B",
        tests=[(entry.id, Decimal("2000"))],
    )
    patient = h["patients"].get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="SYNTH-PATIENT-TWOLAB",
        display_name="Synthetic Patient",
    )
    from daher_adapter.exchange.identity import PractitionerReferenceRepository

    practitioners = PractitionerReferenceRepository(h["sessions"])
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-practitioner-id",
        source_identity_value="SYNTH-DOCTOR-TWOLAB",
        display_name="Dr Synthetic",
    )
    order = (
        h["orders"]
        .create(
            idempotency_key="two-lab-race-1",
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            items=[OrderItemInput(catalog_entry_id=entry.id)],
        )
        .order
    )

    price_by_lab = {}
    with h["sessions"]() as session:
        from daher_adapter.exchange.models import LabOffering, LabPrice

        for lab in (lab_a, lab_b):
            offering_id = session.scalar(
                select(LabOffering.id).where(
                    LabOffering.lab_organization_id == lab.id,
                    LabOffering.catalog_entry_id == entry.id,
                )
            )
            price_by_lab[lab.id] = session.scalar(
                select(LabPrice.id).where(
                    LabPrice.lab_offering_id == offering_id, LabPrice.superseded_at.is_(None)
                )
            )

    outcomes: list[bool] = []
    errors: list[BaseException] = []
    refusals: list[str] = []
    lock = threading.Lock()
    thread_count = 10
    barrier = threading.Barrier(thread_count)

    def attempt(lab_id):
        try:
            barrier.wait(timeout=10)
            _, created = h["selections"].select(
                lab_order_id=order.id,
                lab_organization_id=lab_id,
                expected_price_ids=frozenset({price_by_lab[lab_id]}),
            )
            with lock:
                outcomes.append(created)
        except DifferentLabAlreadySelected:
            with lock:
                refusals.append("different_lab")
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                errors.append(error)

    def _lab_for(i: int):
        return lab_a.id if i % 2 == 0 else lab_b.id

    threads = [threading.Thread(target=attempt, args=(_lab_for(i),)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"unexpected exceptions: {errors!r}"
    assert len(outcomes) + len(refusals) == thread_count
    assert outcomes.count(True) == 1
    with h["sessions"]() as session:
        selection_count = session.scalar(select(func.count()).select_from(LabSelection))
        selection = session.scalar(select(LabSelection))
    assert selection_count == 1
    assert selection.lab_organization_id in (lab_a.id, lab_b.id)


# -- one-live-grant / no-silent-rotation / expiry -----------------------------------------------


def test_reissuing_with_active_grant_is_refused(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="Reissue"
    )
    first = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )
    assert first.status_code == 201
    second = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )
    assert second.status_code == 409


def test_revoke_then_reissue_succeeds(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="RevokeReissue"
    )
    first_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    revoke_response = client.delete(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )
    assert revoke_response.status_code == 204

    # Old token is now dead.
    assert client.get("/exchange/v1/patient/order", headers=_auth(first_token)).status_code == 401

    second_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    assert second_token != first_token
    assert client.get("/exchange/v1/patient/order", headers=_auth(second_token)).status_code == 200


def test_repeated_revoke_is_idempotent_no_op(
    client: TestClient, keypair, organizations, catalog, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="RepeatRevoke"
    )
    _issue_access(client, private_key, provider.id, order_id, token)
    first = client.delete(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )
    second = client.delete(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )
    assert first.status_code == 204
    assert second.status_code == 204

    events = _audit_events(sessions, "patient_access_revoked")
    assert len(events) == 1, "a no-op revoke must never emit a second audit row"


def test_expired_grant_replacement(
    client: TestClient, keypair, organizations, catalog, patients, sessions
) -> None:
    import datetime as dt

    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="Expired"
    )
    old_token, _ = _issue_access(client, private_key, provider.id, order_id, token)

    # Force the grant into the past without waiting -- deterministic frozen-time test.
    with sessions() as session:
        grant = session.scalar(select(PatientAccessGrant))
        grant.issued_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=TTL_HOURS + 2)
        grant.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
        session.commit()

    assert client.get("/exchange/v1/patient/order", headers=_auth(old_token)).status_code == 401

    new_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    assert new_token != old_token
    assert client.get("/exchange/v1/patient/order", headers=_auth(new_token)).status_code == 200
    assert client.get("/exchange/v1/patient/order", headers=_auth(old_token)).status_code == 401

    with sessions() as session:
        live_count = session.scalar(
            select(func.count())
            .select_from(PatientAccessGrant)
            .where(PatientAccessGrant.revoked_at.is_(None))
        )
    assert live_count == 1


# -- DH-EX-005R Part U/V/F: in-transaction grant revalidation -------------------------------------


def test_selection_refuses_a_grant_revoked_before_the_mutation_transaction(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Deterministic (no race needed): a revoke that fully commits before `select()`'s own
    transaction begins is the simplest instance of the "auth dependency validated the grant, but
    it was revoked before the mutation transaction" class. If `select()` ever trusted the
    lab_order_id/grant_id resolved by the (already-closed) auth dependency without re-checking the
    grant's live DB state, this would succeed despite the grant being dead -- DH-EX-005R found this
    as a BLOCKER; `select()` now re-reads the grant inside its own transaction and must refuse."""
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="RevokedRace",
        test_count=1,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="RevokedRace Lab",
        tests=[(entry_ids[0], Decimal("1000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_id = options["options"][0]["prices"][0]["current_price_id"]

    from daher_adapter.exchange.patient_access import PatientAccessGrantRepository

    grants = PatientAccessGrantRepository(sessions, ttl_hours=TTL_HOURS)
    outcome, _grant_id = grants.revoke(lab_order_id=order_id)
    assert outcome == "revoked"

    from daher_adapter.exchange.selection import LabSelectionRepository

    selections = LabSelectionRepository(sessions, organizations)
    with sessions() as session:
        grant_row = session.scalar(
            select(PatientAccessGrant).where(PatientAccessGrant.lab_order_id == order_id)
        )
        stale_grant_id = grant_row.id

    from daher_adapter.exchange.selection import PatientAccessGrantInvalidated

    try:
        selections.select(
            lab_order_id=order_id,
            lab_organization_id=lab.id,
            expected_price_ids=frozenset({uuid.UUID(price_id)}),
            patient_access_grant_id=stale_grant_id,
        )
        raise AssertionError("select() must not succeed using an already-revoked grant")
    except PatientAccessGrantInvalidated:
        pass

    with sessions() as session:
        selection_count = session.scalar(select(func.count()).select_from(LabSelection))
    assert selection_count == 0


def test_selection_vs_revoke_race_only_two_coherent_outcomes(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    """Genuine concurrent race, real threads: the patient's POST /selection request has already
    passed `get_patient_access` (grant valid at that instant) and is about to enter
    `LabSelectionRepository.select()`'s own mutation transaction, exactly when the provider's
    revoke commits. The only two coherent outcomes: selection wins the order-row lock race and
    commits (grant revoked immediately after), or revoke wins and selection observes the now-dead
    grant and refuses (401) -- never a third outcome (never a selection created FROM a grant that
    was already revoked at the moment the mutation actually committed)."""
    import threading
    import time

    from daher_adapter.exchange import selection as selection_module

    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="SelectVsRevoke",
        test_count=1,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="SelectVsRevoke Lab",
        tests=[(entry_ids[0], Decimal("1000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_id = options["options"][0]["prices"][0]["current_price_id"]

    original_select = selection_module.LabSelectionRepository.select
    barrier = threading.Barrier(2)

    def _delayed_select(self, **kwargs):
        barrier.wait(timeout=10)
        # Give the concurrent revoke a head start to commit before select()'s own transaction
        # begins its LabOrder lock attempt -- the narrowest possible reproduction of the race.
        time.sleep(0.05)
        return original_select(self, **kwargs)

    monkeypatch.setattr(selection_module.LabSelectionRepository, "select", _delayed_select)

    results = {}

    def do_select():
        response = client.post(
            "/exchange/v1/patient/order/selection",
            headers=_auth(access_token),
            json={"lab_organization_id": str(lab.id), "expected_price_ids": [price_id]},
        )
        results["select_status"] = response.status_code

    def do_revoke():
        barrier.wait(timeout=10)
        response = client.delete(
            f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
            headers=_auth(token),
        )
        results["revoke_status"] = response.status_code

    t1 = threading.Thread(target=do_select)
    t2 = threading.Thread(target=do_revoke)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert results.get("revoke_status") == 204
    assert results.get("select_status") in (201, 401), results
    with sessions() as session:
        selection_count = session.scalar(select(func.count()).select_from(LabSelection))
    if results["select_status"] == 201:
        assert selection_count == 1
    else:
        assert selection_count == 0


def test_selection_refuses_a_grant_expired_before_the_mutation_transaction(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Part V: same principle as the revoke race, for expiry -- a grant valid when the auth
    dependency checked it, but that crosses `expires_at` before the mutation transaction's own
    re-check, must not be allowed to author the mutation."""
    import datetime as dt

    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="ExpireRace",
        test_count=1,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="ExpireRace Lab",
        tests=[(entry_ids[0], Decimal("1000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_id = options["options"][0]["prices"][0]["current_price_id"]

    # Simulate "expiry lands between auth-dependency validation and the mutation transaction" by
    # directly backdating both issued_at and expires_at into the past -- deterministic, no real
    # clock wait needed. Both must move together to satisfy the `expires_at > issued_at` CHECK.
    with sessions() as session:
        grant = session.scalar(
            select(PatientAccessGrant).where(PatientAccessGrant.lab_order_id == order_id)
        )
        grant.issued_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
        grant.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
        session.commit()

    from daher_adapter.exchange.selection import (
        LabSelectionRepository,
        PatientAccessGrantInvalidated,
    )

    selections = LabSelectionRepository(sessions, organizations)
    with sessions() as session:
        grant_row = session.scalar(
            select(PatientAccessGrant).where(PatientAccessGrant.lab_order_id == order_id)
        )
        grant_id = grant_row.id

    try:
        selections.select(
            lab_order_id=order_id,
            lab_organization_id=lab.id,
            expected_price_ids=frozenset({uuid.UUID(price_id)}),
            patient_access_grant_id=grant_id,
        )
        raise AssertionError("select() must not succeed using an already-expired grant")
    except PatientAccessGrantInvalidated:
        pass

    with sessions() as session:
        selection_count = session.scalar(select(func.count()).select_from(LabSelection))
    assert selection_count == 0


def test_grant_bound_to_a_different_order_cannot_authorize_this_orders_selection(
    tmp_path: Path,
) -> None:
    """Part F: the FK alone only proves 'this grant exists', not 'this grant belongs to this
    order' -- the repository must prove `grant.lab_order_id == lab_order_id` explicitly. Internal
    negative control, bypassing the (already structurally IDOR-resistant) public patient routes to
    directly exercise the repository boundary: Grant A belongs to Order A; attempt an internal
    selection call for Order B using Grant A's id."""
    from sqlalchemy import create_engine

    from daher_adapter.exchange.models import Base
    from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
    from daher_adapter.exchange.patient_access import PatientAccessGrantRepository
    from daher_adapter.exchange.selection import (
        LabSelectionRepository,
        PatientAccessGrantInvalidated,
    )

    database = tmp_path / "cross-order-grant.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    local_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    local_organizations = OrganizationRepository(local_sessions)
    local_catalog = CatalogRepository(local_sessions, local_organizations)
    local_directory = LabDirectoryProfileRepository(local_sessions, local_organizations)
    local_patients = PatientReferenceRepository(local_sessions)
    from daher_adapter.exchange.identity import PractitionerReferenceRepository

    local_practitioners = PractitionerReferenceRepository(local_sessions)
    local_orders = LabOrderRepository(local_sessions, local_organizations, local_catalog)
    local_selections = LabSelectionRepository(local_sessions, local_organizations)
    local_access = PatientAccessGrantRepository(local_sessions, ttl_hours=TTL_HOURS)

    provider = make_provider(local_organizations, name="CrossOrderProvider")
    entry, _ = local_catalog.create_entry(
        code="DHEALTH-CROSSORDER", display_name="Cross Order Test"
    )
    lab = _setup_lab(
        local_catalog,
        local_directory,
        local_organizations,
        name="CrossOrder Lab",
        tests=[(entry.id, Decimal("1000"))],
    )
    patient = local_patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="SYNTH-CROSSORDER",
        display_name="Synthetic Patient",
    )
    practitioner = local_practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-practitioner-id",
        source_identity_value="SYNTH-CROSSORDER-DOC",
        display_name="Dr Synthetic",
    )
    order_a = local_orders.create(
        idempotency_key="cross-order-a",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order
    order_b = local_orders.create(
        idempotency_key="cross-order-b",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order

    grant_a = local_access.issue(lab_order_id=order_a.id)

    with local_sessions() as session:
        from daher_adapter.exchange.models import LabOffering, LabPrice

        offering_id = session.scalar(
            select(LabOffering.id).where(
                LabOffering.lab_organization_id == lab.id, LabOffering.catalog_entry_id == entry.id
            )
        )
        price_id = session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering_id, LabPrice.superseded_at.is_(None)
            )
        )

    try:
        local_selections.select(
            lab_order_id=order_b.id,
            lab_organization_id=lab.id,
            expected_price_ids=frozenset({price_id}),
            patient_access_grant_id=grant_a.id,
        )
        raise AssertionError("a grant bound to Order A must not authorize a selection on Order B")
    except PatientAccessGrantInvalidated:
        pass

    with local_sessions() as session:
        selection_count = session.scalar(select(func.count()).select_from(LabSelection))
    assert selection_count == 0


def test_grant_issuance_concurrency(tmp_path: Path) -> None:
    """SQLite proof only, matching ``test_exchange_catalog_dh_ex_003.py``'s own established
    concurrency-test pattern exactly: a dedicated engine (so this test controls the busy-timeout),
    ``BEGIN IMMEDIATE`` (this test's transactions always write, so acquiring the write lock
    immediately avoids connections racing to upgrade from reader to writer -- the specific
    pattern that otherwise surfaces `OperationalError: database is locked` even within a
    configured busy-timeout), and direct repository calls rather than the HTTP/TestClient stack
    (whose own thread pool adds overhead this proof doesn't need). The real 25-way proof still
    runs against real PostgreSQL separately (not committed here)."""
    import threading

    from sqlalchemy import create_engine, event

    from daher_adapter.exchange.models import Base
    from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
    from daher_adapter.exchange.patient_access import (
        ActiveGrantAlreadyExists,
        PatientAccessGrantRepository,
    )

    database = tmp_path / "patient-access-concurrency.sqlite"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _disable(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin_immediate(connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    Base.metadata.create_all(engine)
    local_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    local_organizations = OrganizationRepository(local_sessions)
    local_catalog = CatalogRepository(local_sessions, local_organizations)
    local_patients = PatientReferenceRepository(local_sessions)
    from daher_adapter.exchange.identity import PractitionerReferenceRepository

    local_practitioners = PractitionerReferenceRepository(local_sessions)
    local_orders = LabOrderRepository(local_sessions, local_organizations, local_catalog)
    local_access = PatientAccessGrantRepository(local_sessions, ttl_hours=TTL_HOURS)

    provider = make_provider(local_organizations, name="IssueRaceProvider")
    entry, _ = local_catalog.create_entry(code="DHEALTH-ISSUERACE", display_name="Race Test")
    patient = local_patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="SYNTH-PATIENT-ISSUERACE",
        display_name="Synthetic Patient",
    )
    practitioner = local_practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-practitioner-id",
        source_identity_value="SYNTH-DOCTOR-ISSUERACE",
        display_name="Dr Synthetic",
    )
    order = local_orders.create(
        idempotency_key="issue-race-1",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order

    thread_count = 10
    barrier = threading.Barrier(thread_count)
    outcomes: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            barrier.wait(timeout=10)
            local_access.issue(lab_order_id=order.id)
            with lock:
                outcomes.append("issued")
        except ActiveGrantAlreadyExists:
            with lock:
                outcomes.append("refused")
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"unexpected exceptions: {errors!r}"
    assert outcomes.count("issued") == 1
    assert outcomes.count("refused") == thread_count - 1
    with local_sessions() as session:
        live_count = session.scalar(
            select(func.count())
            .select_from(PatientAccessGrant)
            .where(PatientAccessGrant.revoked_at.is_(None))
        )
    assert live_count == 1
    engine.dispose()


# -- issuance authorization --------------------------------------------------------------------


def test_issuance_requires_clinician(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, _, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="AuthCheck"
    )
    org_admin_id = _bootstrap_identity(client, private_key, "org-admin-only")
    _grant_roles(client, private_key, provider.id, org_admin_id, "org-admin-only", ["ORG_ADMIN"])
    org_admin_token = make_token(private_key, subject="org-admin-only")

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(org_admin_token),
    )
    assert response.status_code == 403


def test_platform_admin_alone_cannot_issue_access(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, _, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="PlatformIssue"
    )
    admin_token = make_token(
        private_key, subject="platform-admin-issue", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(admin_token),
    )
    assert response.status_code == 403


# -- patient auth negative controls --------------------------------------------------------------


def test_patient_route_missing_auth_is_401(client: TestClient) -> None:
    assert client.get("/exchange/v1/patient/order").status_code == 401


def test_patient_route_unknown_token_is_401(client: TestClient) -> None:
    assert (
        client.get("/exchange/v1/patient/order", headers=_auth("not-a-real-token")).status_code
        == 401
    )


def test_patient_route_malformed_auth_header_is_401(client: TestClient) -> None:
    response = client.get(
        "/exchange/v1/patient/order", headers={"Authorization": "not-bearer-scheme"}
    )
    assert response.status_code == 401


def test_oidc_token_cannot_be_used_as_patient_token(client: TestClient, keypair) -> None:
    private_key, _ = keypair
    oidc_token = make_token(private_key, subject="clinician-oidc-as-patient")
    response = client.get("/exchange/v1/patient/order", headers=_auth(oidc_token))
    assert response.status_code == 401


def test_patient_token_cannot_be_used_for_provider_routes(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="PatientAsProvider"
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)

    # A patient token presented as an OIDC bearer fails normal OIDC authentication (401), not
    # some intermediate/partial authorization state.
    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders", headers=_auth(access_token)
    )
    assert response.status_code == 401


def test_revoked_token_is_401(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="Revoked"
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    client.delete(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )
    response = client.get("/exchange/v1/patient/order", headers=_auth(access_token))
    assert response.status_code == 401


# -- patient order scope ------------------------------------------------------------------------


def test_token_a_cannot_access_order_b(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider_a, token_a, order_a, _ = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="ScopeA"
    )
    provider_b, token_b, order_b, _ = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="ScopeB"
    )
    access_a, _ = _issue_access(client, private_key, provider_a.id, order_a, token_a)

    order_view = client.get("/exchange/v1/patient/order", headers=_auth(access_a))
    assert order_view.status_code == 200
    # There is no way to even *ask* for order B through this token -- no order_id parameter
    # exists anywhere on the patient routes to try to override.
    assert "order_id" not in order_view.json()
    assert "provider_id" not in order_view.json()
    assert "patient_reference_id" not in order_view.json()


def test_cross_provider_token_cannot_expose_other_provider_data(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider_a, token_a, order_a, entries_a = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="CrossA"
    )
    access_a, _ = _issue_access(client, private_key, provider_a.id, order_a, token_a)

    options = client.get("/exchange/v1/patient/order/lab-options", headers=_auth(access_a))
    assert options.status_code == 200
    assert options.json()["options"] == []  # no lab set up for these tests


# -- zero-eligible-labs / result-data exposure ---------------------------------------------------


def test_zero_eligible_labs_returns_empty_not_error(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="ZeroEligible"
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    response = client.get("/exchange/v1/patient/order/lab-options", headers=_auth(access_token))
    assert response.status_code == 200
    assert response.json()["options"] == []

    order_view = client.get("/exchange/v1/patient/order", headers=_auth(access_token))
    assert order_view.json()["status"] == "AWAITING_LAB_SELECTION"


def test_patient_order_response_excludes_internal_and_result_fields(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="NoLeak"
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    body = client.get("/exchange/v1/patient/order", headers=_auth(access_token)).json()

    forbidden_keys = {
        "patient_reference_id",
        "practitioner_reference_id",
        "identity_id",
        "membership_id",
        "numeric_value",
        "text_value",
        "reference_range_low",
        "reference_range_high",
        "contact_value",
    }
    assert forbidden_keys.isdisjoint(body.keys())


def _all_keys(value) -> set[str]:
    """Recursively collect every dict key in a JSON-decoded response body -- the top-level-only
    check above would miss a leak nested inside e.g. `body["selection"]["items"][0]`."""
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, sub_value in value.items():
            keys.add(key)
            keys |= _all_keys(sub_value)
    elif isinstance(value, list):
        for item in value:
            keys |= _all_keys(item)
    return keys


def test_patient_order_response_never_leaks_a_real_lab_result_even_after_selection(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """DH-EX-005R Part Y: do not rely merely on 'results are not normally created yet' -- create a
    genuine `LabResult` row (numeric AND text) directly via the repository, exactly as DH-EX-001's
    own result-entry workflow would, then prove `GET /patient/order` (with a real, populated
    `selection`) still never serializes any of it. DH-EX-005 does not expose detailed results at
    all, per ADR-042's patient result-release policy."""
    from decimal import Decimal as D

    from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
    from daher_adapter.exchange.results import LabResultRepository

    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="ResultLeak",
        test_count=2,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="ResultLeak Lab",
        tests=[(entry_ids[0], Decimal("1000")), (entry_ids[1], Decimal("2000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_ids = [line["current_price_id"] for line in options["options"][0]["prices"]]
    selection_response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert selection_response.status_code == 201

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        item_ids = [item.id for item in order.items]

    # DH-EX-007: a result may only be recorded once the selected lab is IN_PROGRESS -- drive the
    # order there via the exact DH-EX-006 workflow repository before inserting the results this
    # test needs to prove never leak to the patient.
    workflow = LabWorkflowRepository(sessions, organizations)
    workflow.accept(order_id=order_id, lab_organization_id=lab.id)
    workflow.start_processing(order_id=order_id, lab_organization_id=lab.id)

    results = LabResultRepository(sessions)
    results.record_numeric(
        order_id=order_id,
        lab_order_item_id=item_ids[0],
        lab_organization_id=lab.id,
        value=D("999.5"),
        unit="mg/dL",
        reference_range_low=D("70"),
        reference_range_high=D("100"),
    )
    results.record_text(
        order_id=order_id,
        lab_order_item_id=item_ids[1],
        lab_organization_id=lab.id,
        value="Positive -- urgent",
    )

    body = client.get("/exchange/v1/patient/order", headers=_auth(access_token)).json()
    assert body["selection"] is not None, "sanity check: selection must be populated for this test"

    forbidden_keys = {
        "numeric_value",
        "text_value",
        "numeric_unit",
        "reference_range_low",
        "reference_range_high",
        "result_type",
        "value",
        "unit",
    }
    assert forbidden_keys.isdisjoint(_all_keys(body))

    body_str = str(body)
    assert "999.5" not in body_str
    assert "Positive" not in body_str
    assert "mg/dL" not in body_str


# -- selection request validation ----------------------------------------------------------------


def test_selection_rejects_duplicate_expected_price_ids(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="DupPrice",
        test_count=1,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="DupPrice Lab",
        tests=[(entry_ids[0], Decimal("1000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_id = options["options"][0]["prices"][0]["current_price_id"]

    response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [price_id, price_id]},
    )
    assert response.status_code == 422


def test_selection_rejects_extra_body_fields(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="ExtraField",
        test_count=1,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="ExtraField Lab",
        tests=[(entry_ids[0], Decimal("1000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_id = options["options"][0]["prices"][0]["current_price_id"]

    response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={
            "lab_organization_id": str(lab.id),
            "expected_price_ids": [price_id],
            "lab_order_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422


# -- same-lab replay / different-lab refusal -----------------------------------------------------


def test_same_lab_replay_after_selection(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="Replay", test_count=1
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="Replay Lab",
        tests=[(entry_ids[0], Decimal("1000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_id = options["options"][0]["prices"][0]["current_price_id"]

    body = {"lab_organization_id": str(lab.id), "expected_price_ids": [price_id]}
    first = client.post(
        "/exchange/v1/patient/order/selection", headers=_auth(access_token), json=body
    )
    second = client.post(
        "/exchange/v1/patient/order/selection", headers=_auth(access_token), json=body
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["selection"]["selected_at"] == second.json()["selection"]["selected_at"]


def test_same_lab_replay_after_price_change_returns_original_frozen_price(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Distinct from plain replay above: the *second* `POST /selection` call is made with the
    OLD (now-stale) `expected_price_ids` the patient originally consented to -- proving the
    same-lab short-circuit returns the historical frozen record without re-validating against the
    lab's current (changed) price, exactly the "same-lab replay after price change" contract the
    ticket lists separately from plain replay."""
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="ReplayStale",
        test_count=1,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="ReplayStale Lab",
        tests=[(entry_ids[0], Decimal("1000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    original_price_id = options["options"][0]["prices"][0]["current_price_id"]

    first = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [original_price_id]},
    )
    assert first.status_code == 201
    assert Decimal(first.json()["selection"]["total_amount"]) == Decimal("1000")

    with sessions() as session:
        from daher_adapter.exchange.models import LabOffering

        offering_id = session.scalar(
            select(LabOffering.id).where(
                LabOffering.lab_organization_id == lab.id,
                LabOffering.catalog_entry_id == entry_ids[0],
            )
        )
    catalog.set_price(
        offering_id=offering_id,
        amount=Decimal("9999"),
        currency="DJF",
        expected_current_price_id=uuid.UUID(original_price_id),
    )

    # Replayed with the ORIGINAL (now-stale) price id the patient actually consented to --
    # must succeed with the original frozen amount, never reject as "stale" and never re-price.
    replay = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [original_price_id]},
    )
    assert replay.status_code == 200
    assert Decimal(replay.json()["selection"]["total_amount"]) == Decimal("1000")
    assert replay.json()["selection"]["selected_at"] == first.json()["selection"]["selected_at"]


def test_different_lab_second_selection_refused(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="DiffLab",
        test_count=1,
    )
    lab_a = _setup_lab(
        catalog, directory, organizations, name="DiffLab A", tests=[(entry_ids[0], Decimal("1000"))]
    )
    lab_b = _setup_lab(
        catalog, directory, organizations, name="DiffLab B", tests=[(entry_ids[0], Decimal("2000"))]
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_by_lab = {
        opt["lab"]["id"]: opt["prices"][0]["current_price_id"] for opt in options["options"]
    }

    first = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={
            "lab_organization_id": str(lab_a.id),
            "expected_price_ids": [price_by_lab[str(lab_a.id)]],
        },
    )
    assert first.status_code == 201
    second = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={
            "lab_organization_id": str(lab_b.id),
            "expected_price_ids": [price_by_lab[str(lab_b.id)]],
        },
    )
    assert second.status_code == 409


def test_lab_options_and_selection_fail_closed_after_selected(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="PostSelect",
        test_count=1,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="PostSelect Lab",
        tests=[(entry_ids[0], Decimal("1000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_id = options["options"][0]["prices"][0]["current_price_id"]
    client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [price_id]},
    )

    post_options = client.get("/exchange/v1/patient/order/lab-options", headers=_auth(access_token))
    assert post_options.status_code == 409


# -- audit ----------------------------------------------------------------------------------------


def test_issuance_and_revocation_audit(
    client: TestClient, keypair, organizations, catalog, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="AuditCheck"
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    client.delete(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )

    issue_events = _audit_events(sessions, "patient_access_issued")
    revoke_events = _audit_events(sessions, "patient_access_revoked")
    assert len(issue_events) == 1
    assert issue_events[0]["outcome"] == "created"
    assert issue_events[0]["resource_type"] == "patient_access_grant"
    assert issue_events[0]["organization_id"] == str(provider.id)
    assert len(revoke_events) == 1
    assert revoke_events[0]["outcome"] == "updated"

    # The raw token never appears in any audit row's serialized fields.
    with sessions() as session:
        rows = session.scalars(select(ExchangeSecurityAuditEvent)).all()
    for row in rows:
        for value in (row.event_type, row.outcome, row.resource_type):
            if value:
                assert access_token not in value


def test_issuance_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, patients, sessions, monkeypatch
) -> None:
    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="AuditAtomic"
    )

    with sessions() as session:
        before_grants = session.scalar(select(func.count()).select_from(PatientAccessGrant))
        before_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit-insert failure"):
        client.post(
            f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
            headers=_auth(token),
        )

    with sessions() as session:
        after_grants = session.scalar(select(func.count()).select_from(PatientAccessGrant))
        after_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))
    assert after_grants == before_grants
    assert after_audit == before_audit


def test_revocation_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, patients, sessions, monkeypatch
) -> None:
    """DH-EX-005R Part AT: the mirror of `test_issuance_audit_atomicity` above -- a failure in the
    audit write during revocation must roll back the grant's `revoked_at` mutation too, leaving
    the grant live (never a state where the grant is revoked but the audit trail doesn't say so,
    or vice versa)."""
    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="RevokeAuditAtomic"
    )
    _issue_access(client, private_key, provider.id, order_id, token)

    with sessions() as session:
        before_live = session.scalar(
            select(func.count())
            .select_from(PatientAccessGrant)
            .where(PatientAccessGrant.revoked_at.is_(None))
        )
        before_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))
    assert before_live == 1

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated revoke-audit-insert failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated revoke-audit-insert failure"):
        client.delete(
            f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
            headers=_auth(token),
        )

    with sessions() as session:
        after_live = session.scalar(
            select(func.count())
            .select_from(PatientAccessGrant)
            .where(PatientAccessGrant.revoked_at.is_(None))
        )
        after_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))
    assert after_live == 1, "the failed audit write must roll back revoked_at too"
    assert after_audit == before_audit


# -- token storage safety --------------------------------------------------------------------------


def test_raw_token_never_stored(
    client: TestClient, keypair, organizations, catalog, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, token, order_id, _entry_ids = _create_order_awaiting_selection(
        client, private_key, organizations, catalog, patients, clinician_name="TokenStorage"
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)

    with sessions() as session:
        grant = session.scalar(select(PatientAccessGrant))
    assert grant.token_hash != access_token
    assert access_token not in grant.token_hash
    assert len(grant.token_hash) == 64  # SHA-256 hex digest


# -- issuance status gate ------------------------------------------------------------------------


def test_issuance_refused_when_order_not_awaiting_selection(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="WrongStatus",
        test_count=1,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="WrongStatus Lab",
        tests=[(entry_ids[0], Decimal("1000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_id = options["options"][0]["prices"][0]["current_price_id"]
    selection = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [price_id]},
    )
    assert selection.status_code == 201
    # Revoking the now-SELECTED order's grant then re-issuing must be refused -- issuance is
    # gated to AWAITING_LAB_SELECTION only, never SELECTED (no rollback/reselection workflow).
    client.delete(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )
    reissue = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )
    assert reissue.status_code == 409


# -- selection transaction atomicity --------------------------------------------------------------


def test_selection_transaction_atomicity_on_failure(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    """A partial apply of `select()` -- some `LabOrderItem`s frozen, `LabSelection` inserted, or
    `LabOrder.status` transitioned, without the others -- is exactly the "partial selection /
    mixed-price snapshot" failure class the ticket lists explicitly. Force a failure after the
    per-item price freezing loop has already mutated in-memory state but before commit, and prove
    every part of the mutation rolled back together."""
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="TxnAtomic",
        test_count=2,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="TxnAtomic Lab",
        tests=[(entry_ids[0], Decimal("1000")), (entry_ids[1], Decimal("2000"))],
    )
    access_token, _ = _issue_access(client, private_key, provider.id, order_id, token)
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()
    price_ids = [line["current_price_id"] for line in options["options"][0]["prices"]]

    from daher_adapter.exchange import selection as selection_module

    real_lab_selection = selection_module.LabSelection

    def _raising_lab_selection(*args, **kwargs):
        # `select()`'s own price-freezing loop (mutating `item.selected_price_amount` /
        # `selected_price_currency` on already-loaded, session-tracked `LabOrderItem` rows) runs
        # *before* this constructor call -- raising here, inside the still-open
        # `with self._sessions.begin() as session:` block, means those in-session mutations exist
        # but were never flushed/committed, and the block's own exception handling rolls the
        # whole transaction back. This proves a failure between "prices frozen in memory" and
        # "LabSelection inserted + order transitioned" cannot leave a partial/mixed-price state
        # on disk -- exactly the failure class this test exists to rule out.
        raise RuntimeError("simulated failure during selection commit")

    monkeypatch.setattr(selection_module, "LabSelection", _raising_lab_selection)

    with pytest.raises(RuntimeError, match="simulated failure during selection commit"):
        client.post(
            "/exchange/v1/patient/order/selection",
            headers=_auth(access_token),
            json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
        )

    monkeypatch.setattr(selection_module, "LabSelection", real_lab_selection)

    with sessions() as session:
        selection_count = session.scalar(select(func.count()).select_from(LabSelection))
        order = session.get(LabOrder, order_id)
        session.refresh(order)
        items = order.items
    assert selection_count == 0
    assert order.status == "AWAITING_LAB_SELECTION"
    assert all(item.selected_price_amount is None for item in items)
    assert all(item.selected_price_currency is None for item in items)

    # The order must still be cleanly selectable afterward -- the failure didn't corrupt state.
    retry = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert retry.status_code == 201


# -- DH-EX-005R Part N: cache-control hardening ----------------------------------------------------


def test_patient_sensitive_responses_are_not_cacheable(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, token, order_id, entry_ids = _create_order_awaiting_selection(
        client,
        private_key,
        organizations,
        catalog,
        patients,
        clinician_name="CacheControl",
        test_count=1,
    )
    lab = _setup_lab(
        catalog,
        directory,
        organizations,
        name="CacheControl Lab",
        tests=[(entry_ids[0], Decimal("1000"))],
    )

    issue_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )
    assert issue_response.status_code == 201
    assert issue_response.headers.get("cache-control") == "no-store"
    access_token = issue_response.json()["access_token"]

    order_response = client.get("/exchange/v1/patient/order", headers=_auth(access_token))
    assert order_response.headers.get("cache-control") == "no-store"

    options_response = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    )
    assert options_response.headers.get("cache-control") == "no-store"
    price_id = options_response.json()["options"][0]["prices"][0]["current_price_id"]

    selection_response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [price_id]},
    )
    assert selection_response.status_code == 201
    assert selection_response.headers.get("cache-control") == "no-store"


# -- DH-EX-005R Part I: bearer input bounds --------------------------------------------------------


def test_overlong_bearer_token_is_refused_generically(client: TestClient) -> None:
    huge_token = "a" * 100_000
    response = client.get(
        "/exchange/v1/patient/order", headers={"Authorization": f"Bearer {huge_token}"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid or missing credentials"}


def test_bearer_token_just_under_the_bound_gets_the_same_generic_401(client: TestClient) -> None:
    # Not the real token shape, but under the length bound -- must still fail with the identical
    # generic 401 as every other unknown-token case, never a distinct "too long" vs "not found"
    # response that would leak the existence of a length bound to an unauthenticated caller.
    near_bound_token = "a" * 500
    response = client.get(
        "/exchange/v1/patient/order", headers={"Authorization": f"Bearer {near_bound_token}"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid or missing credentials"}
