"""DH-EX-006: selected-lab assigned-order workflow -- LAB_TECH sees its assigned order, accepts
or rejects it, and may start processing an accepted order. Stops at IN_PROGRESS; no results, no
COMPLETED, no message transport, no UI.

Same harness pattern as ``test_exchange_patient_access.py``: a bare ``FastAPI()`` app with
``build_exchange_router`` mounted directly, a ``StaticKeySource``-backed ``TokenValidator`` (local
RSA keypair, zero network).
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
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import (
    DoctorReview,
    ExchangeSecurityAuditEvent,
    LabOrder,
    LabResult,
    Organization,
    OrganizationMembership,
    PatientAccessGrant,
    ResultDelivery,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository

TTL_HOURS = 168


@pytest.fixture
def keypair():
    return generate_keypair()


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


def _grant_roles(client, private_key, org_id, identity_id, subject: str, roles: list[str]) -> None:
    platform_admin_token = make_token(
        private_key, subject=f"setup-admin-{subject}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(platform_admin_token),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text


def _setup_clinician(client, private_key, organizations, name: str):
    provider = make_provider(organizations, name=name)
    identity_id = _bootstrap_identity(client, private_key, f"clinician-{name}")
    _grant_roles(client, private_key, provider.id, identity_id, f"clinician-{name}", ["CLINICIAN"])
    token = make_token(private_key, subject=f"clinician-{name}")
    return provider, token


def _setup_lab_tech(client, private_key, organizations, name: str, *, roles=None):
    lab = make_lab(organizations, name=f"Lab-{name}")
    identity_id = _bootstrap_identity(client, private_key, f"labtech-{name}")
    _grant_roles(client, private_key, lab.id, identity_id, f"labtech-{name}", roles or ["LAB_TECH"])
    token = make_token(private_key, subject=f"labtech-{name}")
    return lab, token, identity_id


def _create_patient(client, private_key, patients, provider_id, name: str) -> uuid.UUID:
    record = patients.get_or_create(
        source_organization_id=provider_id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-{name}",
        display_name=f"Synthetic Patient {name}",
    )
    return record.id


def _new_key() -> str:
    return str(uuid.uuid4())


def _price_lab(
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


def _create_selected_order(
    client,
    private_key,
    organizations,
    catalog,
    directory,
    patients,
    *,
    name: str,
    test_count: int = 1,
):
    """Full flow up through a committed patient selection -- returns
    (provider, lab, order_id, entry_ids, price_ids)."""
    provider, clinician_token = _setup_clinician(client, private_key, organizations, name)
    patient_id = _create_patient(client, private_key, patients, provider.id, name)
    entries = []
    for i in range(test_count):
        entry, _ = catalog.create_entry(code=f"DHEALTH-LW-{name}-{i}", display_name=f"LW Test {i}")
        entries.append(entry)
    lab = _price_lab(
        catalog,
        directory,
        organizations,
        name=f"LWLab-{name}",
        tests=[(e.id, Decimal("1000") * (i + 1)) for i, e in enumerate(entries)],
    )
    create_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(e.id) for e in entries],
        },
    )
    assert create_response.status_code == 201, create_response.text
    order_id = uuid.UUID(create_response.json()["id"])

    access_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    assert access_response.status_code == 201, access_response.text
    access_token = access_response.json()["access_token"]

    options_response = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    )
    assert options_response.status_code == 200, options_response.text
    options = options_response.json()["options"]
    matching = [opt for opt in options if opt["lab"]["id"] == str(lab.id)]
    assert matching, f"expected lab {lab.id} among options {options}"
    price_ids = [line["current_price_id"] for line in matching[0]["prices"]]

    selection_response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert selection_response.status_code == 201, selection_response.text

    return (
        provider,
        lab,
        order_id,
        [e.id for e in entries],
        price_ids,
        access_token,
        clinician_token,
    )


def _audit_events(sessions, event_type: str) -> list[dict]:
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


# -- golden lab workflow test ------------------------------------------------------------------


def test_golden_lab_workflow_journey(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client,
            private_key,
            organizations,
            catalog,
            directory,
            patients,
            name="Golden",
            test_count=2,
        )
    )

    identity_id = _bootstrap_identity(client, private_key, "labtech-golden-real")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-golden-real", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-golden-real")

    list_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders", headers=_auth(lab_tech_token)
    )
    assert list_response.status_code == 200, list_response.text
    assert any(o["order_id"] == str(order_id) for o in list_response.json())

    detail_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["status"] == "SELECTED"
    assert len(detail["requested_tests"]) == 2
    assert detail["patient_display_name"] is not None
    assert detail["ordering_provider_name"] == provider.name

    accept_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_response.status_code == 200, accept_response.text
    assert accept_response.json()["status"] == "ACCEPTED"
    assert accept_response.json()["collection_recorded_at"] is None

    # DH-EX-011: a new ACCEPTED -> IN_PROGRESS transition requires an explicit collection
    # milestone -- start-processing before recording it must fail, then succeed after.
    premature_start = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert premature_start.status_code == 409, premature_start.text

    collection_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert collection_response.status_code == 201, collection_response.text
    recorded_at = collection_response.json()["recorded_at"]

    # `.rstrip("Z")` tolerates a SQLite-only naive-vs-aware datetime serialization quirk (a fresh
    # read after commit can come back tz-naive on SQLite -- see `_aware()`'s docstring in
    # `patient_access.py`/`selection.py` for the identical, already-documented class of gap; never
    # observed against real PostgreSQL) -- both values are the same instant either way.
    detail_after_collection = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    )
    assert detail_after_collection.json()["collection_recorded_at"].rstrip(
        "Z"
    ) == recorded_at.rstrip("Z")

    start_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert start_response.status_code == 200, start_response.text
    assert start_response.json()["status"] == "IN_PROGRESS"
    assert start_response.json()["collection_recorded_at"].rstrip("Z") == recorded_at.rstrip("Z")

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"
        review = session.scalar(select(DoctorReview).where(DoctorReview.lab_order_id == order_id))
        assert review.status == "PENDING"
        delivery = session.scalar(
            select(ResultDelivery).where(ResultDelivery.lab_order_id == order_id)
        )
        assert delivery.status == "PENDING"
        result_count = session.scalar(select(func.count()).select_from(LabResult))
        assert result_count == 0


# -- golden rejection flow --------------------------------------------------------------------


def test_golden_rejection_flow(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="Reject"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-reject")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-reject", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-reject")

    reject_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/reject",
        headers=_auth(lab_tech_token),
    )
    assert reject_response.status_code == 200, reject_response.text
    assert reject_response.json()["status"] == "REJECTED"

    replay_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/reject",
        headers=_auth(lab_tech_token),
    )
    assert replay_response.status_code == 200
    assert replay_response.json()["status"] == "REJECTED"

    events = _audit_events(sessions, "lab_order_rejected")
    assert len(events) == 1

    with sessions() as session:
        from daher_adapter.exchange.models import LabSelection

        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        assert selection is not None
        assert selection.lab_organization_id == lab.id
        order = session.get(LabOrder, order_id)
        assert all(item.selected_price_amount is not None for item in order.items)
        result_count = session.scalar(select(func.count()).select_from(LabResult))
        assert result_count == 0


# -- post-selection marketplace-change boundary --------------------------------------------------


def test_marketplace_changes_after_selection_do_not_invalidate_assignment(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="MktChange"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-mktchange")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-mktchange", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-mktchange")

    # Make the lab unlisted, not accepting orders, and deactivate its offering + change price.
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="addr",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=False,
    )
    from daher_adapter.exchange.models import LabOffering, LabPrice

    with sessions() as session:
        offering_id = session.scalar(
            select(LabOffering.id).where(
                LabOffering.lab_organization_id == lab.id,
                LabOffering.catalog_entry_id == entry_ids[0],
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
    catalog.set_offering_active(offering_id, False)

    detail_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    )
    assert detail_response.status_code == 200
    assert Decimal(str(detail_response.json()["requested_tests"][0]["selected_price_amount"])) == (
        Decimal("1000")
    )

    accept_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_response.status_code == 200

    collection_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert collection_response.status_code == 201, collection_response.text

    start_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert start_response.status_code == 200
    assert start_response.json()["status"] == "IN_PROGRESS"


# -- lab organization / membership deactivation ------------------------------------------------


def test_inactive_lab_organization_blocks_ordinary_workflow(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="InactiveOrg"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-inactiveorg")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-inactiveorg", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-inactiveorg")

    with sessions() as session:
        org = session.get(Organization, lab.id)
        org.status = "INACTIVE"
        session.commit()

    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    )
    assert response.status_code == 403

    accept_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_response.status_code == 403

    with sessions() as session:
        from daher_adapter.exchange.models import LabSelection

        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        assert selection is not None, "LabSelection must not be erased by org deactivation"


def test_inactive_membership_blocks_ordinary_workflow(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="InactiveMember"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-inactivemember")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-inactivemember", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-inactivemember")

    with sessions() as session:
        membership = session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == lab.id,
                OrganizationMembership.identity_id == uuid.UUID(identity_id),
            )
        )
        membership.status = "INACTIVE"
        session.commit()

    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    )
    assert response.status_code == 404


# -- cross-lab / cross-tenant isolation ----------------------------------------------------------


def test_other_lab_cannot_list_or_read_assigned_order(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, lab_a, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="CrossListA"
        )
    )
    lab_b, lab_b_token, _ = _setup_lab_tech(client, private_key, organizations, "CrossListB")

    list_response = client.get(
        f"/exchange/v1/organizations/{lab_b.id}/lab-orders", headers=_auth(lab_b_token)
    )
    assert list_response.status_code == 200
    assert all(o["order_id"] != str(order_id) for o in list_response.json())

    detail_response = client.get(
        f"/exchange/v1/organizations/{lab_b.id}/lab-orders/{order_id}", headers=_auth(lab_b_token)
    )
    assert detail_response.status_code == 404

    fake_order_response = client.get(
        f"/exchange/v1/organizations/{lab_b.id}/lab-orders/{uuid.uuid4()}",
        headers=_auth(lab_b_token),
    )
    assert fake_order_response.status_code == 404
    assert fake_order_response.json() == detail_response.json()


def test_other_lab_cannot_mutate_assigned_order(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, lab_a, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="CrossMutA"
        )
    )
    lab_b, lab_b_token, _ = _setup_lab_tech(client, private_key, organizations, "CrossMutB")

    for action in ("accept", "reject", "start-processing"):
        response = client.post(
            f"/exchange/v1/organizations/{lab_b.id}/lab-orders/{order_id}/{action}",
            headers=_auth(lab_b_token),
        )
        assert response.status_code == 404, action

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "SELECTED"
    assert _audit_events(sessions, "lab_order_accepted") == []
    assert _audit_events(sessions, "lab_order_rejected") == []
    assert _audit_events(sessions, "lab_order_processing_started") == []


def test_other_lab_probing_an_already_mutated_order_still_gets_404_not_replay(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """DH-EX-006R Part Y/AB/AD: the assignment check must happen BEFORE the idempotent-replay
    check, never after -- otherwise a wrong-lab caller probing an order Lab A already transitioned
    could receive a false "200 replay" (or a same-state response) instead of a 404, silently
    confirming the order's current workflow state to a lab that was never assigned it. Lab A
    genuinely accepts; Lab B then probes accept/reject/start-processing on that now-ACCEPTED
    order and must get 404 for every one, never 200/409-as-if-legitimately-scoped."""
    private_key, _ = keypair
    provider, lab_a, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="CrossMutAfter"
        )
    )
    lab_a_identity = _bootstrap_identity(client, private_key, "labtech-crossmutafter-a")
    _grant_roles(
        client, private_key, lab_a.id, lab_a_identity, "labtech-crossmutafter-a", ["LAB_TECH"]
    )
    lab_a_token = make_token(private_key, subject="labtech-crossmutafter-a")
    accept_response = client.post(
        f"/exchange/v1/organizations/{lab_a.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_a_token),
    )
    assert accept_response.status_code == 200
    assert accept_response.json()["status"] == "ACCEPTED"

    lab_b, lab_b_token, _ = _setup_lab_tech(client, private_key, organizations, "CrossMutAfterB")

    detail_response = client.get(
        f"/exchange/v1/organizations/{lab_b.id}/lab-orders/{order_id}", headers=_auth(lab_b_token)
    )
    assert detail_response.status_code == 404

    for action in ("accept", "reject", "start-processing"):
        response = client.post(
            f"/exchange/v1/organizations/{lab_b.id}/lab-orders/{order_id}/{action}",
            headers=_auth(lab_b_token),
        )
        assert response.status_code == 404, action

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "ACCEPTED"
    assert len(_audit_events(sessions, "lab_order_accepted")) == 1
    assert _audit_events(sessions, "lab_order_rejected") == []
    assert _audit_events(sessions, "lab_order_processing_started") == []


# -- platform admin / org admin / provider clinician authorization matrix -----------------------


def test_platform_admin_alone_cannot_read_or_mutate_lab_orders(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="PlatAdmin"
        )
    )
    admin_token = make_token(private_key, subject="platform-admin-lab", roles=[PLATFORM_ADMIN_ROLE])

    real_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(admin_token)
    )
    fake_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{uuid.uuid4()}", headers=_auth(admin_token)
    )
    assert real_response.status_code == 403
    assert fake_response.status_code == 403
    assert real_response.json() == fake_response.json()

    accept_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(admin_token),
    )
    assert accept_response.status_code == 403


def test_lab_org_admin_alone_is_refused_lab_tech_and_lab_tech_org_admin_is_allowed(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="OrgAdminOnly"
        )
    )
    org_admin_id = _bootstrap_identity(client, private_key, "lab-org-admin-only")
    _grant_roles(client, private_key, lab.id, org_admin_id, "lab-org-admin-only", ["ORG_ADMIN"])
    org_admin_token = make_token(private_key, subject="lab-org-admin-only")

    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(org_admin_token)
    )
    assert response.status_code == 403

    both_id = _bootstrap_identity(client, private_key, "lab-org-admin-and-tech")
    _grant_roles(
        client, private_key, lab.id, both_id, "lab-org-admin-and-tech", ["ORG_ADMIN", "LAB_TECH"]
    )
    both_token = make_token(private_key, subject="lab-org-admin-and-tech")
    both_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(both_token)
    )
    assert both_response.status_code == 200


def test_provider_clinician_cannot_act_as_lab(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="ClinicianAsLab"
        )
    )
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(clinician_token)
    )
    assert response.status_code == 404


def test_multi_lab_identity_no_assignment_crossover(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider_a, lab_a, order_a, _entries_a, _prices_a, _access_a, _clin_a = _create_selected_order(
        client, private_key, organizations, catalog, directory, patients, name="MultiA"
    )
    provider_b, lab_b, order_b, _entries_b, _prices_b, _access_b, _clin_b = _create_selected_order(
        client, private_key, organizations, catalog, directory, patients, name="MultiB"
    )

    shared_identity_id = _bootstrap_identity(client, private_key, "multi-lab-tech")
    _grant_roles(
        client, private_key, lab_a.id, shared_identity_id, "multi-lab-tech-a", ["LAB_TECH"]
    )
    _grant_roles(
        client, private_key, lab_b.id, shared_identity_id, "multi-lab-tech-b", ["LAB_TECH"]
    )
    shared_token = make_token(private_key, subject="multi-lab-tech")

    a_via_a = client.get(
        f"/exchange/v1/organizations/{lab_a.id}/lab-orders/{order_a}", headers=_auth(shared_token)
    )
    assert a_via_a.status_code == 200
    b_via_b = client.get(
        f"/exchange/v1/organizations/{lab_b.id}/lab-orders/{order_b}", headers=_auth(shared_token)
    )
    assert b_via_b.status_code == 200
    a_via_b = client.get(
        f"/exchange/v1/organizations/{lab_b.id}/lab-orders/{order_a}", headers=_auth(shared_token)
    )
    assert a_via_b.status_code == 404
    b_via_a = client.get(
        f"/exchange/v1/organizations/{lab_a.id}/lab-orders/{order_b}", headers=_auth(shared_token)
    )
    assert b_via_a.status_code == 404


# -- status transition contract ------------------------------------------------------------------


def test_direct_selected_to_in_progress_is_refused(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="DirectJump"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-directjump")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-directjump", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-directjump")

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert response.status_code == 409


def test_incompatible_transitions_return_409(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="Incompatible"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-incompatible")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-incompatible", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-incompatible")

    accept = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept.status_code == 200

    reject_after_accept = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/reject",
        headers=_auth(lab_tech_token),
    )
    assert reject_after_accept.status_code == 409

    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    start = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert start.status_code == 200

    accept_after_start = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_after_start.status_code == 409


def test_rejection_is_terminal_accept_and_start_both_refused_after(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """DH-EX-006R Part AE: REJECTED is terminal in the existing, unmodified status machine --
    neither accept nor start-processing may follow it, and no rollback/reselection is invented."""
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="RejectTerminal"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-rejectterminal")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-rejectterminal", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-rejectterminal")

    reject = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/reject",
        headers=_auth(lab_tech_token),
    )
    assert reject.status_code == 200
    assert reject.json()["status"] == "REJECTED"

    accept_after_reject = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_after_reject.status_code == 409

    start_after_reject = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert start_after_reject.status_code == 409

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "REJECTED"
    assert len(_audit_events(sessions, "lab_order_rejected")) == 1
    assert _audit_events(sessions, "lab_order_accepted") == []
    assert _audit_events(sessions, "lab_order_processing_started") == []


def test_accept_replay_is_idempotent_no_duplicate_audit(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="AcceptReplay"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-acceptreplay")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-acceptreplay", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-acceptreplay")

    first = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    second = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "ACCEPTED"
    assert len(_audit_events(sessions, "lab_order_accepted")) == 1


def test_start_replay_is_idempotent_no_duplicate_audit(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="StartReplay"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-startreplay")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-startreplay", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-startreplay")

    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    first = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    second = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(_audit_events(sessions, "lab_order_processing_started")) == 1


# -- frozen price / patient-grant independence ----------------------------------------------------


def test_frozen_price_unchanged_by_lab_workflow(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="FrozenPrice"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-frozenprice")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-frozenprice", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-frozenprice")

    from daher_adapter.exchange.models import LabOffering, LabPrice

    with sessions() as session:
        offering_id = session.scalar(
            select(LabOffering.id).where(
                LabOffering.lab_organization_id == lab.id,
                LabOffering.catalog_entry_id == entry_ids[0],
            )
        )
        current_price_id = session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering_id, LabPrice.superseded_at.is_(None)
            )
        )
    catalog.set_price(
        offering_id=offering_id,
        amount=Decimal("5000"),
        currency="DJF",
        expected_current_price_id=current_price_id,
    )

    accept_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_response.status_code == 200
    collection_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert collection_response.status_code == 201
    start_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert start_response.status_code == 200

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.items[0].selected_price_amount == Decimal("1000.00")

    detail = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    ).json()
    assert Decimal(str(detail["requested_tests"][0]["selected_price_amount"])) == Decimal("1000")


def test_patient_grant_revocation_does_not_remove_lab_assignment(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="GrantRevoked"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-grantrevoked")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-grantrevoked", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-grantrevoked")

    revoke_response = client.delete(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    assert revoke_response.status_code == 204

    detail_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    )
    assert detail_response.status_code == 200

    accept_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_response.status_code == 200


def test_patient_grant_expiry_does_not_remove_lab_assignment(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    import datetime as dt

    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="GrantExpired"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-grantexpired")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-grantexpired", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-grantexpired")

    with sessions() as session:
        grant = session.scalar(
            select(PatientAccessGrant).where(PatientAccessGrant.lab_order_id == order_id)
        )
        grant.issued_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=200)
        grant.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
        session.commit()

    assert client.get("/exchange/v1/patient/order", headers=_auth(access_token)).status_code == 401

    accept_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_response.status_code == 200


# -- audit atomicity --------------------------------------------------------------------------


def test_accept_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="AcceptAuditFail"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-acceptauditfail")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-acceptauditfail", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-acceptauditfail")

    with sessions() as session:
        before_updated_at = session.get(LabOrder, order_id).updated_at

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
            headers=_auth(lab_tech_token),
        )

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "SELECTED"
        # DH-EX-006R Part AR: updated_at must roll back together with status -- both are part
        # of the same UPDATE statement within the same rolled-back transaction, never a value
        # that falsely advances despite the mutation it was supposed to accompany failing.
        assert order.updated_at == before_updated_at
    assert _audit_events(sessions, "lab_order_accepted") == []


def test_reject_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="RejectAuditFail"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-rejectauditfail")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-rejectauditfail", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-rejectauditfail")

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/reject",
            headers=_auth(lab_tech_token),
        )

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "SELECTED"
    assert _audit_events(sessions, "lab_order_rejected") == []


def test_audit_actor_reflects_whichever_identity_actually_caused_the_transition(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """DH-EX-006R Part BB/BC: two different, independently-authorized LAB_TECH identities in the
    same lab may both act on the same order -- authorization is organization+role based, never
    tied to whichever identity saw the order first. This is fundamentally a sequential-
    correctness fact, not a concurrency one: it is proven deterministically here by having
    identity_1 make the real transition and identity_2 immediately replay it, and asserting the
    persisted audit actor is identity_1 (whoever actually caused the mutation), never identity_2
    (whoever merely observed/replayed it) or a stale/mismatched value. (A genuine two-thread race
    was tried and abandoned here -- it reproduces exactly the HTTP/TestClient-stack SQLite
    fragility DH-EX-005R already diagnosed and worked around by moving to repository-direct
    concurrency tests; that lesson applies here too, and the property below does not actually
    require a race to prove.)"""
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="AuditWinner"
        )
    )

    identity_1 = _bootstrap_identity(client, private_key, "labtech-auditwinner-1")
    _grant_roles(client, private_key, lab.id, identity_1, "labtech-auditwinner-1", ["LAB_TECH"])
    token_1 = make_token(private_key, subject="labtech-auditwinner-1")

    identity_2 = _bootstrap_identity(client, private_key, "labtech-auditwinner-2")
    _grant_roles(client, private_key, lab.id, identity_2, "labtech-auditwinner-2", ["LAB_TECH"])
    token_2 = make_token(private_key, subject="labtech-auditwinner-2")

    first = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(token_1),
    )
    assert first.status_code == 200
    assert first.json()["status"] == "ACCEPTED"

    # identity_2 is a fully authorized Lab A LAB_TECH too -- a safe replay, not a refusal.
    second = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(token_2),
    )
    assert second.status_code == 200
    assert second.json()["status"] == "ACCEPTED"

    events = _audit_events(sessions, "lab_order_accepted")
    assert len(events) == 1, "exactly one real transition, exactly one audit row"

    with sessions() as session:
        row = session.scalar(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "lab_order_accepted"
            )
        )
        actor_id = str(row.actor_identity_id)
    assert actor_id == identity_1, "audit actor must be identity_1 (who caused it), not identity_2"


def test_start_processing_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="StartAuditFail"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-startauditfail")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-startauditfail", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-startauditfail")

    accept = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept.status_code == 200

    collection = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert collection.status_code == 201, collection.text

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
            headers=_auth(lab_tech_token),
        )

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "ACCEPTED"
    assert _audit_events(sessions, "lab_order_processing_started") == []


# -- accept/reject audit correctness ------------------------------------------------------------


def test_accept_and_reject_audit_fields(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="AuditFields"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-auditfields")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-auditfields", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-auditfields")

    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )

    events = _audit_events(sessions, "lab_order_accepted")
    assert len(events) == 1
    assert events[0]["outcome"] == "updated"
    assert events[0]["resource_type"] == "lab_order"
    assert events[0]["resource_id"] == str(order_id)
    assert events[0]["organization_id"] == str(lab.id)

    with sessions() as session:
        rows = session.scalars(select(ExchangeSecurityAuditEvent)).all()
    for row in rows:
        for value in (row.event_type, row.outcome, row.resource_type):
            if value:
                assert "SYNTH-" not in value


# -- listing behavior ---------------------------------------------------------------------------


def test_list_orders_bounded_and_ordering(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    lab_id = None
    order_ids = []
    for i in range(3):
        provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
            _create_selected_order(
                client,
                private_key,
                organizations,
                catalog,
                directory,
                patients,
                name=f"ListOrder{i}",
            )
        )
        if lab_id is None:
            lab_id = lab.id
        order_ids.append(order_id)

    identity_id = _bootstrap_identity(client, private_key, "labtech-listorder")
    # Each call above creates its own lab -- only order_ids[0] belongs to lab_id.
    _grant_roles(client, private_key, lab_id, identity_id, "labtech-listorder", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-listorder")

    response = client.get(
        f"/exchange/v1/organizations/{lab_id}/lab-orders?limit=1", headers=_auth(lab_tech_token)
    )
    assert response.status_code == 200
    assert len(response.json()) <= 1

    bad_limit = client.get(
        f"/exchange/v1/organizations/{lab_id}/lab-orders?limit=0", headers=_auth(lab_tech_token)
    )
    assert bad_limit.status_code == 422

    too_high_limit = client.get(
        f"/exchange/v1/organizations/{lab_id}/lab-orders?limit=101", headers=_auth(lab_tech_token)
    )
    assert too_high_limit.status_code == 422


def test_list_ordering_reflects_most_recent_activity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """DH-EX-006R Part Q: proves the `updated_at DESC` claim directly, not just limit/count.
    Order A is created and selected first (older); order B is created and selected second
    (newer) -- both assigned to the same lab. Without any mutation, B (newer `updated_at`) would
    sort before A. After the lab explicitly accepts A, A's `updated_at` advances past B's, and A
    must now sort first -- proving `LabOrder.status` transitions actually advance the column
    SQLAlchemy's `onupdate=utc_now` is relied on for, and that the list query reflects it."""
    private_key, _ = keypair
    provider, lab, order_a, entries_a, prices_a, access_a, clinician_a = _create_selected_order(
        client, private_key, organizations, catalog, directory, patients, name="OrderingA"
    )

    # A second, independent order selected to the SAME lab -- reuse the lab's own directory/
    # catalog setup by adding a fresh priced offering for a new catalog entry at that lab.
    provider_b, clinician_token_b = _setup_clinician(
        client, private_key, organizations, "OrderingB"
    )
    patient_b = _create_patient(client, private_key, patients, provider_b.id, "OrderingB")
    entry_b, _ = catalog.create_entry(code="DHEALTH-LW-OrderingB-0", display_name="LW Test B")
    offering_b, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_b.id)
    catalog.set_price(
        offering_id=offering_b.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    create_response = client.post(
        f"/exchange/v1/organizations/{provider_b.id}/orders",
        headers={**_auth(clinician_token_b), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_b), "catalog_entry_ids": [str(entry_b.id)]},
    )
    assert create_response.status_code == 201, create_response.text
    order_b = uuid.UUID(create_response.json()["id"])
    access_response_b = client.post(
        f"/exchange/v1/organizations/{provider_b.id}/orders/{order_b}/patient-access",
        headers=_auth(clinician_token_b),
    )
    access_token_b = access_response_b.json()["access_token"]
    options_b = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token_b)
    ).json()
    price_ids_b = [line["current_price_id"] for line in options_b["options"][0]["prices"]]
    selection_response_b = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token_b),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids_b},
    )
    assert selection_response_b.status_code == 201, selection_response_b.text

    identity_id = _bootstrap_identity(client, private_key, "labtech-ordering")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-ordering", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-ordering")

    # Before any mutation: B (created later) sorts first.
    before = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders", headers=_auth(lab_tech_token)
    ).json()
    before_ids = [o["order_id"] for o in before]
    assert before_ids.index(str(order_b)) < before_ids.index(str(order_a))

    # Lab explicitly accepts A -- its updated_at advances past B's.
    accept_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_a}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_response.status_code == 200

    after = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders", headers=_auth(lab_tech_token)
    ).json()
    after_ids = [o["order_id"] for o in after]
    assert after_ids.index(str(order_a)) < after_ids.index(str(order_b)), (
        "the just-mutated older order must now sort before the untouched newer order"
    )

    with sessions() as session:
        row_a = session.get(LabOrder, order_a)
        row_b = session.get(LabOrder, order_b)
        assert row_a.updated_at > row_b.updated_at


