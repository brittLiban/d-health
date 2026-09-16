"""DH-EX-004: the HTTP-level required matrix for provider lab-order creation -- clinician-only
authorization, exact patient/practitioner provenance, provider-scoped idempotency, no lab
selection/price snapshot yet, and transactional creation audit.

Same harness pattern as ``test_exchange_api_marketplace.py``: a bare ``FastAPI()`` app with
``build_exchange_router`` mounted directly, a ``StaticKeySource``-backed ``TokenValidator`` (local
RSA keypair, zero network).
"""

from __future__ import annotations

import uuid
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
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
)
from daher_adapter.exchange.models import (
    DoctorReview,
    ExchangeSecurityAuditEvent,
    LabOrder,
    LabOrderIntent,
    LabOrderItem,
    LabSelection,
    PractitionerReference,
    ResultDelivery,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository


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


def _setup_clinician(
    client: TestClient, private_key, organizations: OrganizationRepository, name: str, *, roles=None
):
    """Returns (provider, token) -- a fresh PROVIDER org with a CLINICIAN member."""
    provider = make_provider(organizations, name=name)
    identity_id = _bootstrap_identity(client, private_key, f"clinician-{name}")
    _grant_roles(
        client, private_key, provider.id, identity_id, f"clinician-{name}", roles or ["CLINICIAN"]
    )
    token = make_token(private_key, subject=f"clinician-{name}")
    return provider, token, identity_id


def _create_patient(
    client: TestClient, private_key, patients: PatientReferenceRepository, provider_id, name: str
) -> uuid.UUID:
    record = patients.get_or_create(
        source_organization_id=provider_id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-PATIENT-{name}",
        display_name="Synthetic Patient",
    )
    return record.id


def _make_tests(catalog: CatalogRepository, count: int = 2) -> list[uuid.UUID]:
    ids = []
    for i in range(count):
        entry, _ = catalog.create_entry(
            code=f"DHEALTH-ORDER-DEMO-{i}", display_name=f"Demo Order Test {i}"
        )
        ids.append(entry.id)
    return ids


def _new_key() -> str:
    return str(uuid.uuid4())


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


# -- positive control ------------------------------------------------------------------------


def test_clinician_creates_order_positive_control(
    client: TestClient, keypair, organizations, catalog, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, token, identity_id = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 2)
    key = _new_key()

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider_organization_id"] == str(provider.id)
    assert body["patient_reference_id"] == str(patient_id)
    assert body["status"] == "AWAITING_LAB_SELECTION"
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert "selected_price_amount" not in item
        assert "selected_price_currency" not in item

    # Practitioner derivation: exactly maps to Clinician A's ExchangeIdentity, scoped to provider.
    with sessions() as session:
        practitioner = session.get(
            PractitionerReference, uuid.UUID(body["practitioner_reference_id"])
        )
        assert practitioner.source_organization_id == provider.id
        assert practitioner.source_identity_value == identity_id

        doctor_reviews = session.scalar(
            select(func.count())
            .select_from(DoctorReview)
            .where(DoctorReview.lab_order_id == uuid.UUID(body["id"]))
        )
        assert doctor_reviews == 1
        result_deliveries = session.scalar(
            select(func.count())
            .select_from(ResultDelivery)
            .where(ResultDelivery.lab_order_id == uuid.UUID(body["id"]))
        )
        assert result_deliveries == 1
        selections = session.scalar(
            select(func.count())
            .select_from(LabSelection)
            .where(LabSelection.lab_order_id == uuid.UUID(body["id"]))
        )
        assert selections == 0

    events = _audit_events(sessions, "lab_order_created")
    assert len(events) == 1
    assert events[0]["outcome"] == "created"
    assert events[0]["resource_type"] == "lab_order"
    assert events[0]["resource_id"] == body["id"]
    assert events[0]["organization_id"] == str(provider.id)


# -- exact replay / idempotency ----------------------------------------------------------------


def test_exact_replay_returns_same_order_no_duplicates(
    client: TestClient, keypair, organizations, catalog, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 2)
    key = _new_key()
    body = {
        "patient_reference_id": str(patient_id),
        "catalog_entry_ids": [str(t) for t in test_ids],
    }

    first = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json=body,
    )
    second = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json=body,
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["practitioner_reference_id"] == second.json()["practitioner_reference_id"]

    with sessions() as session:
        order_count = session.scalar(select(func.count()).select_from(LabOrder))
        item_count = session.scalar(select(func.count()).select_from(LabOrderItem))
    assert order_count == 1
    assert item_count == 2

    events = _audit_events(sessions, "lab_order_created")
    assert len(events) == 1, "replay must never emit a second lab_order_created audit row"


def test_idempotency_conflict_different_patient(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_a = _create_patient(client, private_key, patients, provider.id, "A")
    patient_b = _create_patient(client, private_key, patients, provider.id, "B")
    test_ids = _make_tests(catalog, 1)
    key = _new_key()

    first = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_a),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert first.status_code == 201
    second = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_b),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert second.status_code == 409


def test_idempotency_conflict_different_test_set(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 3)
    key = _new_key()

    first = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert first.status_code == 201
    second = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[1])]},
    )
    assert second.status_code == 409


