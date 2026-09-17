"""DH-IDENTITY-001: the HTTP-level required matrix for the native, provider-scoped Patient
directory -- creation authorization, server-owned identity, idempotent replay/conflict semantics,
directory/search tenancy, and the explicit proof that demographics are never an identity key.

Same harness pattern as ``test_exchange_patient_references_api.py``/``test_exchange_orders_api.py``:
a bare ``FastAPI()`` app with ``build_exchange_router`` mounted directly, a
``StaticKeySource``-backed ``TokenValidator`` (local RSA keypair, zero network), synthetic
``@example.invalid``-shaped identities only.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    PatientReference,
    PatientRegistrationRequest,
)
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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _new_key() -> str:
    return str(uuid.uuid4())


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


def _setup_member(
    client: TestClient, private_key, organizations: OrganizationRepository, name: str, *, roles
):
    """Returns (org, token) -- a fresh PROVIDER org with the given role(s) granted."""
    org = make_provider(organizations, name=name)
    identity_id = _bootstrap_identity(client, private_key, f"member-{name}")
    _grant_roles(client, private_key, org.id, identity_id, f"member-{name}", roles)
    token = make_token(private_key, subject=f"member-{name}")
    return org, token


def _setup_clinician(
    client: TestClient, private_key, organizations: OrganizationRepository, name: str
):
    return _setup_member(client, private_key, organizations, name, roles=["CLINICIAN"])


def _create(client: TestClient, token: str, provider_id, *, key: str | None = None, **overrides):
    body = {"display_name": "Synthetic Patient"}
    body.update(overrides)
    return client.post(
        f"/exchange/v1/organizations/{provider_id}/patients",
        headers={**_auth(token), "Idempotency-Key": key or _new_key()},
        json=body,
    )


def _audit_events(sessions: sessionmaker[Session]) -> list[dict]:
    with sessions() as session:
        rows = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "patient_created"
            )
        ).all()
        return [{"outcome": row.outcome, "resource_id": row.resource_id} for row in rows]


# -- P1-P12: native creation ------------------------------------------------------------------


def test_p1_authorized_clinician_creates_native_patient(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "P1Clinic")
    response = _create(client, token, provider.id, display_name="Amina Hassan")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["display_name"] == "Amina Hassan"
    assert body["provider_organization_id"] == str(provider.id)


def test_p2_server_creates_native_patient_reference_automatically(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "P2Clinic")
    response = _create(client, token, provider.id)
    assert response.status_code == 201, response.text
    assert response.json()["primary_patient_reference_id"]


def test_p3_browser_cannot_supply_authoritative_patient_id(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "P3Clinic")
    forced_id = str(uuid.uuid4())
    response = _create(client, token, provider.id, patient_id=forced_id)
    assert response.status_code == 422, response.text


def test_p4_browser_cannot_supply_source_identity(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "P4Clinic")
    response = _create(
        client,
        token,
        provider.id,
        source_system="attacker-supplied",
        source_identity_value="X",
    )
    assert response.status_code == 422, response.text


def test_p5_native_reference_uses_server_owned_namespace(
    client: TestClient, keypair, organizations, sessions: sessionmaker[Session]
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "P5Clinic")
    response = _create(client, token, provider.id)
    assert response.status_code == 201, response.text
    references = PatientReferenceRepository(sessions)
    record = references.get(uuid.UUID(response.json()["primary_patient_reference_id"]))
    assert record.source_system == "dhealth"
    assert record.source_identity_system == "urn:dhealth:patient-id"
    assert record.source_identity_value == response.json()["patient_id"]


def test_p6_patient_and_reference_provider_ownership_match(
    client: TestClient, keypair, organizations, sessions: sessionmaker[Session]
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "P6Clinic")
    response = _create(client, token, provider.id)
    assert response.status_code == 201, response.text
    references = PatientReferenceRepository(sessions)
    record = references.get(uuid.UUID(response.json()["primary_patient_reference_id"]))
    assert str(record.source_organization_id) == str(provider.id)


def test_p7_display_name_required_for_native_creation(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "P7Clinic")
    response = _create(client, token, provider.id, display_name="   ")
    assert response.status_code == 422, response.text
    response_missing = client.post(
        f"/exchange/v1/organizations/{provider.id}/patients",
        headers={**_auth(token), "Idempotency-Key": _new_key()},
        json={},
    )
    assert response_missing.status_code == 422, response_missing.text


def test_p8_date_of_birth_optional(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "P8Clinic")
    without_dob = _create(client, token, provider.id)
    assert without_dob.status_code == 201, without_dob.text
    assert without_dob.json()["date_of_birth"] is None

    with_dob = _create(client, token, provider.id, date_of_birth="1990-05-14")
    assert with_dob.status_code == 201, with_dob.text
    assert with_dob.json()["date_of_birth"] == "1990-05-14"


def test_p9_contact_optional(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "P9Clinic")
    response = _create(client, token, provider.id)
    assert response.status_code == 201, response.text
    assert response.json()["contact_channel_type"] is None
    assert response.json()["contact_value"] is None


def test_p10_contact_pair_validation(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "P10Clinic")
    only_type = _create(client, token, provider.id, contact_channel_type="PHONE")
    assert only_type.status_code == 422, only_type.text
    only_value = _create(client, token, provider.id, contact_value="+25377000000")
    assert only_value.status_code == 422, only_value.text
    both = _create(
        client,
        token,
        provider.id,
        contact_channel_type="PHONE",
        contact_value="+25377000000",
    )
    assert both.status_code == 201, both.text


def test_p11_lab_user_cannot_create_provider_patient(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    lab = make_lab(organizations, name="P11Lab")
    identity_id = _bootstrap_identity(client, private_key, "p11-lab-tech")
    _grant_roles(client, private_key, lab.id, identity_id, "p11-lab-tech", ["LAB_TECH"])
    token = make_token(private_key, subject="p11-lab-tech")
    # A LAB_TECH membership at the LAB organization itself -- CLINICIAN is not even an assignable
    # role for a LAB org (membership.py's ALLOWED_ROLES_BY_ORG_TYPE), so creating a patient
    # "at" this LAB organization must be refused regardless of which specific membership state
    # produces the refusal.
    response = _create(client, token, lab.id)
    assert response.status_code in (403, 404), response.text


def test_p12_unassigned_identity_cannot_create(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations, name="P12Clinic")
    token = make_token(private_key, subject="unassigned-p12")
    response = _create(client, token, provider.id)
    assert response.status_code == 404, response.text


# -- I1-I8: idempotency ------------------------------------------------------------------------


def test_i1_missing_idempotency_key_rejected(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "I1Clinic")
    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/patients",
        headers=_auth(token),
        json={"display_name": "No Key"},
    )
    assert response.status_code == 422, response.text


def test_i2_first_submission_is_201(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "I2Clinic")
    response = _create(client, token, provider.id)
    assert response.status_code == 201, response.text


def test_i3_exact_replay_returns_same_patient_id(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "I3Clinic")
    key = _new_key()
    first = _create(client, token, provider.id, key=key, display_name="Fatouma Ali")
    assert first.status_code == 201, first.text
    second = _create(client, token, provider.id, key=key, display_name="Fatouma Ali")
    assert second.status_code == 200, second.text
    assert second.json()["patient_id"] == first.json()["patient_id"]


def test_i4_exact_replay_returns_same_patient_reference_id(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "I4Clinic")
    key = _new_key()
    first = _create(client, token, provider.id, key=key)
    second = _create(client, token, provider.id, key=key)
    assert (
        first.json()["primary_patient_reference_id"]
        == second.json()["primary_patient_reference_id"]
    )


def test_i5_conflicting_payload_same_key_is_409(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "I5Clinic")
    key = _new_key()
    first = _create(client, token, provider.id, key=key, display_name="Original Name")
    assert first.status_code == 201, first.text
    second = _create(client, token, provider.id, key=key, display_name="Different Name")
    assert second.status_code == 409, second.text


def test_i6_same_demographics_new_key_creates_second_patient(
    client: TestClient, keypair, organizations
) -> None:
    """Proves demographics are NOT an identity key -- the central DH-IDENTITY-001 invariant."""
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "I6Clinic")
    first = _create(
        client,
        token,
        provider.id,
        display_name="Mohamed Ali",
        date_of_birth="1990-01-01",
        contact_channel_type="PHONE",
        contact_value="+25377000099",
    )
    second = _create(
        client,
        token,
        provider.id,
        display_name="Mohamed Ali",
        date_of_birth="1990-01-01",
        contact_channel_type="PHONE",
        contact_value="+25377000099",
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["patient_id"] != second.json()["patient_id"]


def test_i7_same_key_different_providers_independent(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider_a, token_a = _setup_clinician(client, private_key, organizations, "I7ClinicA")
    provider_b, token_b = _setup_clinician(client, private_key, organizations, "I7ClinicB")
    key = _new_key()
    from_a = _create(client, token_a, provider_a.id, key=key, display_name="Provider A Patient")
    from_b = _create(client, token_b, provider_b.id, key=key, display_name="Provider B Patient")
    assert from_a.status_code == 201, from_a.text
    assert from_b.status_code == 201, from_b.text
    assert from_a.json()["patient_id"] != from_b.json()["patient_id"]


def test_i8_registration_fingerprint_survives_replay(
    client: TestClient, keypair, organizations, sessions: sessionmaker[Session]
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "I8Clinic")
    key = _new_key()
    _create(client, token, provider.id, key=key, display_name="Durable Fingerprint")
    _create(client, token, provider.id, key=key, display_name="Durable Fingerprint")
    with sessions() as session:
        rows = session.scalars(
            select(PatientRegistrationRequest).where(
                PatientRegistrationRequest.provider_organization_id == provider.id,
                PatientRegistrationRequest.idempotency_key == key,
            )
        ).all()
    assert len(rows) == 1, "a replay must never create a second registration-request row"


# -- D1-D11: directory / tenancy -----------------------------------------------------------------


def test_d1_provider_lists_own_patients(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "D1Clinic")
    _create(client, token, provider.id, display_name="Listed Patient")
    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/patients", headers=_auth(token)
    )
    assert response.status_code == 200, response.text
    assert any(p["display_name"] == "Listed Patient" for p in response.json())


def test_d2_provider_cannot_list_another_provider(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider_a, token_a = _setup_clinician(client, private_key, organizations, "D2ClinicA")
    provider_b, _token_b = _setup_clinician(client, private_key, organizations, "D2ClinicB")
    response = client.get(
        f"/exchange/v1/organizations/{provider_b.id}/patients", headers=_auth(token_a)
    )
    assert response.status_code == 404, response.text


def test_d3_provider_a_cannot_get_provider_b_patient(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider_a, token_a = _setup_clinician(client, private_key, organizations, "D3ClinicA")
    provider_b, token_b = _setup_clinician(client, private_key, organizations, "D3ClinicB")
    created = _create(client, token_b, provider_b.id)
    patient_id = created.json()["patient_id"]
    response = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/patients/{patient_id}",
        headers=_auth(token_a),
    )
    assert response.status_code == 404, response.text


def test_d4_cross_provider_lookup_anti_enumerates_404(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider_a, token_a = _setup_clinician(client, private_key, organizations, "D4ClinicA")
    provider_b, token_b = _setup_clinician(client, private_key, organizations, "D4ClinicB")
    created = _create(client, token_b, provider_b.id)
    patient_id = created.json()["patient_id"]
    nonexistent = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/patients/{uuid.uuid4()}",
        headers=_auth(token_a),
    )
    real_but_foreign = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/patients/{patient_id}",
        headers=_auth(token_a),
    )
    assert nonexistent.status_code == real_but_foreign.status_code == 404


def test_d5_search_matches_name(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "D5Clinic")
    _create(client, token, provider.id, display_name="Hodan Ibrahim")
    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/patients/search",
        headers=_auth(token),
        json={"query": "Hodan"},
    )
    assert response.status_code == 200, response.text
    assert any(p["display_name"] == "Hodan Ibrahim" for p in response.json())


def test_d6_search_matches_contact(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "D6Clinic")
    _create(
        client,
        token,
        provider.id,
        display_name="Contact Match",
        contact_channel_type="PHONE",
        contact_value="+25377001234",
    )
    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/patients/search",
        headers=_auth(token),
        json={"query": "77001234"},
    )
    assert response.status_code == 200, response.text
    assert any(p["display_name"] == "Contact Match" for p in response.json())


def test_d7_search_matches_linked_source_identity(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "D7Clinic")
    created = _create(client, token, provider.id, display_name="Reference Match")
    # The native reference's own source_identity_value is the Patient's own id (DH-IDENTITY-001
    # §13's urn:dhealth:patient-id namespace) -- search a prefix of *that*, not the reference row's
    # own unrelated id, to exercise the PatientReference.source_identity_value match path.
    patient_id = created.json()["patient_id"]
    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/patients/search",
        headers=_auth(token),
        json={"query": patient_id[:8]},
    )
    assert response.status_code == 200, response.text
    assert any(p["patient_id"] == patient_id for p in response.json())


def test_d8_multiple_references_do_not_duplicate_directory_result(
    client: TestClient, keypair, organizations, sessions: sessionmaker[Session]
) -> None:
    """No link/merge API exists in this ticket -- constructed directly at repository level to
    prove the search query itself cannot produce a duplicate row, per §21's own instruction."""
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "D8Clinic")
    created = _create(client, token, provider.id, display_name="Multi Reference Patient")
    patient_id = uuid.UUID(created.json()["patient_id"])

    with sessions.begin() as session:
        session.add(
            PatientReference(
                source_organization_id=provider.id,
                patient_id=patient_id,
                source_system="second-source",
                source_identity_system="urn:second:patient",
                source_identity_value="Multi Reference Patient",
            )
        )

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/patients/search",
        headers=_auth(token),
        json={"query": "Multi Reference Patient"},
    )
    assert response.status_code == 200, response.text
    matches = [p for p in response.json() if p["patient_id"] == str(patient_id)]
    assert len(matches) == 1, (
        f"expected exactly one row for a multi-reference Patient, got {matches!r}"
    )