def test_list_orders_status_filter(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, entry_ids, price_ids, access_token, clinician_token = (
        _create_selected_order(
            client, private_key, organizations, catalog, directory, patients, name="StatusFilter"
        )
    )
    identity_id = _bootstrap_identity(client, private_key, "labtech-statusfilter")
    _grant_roles(client, private_key, lab.id, identity_id, "labtech-statusfilter", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-statusfilter")

    selected_filter = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders?status=SELECTED",
        headers=_auth(lab_tech_token),
    )
    assert selected_filter.status_code == 200
    assert any(o["order_id"] == str(order_id) for o in selected_filter.json())

    accepted_filter = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders?status=ACCEPTED",
        headers=_auth(lab_tech_token),
    )
    assert accepted_filter.status_code == 200
    assert all(o["order_id"] != str(order_id) for o in accepted_filter.json())

    invalid_filter = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders?status=NOT_A_REAL_STATUS",
        headers=_auth(lab_tech_token),
    )
    assert invalid_filter.status_code == 422


# -- concurrency (SQLite proof; real-Postgres proof runs separately, not committed) ----------------


def _build_repo_harness(tmp_path: Path, *, db_name: str):
    """Repository-direct harness for real-thread concurrency proofs -- matches
    ``test_exchange_catalog_dh_ex_003.py``'s and ``test_exchange_patient_access.py``'s own
    established pattern exactly (dedicated engine, ``BEGIN IMMEDIATE``, 30s busy-timeout, direct
    repository calls, no HTTP/TestClient layer -- that stack's own thread pool compounds badly
    under genuine multi-thread write contention, observed directly in the DH-EX-005 session)."""
    from sqlalchemy import create_engine, event

    from daher_adapter.exchange.identity import PractitionerReferenceRepository
    from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
    from daher_adapter.exchange.models import Base
    from daher_adapter.exchange.orders import LabOrderRepository
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
    local_practitioners = PractitionerReferenceRepository(local_sessions)
    local_orders = LabOrderRepository(local_sessions, local_organizations, local_catalog)
    local_selections = LabSelectionRepository(local_sessions, local_organizations)
    local_workflow = LabWorkflowRepository(local_sessions, local_organizations)
    return {
        "sessions": local_sessions,
        "organizations": local_organizations,
        "catalog": local_catalog,
        "directory": local_directory,
        "patients": local_patients,
        "practitioners": local_practitioners,
        "orders": local_orders,
        "selections": local_selections,
        "workflow": local_workflow,
    }