def test_idempotency_conflict_different_clinician(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    """Same provider, same key, two different authenticated clinicians -- practitioner
    provenance differs, so this must be a 409, never a mismatched order silently returned."""
    private_key, _ = keypair
    provider = make_provider(organizations, name="Shared-Provider")
    id_a = _bootstrap_identity(client, private_key, "clinician-shared-A")
    _grant_roles(client, private_key, provider.id, id_a, "clinician-shared-A", ["CLINICIAN"])
    token_a = make_token(private_key, subject="clinician-shared-A")
    id_b = _bootstrap_identity(client, private_key, "clinician-shared-B")
    _grant_roles(client, private_key, provider.id, id_b, "clinician-shared-B", ["CLINICIAN"])
    token_b = make_token(private_key, subject="clinician-shared-B")

    patient_id = _create_patient(client, private_key, patients, provider.id, "shared")
    test_ids = _make_tests(catalog, 1)
    key = _new_key()
    body = {"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]}

    first = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token_a), "Idempotency-Key": key},
        json=body,
    )
    assert first.status_code == 201
    second = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token_b), "Idempotency-Key": key},
        json=body,
    )
    assert second.status_code == 409


def test_new_key_same_content_creates_new_order(
    client: TestClient, keypair, organizations, catalog, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)
    body = {"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]}

    first = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json=body,
    )
    second = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json=body,
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]

    with sessions() as session:
        order_count = session.scalar(select(func.count()).select_from(LabOrder))
    assert order_count == 2


# -- auth negative controls ---------------------------------------------------------------------