def test_d9_search_escapes_like_wildcards(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "D9Clinic")
    _create(client, token, provider.id, display_name="Test%Percent")
    _create(client, token, provider.id, display_name="TestXPercent")
    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/patients/search",
        headers=_auth(token),
        json={"query": "%Percent"},
    )
    names = {p["display_name"] for p in response.json()}
    assert "Test%Percent" in names
    assert "TestXPercent" not in names


def test_d10_list_limit_bounded(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "D10Clinic")
    for i in range(5):
        _create(client, token, provider.id, display_name=f"Bounded {i}")
    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/patients?limit=2", headers=_auth(token)
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 2


def test_d11_list_ordering_stable(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "D11Clinic")
    _create(client, token, provider.id, display_name="First")
    _create(client, token, provider.id, display_name="Second")
    first_call = client.get(
        f"/exchange/v1/organizations/{provider.id}/patients", headers=_auth(token)
    ).json()
    second_call = client.get(
        f"/exchange/v1/organizations/{provider.id}/patients", headers=_auth(token)
    ).json()
    assert [p["patient_id"] for p in first_call] == [p["patient_id"] for p in second_call]


# -- Registration recovery (§19) -----------------------------------------------------------------


def test_registration_recovery_resolves_committed_registration(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "RecoveryClinic")
    key = _new_key()
    created = _create(client, token, provider.id, key=key, display_name="Recoverable Patient")
    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-registrations/{key}",
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["patient_id"] == created.json()["patient_id"]


def test_registration_recovery_unknown_key_is_404(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "RecoveryMissingClinic")
    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-registrations/{_new_key()}",
        headers=_auth(token),
    )
    assert response.status_code == 404, response.text


def test_registration_recovery_cross_provider_anti_enumerates(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider_a, token_a = _setup_clinician(client, private_key, organizations, "RecoveryClinicA")
    provider_b, token_b = _setup_clinician(client, private_key, organizations, "RecoveryClinicB")
    key = _new_key()
    _create(client, token_b, provider_b.id, key=key)
    response = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/patient-registrations/{key}",
        headers=_auth(token_a),
    )
    assert response.status_code == 404, response.text


# -- Audit ------------------------------------------------------------------------------------


def test_audit_records_patient_created_once_per_real_creation(
    client: TestClient, keypair, organizations, sessions: sessionmaker[Session]
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "AuditClinic")
    key = _new_key()
    _create(client, token, provider.id, key=key)
    _create(client, token, provider.id, key=key)  # exact replay must not re-audit
    events = _audit_events(sessions)
    assert len(events) == 1
    assert events[0]["outcome"] == "created"