def _seed_selected_order(h, *, name: str):
    from daher_adapter.exchange.orders import OrderItemInput

    provider = make_provider(h["organizations"], name=f"CProvider-{name}")
    lab = make_lab(h["organizations"], name=f"CLab-{name}")
    entry, _ = h["catalog"].create_entry(code=f"DHEALTH-C-{name}", display_name="Concurrency Test")
    offering, _ = h["catalog"].create_offering(
        lab_organization_id=lab.id, catalog_entry_id=entry.id
    )
    h["catalog"].set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    h["directory"].upsert(
        lab_organization_id=lab.id,
        public_address="addr",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    patient = h["patients"].get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-C-{name}",
        display_name="Synthetic Patient",
    )
    practitioner = h["practitioners"].get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-practitioner-id",
        source_identity_value=f"SYNTH-C-DOC-{name}",
        display_name="Dr Synthetic",
    )
    order = (
        h["orders"]
        .create(
            idempotency_key=f"concurrency-{name}-1",
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            items=[OrderItemInput(catalog_entry_id=entry.id)],
        )
        .order
    )
    with h["sessions"]() as session:
        from daher_adapter.exchange.models import LabPrice

        price_id = session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering.id, LabPrice.superseded_at.is_(None)
            )
        )
    h["selections"].select(
        lab_order_id=order.id, lab_organization_id=lab.id, expected_price_ids=frozenset({price_id})
    )
    return lab, order.id