def test_anonymous_is_401(client: TestClient) -> None:
    response = client.post(
        f"/exchange/v1/organizations/{uuid.uuid4()}/orders",
        headers={"Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(uuid.uuid4()), "catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 401


def test_invalid_bearer_is_401(client: TestClient) -> None:
    response = client.post(
        f"/exchange/v1/organizations/{uuid.uuid4()}/orders",
        headers={"Authorization": "Bearer not-a-real-token", "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(uuid.uuid4()), "catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 401


def test_zero_membership_identity_refused(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations, name="Zero-Membership-Target")
    token = make_token(private_key, subject="zero-membership-orderer")
    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(uuid.uuid4()), "catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code in (403, 404)


def test_lab_tech_refused(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations, name="LabTech-Target")
    lab = make_lab(organizations, name="LabTech-Home-Lab")
    identity_id = _bootstrap_identity(client, private_key, "lab-tech-orderer")
    _grant_roles(client, private_key, lab.id, identity_id, "lab-tech-orderer", ["LAB_TECH"])
    token = make_token(private_key, subject="lab-tech-orderer")

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(uuid.uuid4()), "catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 404  # no relationship at all to this provider


def test_lab_org_admin_refused(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations, name="LabAdmin-Target")
    lab = make_lab(organizations, name="LabAdmin-Home-Lab")
    identity_id = _bootstrap_identity(client, private_key, "lab-admin-orderer")
    _grant_roles(client, private_key, lab.id, identity_id, "lab-admin-orderer", ["ORG_ADMIN"])
    token = make_token(private_key, subject="lab-admin-orderer")

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(uuid.uuid4()), "catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 404


def test_provider_org_admin_without_clinician_refused(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, _, _ = _setup_clinician(
        client, private_key, organizations, "OrgAdminOnly", roles=["ORG_ADMIN"]
    )
    token = make_token(private_key, subject="clinician-OrgAdminOnly")

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(uuid.uuid4()), "catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 403  # known tenant, insufficient clinical permission


def test_org_admin_plus_clinician_can_order(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    """A user holding ORG_ADMIN + CLINICIAN may order -- because they explicitly hold CLINICIAN."""
    private_key, _ = keypair
    provider, _, _ = _setup_clinician(
        client, private_key, organizations, "BothRoles", roles=["ORG_ADMIN", "CLINICIAN"]
    )
    token = make_token(private_key, subject="clinician-BothRoles")
    patient_id = _create_patient(client, private_key, patients, provider.id, "both")
    test_ids = _make_tests(catalog, 1)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert response.status_code == 201


def test_platform_admin_alone_refused(client: TestClient, keypair, organizations) -> None:
    """DH-EX-004 permanent negative test: network administration is not clinical access."""
    private_key, _ = keypair
    provider = make_provider(organizations, name="PlatformAdmin-Target")
    admin_token = make_token(
        private_key, subject="platform-admin-orderer", roles=[PLATFORM_ADMIN_ROLE]
    )

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(admin_token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(uuid.uuid4()), "catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 403  # org visibly exists to platform admin -- not 404

    read_response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders", headers=_auth(admin_token)
    )
    assert read_response.status_code == 403


def test_inactive_clinician_membership_refused(
    client: TestClient, keypair, organizations, sessions
) -> None:
    from daher_adapter.exchange.models import OrganizationMembership

    private_key, _ = keypair
    provider, token, identity_id = _setup_clinician(
        client, private_key, organizations, "InactiveMember"
    )
    with sessions() as session:
        membership = session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == provider.id,
                OrganizationMembership.identity_id == uuid.UUID(identity_id),
            )
        )
        membership.status = "INACTIVE"
        session.commit()

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(uuid.uuid4()), "catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert (
        response.status_code == 404
    )  # find_active returns None -- indistinguishable from no membership


def test_clinician_in_inactive_provider_refused(
    client: TestClient, keypair, organizations, sessions
) -> None:
    from daher_adapter.exchange.models import Organization

    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "InactiveProvider")
    with sessions() as session:
        org_row = session.get(Organization, provider.id)
        org_row.status = "INACTIVE"
        session.commit()

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(uuid.uuid4()), "catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 403


def test_clinician_a_using_provider_b_url_is_404(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider_a, token_a, _ = _setup_clinician(client, private_key, organizations, "A")
    provider_b, _, _ = _setup_clinician(client, private_key, organizations, "B")

    response = client.post(
        f"/exchange/v1/organizations/{provider_b.id}/orders",
        headers={**_auth(token_a), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(uuid.uuid4()), "catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 404


# -- reference negative controls -----------------------------------------------------------------


def test_unknown_patient_reference_is_404(
    client: TestClient, keypair, organizations, catalog
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    test_ids = _make_tests(catalog, 1)
    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(uuid.uuid4()), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert response.status_code == 404


def test_patient_from_another_provider_is_404(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider_a, token_a, _ = _setup_clinician(client, private_key, organizations, "A")
    provider_b, _, _ = _setup_clinician(client, private_key, organizations, "B")
    patient_b = _create_patient(client, private_key, patients, provider_b.id, "B")
    test_ids = _make_tests(catalog, 1)

    response = client.post(
        f"/exchange/v1/organizations/{provider_a.id}/orders",
        headers={**_auth(token_a), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_b), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert response.status_code == 404


def test_duplicate_test_ids_rejected(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(test_ids[0]), str(test_ids[0])],
        },
    )
    assert response.status_code == 422


def test_unknown_catalog_test_is_404(client: TestClient, keypair, organizations, patients) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 404


def test_inactive_catalog_test_fails_closed(
    client: TestClient, keypair, organizations, catalog, patients, sessions
) -> None:
    from daher_adapter.exchange.models import TestCatalogEntry

    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)
    with sessions() as session:
        session.get(TestCatalogEntry, test_ids[0]).active = False
        session.commit()

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert response.status_code == 422


def test_non_synthetic_catalog_test_fails_closed(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    entry, _ = catalog.create_entry(
        code="DHEALTH-REAL-ORDER", display_name="Not Synthetic", is_synthetic=False
    )

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(entry.id)]},
    )
    assert response.status_code == 422


def test_zero_tests_rejected(client: TestClient, keypair, organizations, patients) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": []},
    )
    assert response.status_code == 422


def test_more_than_fifty_tests_rejected(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 51)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert response.status_code == 422


def test_malformed_idempotency_key_rejected(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": "not-a-uuid"},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert response.status_code == 422


def test_missing_idempotency_key_rejected(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers=_auth(token),
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert response.status_code == 422


# -- clinician provenance control ----------------------------------------------------------------


def test_request_body_cannot_supply_practitioner_reference_id(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(test_ids[0])],
            "practitioner_reference_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422  # extra="forbid"


# -- no lab routing / no price snapshot / no status advance --------------------------------------


def test_no_price_snapshot_and_status_stays_awaiting_lab_selection(
    client: TestClient, keypair, organizations, catalog, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert response.status_code == 201
    order_id = uuid.UUID(response.json()["id"])

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "AWAITING_LAB_SELECTION"
        for item in order.items:
            assert item.selected_price_amount is None
            assert item.selected_price_currency is None
        selection_count = session.scalar(
            select(func.count())
            .select_from(LabSelection)
            .where(LabSelection.lab_order_id == order_id)
        )
        assert selection_count == 0


def test_order_creation_does_not_require_an_eligible_lab(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    """The clinical decision must not disappear merely because the marketplace currently has
    zero matching labs -- no lab offering/directory profile exists anywhere in this test."""
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "AWAITING_LAB_SELECTION"


# -- order list / detail --------------------------------------------------------------------------


def test_order_list_and_detail(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)

    created = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    order_id = created.json()["id"]

    listing = client.get(f"/exchange/v1/organizations/{provider.id}/orders", headers=_auth(token))
    assert listing.status_code == 200
    assert any(o["id"] == order_id for o in listing.json())
    assert "items" not in listing.json()[0]
    assert "item_count" in listing.json()[0]

    detail = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}", headers=_auth(token)
    )
    assert detail.status_code == 200
    assert detail.json()["id"] == order_id
    assert len(detail.json()["items"]) == 1


def test_cross_provider_order_read_is_404(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider_a, token_a, _ = _setup_clinician(client, private_key, organizations, "A")
    provider_b, token_b, _ = _setup_clinician(client, private_key, organizations, "B")
    patient_a = _create_patient(client, private_key, patients, provider_a.id, "A")
    test_ids = _make_tests(catalog, 1)

    created = client.post(
        f"/exchange/v1/organizations/{provider_a.id}/orders",
        headers={**_auth(token_a), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_a), "catalog_entry_ids": [str(test_ids[0])]},
    )
    order_id = created.json()["id"]

    response = client.get(
        f"/exchange/v1/organizations/{provider_b.id}/orders/{order_id}", headers=_auth(token_b)
    )
    assert response.status_code == 404


# -- audit atomicity failure injection -------------------------------------------------------------


def test_audit_failure_leaves_zero_order_and_zero_practitioner(
    client: TestClient, keypair, organizations, catalog, patients, sessions, monkeypatch
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)

    with sessions() as session:
        before_orders = session.scalar(select(func.count()).select_from(LabOrder))
        before_practitioners = session.scalar(
            select(func.count()).select_from(PractitionerReference)
        )
        before_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit-insert failure"):
        client.post(
            f"/exchange/v1/organizations/{provider.id}/orders",
            headers={**_auth(token), "Idempotency-Key": _new_key()},
            json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
        )

    with sessions() as session:
        after_orders = session.scalar(select(func.count()).select_from(LabOrder))
        after_practitioners = session.scalar(
            select(func.count()).select_from(PractitionerReference)
        )
        after_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))
    assert after_orders == before_orders
    assert after_practitioners == before_practitioners, "no orphaned practitioner reference"
    assert after_audit == before_audit


# -- DH-EX-004R: platform-admin order-existence oracle (Part D) ----------------------------------


def test_platform_admin_order_existence_is_not_leaked(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    """A platform admin lacking CLINICIAN must receive the identical 403 whether the order UUID
    in the URL is real or completely made up -- the authorization check must short-circuit before
    any order-specific lookup, so a platform admin can never learn "this order id exists" from the
    response alone."""
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)
    created = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    real_order_id = created.json()["id"]

    admin_token = make_token(
        private_key, subject="platform-admin-oracle-check", roles=[PLATFORM_ADMIN_ROLE]
    )

    real_response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{real_order_id}",
        headers=_auth(admin_token),
    )
    fake_response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{uuid.uuid4()}",
        headers=_auth(admin_token),
    )
    assert real_response.status_code == fake_response.status_code == 403
    assert real_response.json() == fake_response.json()


