"""DH-CLINIC-003C: HTTP API tests for backend-owned LabOrderIntent and commit endpoints."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import ExchangeSecurityAuditEvent
from daher_adapter.exchange.organizations import OrganizationRepository


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
    provider = make_provider(organizations, name=name)
    identity_id = _bootstrap_identity(client, private_key, f"clinician-{name}")
    _grant_roles(
        client, private_key, provider.id, identity_id, f"clinician-{name}", roles or ["CLINICIAN"]
    )
    token = make_token(private_key, subject=f"clinician-{name}")
    return provider, token, identity_id


def _create_patient(
    patients: PatientReferenceRepository, provider_id: uuid.UUID, name: str
) -> uuid.UUID:
    record = patients.get_or_create(
        source_organization_id=provider_id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-PATIENT-{name}",
        display_name=f"Patient {name}",
    )
    return record.id


def _make_tests(catalog: CatalogRepository, count: int = 2) -> list[uuid.UUID]:
    ids = []
    for i in range(count):
        entry, _ = catalog.create_entry(
            code=f"DHEALTH-INTENT-DEMO-{i}", display_name=f"Demo Intent Test {i}"
        )
        ids.append(entry.id)
    return ids


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


def test_clinician_can_create_order_intent(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "A")
    patient_id = _create_patient(patients, provider.id, "A")
    test_ids = _make_tests(catalog, 2)
    key = str(uuid.uuid4())

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["idempotency_key"] == key
    assert data["provider_organization_id"] == str(provider.id)
    assert data["patient_reference_id"] == str(patient_id)
    assert data["committed_lab_order_id"] is None
    assert len(data["items"]) == 2
    for item in data["items"]:
        assert item["catalog_code_snapshot"].startswith("DHEALTH-INTENT-DEMO-")
        assert item["catalog_display_name_snapshot"].startswith("Demo Intent Test ")


def test_order_intent_replay_exact_returns_200(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "B")
    patient_id = _create_patient(patients, provider.id, "B")
    test_ids = _make_tests(catalog, 2)
    key = str(uuid.uuid4())

    payload = {
        "patient_reference_id": str(patient_id),
        "catalog_entry_ids": [str(t) for t in test_ids],
    }
    r1 = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": key},
        json=payload,
    )
    assert r1.status_code == 201

    r2 = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": key},
        json=payload,
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]


def test_order_intent_replay_conflict_returns_409(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "C")
    patient_id = _create_patient(patients, provider.id, "C")
    test_ids = _make_tests(catalog, 2)
    key = str(uuid.uuid4())

    r1 = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert r1.status_code == 201

    # Different catalog entry
    r2 = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(test_ids[0])],
        },
    )
    assert r2.status_code == 409


def test_order_intent_requires_idempotency_key_header(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "D")
    patient_id = _create_patient(patients, provider.id, "D")
    test_ids = _make_tests(catalog, 1)

    r = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers=_auth(token),
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert r.status_code == 422


def test_order_intent_anti_enumeration_refusal(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider1, token1, _ = _setup_clinician(client, private_key, organizations, "E1")
    provider2 = make_provider(organizations, name="E2")
    patient2_id = _create_patient(patients, provider2.id, "E2")
    test_ids = _make_tests(catalog, 1)
    key = str(uuid.uuid4())

    # Patient belongs to provider 2, clinician is at provider 1 -> must 404 anti-enumeration
    r = client.post(
        f"/exchange/v1/organizations/{provider1.id}/order-intents",
        headers={**_auth(token1), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient2_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert r.status_code == 404


def test_order_intent_non_clinician_forbidden(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(
        client, private_key, organizations, "F", roles=["ORG_ADMIN"]
    )
    patient_id = _create_patient(patients, provider.id, "F")
    test_ids = _make_tests(catalog, 1)
    key = str(uuid.uuid4())

    r = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert r.status_code == 403


def test_order_intent_get_by_id_and_key(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "G")
    patient_id = _create_patient(patients, provider.id, "G")
    test_ids = _make_tests(catalog, 2)
    key = str(uuid.uuid4())

    r_create = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert r_create.status_code == 201
    intent_id = r_create.json()["id"]

    # GET by id
    r_id = client.get(
        f"/exchange/v1/organizations/{provider.id}/order-intents/{intent_id}",
        headers=_auth(token),
    )
    assert r_id.status_code == 200
    assert r_id.json()["id"] == intent_id

    # GET by key
    r_key = client.get(
        f"/exchange/v1/organizations/{provider.id}/order-intents/by-idempotency-key/{key}",
        headers=_auth(token),
    )
    assert r_key.status_code == 200
    assert r_key.json()["id"] == intent_id

    # Non-existent key
    r_missing_key = client.get(
        f"/exchange/v1/organizations/{provider.id}/order-intents/by-idempotency-key/non-existent",
        headers=_auth(token),
    )
    assert r_missing_key.status_code == 404

    # Non-existent ID
    r_missing_id = client.get(
        f"/exchange/v1/organizations/{provider.id}/order-intents/{uuid.uuid4()}",
        headers=_auth(token),
    )
    assert r_missing_id.status_code == 404


def test_order_intent_commit_creates_order_and_audits(
    client: TestClient, keypair, organizations, catalog, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "H")
    patient_id = _create_patient(patients, provider.id, "H")
    test_ids = _make_tests(catalog, 2)
    key = str(uuid.uuid4())

    r_create = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert r_create.status_code == 201
    intent_id = r_create.json()["id"]

    # Commit
    r_commit = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents/{intent_id}/commit",
        headers=_auth(token),
    )
    assert r_commit.status_code == 201, r_commit.text
    order_data = r_commit.json()
    assert order_data["patient_reference_id"] == str(patient_id)
    assert order_data["status"] == "AWAITING_LAB_SELECTION"
    order_id = order_data["id"]

    # Intent now reflects committed order ID
    r_get_intent = client.get(
        f"/exchange/v1/organizations/{provider.id}/order-intents/{intent_id}",
        headers=_auth(token),
    )
    assert r_get_intent.json()["committed_lab_order_id"] == order_id

    # Audit event emitted
    audits = _audit_events(sessions, "lab_order_created")
    assert any(a["resource_id"] == order_id for a in audits)

    # Commit replay returns 200 OK with same order
    r_replay = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents/{intent_id}/commit",
        headers=_auth(token),
    )
    assert r_replay.status_code == 200
    assert r_replay.json()["id"] == order_id


def test_order_intent_commit_by_different_clinician_refused_403(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token_a, _ = _setup_clinician(client, private_key, organizations, "I_A")
    # Clinician B in same provider
    identity_b_id = _bootstrap_identity(client, private_key, "clinician-I_B")
    _grant_roles(client, private_key, provider.id, identity_b_id, "clinician-I_B", ["CLINICIAN"])
    token_b = make_token(private_key, subject="clinician-I_B")

    patient_id = _create_patient(patients, provider.id, "I")
    test_ids = _make_tests(catalog, 1)
    key = str(uuid.uuid4())

    r_create = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token_a), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert r_create.status_code == 201
    intent_id = r_create.json()["id"]

    # Clinician B attempts commit -> 403 Forbidden
    r_commit = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents/{intent_id}/commit",
        headers=_auth(token_b),
    )
    assert r_commit.status_code == 403
    assert "authored this intent" in r_commit.json()["detail"].lower()


def test_order_intent_commit_cross_provider_refused_404(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider1, token1, _ = _setup_clinician(client, private_key, organizations, "J1")
    provider2, token2, _ = _setup_clinician(client, private_key, organizations, "J2")

    patient_id = _create_patient(patients, provider1.id, "J1")
    test_ids = _make_tests(catalog, 1)
    key = str(uuid.uuid4())

    r_create = client.post(
        f"/exchange/v1/organizations/{provider1.id}/order-intents",
        headers={**_auth(token1), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert r_create.status_code == 201
    intent_id = r_create.json()["id"]

    # Provider 2 attempts commit on Provider 1's intent -> 404 Not Found
    r_commit = client.post(
        f"/exchange/v1/organizations/{provider2.id}/order-intents/{intent_id}/commit",
        headers=_auth(token2),
    )
    assert r_commit.status_code == 404


# -- DH-CLINIC-003C negative-control coverage closure: NC12 permanent regression ------------------
#
# The commit endpoint's ownership check was already covered (403, above). The negative-control
# audit found the GET-retrieval ownership check -- same provider, a *different* clinician -- had
# zero coverage: only cross-provider anti-enumeration and same-clinician positive control existed.


def test_order_intent_get_by_id_different_clinician_same_provider_anti_enumerating_404(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token_a, _ = _setup_clinician(client, private_key, organizations, "NC12_A")
    identity_b_id = _bootstrap_identity(client, private_key, "clinician-NC12_B")
    _grant_roles(client, private_key, provider.id, identity_b_id, "clinician-NC12_B", ["CLINICIAN"])
    token_b = make_token(private_key, subject="clinician-NC12_B")

    patient_id = _create_patient(patients, provider.id, "NC12")
    test_ids = _make_tests(catalog, 1)
    key = str(uuid.uuid4())

    r_create = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token_a), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert r_create.status_code == 201, r_create.text
    intent_id = r_create.json()["id"]

    # Author (clinician A) can retrieve it normally.
    r_get_a = client.get(
        f"/exchange/v1/organizations/{provider.id}/order-intents/{intent_id}",
        headers=_auth(token_a),
    )
    assert r_get_a.status_code == 200

    # A different clinician at the SAME provider must get an anti-enumerating 404, not 403 --
    # the response must not disclose that an intent with this id exists at all.
    r_get_b = client.get(
        f"/exchange/v1/organizations/{provider.id}/order-intents/{intent_id}",
        headers=_auth(token_b),
    )
    assert r_get_b.status_code == 404
    assert r_get_b.json()["detail"] == "order intent not found"


def test_order_intent_get_by_idempotency_key_different_clinician_same_provider_anti_enumerating_404(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token_a, _ = _setup_clinician(client, private_key, organizations, "NC12K_A")
    identity_b_id = _bootstrap_identity(client, private_key, "clinician-NC12K_B")
    _grant_roles(
        client, private_key, provider.id, identity_b_id, "clinician-NC12K_B", ["CLINICIAN"]
    )
    token_b = make_token(private_key, subject="clinician-NC12K_B")

    patient_id = _create_patient(patients, provider.id, "NC12K")
    test_ids = _make_tests(catalog, 1)
    key = str(uuid.uuid4())

    r_create = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token_a), "Idempotency-Key": key},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(t) for t in test_ids],
        },
    )
    assert r_create.status_code == 201, r_create.text

    r_get_a = client.get(
        f"/exchange/v1/organizations/{provider.id}/order-intents/by-idempotency-key/{key}",
        headers=_auth(token_a),
    )
    assert r_get_a.status_code == 200

    r_get_b = client.get(
        f"/exchange/v1/organizations/{provider.id}/order-intents/by-idempotency-key/{key}",
        headers=_auth(token_b),
    )
    assert r_get_b.status_code == 404
    assert r_get_b.json()["detail"] == "order intent not found"


# -- DH-CLINIC-003C negative-control coverage closure: NC17 permanent regression ------------------
#
# The legacy `/orders` endpoint already has
# `test_request_body_cannot_supply_practitioner_reference_id` proving `extra="forbid"` rejects a
# spoofed practitioner. The negative-control audit found no mirrored test for `/order-intents`.


def test_order_intent_request_body_cannot_supply_practitioner_reference_id(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "NC17")
    patient_id = _create_patient(patients, provider.id, "NC17")
    test_ids = _make_tests(catalog, 1)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": str(uuid.uuid4())},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(test_ids[0])],
            "practitioner_reference_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422  # extra="forbid"

    # No durable intent was created by the rejected request.
    r_lookup = client.get(
        f"/exchange/v1/organizations/{provider.id}/order-intents"
        f"/by-idempotency-key/{response.request.headers['Idempotency-Key']}",
        headers=_auth(token),
    )
    assert r_lookup.status_code == 404


def test_order_intent_practitioner_is_always_derived_from_authenticated_clinician(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    """Positive-proof counterpart to the spoof-rejection test above: the browser/caller never
    chooses practitioner ownership -- it is derived once per (clinician, provider) and stays
    identical across separate intents from the same clinician."""
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "NC17D")
    patient_id = _create_patient(patients, provider.id, "NC17D")
    test_ids = _make_tests(catalog, 2)

    first = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": str(uuid.uuid4())},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(test_ids[0])],
        },
    )
    assert first.status_code == 201, first.text

    second = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": str(uuid.uuid4())},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(test_ids[1])],
        },
    )
    assert second.status_code == 201, second.text

    assert first.json()["practitioner_reference_id"] == second.json()["practitioner_reference_id"]
    assert first.json()["practitioner_reference_id"] is not None


# -- DH-CLINIC-003C negative-control coverage closure: NC13/NC14 permanent adversarial proof -------
#
# NC13/NC14 were marked "N/A by design" in the audit because `commit_order_intent` declares no
# request body. This test proves that observation empirically rather than relying on structural
# inspection alone: attempting to smuggle a different patient/practitioner/service through the
# commit request must have zero effect on the resulting LabOrder.


def test_order_intent_commit_ignores_extraneous_body_and_uses_only_durable_intent_content(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, token, _ = _setup_clinician(client, private_key, organizations, "NC13_14")
    patient_a = _create_patient(patients, provider.id, "NC13_14_A")
    patient_b = _create_patient(patients, provider.id, "NC13_14_B")
    test_ids = _make_tests(catalog, 2)
    service_1, service_2 = test_ids

    r_create = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents",
        headers={**_auth(token), "Idempotency-Key": str(uuid.uuid4())},
        json={
            "patient_reference_id": str(patient_a),
            "catalog_entry_ids": [str(service_1)],
        },
    )
    assert r_create.status_code == 201, r_create.text
    intent_id = r_create.json()["id"]
    original_practitioner_id = r_create.json()["practitioner_reference_id"]

    # Adversarial commit: attempt to smuggle a different patient, practitioner, and service
    # through the request body even though the route declares no body parameter at all.
    r_commit = client.post(
        f"/exchange/v1/organizations/{provider.id}/order-intents/{intent_id}/commit",
        headers=_auth(token),
        json={
            "patient_reference_id": str(patient_b),
            "practitioner_reference_id": str(uuid.uuid4()),
            "catalog_entry_ids": [str(service_2)],
        },
    )
    # The endpoint accepts (or would reject cleanly); either way the resulting order must
    # reflect ONLY the durable intent's frozen content, never the smuggled body.
    assert r_commit.status_code in (200, 201, 403, 404, 409, 422), r_commit.text
    if r_commit.status_code in (200, 201):
        order = r_commit.json()
        assert order["patient_reference_id"] == str(patient_a)
        assert order["practitioner_reference_id"] == original_practitioner_id
        assert order["items"][0]["catalog_entry_id"] == str(service_1)
        assert len(order["items"]) == 1