def _run_accept_vs_reject_once(tmp_path: Path, i: int) -> None:
    import threading

    from daher_adapter.exchange.lab_workflow import InvalidLabWorkflowTransition

    h = _build_repo_harness(tmp_path, db_name=f"accept-vs-reject-{i}")
    lab, order_id = _seed_selected_order(h, name=f"avr{i}")

    outcomes = []
    errors = []
    lock = threading.Lock()
    thread_count = 25
    barrier = threading.Barrier(thread_count)

    def attempt(action):
        try:
            barrier.wait(timeout=10)
            fn = h["workflow"].accept if action == "accept" else h["workflow"].reject
            _record, transitioned = fn(order_id=order_id, lab_organization_id=lab.id)
            with lock:
                outcomes.append((action, transitioned))
        except InvalidLabWorkflowTransition:
            with lock:
                outcomes.append((action, "conflict"))
        except Exception as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [
        threading.Thread(target=attempt, args=("accept" if j % 2 == 0 else "reject",))
        for j in range(thread_count)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(outcomes) == thread_count
    real_transitions = [o for o in outcomes if o[1] is True]
    assert len(real_transitions) == 1, real_transitions

    with h["sessions"]() as session:
        order = session.get(LabOrder, order_id)
        final_status = order.status
    assert final_status in ("ACCEPTED", "REJECTED")
    assert real_transitions[0][0] == ("accept" if final_status == "ACCEPTED" else "reject")


def test_accept_vs_reject_race(tmp_path: Path) -> None:
    for i in range(5):
        _run_accept_vs_reject_once(tmp_path, i)


def test_25way_same_action_accept_concurrency(tmp_path: Path) -> None:
    import threading

    h = _build_repo_harness(tmp_path, db_name="same-accept")
    lab, order_id = _seed_selected_order(h, name="sameaccept")

    outcomes = []
    errors = []
    lock = threading.Lock()
    thread_count = 25
    barrier = threading.Barrier(thread_count)

    def attempt():
        try:
            barrier.wait(timeout=10)
            _record, transitioned = h["workflow"].accept(
                order_id=order_id, lab_organization_id=lab.id
            )
            with lock:
                outcomes.append(transitioned)
        except Exception as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(outcomes) == thread_count
    assert outcomes.count(True) == 1
    with h["sessions"]() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "ACCEPTED"


def test_25way_same_action_reject_concurrency(tmp_path: Path) -> None:
    import threading

    h = _build_repo_harness(tmp_path, db_name="same-reject")
    lab, order_id = _seed_selected_order(h, name="samereject")

    outcomes = []
    errors = []
    lock = threading.Lock()
    thread_count = 25
    barrier = threading.Barrier(thread_count)

    def attempt():
        try:
            barrier.wait(timeout=10)
            _record, transitioned = h["workflow"].reject(
                order_id=order_id, lab_organization_id=lab.id
            )
            with lock:
                outcomes.append(transitioned)
        except Exception as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(outcomes) == thread_count
    assert outcomes.count(True) == 1
    with h["sessions"]() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "REJECTED"


def test_25way_same_action_start_processing_concurrency(tmp_path: Path) -> None:
    import threading

    h = _build_repo_harness(tmp_path, db_name="same-start")
    lab, order_id = _seed_selected_order(h, name="samestart")
    h["workflow"].accept(order_id=order_id, lab_organization_id=lab.id)
    h["workflow"].record_collection(order_id=order_id, lab_organization_id=lab.id)

    outcomes = []
    errors = []
    lock = threading.Lock()
    thread_count = 25
    barrier = threading.Barrier(thread_count)

    def attempt():
        try:
            barrier.wait(timeout=10)
            _record, transitioned = h["workflow"].start_processing(
                order_id=order_id, lab_organization_id=lab.id
            )
            with lock:
                outcomes.append(transitioned)
        except Exception as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(outcomes) == thread_count
    assert outcomes.count(True) == 1
    with h["sessions"]() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"


def _run_accept_vs_collection_once(tmp_path: Path, i: int) -> None:
    """DH-EX-011's own required "ACCEPT / COLLECTION RACE": racing `accept` against
    `record_collection` from SELECTED. This test **replaces**
    `test_accept_vs_start_processing_race`'s original accept-vs-start-processing race, which
    DH-EX-011 makes structurally impossible to preserve in its original form: `start_processing`
    now always requires a recorded collection milestone, which itself always requires ACCEPTED --
    a single racing `start_processing` call can never satisfy both preconditions by winning a race
    against `accept` alone, so every run would now deterministically observe "conflict" regardless
    of thread scheduling, silently destroying the test's own reason to exist as a race proof. The
    genuinely meaningful race at this same SELECTED/ACCEPTED boundary post-DH-EX-011 is accept vs.
    collection instead -- exactly the race this ticket's own "ACCEPT / COLLECTION RACE" section
    names explicitly. See `test_collection_vs_start_processing_race` below for the new, separate,
    genuinely meaningful race this ticket introduces at the ACCEPTED/IN_PROGRESS boundary."""
    import threading

    from daher_adapter.exchange.lab_workflow import InvalidLabWorkflowTransition

    h = _build_repo_harness(tmp_path, db_name=f"accept-vs-collection-{i}")
    lab, order_id = _seed_selected_order(h, name=f"avc{i}")

    results = {}
    barrier = threading.Barrier(2)

    def do_accept():
        barrier.wait(timeout=10)
        h["workflow"].accept(order_id=order_id, lab_organization_id=lab.id)
        results["accept"] = "done"

    def do_collection():
        barrier.wait(timeout=10)
        try:
            h["workflow"].record_collection(order_id=order_id, lab_organization_id=lab.id)
            results["collection"] = "ok"
        except InvalidLabWorkflowTransition:
            results["collection"] = "conflict"

    t1 = threading.Thread(target=do_accept)
    t2 = threading.Thread(target=do_collection)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    if results["collection"] == "conflict":
        # collection lost the race (order was still SELECTED) -- retry it now that accept landed.
        _record, created = h["workflow"].record_collection(
            order_id=order_id, lab_organization_id=lab.id
        )
        assert created is True

    with h["sessions"]() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "ACCEPTED"
    collection = h["workflow"].get_collection_for_order(order_id)
    assert collection is not None
    # No fabricated success either way: the milestone exists and the order is ACCEPTED regardless
    # of which thread actually created it, never a state where collection was recorded against a
    # still-SELECTED order.


def test_accept_vs_collection_race(tmp_path: Path) -> None:
    for i in range(5):
        _run_accept_vs_collection_once(tmp_path, i)


def _run_collection_vs_start_processing_once(tmp_path: Path, i: int) -> None:
    """DH-EX-011's own required "COLLECTION VS START-PROCESSING" race, at the ACCEPTED/IN_PROGRESS
    boundary: from ACCEPTED with no collection yet, race `record_collection` against
    `start_processing`. Both valid outcomes per the ticket: collection commits first and
    start-processing then sees it and succeeds, or start-processing commits first (observes no
    collection yet) and is refused with a controlled conflict, collection then commits, and a
    retried start-processing succeeds. Never a state where IN_PROGRESS is reached with no
    collection milestone ever recorded."""
    import threading

    from daher_adapter.exchange.lab_workflow import InvalidLabWorkflowTransition

    h = _build_repo_harness(tmp_path, db_name=f"collection-vs-start-{i}")
    lab, order_id = _seed_selected_order(h, name=f"cvs{i}")
    h["workflow"].accept(order_id=order_id, lab_organization_id=lab.id)

    results = {}
    barrier = threading.Barrier(2)

    def do_collection():
        barrier.wait(timeout=10)
        h["workflow"].record_collection(order_id=order_id, lab_organization_id=lab.id)
        results["collection"] = "done"

    def do_start():
        barrier.wait(timeout=10)
        try:
            h["workflow"].start_processing(order_id=order_id, lab_organization_id=lab.id)
            results["start"] = "ok"
        except InvalidLabWorkflowTransition:
            results["start"] = "conflict"

    t1 = threading.Thread(target=do_collection)
    t2 = threading.Thread(target=do_start)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    with h["sessions"]() as session:
        order = session.get(LabOrder, order_id)
        final_status = order.status

    if results["start"] == "conflict":
        # start lost the race (no collection existed yet at that instant) -- collection has since
        # committed (the other thread's own join above already completed), so a retry must succeed.
        _record, transitioned = h["workflow"].start_processing(
            order_id=order_id, lab_organization_id=lab.id
        )
        assert transitioned is True
        with h["sessions"]() as session:
            order = session.get(LabOrder, order_id)
            final_status = order.status
    assert final_status == "IN_PROGRESS"
    assert h["workflow"].get_collection_for_order(order_id) is not None


def test_collection_vs_start_processing_race(tmp_path: Path) -> None:
    for i in range(5):
        _run_collection_vs_start_processing_once(tmp_path, i)


def test_10way_concurrent_collection_recording(tmp_path: Path) -> None:
    """DH-EX-011's own required "10 simultaneous collection recordings": exactly one milestone
    row, exactly one real mutation, every other concurrent attempt resolves to the same milestone
    via the idempotent-replay path -- never a duplicate row, never an exception."""
    import threading

    h = _build_repo_harness(tmp_path, db_name="same-collection")
    lab, order_id = _seed_selected_order(h, name="samecollection")
    h["workflow"].accept(order_id=order_id, lab_organization_id=lab.id)

    outcomes = []
    errors = []
    lock = threading.Lock()
    thread_count = 10
    barrier = threading.Barrier(thread_count)

    def attempt():
        try:
            barrier.wait(timeout=10)
            _record, created = h["workflow"].record_collection(
                order_id=order_id, lab_organization_id=lab.id
            )
            with lock:
                outcomes.append(created)
        except Exception as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(outcomes) == thread_count
    assert outcomes.count(True) == 1
    with h["sessions"]() as session:
        from daher_adapter.exchange.models import LabSampleCollection

        count = session.scalar(select(func.count()).select_from(LabSampleCollection))
    assert count == 1


# -- DH-LAB-001 §16/§17/§51: lab-safe Patient-directory identity (BI1-BI7) ----------------------


def test_bi1_lab_work_queue_uses_patient_directory_identity(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, *_rest = _create_selected_order(
        client, private_key, organizations, catalog, directory, patients, name="BI1"
    )
    lab_tech_token = _lab_tech_token_for(client, private_key, lab, organizations, "bi1")

    detail = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    ).json()
    assert detail["patient"]["display_name"] == "Synthetic Patient BI1"
    assert "patient_id" in detail["patient"]


def test_bi2_patient_reference_snapshot_intentionally_differs_api_returns_patient_value(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Adversarial: mutate the linked ``Patient`` row directly (never reachable through any real
    API -- ADR-051 Decision 12 has no PATCH/PUT on Patient) so it genuinely diverges from the
    immutable ``PatientReference`` snapshot recorded at order-creation time. The lab-facing
    `patient` field must reflect the current `Patient` row, never the stale snapshot -- exactly
    the DH-CLINIC-005 rule ADR-051's dated clarification states, now proven for Native Lab too."""
    from daher_adapter.exchange.models import Patient

    private_key, _ = keypair
    provider, lab, order_id, *_rest = _create_selected_order(
        client, private_key, organizations, catalog, directory, patients, name="BI2"
    )
    lab_tech_token = _lab_tech_token_for(client, private_key, lab, organizations, "bi2")

    # `LabOrder.patient_reference_id` points at PatientReference, not Patient directly --
    # resolve through the reference row to find the linked Patient to mutate.
    from daher_adapter.exchange.models import PatientReference

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        reference = session.get(PatientReference, order.patient_reference_id)
        patient_id = reference.patient_id
        assert reference.display_name == "Synthetic Patient BI2"
        patient_row = session.get(Patient, patient_id)
        patient_row.display_name = "Corrected Real Name BI2"
        session.commit()

    detail = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    ).json()
    assert detail["patient"]["display_name"] == "Corrected Real Name BI2"
    # The legacy compat field still carries the original, now-stale PatientReference snapshot --
    # proving the two fields genuinely come from different tables, not one derived from the other.
    assert detail["patient_display_name"] == "Synthetic Patient BI2"

    list_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders", headers=_auth(lab_tech_token)
    ).json()
    matching = next(o for o in list_response if o["order_id"] == str(order_id))
    assert matching["patient"]["display_name"] == "Corrected Real Name BI2"