# -- DH-EX-004R: practitioner reference is per-provider, never shared (Part G) -------------------


def test_practitioner_reference_is_separate_per_provider(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    """The same clinician ExchangeIdentity, CLINICIAN in two different provider organizations,
    must resolve to two distinct PractitionerReference rows -- never one reused across tenants."""
    private_key, _ = keypair
    provider_a = make_provider(organizations, name="MultiOrgClinician-A")
    provider_b = make_provider(organizations, name="MultiOrgClinician-B")
    identity_id = _bootstrap_identity(client, private_key, "multi-org-clinician")
    _grant_roles(
        client, private_key, provider_a.id, identity_id, "multi-org-clinician-a", ["CLINICIAN"]
    )
    _grant_roles(
        client, private_key, provider_b.id, identity_id, "multi-org-clinician-b", ["CLINICIAN"]
    )
    token = make_token(private_key, subject="multi-org-clinician")

    patient_a = _create_patient(client, private_key, patients, provider_a.id, "A")
    patient_b = _create_patient(client, private_key, patients, provider_b.id, "B")
    test_ids = _make_tests(catalog, 1)

    order_a = client.post(
        f"/exchange/v1/organizations/{provider_a.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_a), "catalog_entry_ids": [str(test_ids[0])]},
    )
    order_b = client.post(
        f"/exchange/v1/organizations/{provider_b.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_b), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert order_a.status_code == 201
    assert order_b.status_code == 201
    assert (
        order_a.json()["practitioner_reference_id"] != order_b.json()["practitioner_reference_id"]
    )


# -- DH-EX-004R: losing a conflicting-key race must leave no orphan practitioner (Part H) ---------


def test_losing_conflicting_request_leaves_no_orphan_practitioner(
    client: TestClient, keypair, organizations, catalog, patients, sessions
) -> None:
    """A brand-new clinician (no PractitionerReference yet) submits a CONFLICTING same-key
    request against an order another clinician already created. The request must fail with 409,
    and -- because practitioner get/create + order creation are one transaction -- the new
    clinician's practitioner reference must not survive merely because their request lost."""
    private_key, _ = keypair
    provider = make_provider(organizations, name="OrphanCheck-Provider")
    id_winner = _bootstrap_identity(client, private_key, "orphan-check-winner")
    _grant_roles(client, private_key, provider.id, id_winner, "orphan-check-winner", ["CLINICIAN"])
    token_winner = make_token(private_key, subject="orphan-check-winner")
    id_loser = _bootstrap_identity(client, private_key, "orphan-check-loser")
    _grant_roles(client, private_key, provider.id, id_loser, "orphan-check-loser", ["CLINICIAN"])
    token_loser = make_token(private_key, subject="orphan-check-loser")

    patient_id = _create_patient(client, private_key, patients, provider.id, "orphan-check")
    test_ids = _make_tests(catalog, 1)
    key = _new_key()

    winner = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token_winner), "Idempotency-Key": key},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert winner.status_code == 201

    with sessions() as session:
        before_practitioners = session.scalar(
            select(func.count()).select_from(PractitionerReference)
        )

    # The loser has never ordered before -- their PractitionerReference does not exist yet.
    loser = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token_loser), "Idempotency-Key": key},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert loser.status_code == 409

    with sessions() as session:
        after_practitioners = session.scalar(
            select(func.count()).select_from(PractitionerReference)
        )
        loser_practitioner = session.scalar(
            select(func.count())
            .select_from(PractitionerReference)
            .where(
                PractitionerReference.source_organization_id == provider.id,
                PractitionerReference.source_identity_value == id_loser,
            )
        )
    assert after_practitioners == before_practitioners, (
        "the loser's practitioner reference must not survive a lost idempotency-conflict race"
    )
    assert loser_practitioner == 0


# -- DH-EX-004R: adversarial patient-reference source-organization-type check (Part K) ------------


def test_patient_reference_seeded_at_a_lab_org_cannot_be_used(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    """Adversarial: a PatientReference row whose source_organization_id happens to point at a LAB
    organization (never producible through this ticket's own API, but not structurally impossible
    at the DB layer) must still never be usable for an order -- the exact-equality check against
    the route's own (CLINICIAN-verified, therefore PROVIDER-typed) organization id is what
    actually protects this, not any type check on the patient reference itself."""
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    lab = make_lab(organizations, name="AdversarialPatientLab")
    adversarial_patient = patients.get_or_create(
        source_organization_id=lab.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="ADVERSARIAL-LAB-SEEDED-PATIENT",
    )
    test_ids = _make_tests(catalog, 1)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(adversarial_patient.id),
            "catalog_entry_ids": [str(test_ids[0])],
        },
    )
    assert response.status_code == 404


# -- DH-EX-004R: duplicate catalog entries refused at the repository boundary too (Part N) --------


def test_duplicate_catalog_entries_refused_at_repository_level(
    sessions, organizations, catalog, patients
) -> None:
    """The HTTP schema already rejects duplicate catalog_entry_ids -- this proves the repository
    itself is the actual enforcement point, not merely one caller, matching this package's
    established discipline for every other duplicate/conflict check."""
    from daher_adapter.exchange.orders import (
        DuplicateOrderItems,
        LabOrderRepository,
        OrderItemInput,
    )

    provider = make_provider(organizations, name="RepoDuplicateCheck")
    patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="REPO-DUPLICATE-PATIENT",
    )
    entry, _ = catalog.create_entry(code="DHEALTH-REPO-DUP", display_name="Repo Dup Test")
    orders_repo = LabOrderRepository(sessions, organizations, catalog)

    with pytest.raises(DuplicateOrderItems):
        orders_repo.create(
            idempotency_key=_new_key(),
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=uuid.uuid4(),
            items=[
                OrderItemInput(catalog_entry_id=entry.id),
                OrderItemInput(catalog_entry_id=entry.id),
            ],
        )


# -- DH-EX-004R: Idempotency-Key canonical-form enforcement (Part O) -------------------------------


@pytest.mark.parametrize(
    "malformed_key",
    [
        "550E8400-E29B-41D4-A716-446655440000",  # uppercase
        "{550e8400-e29b-41d4-a716-446655440000}",  # braced
        "urn:uuid:550e8400-e29b-41d4-a716-446655440000",  # URN form
        " 550e8400-e29b-41d4-a716-446655440000",  # leading whitespace
        "550e8400-e29b-41d4-a716-446655440000 ",  # trailing whitespace
        "550e8400e29b41d4a716446655440000",  # no hyphens
        "not-a-uuid-at-all",
    ],
)
def test_idempotency_key_rejects_noncanonical_representations(
    client: TestClient, keypair, organizations, catalog, patients, malformed_key
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": malformed_key},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert response.status_code == 422, (
        f"expected 422 for {malformed_key!r}, got {response.status_code}"
    )


def test_idempotency_key_rejects_non_v4_uuid(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    """A syntactically valid but wrong-version UUID (v1, time-based) must still be refused --
    "canonical UUIDv4" means version 4 specifically, not merely "any UUID-shaped string"."""
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)
    v1_uuid = str(uuid.uuid1())

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": v1_uuid},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert response.status_code == 422


def test_idempotency_key_accepts_exact_canonical_form(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)
    canonical_key = str(uuid.uuid4())
    assert canonical_key == canonical_key.lower()

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": canonical_key},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert response.status_code == 201


# -- DH-EX-004R: same-key replay is set-identity, not list-order (Part P) -------------------------


def test_replay_matches_regardless_of_test_set_order(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 3)
    key = _new_key()

    first = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    reordered = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in reversed(test_ids)],
        },
    )
    assert first.status_code == 201
    assert reordered.status_code == 200
    assert first.json()["id"] == reordered.json()["id"]


# -- DH-EX-004R: order list limit query parameter (Part Y) -----------------------------------------


@pytest.mark.parametrize("limit", [1, 50, 100])
def test_order_list_limit_accepts_valid_range(
    client: TestClient, keypair, organizations, catalog, patients, limit
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(client, private_key, patients, provider.id, "A")
    test_ids = _make_tests(catalog, 1)
    client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(test_ids[0])]},
    )

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders?limit={limit}", headers=_auth(token)
    )
    assert response.status_code == 200
    assert len(response.json()) <= limit


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_order_list_limit_rejects_out_of_range(
    client: TestClient, keypair, organizations, limit
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders?limit={limit}", headers=_auth(token)
    )
    assert response.status_code == 422


# -- DH-CLINIC-003: patient-reference order list endpoint ----------------------------------------


def test_list_patient_orders_positive_control(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_1 = _create_patient(client, private_key, patients, provider.id, "P1")
    patient_2 = _create_patient(client, private_key, patients, provider.id, "P2")
    test_ids = _make_tests(catalog, 3)

    # Patient 1: Order 1 (test 0, 1)
    res1 = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_1),
            "catalog_entry_ids": [str(test_ids[0]), str(test_ids[1])],
        },
    )
    assert res1.status_code == 201
    order1_id = res1.json()["id"]

    # Patient 2: Order 2 (test 2)
    res2 = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_2), "catalog_entry_ids": [str(test_ids[2])]},
    )
    assert res2.status_code == 201
    order2_id = res2.json()["id"]

    # Patient 1: Order 3 (test 0)
    res3 = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_1), "catalog_entry_ids": [str(test_ids[0])]},
    )
    assert res3.status_code == 201
    order3_id = res3.json()["id"]

    # Query Patient 1's orders: should return order3 first, then order1; should NOT contain order2
    resp1 = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-references/{patient_1}/orders",
        headers=_auth(token),
    )
    assert resp1.status_code == 200, resp1.text
    p1_orders = resp1.json()
    assert len(p1_orders) == 2
    assert p1_orders[0]["id"] == order3_id
    assert p1_orders[1]["id"] == order1_id
    assert len(p1_orders[0]["items"]) == 1
    assert p1_orders[0]["items"][0]["catalog_entry_id"] == str(test_ids[0])
    assert len(p1_orders[1]["items"]) == 2

    # Query Patient 2's orders: should return only order2
    resp2 = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-references/{patient_2}/orders",
        headers=_auth(token),
    )
    assert resp2.status_code == 200, resp2.text
    p2_orders = resp2.json()
    assert len(p2_orders) == 1
    assert p2_orders[0]["id"] == order2_id