def test_bi3_date_of_birth_returned_when_known(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    import datetime as dt

    from daher_adapter.exchange.models import Patient, PatientReference

    private_key, _ = keypair
    provider, lab, order_id, *_rest = _create_selected_order(
        client, private_key, organizations, catalog, directory, patients, name="BI3"
    )
    lab_tech_token = _lab_tech_token_for(client, private_key, lab, organizations, "bi3")

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        reference = session.get(PatientReference, order.patient_reference_id)
        patient_row = session.get(Patient, reference.patient_id)
        patient_row.date_of_birth = dt.date(1990, 1, 1)
        session.commit()

    detail = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    ).json()
    assert detail["patient"]["date_of_birth"] == "1990-01-01"


def test_bi4_unknown_date_of_birth_remains_null_never_fabricated(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, *_rest = _create_selected_order(
        client, private_key, organizations, catalog, directory, patients, name="BI4"
    )
    lab_tech_token = _lab_tech_token_for(client, private_key, lab, organizations, "bi4")

    detail = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    ).json()
    assert detail["patient"]["date_of_birth"] is None


def test_bi5_no_contact_data_added_to_lab_response(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, lab, order_id, *_rest = _create_selected_order(
        client, private_key, organizations, catalog, directory, patients, name="BI5"
    )
    lab_tech_token = _lab_tech_token_for(client, private_key, lab, organizations, "bi5")

    detail = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}", headers=_auth(lab_tech_token)
    ).json()
    assert set(detail["patient"].keys()) == {"patient_id", "display_name", "date_of_birth"}

    list_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders", headers=_auth(lab_tech_token)
    ).json()
    for entry in list_response:
        assert set(entry["patient"].keys()) == {"patient_id", "display_name", "date_of_birth"}