def test_list_patient_orders_anti_enumeration_and_cross_provider(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider_a, token_a, _ = _setup_clinician(client, private_key, organizations, "ProviderA")
    provider_b, token_b, _ = _setup_clinician(client, private_key, organizations, "ProviderB")
    patient_a = _create_patient(client, private_key, patients, provider_a.id, "PatientA")

    # Non-existent patient under Provider A -> 404
    non_existent = uuid.uuid4()
    resp_404 = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/patient-references/{non_existent}/orders",
        headers=_auth(token_a),
    )
    assert resp_404.status_code == 404

    # Clinician B queries Patient A under Provider B -> 404
    # (anti-enumeration: never reveals Patient A belongs to Provider A)
    resp_cross_404 = client.get(
        f"/exchange/v1/organizations/{provider_b.id}/patient-references/{patient_a}/orders",
        headers=_auth(token_b),
    )
    assert resp_cross_404.status_code == 404

    # Clinician B queries Patient A under Provider A -> 404
    # (Clinician B has zero relationship to Provider A, anti-enumeration refuses)
    resp_cross_404_a = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/patient-references/{patient_a}/orders",
        headers=_auth(token_b),
    )
    assert resp_cross_404_a.status_code == 404

    # Platform admin queries Provider A without CLINICIAN role -> 403 (platform admin has visibility
    # of org existence, but lacks clinical ordering/viewing authority)
    platform_admin_token = make_token(
        private_key, subject="network-admin", roles=[PLATFORM_ADMIN_ROLE]
    )
    resp_admin_403 = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/patient-references/{patient_a}/orders",
        headers=_auth(platform_admin_token),
    )
    assert resp_admin_403.status_code == 403


def test_list_patient_orders_non_clinician_refused(
    client: TestClient, keypair, organizations, patients
) -> None:
    private_key, _ = keypair
    provider, token_admin, identity_id = _setup_clinician(
        client, private_key, organizations, "AdminOrg", roles=["ORG_ADMIN"]
    )
    patient = _create_patient(client, private_key, patients, provider.id, "PatientX")

    # ORG_ADMIN without CLINICIAN -> 403
    resp = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-references/{patient}/orders",
        headers=_auth(token_admin),
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("limit", [1, 50, 100])
def test_list_patient_orders_limit_valid(
    client: TestClient, keypair, organizations, patients, limit
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "LimitValid")
    patient = _create_patient(client, private_key, patients, provider.id, "PatientLim")

    resp = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-references/{patient}/orders?limit={limit}",
        headers=_auth(token),
    )
    assert resp.status_code == 200


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_list_patient_orders_limit_invalid(
    client: TestClient, keypair, organizations, patients, limit
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "LimitInvalid")
    patient = _create_patient(client, private_key, patients, provider.id, "PatientLimInv")

    resp = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-references/{patient}/orders?limit={limit}",
        headers=_auth(token),
    )
    assert resp.status_code == 422


# -- DH-CLINIC-003A: order reconciliation by idempotency key -------------------------------------


def test_get_order_by_idempotency_key_positive_control(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "IdempClinician")
    patient = _create_patient(client, private_key, patients, provider.id, "PatientIdemp")
    test_ids = _make_tests(catalog, 2)
    key = _new_key()

    res = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient),
            "catalog_entry_ids": [str(test_ids[0]), str(test_ids[1])],
        },
    )
    assert res.status_code == 201
    created_order = res.json()

    # Query by idempotency key
    lookup = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/by-idempotency-key/{key}",
        headers=_auth(token),
    )
    assert lookup.status_code == 200, lookup.text
    retrieved = lookup.json()
    assert retrieved["id"] == created_order["id"]
    assert retrieved["provider_organization_id"] == str(provider.id)
    assert retrieved["patient_reference_id"] == str(patient)
    assert retrieved["status"] == "AWAITING_LAB_SELECTION"
    assert len(retrieved["items"]) == 2
    assert {it["catalog_entry_id"] for it in retrieved["items"]} == {str(t) for t in test_ids}


def test_get_order_by_idempotency_key_unknown_returns_404(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "UnknownKey")
    resp = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/by-idempotency-key/non-existent-key-123",
        headers=_auth(token),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "order not found"