def test_bi6_no_standalone_patient_route_exists_for_lab(client: TestClient, keypair) -> None:
    """A lab-facing caller has no way to fetch Patient identity independent of an order it is
    already assigned to -- there is no `/organizations/{lab_id}/patients/...` route at all."""
    private_key, _ = keypair
    token = make_token(private_key, subject="bi6-probe")
    response = client.get(
        f"/exchange/v1/organizations/{uuid.uuid4()}/patients/{uuid.uuid4()}", headers=_auth(token)
    )
    assert response.status_code in (404, 405)


def test_bi7_unassigned_lab_queue_never_contains_others_patient_identity(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, lab_a, order_id, *_rest = _create_selected_order(
        client, private_key, organizations, catalog, directory, patients, name="BI7"
    )
    lab_a_tech_token = _lab_tech_token_for(client, private_key, lab_a, organizations, "bi7a")
    accept = client.post(
        f"/exchange/v1/organizations/{lab_a.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_a_tech_token),
    )
    assert accept.status_code == 200, accept.text

    lab_b, lab_b_tech_token, _identity_id = _setup_lab_tech(
        client, private_key, organizations, "bi7b"
    )
    queue_b = client.get(
        f"/exchange/v1/organizations/{lab_b.id}/lab-orders", headers=_auth(lab_b_tech_token)
    ).json()
    assert not any(o["order_id"] == str(order_id) for o in queue_b)
    detail_b = client.get(
        f"/exchange/v1/organizations/{lab_b.id}/lab-orders/{order_id}",
        headers=_auth(lab_b_tech_token),
    )
    assert detail_b.status_code == 404
    assert detail_b.json() == {"detail": "order not found"}


# -- DH-LAB-001 §21/§51: N+1 query-shape qualification (BQ1-BQ2) --------------------------------


def _measure_lab_queue_identity_selects(client, lab_id, lab_tech_token, engine) -> int:
    """Counts only SELECTs against the tables DH-LAB-001 §21 names (`exchange_organizations`,
    `exchange_patients`, `exchange_patient_references`) -- deliberately excludes
    `exchange_lab_order_items`'s own pre-existing, unrelated lazy-relationship load (a different,
    out-of-scope N+1 this ticket does not touch), so this measurement isolates exactly the
    provider-name/patient-identity batching this ticket adds."""
    statements: list[str] = []

    def _record(conn, cursor, statement, *args) -> None:  # noqa: ARG001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        resp = client.get(
            f"/exchange/v1/organizations/{lab_id}/lab-orders", headers=_auth(lab_tech_token)
        )
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    assert resp.status_code == 200, resp.text
    identity_tables = ("exchange_organizations", "exchange_patients", "exchange_patient_references")
    return len(
        [
            s
            for s in statements
            if s.strip().upper().startswith("SELECT") and any(t in s for t in identity_tables)
        ]
    )