def test_get_order_by_idempotency_key_cross_provider_isolation(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider_a, token_a, _ = _setup_clinician(client, private_key, organizations, "IdempProvA")
    provider_b, token_b, _ = _setup_clinician(client, private_key, organizations, "IdempProvB")
    patient_a = _create_patient(client, private_key, patients, provider_a.id, "PatientA")
    test_ids = _make_tests(catalog, 1)
    key_a = _new_key()

    res = client.post(
        f"/exchange/v1/organizations/{provider_a.id}/orders",
        headers={**_auth(token_a), "Idempotency-Key": key_a},
        json={
            "patient_reference_id": str(patient_a),
            "catalog_entry_ids": [str(test_ids[0])],
        },
    )
    assert res.status_code == 201

    # Clinician B queries Key A under Provider B -> 404 (does not leak Key A exists under A)
    resp_cross = client.get(
        f"/exchange/v1/organizations/{provider_b.id}/orders/by-idempotency-key/{key_a}",
        headers=_auth(token_b),
    )
    assert resp_cross.status_code == 404

    # Clinician B queries Key A under Provider A -> 404 (Clinician B has zero relation to A)
    resp_cross_a = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/orders/by-idempotency-key/{key_a}",
        headers=_auth(token_b),
    )
    assert resp_cross_a.status_code == 404


def test_get_order_by_idempotency_key_non_clinician_refused(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token_admin, _ = _setup_clinician(
        client, private_key, organizations, "AdminOrgOnly", roles=["ORG_ADMIN"]
    )
    # ORG_ADMIN without CLINICIAN -> 403
    resp = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/by-idempotency-key/some-key-123",
        headers=_auth(token_admin),
    )
    assert resp.status_code == 403


def test_get_order_by_idempotency_key_length_validation(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "LengthVal")
    too_long = "a" * 257
    resp = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/by-idempotency-key/{too_long}",
        headers=_auth(token),
    )
    assert resp.status_code == 422


# -- DH-CLINIC-003C negative-control coverage closure: NC1 permanent focused regression -----------


def test_legacy_order_is_backed_by_exactly_one_bidirectionally_owned_intent(
    client: TestClient, keypair, organizations, catalog, patients, sessions: sessionmaker[Session]
) -> None:
    """DH-CLINIC-003C / NC1 permanent regression.

    The negative-control audit found that a legacy ``POST /orders`` creating a LabOrder without a
    backing ``LabOrderIntent`` went undetected by every local (SQLite) test -- only the real
    PostgreSQL race suite (PG3/PG5) caught it. This test gives the same property fast, local,
    single-request coverage: a plain legacy order create must always be backed by exactly one
    durable intent, with bidirectional ownership agreement.
    """
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "NC1")
    patient_id = _create_patient(client, private_key, patients, provider.id, "NC1")
    test_ids = _make_tests(catalog, 1)
    key = _new_key()

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(test_ids[0])],
        },
    )
    assert response.status_code == 201, response.text
    order_id = uuid.UUID(response.json()["id"])

    with sessions() as session:
        intents = session.scalars(
            select(LabOrderIntent).where(
                LabOrderIntent.provider_organization_id == provider.id,
                LabOrderIntent.idempotency_key == key,
            )
        ).all()
        assert len(intents) == 1, "exactly one durable intent must exist for this provider+key"
        intent = intents[0]

        orders = session.scalars(
            select(LabOrder).where(
                LabOrder.provider_organization_id == provider.id,
                LabOrder.idempotency_key == key,
            )
        ).all()
        assert len(orders) == 1, "exactly one LabOrder must exist for this provider+key"
        order = orders[0]
        assert order.id == order_id

        # Bidirectional ownership agreement (the exact property NC1/NC24 exist to protect).
        assert order.intent_id == intent.id
        assert intent.committed_lab_order_id == order.id

        # Canonical content agreement.
        assert intent.patient_reference_id == order.patient_reference_id
        assert intent.practitioner_reference_id == order.practitioner_reference_id
        intent_item_ids = {item.catalog_entry_id for item in intent.items}
        order_item_ids = {item.catalog_entry_id for item in order.items}
        assert intent_item_ids == order_item_ids == {test_ids[0]}


def test_legacy_conflicts_with_uncommitted_reservation_and_creates_zero_orders(
    client: TestClient, keypair, organizations, catalog, patients, sessions: sessionmaker[Session]
) -> None:
    """DH-CLINIC-003C / PG4 deterministic companion.

    The real-PostgreSQL PG4 race (`test_dhex003c_postgres_qualification.py`) exercises this same
    property under genuine thread concurrency, so its outcome depends on which side wins the race.
    This test pins down, deterministically and without any threading, the one interleaving that
    race can produce where native registration wins: an uncommitted reservation for one patient
    must make a mismatched legacy request conflict (409) and must never create an order at all --
    registration alone never commits.
    """
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "PG4Companion")
    patient_a = _create_patient(client, private_key, patients, provider.id, "PG4Companion-A")
    patient_b = _create_patient(client, private_key, patients, provider.id, "PG4Companion-B")
    test_ids = _make_tests(catalog, 1)
    key = _new_key()

    # 1. Register a durable intent for Patient A. Deliberately NOT committed.
    r_register = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_a),
            "catalog_entry_ids": [str(test_ids[0])],
        },
    )
    assert r_register.status_code == 201, r_register.text

    # 2. Legacy POST under the same key with a different (mismatched) patient.
    r_legacy = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_b),
            "catalog_entry_ids": [str(test_ids[0])],
        },
    )

    # 3. The mismatch must conflict.
    assert r_legacy.status_code == 409, r_legacy.text

    with sessions() as session:
        intents = session.scalars(
            select(LabOrderIntent).where(
                LabOrderIntent.provider_organization_id == provider.id,
                LabOrderIntent.idempotency_key == key,
            )
        ).all()
        orders = session.scalars(
            select(LabOrder).where(
                LabOrder.provider_organization_id == provider.id,
                LabOrder.idempotency_key == key,
            )
        ).all()

        # 4. Exactly one intent exists -- the original Patient-A reservation, untouched.
        assert len(intents) == 1
        assert intents[0].patient_reference_id == patient_a
        assert intents[0].committed_lab_order_id is None

        # 5. Zero orders -- the legacy conflict never created anything.
        assert len(orders) == 0


# -- DH-CLINIC-003C historical (pre-003C, intent_id IS NULL) replay compatibility -----------------


def _make_historical_order(
    sessions: sessionmaker[Session],
    organizations,
    catalog,
    *,
    provider_id: uuid.UUID,
    patient_id: uuid.UUID,
    practitioner_id: uuid.UUID,
    idempotency_key: str,
    catalog_entry_id: uuid.UUID,
):
    """Constructs an order shaped exactly like a pre-DH-CLINIC-003C `LabOrder`: created directly
    through the repository, never through `order_intents`, so `intent_id` is `NULL` -- precisely
    the historical state that predates durable intent ownership."""
    from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput

    orders_repo = LabOrderRepository(sessions, organizations, catalog)
    result = orders_repo.create(
        idempotency_key=idempotency_key,
        provider_organization_id=provider_id,
        patient_reference_id=patient_id,
        practitioner_reference_id=practitioner_id,
        items=[OrderItemInput(catalog_entry_id=catalog_entry_id)],
    )
    assert result.created is True
    assert result.order.intent_id is None
    return result.order


def test_legacy_exact_replay_of_a_historical_intentless_order_returns_200_no_new_intent(
    client: TestClient, keypair, organizations, catalog, patients, sessions: sessionmaker[Session]
) -> None:
    """DH-CLINIC-003C historical compatibility (final-advisor-review BLOCKER 3).

    Before durable order intents existed, the legacy `/orders` endpoint's own idempotency
    contract was: same provider + same key + exact canonical content -> return the existing
    order, 200, no duplicate. A pre-003C `LabOrder` legitimately has `intent_id IS NULL` and is
    never backfilled. An exact retry of such an order must still replay idempotently -- it must
    never become a 409 just because 003C's intent-reservation machinery didn't create it.
    """
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "HistReplay")
    patient_id = _create_patient(client, private_key, patients, provider.id, "HistReplay")
    test_ids = _make_tests(catalog, 1)
    key = _new_key()

    # Establish this clinician's practitioner_reference_id exactly as the endpoint would derive
    # it, via a throwaway order under an unrelated key.
    throwaway = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(test_ids[0])],
        },
    )
    assert throwaway.status_code == 201, throwaway.text
    practitioner_id = uuid.UUID(throwaway.json()["practitioner_reference_id"])

    historical = _make_historical_order(
        sessions,
        organizations,
        catalog,
        provider_id=provider.id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        idempotency_key=key,
        catalog_entry_id=test_ids[0],
    )

    audits_before = _audit_events(sessions, "lab_order_created")

    # Exact canonical retry of the historical order.
    r_replay = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(test_ids[0])],
        },
    )

    assert r_replay.status_code == 200, r_replay.text
    assert r_replay.json()["id"] == str(historical.id)

    with sessions() as session:
        intents = session.scalars(
            select(LabOrderIntent).where(
                LabOrderIntent.provider_organization_id == provider.id,
                LabOrderIntent.idempotency_key == key,
            )
        ).all()
        # Zero new durable intents for that key -- the historical order stays unowned forever.
        assert len(intents) == 0

        order = session.get(LabOrder, historical.id)
        assert order is not None
        assert order.intent_id is None

    # No duplicate audit event for this key.
    audits_after = _audit_events(sessions, "lab_order_created")
    assert len(audits_after) == len(audits_before)


def test_legacy_mismatched_retry_of_a_historical_intentless_order_returns_409_unclaimed(
    client: TestClient, keypair, organizations, catalog, patients, sessions: sessionmaker[Session]
) -> None:
    """The mismatch counterpart: a changed patient/service under the same key as a historical
    order must conflict (409), exactly as it did before 003C, and must never retroactively claim
    the historical row into a new intent."""
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "HistMismatch")
    patient_a = _create_patient(client, private_key, patients, provider.id, "HistMismatch-A")
    patient_b = _create_patient(client, private_key, patients, provider.id, "HistMismatch-B")
    test_ids = _make_tests(catalog, 1)
    key = _new_key()

    throwaway = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_a),
            "catalog_entry_ids": [str(test_ids[0])],
        },
    )
    assert throwaway.status_code == 201, throwaway.text
    practitioner_id = uuid.UUID(throwaway.json()["practitioner_reference_id"])

    historical = _make_historical_order(
        sessions,
        organizations,
        catalog,
        provider_id=provider.id,
        patient_id=patient_a,
        practitioner_id=practitioner_id,
        idempotency_key=key,
        catalog_entry_id=test_ids[0],
    )

    r_mismatch = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_b),
            "catalog_entry_ids": [str(test_ids[0])],
        },
    )
    assert r_mismatch.status_code == 409, r_mismatch.text

    with sessions() as session:
        order = session.get(LabOrder, historical.id)
        assert order is not None
        # Historical row unchanged, still unowned.
        assert order.intent_id is None
        assert order.patient_reference_id == patient_a

        intents = session.scalars(
            select(LabOrderIntent).where(
                LabOrderIntent.provider_organization_id == provider.id,
                LabOrderIntent.idempotency_key == key,
            )
        ).all()
        assert len(intents) == 0