def test_bq1_same_provider_many_orders_query_count_does_not_scale_with_order_count(
    client: TestClient, keypair, organizations, catalog, directory, patients, engine
) -> None:
    """§21's literal anti-pattern: "for each order: query provider; query patient". With a
    *single* provider (so distinct-provider-count is fixed at 1 throughout), the query count for
    the whole listing must not grow linearly as more orders from that same provider are added."""
    private_key, _ = keypair
    provider, lab, _order_id, entries, *_rest = _create_selected_order(
        client, private_key, organizations, catalog, directory, patients, name="BQ1", test_count=1
    )
    lab_tech_token = _lab_tech_token_for(client, private_key, lab, organizations, "bq1")
    clinician_token = make_token(private_key, subject="clinician-BQ1")

    selects_1 = _measure_lab_queue_identity_selects(client, lab.id, lab_tech_token, engine)

    # Add 4 more orders from the SAME provider/patient, all assigned to the same lab.
    patient_id = _create_patient(client, private_key, patients, provider.id, "BQ1-shared")
    for i in range(4):
        entry, _ = catalog.create_entry(code=f"DHEALTH-BQ1-{i}", display_name=f"BQ1 Test {i}")
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("1000"),
            currency="DJF",
            expected_current_price_id=None,
        )
        create_response = client.post(
            f"/exchange/v1/organizations/{provider.id}/orders",
            headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
            json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(entry.id)]},
        )
        assert create_response.status_code == 201, create_response.text
        order_id_n = uuid.UUID(create_response.json()["id"])
        access_token_n = client.post(
            f"/exchange/v1/organizations/{provider.id}/orders/{order_id_n}/patient-access",
            headers=_auth(clinician_token),
        ).json()["access_token"]
        options = client.get(
            "/exchange/v1/patient/order/lab-options", headers=_auth(access_token_n)
        ).json()["options"]
        matching = next(opt for opt in options if opt["lab"]["id"] == str(lab.id))
        price_ids_n = [line["current_price_id"] for line in matching["prices"]]
        selection = client.post(
            "/exchange/v1/patient/order/selection",
            headers=_auth(access_token_n),
            json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids_n},
        )
        assert selection.status_code == 201, selection.text

    selects_5 = _measure_lab_queue_identity_selects(client, lab.id, lab_tech_token, engine)

    list_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders", headers=_auth(lab_tech_token)
    ).json()
    assert len(list_response) == 5

    # A true per-order N+1 (one provider query + one patient query per row) would have grown
    # roughly 2 queries per added order (+8 for 4 more orders). A single-provider/single-patient
    # batch stays flat regardless of order count.
    assert selects_5 <= selects_1 + 1, (
        f"expected roughly constant query count for a single provider, got {selects_1} for 1 "
        f"order vs {selects_5} for 5 orders -- looks like a per-row N+1"
    )


def test_bq2_distinct_providers_query_count_scales_with_provider_count_not_order_count(
    client: TestClient, keypair, organizations, catalog, directory, patients, engine
) -> None:
    """The queue can span multiple distinct ordering providers (§21's own stated shape, unlike
    Clinic's single-provider order lists). Query count is bounded by *distinct provider count*,
    never by order count independently: doubling the orders from the SAME two providers must not
    add any further provider/patient-resolution queries."""
    private_key, _ = keypair
    provider_a, lab, order_a1, entries_a, *_rest = _create_selected_order(
        client, private_key, organizations, catalog, directory, patients, name="BQ2A", test_count=1
    )
    lab_tech_token = _lab_tech_token_for(client, private_key, lab, organizations, "bq2")
    clinician_a_token = make_token(private_key, subject="clinician-BQ2A")

    provider_b, clinician_b_token = _setup_clinician(client, private_key, organizations, "BQ2B")
    patient_b = _create_patient(client, private_key, patients, provider_b.id, "BQ2B")
    entry_b, _ = catalog.create_entry(code="DHEALTH-BQ2-B0", display_name="BQ2 B Test")
    offering_b, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_b.id)
    catalog.set_price(
        offering_id=offering_b.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    create_b = client.post(
        f"/exchange/v1/organizations/{provider_b.id}/orders",
        headers={**_auth(clinician_b_token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_b), "catalog_entry_ids": [str(entry_b.id)]},
    )
    assert create_b.status_code == 201, create_b.text
    order_b1 = uuid.UUID(create_b.json()["id"])
    access_b1 = client.post(
        f"/exchange/v1/organizations/{provider_b.id}/orders/{order_b1}/patient-access",
        headers=_auth(clinician_b_token),
    ).json()["access_token"]
    options_b1 = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_b1)
    ).json()["options"]
    matching_b1 = next(opt for opt in options_b1 if opt["lab"]["id"] == str(lab.id))
    price_ids_b1 = [line["current_price_id"] for line in matching_b1["prices"]]
    selection_b1 = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_b1),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids_b1},
    )
    assert selection_b1.status_code == 201, selection_b1.text

    # Two providers, two orders total (one each).
    list_2 = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders", headers=_auth(lab_tech_token)
    ).json()
    assert len(list_2) == 2
    selects_2_orders_2_providers = _measure_lab_queue_identity_selects(
        client, lab.id, lab_tech_token, engine
    )

    # Add one more order from EACH already-existing provider -- order count doubles to 4,
    # distinct-provider count stays at 2. A fresh patient under each existing provider still
    # counts as that same provider for the query-grouping this test is proving.
    patient_a_again = _create_patient(client, private_key, patients, provider_a.id, "BQ2A-again")
    for provider, clinician_token, patient_id, tag in (
        (provider_a, clinician_a_token, patient_a_again, "a"),
        (provider_b, clinician_b_token, patient_b, "b"),
    ):
        entry, _ = catalog.create_entry(
            code=f"DHEALTH-BQ2-{tag}-2", display_name=f"BQ2 {tag} Test 2"
        )
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("1000"),
            currency="DJF",
            expected_current_price_id=None,
        )
        create_response = client.post(
            f"/exchange/v1/organizations/{provider.id}/orders",
            headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
            json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(entry.id)]},
        )
        assert create_response.status_code == 201, create_response.text
        order_id_n = uuid.UUID(create_response.json()["id"])
        access_token_n = client.post(
            f"/exchange/v1/organizations/{provider.id}/orders/{order_id_n}/patient-access",
            headers=_auth(clinician_token),
        ).json()["access_token"]
        options_n = client.get(
            "/exchange/v1/patient/order/lab-options", headers=_auth(access_token_n)
        ).json()["options"]
        matching_n = next(opt for opt in options_n if opt["lab"]["id"] == str(lab.id))
        price_ids_n = [line["current_price_id"] for line in matching_n["prices"]]
        selection_n = client.post(
            "/exchange/v1/patient/order/selection",
            headers=_auth(access_token_n),
            json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids_n},
        )
        assert selection_n.status_code == 201, selection_n.text

    list_4 = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders", headers=_auth(lab_tech_token)
    ).json()
    assert len(list_4) == 4
    selects_4_orders_2_providers = _measure_lab_queue_identity_selects(
        client, lab.id, lab_tech_token, engine
    )

    # Same 2 distinct providers, twice the orders -- query count must not have grown further.
    assert selects_4_orders_2_providers <= selects_2_orders_2_providers, (
        f"expected query count bounded by distinct-provider count (2), got "
        f"{selects_2_orders_2_providers} for 2 orders/2 providers vs "
        f"{selects_4_orders_2_providers} for 4 orders/2 providers"
    )


def _lab_tech_token_for(client, private_key, lab, organizations, suffix: str) -> str:
    identity_id = _bootstrap_identity(client, private_key, f"labtech-{suffix}")
    _grant_roles(client, private_key, lab.id, identity_id, f"labtech-{suffix}", ["LAB_TECH"])
    return make_token(private_key, subject=f"labtech-{suffix}")
