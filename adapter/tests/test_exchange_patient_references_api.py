"""DH-EX-009: the HTTP-level required matrix for provider-scoped patient-reference binding --
clinician-only authorization, forged-organization-id rejection, exact-identity idempotent binding,
organization-scoped uniqueness, representation validation at the API boundary, no silent snapshot
rewrite, and transactional binding audit.

Same harness pattern as ``test_exchange_orders_api.py``: a bare ``FastAPI()`` app with
``build_exchange_router`` mounted directly, a ``StaticKeySource``-backed ``TokenValidator`` (local
RSA keypair, zero network).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
from daher_adapter.exchange.models import ExchangeSecurityAuditEvent, Patient, PatientReference
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


def _bind(client: TestClient, token: str, provider_id, **overrides):
    body = {
        "source_system": "dhealth_provider_portal",
        "source_identity_system": "urn:daher-health:provider-portal-patient-id",
        "source_identity_value": "SYNTH-PATIENT-1",
    }
    body.update(overrides)
    return client.post(
        f"/exchange/v1/organizations/{provider_id}/patient-references",
        headers=_auth(token),
        json=body,
    )


def _search(client: TestClient, token: str, provider_id, query: str, *, limit: int = 50):
    return client.post(
        f"/exchange/v1/organizations/{provider_id}/patient-references/search",
        headers=_auth(token),
        json={"query": query, "limit": limit},
    )


def _audit_events(sessions: sessionmaker[Session]) -> list[dict]:
    with sessions() as session:
        rows = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "patient_reference_bound"
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


def _reference_count(sessions: sessionmaker[Session]) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(PatientReference))


# -- positive control: new exact binding ----------------------------------------------------------


def test_clinician_binds_new_exact_source_identity(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    response = _bind(client, token, provider.id, display_name="Amina Hassan")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_organization_id"] == str(provider.id)
    assert body["source_system"] == "dhealth_provider_portal"
    assert body["source_identity_system"] == "urn:daher-health:provider-portal-patient-id"
    assert body["source_identity_value"] == "SYNTH-PATIENT-1"
    assert "display_name" not in body, "response must stay minimal -- no snapshot fields"

    assert _reference_count(sessions) == 1
    events = _audit_events(sessions)
    assert len(events) == 1
    assert events[0]["outcome"] == "created"
    assert events[0]["resource_type"] == "patient_reference"
    assert events[0]["resource_id"] == body["id"]
    assert events[0]["organization_id"] == str(provider.id)


# -- idempotent repeat -------------------------------------------------------------------------


def test_idempotent_repeat_returns_same_reference_and_no_duplicate(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    first = _bind(client, token, provider.id)
    second = _bind(client, token, provider.id)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert _reference_count(sessions) == 1

    events = _audit_events(sessions)
    assert [e["outcome"] for e in events] == ["created", "returned_existing"]


def test_repeat_with_changed_display_name_does_not_rewrite_existing_snapshot(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    first = _bind(
        client,
        token,
        provider.id,
        display_name="A",
        contact_channel_type="whatsapp",
        contact_value="+25377000001",
    )
    second = _bind(
        client,
        token,
        provider.id,
        display_name="B",
        contact_channel_type="sms",
        contact_value="+25377000002",
    )

    assert first.json()["id"] == second.json()["id"]
    with sessions() as session:
        reference = session.get(PatientReference, uuid.UUID(first.json()["id"]))
        assert reference.display_name == "A", (
            "a replay with different demographics must not silently rewrite the existing snapshot "
            "-- this endpoint binds identity, it does not update patient demographics"
        )
        assert reference.contact_channel_type == "whatsapp", (
            "contact fields are part of the same immutable snapshot as display_name"
        )
        assert reference.contact_value == "+25377000001"


# -- cross-organization collision -----------------------------------------------------------------


def test_cross_organization_same_raw_identity_yields_distinct_references(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider_a, token_a = _setup_clinician(client, private_key, organizations, "A")
    provider_b, token_b = _setup_clinician(client, private_key, organizations, "B")

    from_a = _bind(client, token_a, provider_a.id, source_identity_value="123")
    from_b = _bind(client, token_b, provider_b.id, source_identity_value="123")

    assert from_a.status_code == 201, from_a.text
    assert from_b.status_code == 201, from_b.text
    assert from_a.json()["id"] != from_b.json()["id"], (
        "Clinic A's local patient '123' must never be treated as Clinic B's local patient '123' "
        "-- the organization boundary is part of the identity"
    )
    assert _reference_count(sessions) == 2


# -- DH-IDENTITY-001 L1-L7: legacy binding compatibility under the new Patient model -------------


def test_l1_existing_exact_reference_returns_same_binding(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "L1")
    first = _bind(client, token, provider.id)
    second = _bind(client, token, provider.id)
    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]


def test_l2_existing_binding_keeps_its_patient(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "L2")
    first = _bind(client, token, provider.id)
    second = _bind(client, token, provider.id)
    with sessions() as session:
        first_ref = session.get(PatientReference, uuid.UUID(first.json()["id"]))
        second_ref = session.get(PatientReference, uuid.UUID(second.json()["id"]))
        assert first_ref.patient_id == second_ref.patient_id
        assert session.get(Patient, first_ref.patient_id) is not None


def test_l3_new_external_reference_creates_a_new_patient_1to1(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "L3")
    response = _bind(client, token, provider.id, display_name="Newly Bound Patient")
    assert response.status_code == 201, response.text
    with sessions() as session:
        reference = session.get(PatientReference, uuid.UUID(response.json()["id"]))
        assert reference.patient_id is not None
        patient = session.get(Patient, reference.patient_id)
        assert patient is not None
        assert patient.provider_organization_id == provider.id
        assert patient.display_name == "Newly Bound Patient"
        assert patient.date_of_birth is None, "legacy binding never sets a date of birth"


def test_l4_same_demographics_different_source_identity_do_not_auto_link(
    client: TestClient, keypair, organizations, sessions
) -> None:
    """The no-fuzzy-matching law applies to the legacy bind endpoint too -- two different exact
    source identities with byte-identical demographics must never be silently attached to the
    same Patient."""
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "L4")
    first = _bind(
        client,
        token,
        provider.id,
        source_identity_value="EXT-1",
        display_name="Mohamed Ali",
        contact_channel_type="PHONE",
        contact_value="+25377000000",
    )
    second = _bind(
        client,
        token,
        provider.id,
        source_identity_value="EXT-2",
        display_name="Mohamed Ali",
        contact_channel_type="PHONE",
        contact_value="+25377000000",
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    with sessions() as session:
        first_ref = session.get(PatientReference, uuid.UUID(first.json()["id"]))
        second_ref = session.get(PatientReference, uuid.UUID(second.json()["id"]))
        assert first_ref.patient_id != second_ref.patient_id, (
            "identical demographics under two different exact source identities must produce two "
            "different Patient rows, never an automatic link"
        )


def test_l5_cross_provider_source_identity_collision_remains_isolated(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider_a, token_a = _setup_clinician(client, private_key, organizations, "L5A")
    provider_b, token_b = _setup_clinician(client, private_key, organizations, "L5B")
    from_a = _bind(client, token_a, provider_a.id, source_identity_value="SAME-ID")
    from_b = _bind(client, token_b, provider_b.id, source_identity_value="SAME-ID")
    with sessions() as session:
        ref_a = session.get(PatientReference, uuid.UUID(from_a.json()["id"]))
        ref_b = session.get(PatientReference, uuid.UUID(from_b.json()["id"]))
        patient_a = session.get(Patient, ref_a.patient_id)
        patient_b = session.get(Patient, ref_b.patient_id)
        assert patient_a.id != patient_b.id
        assert patient_a.provider_organization_id == provider_a.id
        assert patient_b.provider_organization_id == provider_b.id


def test_l6_snapshot_replay_behavior_remains_unchanged(
    client: TestClient, keypair, organizations, sessions
) -> None:
    """Same assertion as `test_repeat_with_changed_display_name_does_not_rewrite_existing_snapshot`
    above, re-run explicitly under the DH-IDENTITY-001 name to keep this ticket's own required L6
    proof easy to find -- the underlying behavior was already correct and is unchanged."""
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "L6")
    first = _bind(client, token, provider.id, display_name="Original")
    second = _bind(client, token, provider.id, display_name="Replayed")
    assert first.json()["id"] == second.json()["id"]
    with sessions() as session:
        reference = session.get(PatientReference, uuid.UUID(first.json()["id"]))
        assert reference.display_name == "Original"


def test_l7_unpaired_contact_on_legacy_bind_is_422(
    client: TestClient, keypair, organizations
) -> None:
    """DH-IDENTITY-001: every new binding now also creates a linked ``Patient`` row, whose own
    contact-pairing CHECK constraint an unpaired contact would otherwise violate at the database
    layer -- this must surface as a clear 422 at the API boundary, not an opaque 500."""
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "L7")

    response = _bind(
        client,
        token,
        provider.id,
        contact_channel_type="whatsapp",
        contact_value=None,
    )

    assert response.status_code == 422, response.text


# -- authorization -----------------------------------------------------------------------------


def test_org_admin_without_clinician_role_denied(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    org, token = _setup_member(
        client, private_key, organizations, "OrgAdminOnly", roles=["ORG_ADMIN"]
    )

    response = _bind(client, token, org.id)

    assert response.status_code == 403, response.text


def test_lab_tech_cannot_bind_provider_patient(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    lab = make_lab(organizations, name="LabTechLab")
    identity_id = _bootstrap_identity(client, private_key, "lab-tech-1")
    _grant_roles(client, private_key, lab.id, identity_id, "lab-tech-1", ["LAB_TECH"])
    token = make_token(private_key, subject="lab-tech-1")

    response = _bind(client, token, lab.id)

    assert response.status_code == 403, response.text


def test_platform_admin_alone_denied(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations, name="AdminOnlyProvider")
    admin_token = make_token(private_key, subject="platform-admin-1", roles=[PLATFORM_ADMIN_ROLE])

    response = _bind(client, admin_token, provider.id)

    assert response.status_code == 403, response.text


def test_inactive_clinician_membership_denied(
    client: TestClient, keypair, organizations, sessions
) -> None:
    """Mirrors ``test_exchange_orders_api.py::test_inactive_clinician_membership_refused`` --
    ``find_active`` returns ``None`` for an INACTIVE membership, indistinguishable from no
    membership at all, so this must 404 the same way an unknown organization does."""
    from daher_adapter.exchange.models import OrganizationMembership

    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "InactiveMember")
    identity_id = _bootstrap_identity(client, private_key, "member-InactiveMember")
    with sessions() as session:
        membership = session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == provider.id,
                OrganizationMembership.identity_id == uuid.UUID(identity_id),
            )
        )
        membership.status = "INACTIVE"
        session.commit()

    response = _bind(client, token, provider.id)

    assert response.status_code == 404, response.text


def test_inactive_provider_organization_denied(
    client: TestClient, keypair, organizations, sessions
) -> None:
    """Mirrors ``test_exchange_orders_api.py::test_clinician_in_inactive_provider_refused`` --
    ``require_clinician`` refuses an INACTIVE organization before any membership lookup, so a
    clinician who was previously valid there gets 403, not 404."""
    from daher_adapter.exchange.models import Organization

    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "InactiveProvider")
    with sessions() as session:
        org_row = session.get(Organization, provider.id)
        org_row.status = "INACTIVE"
        session.commit()

    response = _bind(client, token, provider.id)

    assert response.status_code == 403, response.text


def test_cross_tenant_clinician_denied(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    _provider_a, token_a = _setup_clinician(client, private_key, organizations, "A")
    provider_b = make_provider(organizations, name="B-not-a-member")

    response = _bind(client, token_a, provider_b.id)

    assert response.status_code == 404, response.text


def test_unknown_organization_denied(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    _provider, token = _setup_clinician(client, private_key, organizations, "A")

    response = _bind(client, token, uuid.uuid4())

    assert response.status_code == 404, response.text


def test_source_organization_id_in_body_is_rejected(
    client: TestClient, keypair, organizations
) -> None:
    """Never accepted from the body -- `extra="forbid"` refuses the whole request outright rather
    than silently ignoring a forged value."""
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    response = _bind(client, token, provider.id, source_organization_id=str(uuid.uuid4()))

    assert response.status_code == 422, response.text


# -- representation validation -------------------------------------------------------------------


def test_oversized_source_system_rejected(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    response = _bind(client, token, provider.id, source_system="x" * 65)

    assert response.status_code == 422, response.text
    assert _reference_count(sessions) == 0


def test_oversized_source_identity_system_rejected(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    response = _bind(client, token, provider.id, source_identity_system="x" * 257)

    assert response.status_code == 422, response.text
    assert _reference_count(sessions) == 0


def test_oversized_source_identity_value_rejected(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    response = _bind(client, token, provider.id, source_identity_value="x" * 257)

    assert response.status_code == 422, response.text
    assert _reference_count(sessions) == 0


def test_oversized_display_name_rejected(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    response = _bind(client, token, provider.id, display_name="x" * 257)

    assert response.status_code == 422, response.text
    assert _reference_count(sessions) == 0


def test_oversized_contact_channel_type_rejected(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    response = _bind(client, token, provider.id, contact_channel_type="x" * 33)

    assert response.status_code == 422, response.text
    assert _reference_count(sessions) == 0


def test_oversized_contact_value_rejected(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    response = _bind(client, token, provider.id, contact_value="x" * 257)

    assert response.status_code == 422, response.text
    assert _reference_count(sessions) == 0


@pytest.mark.parametrize(
    "field", ["source_system", "source_identity_system", "source_identity_value"]
)
def test_blank_required_identity_field_rejected(
    client: TestClient, keypair, organizations, sessions, field: str
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    response = _bind(client, token, provider.id, **{field: "   "})

    assert response.status_code == 422, response.text
    assert _reference_count(sessions) == 0


def test_empty_string_required_identity_field_rejected(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")

    response = _bind(client, token, provider.id, source_identity_value="")

    assert response.status_code == 422, response.text
    assert _reference_count(sessions) == 0


# -- order integration -------------------------------------------------------------------------


def _make_test_entry(catalog, code: str = "DHEALTH-PATREF-DEMO"):
    entry, _ = catalog.create_entry(code=code, display_name="Demo test for patient-ref integration")
    return entry.id


def test_bound_reference_works_with_order_creation(
    client: TestClient, keypair, organizations, sessions
) -> None:
    from daher_adapter.exchange.catalog import CatalogRepository

    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "A")
    catalog = CatalogRepository(sessions, organizations)
    entry_id = _make_test_entry(catalog)

    bind_response = _bind(client, token, provider.id)
    assert bind_response.status_code == 201, bind_response.text
    patient_reference_id = bind_response.json()["id"]

    order_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(token), "Idempotency-Key": str(uuid.uuid4())},
        json={
            "patient_reference_id": patient_reference_id,
            "catalog_entry_ids": [str(entry_id)],
        },
    )

    assert order_response.status_code == 201, order_response.text
    assert order_response.json()["patient_reference_id"] == patient_reference_id


def test_cross_org_bound_reference_cannot_be_used_for_another_providers_order(
    client: TestClient, keypair, organizations, sessions
) -> None:
    from daher_adapter.exchange.catalog import CatalogRepository

    private_key, _ = keypair
    provider_a, token_a = _setup_clinician(client, private_key, organizations, "A")
    provider_b, token_b = _setup_clinician(client, private_key, organizations, "B")
    catalog = CatalogRepository(sessions, organizations)
    entry_id = _make_test_entry(catalog, code="DHEALTH-PATREF-XORG")

    bind_response = _bind(client, token_a, provider_a.id)
    patient_reference_id = bind_response.json()["id"]

    order_response = client.post(
        f"/exchange/v1/organizations/{provider_b.id}/orders",
        headers={**_auth(token_b), "Idempotency-Key": str(uuid.uuid4())},
        json={
            "patient_reference_id": patient_reference_id,
            "catalog_entry_ids": [str(entry_id)],
        },
    )

    assert order_response.status_code == 404, order_response.text


# -- DH-CLINIC-002: provider-scoped patient lookup and search -------------------------------------


def test_clinician_lists_patients_empty(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "ClinicEmpty")

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-references",
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_clinician_lists_patients_with_data_and_deterministic_order(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "ClinicList")

    # Bind 3 patients, including one with display_name=None
    res1 = _bind(
        client,
        token,
        provider.id,
        source_identity_value="DOS-001",
        display_name="Patient One",
        contact_channel_type="PHONE",
        contact_value="+25377000001",
    )
    res2 = _bind(
        client,
        token,
        provider.id,
        source_identity_value="DOS-002",
        display_name=None,
        contact_channel_type=None,
        contact_value=None,
    )
    res3 = _bind(
        client,
        token,
        provider.id,
        source_identity_value="DOS-003",
        display_name="Patient Three",
        contact_channel_type="SMS",
        contact_value="+25377000003",
    )
    assert res1.status_code == 201
    assert res2.status_code == 201
    assert res3.status_code == 201

    list_resp = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-references",
        headers=_auth(token),
    )
    assert list_resp.status_code == 200, list_resp.text
    items = list_resp.json()
    assert len(items) == 3

    # Deterministic order: created_at.desc(), id.asc() -> 3, 2, 1
    assert items[0]["local_reference"] == "DOS-003"
    assert items[0]["display_name"] == "Patient Three"
    assert items[0]["contact_channel_type"] == "SMS"
    assert items[0]["contact_value"] == "+25377000003"
    assert set(items[0]) == {
        "patient_reference_id",
        "display_name",
        "contact_channel_type",
        "contact_value",
        "local_reference",
    }

    assert items[1]["local_reference"] == "DOS-002"
    assert items[1]["display_name"] is None

    assert items[2]["local_reference"] == "DOS-001"
    assert items[2]["display_name"] == "Patient One"


def test_patient_list_uses_uuid_tie_break_for_equal_created_at(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "ClinicTieBreak")
    same_time = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
    lower_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    higher_id = uuid.UUID("00000000-0000-4000-8000-000000000002")
    with sessions.begin() as session:
        session.add_all(
            [
                # DH-IDENTITY-001: every PatientReference now requires a linked Patient -- reusing
                # the reference's own id as its Patient's id, the identical technique migration
                # 0022's own backfill uses, since this test constructs rows directly rather than
                # through the repository (it needs exact control over `created_at`).
                Patient(id=higher_id, provider_organization_id=provider.id, created_at=same_time),
                Patient(id=lower_id, provider_organization_id=provider.id, created_at=same_time),
                PatientReference(
                    id=higher_id,
                    source_organization_id=provider.id,
                    patient_id=higher_id,
                    source_system="synthetic",
                    source_identity_system="urn:synthetic:patient",
                    source_identity_value="TIE-HIGH",
                    created_at=same_time,
                ),
                PatientReference(
                    id=lower_id,
                    source_organization_id=provider.id,
                    patient_id=lower_id,
                    source_system="synthetic",
                    source_identity_system="urn:synthetic:patient",
                    source_identity_value="TIE-LOW",
                    created_at=same_time,
                ),
            ]
        )

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-references",
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    assert [item["patient_reference_id"] for item in response.json()] == [
        str(lower_id),
        str(higher_id),
    ]


def test_clinician_searches_by_name_phone_or_identity_value(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "ClinicSearch")

    _bind(
        client,
        token,
        provider.id,
        source_identity_value="DOS-101",
        display_name="Amina Hassan",
        contact_channel_type="PHONE",
        contact_value="+25377112233",
    )
    _bind(
        client,
        token,
        provider.id,
        source_identity_value="DOS-102",
        display_name="Farhan Ali",
        contact_channel_type="PHONE",
        contact_value="+25377445566",
    )

    # Search by partial display name
    resp_name = _search(client, token, provider.id, "amina")
    assert resp_name.status_code == 200
    assert len(resp_name.json()) == 1
    assert resp_name.json()[0]["local_reference"] == "DOS-101"

    # Search by partial contact value
    resp_phone = _search(client, token, provider.id, "4455")
    assert resp_phone.status_code == 200
    assert len(resp_phone.json()) == 1
    assert resp_phone.json()[0]["local_reference"] == "DOS-102"

    # Search by exact local dossier number
    resp_mrn = _search(client, token, provider.id, "DOS-101")
    assert resp_mrn.status_code == 200
    assert len(resp_mrn.json()) == 1
    assert resp_mrn.json()[0]["display_name"] == "Amina Hassan"

    # Search without matches
    resp_none = _search(client, token, provider.id, "Inconnu")
    assert resp_none.status_code == 200
    assert resp_none.json() == []


def test_search_query_escapes_sql_wildcards(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "ClinicWildcards")

    _bind(
        client,
        token,
        provider.id,
        source_identity_value="DOS_001",
        display_name="Test_Patient",
    )
    _bind(
        client,
        token,
        provider.id,
        source_identity_value="DOS-001",
        display_name="Test-Patient",
    )
    _bind(
        client,
        token,
        provider.id,
        source_identity_value=r"DOS\\001",
        display_name=r"Test\\Patient",
    )

    # Search literal '_' should match only DOS_001, not DOS-001
    resp = _search(client, token, provider.id, "DOS_001")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["local_reference"] == "DOS_001"

    # Search literal '%' matches nothing if no record contains '%'
    resp_pct = _search(client, token, provider.id, "%")
    assert resp_pct.status_code == 200
    assert resp_pct.json() == []

    resp_backslash = _search(client, token, provider.id, "\\")
    assert resp_backslash.status_code == 200
    assert [item["local_reference"] for item in resp_backslash.json()] == [r"DOS\\001"]


def test_search_whitespace_or_empty_is_rejected(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "ClinicWhitespace")

    _bind(client, token, provider.id, source_identity_value="DOS-A", display_name=None)
    _bind(client, token, provider.id, source_identity_value="DOS-B", display_name="Has Name")

    assert _search(client, token, provider.id, "").status_code == 422
    assert _search(client, token, provider.id, "  ").status_code == 422


def test_search_is_strictly_provider_isolated(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider_a, token_a = _setup_clinician(client, private_key, organizations, "ClinicA")
    provider_b, token_b = _setup_clinician(client, private_key, organizations, "ClinicB")

    res_a = _bind(
        client,
        token_a,
        provider_a.id,
        source_identity_value="SHARED-MRN",
        display_name="Shared Name",
    )
    res_b = _bind(
        client,
        token_b,
        provider_b.id,
        source_identity_value="SHARED-MRN",
        display_name="Shared Name",
    )
    id_a = res_a.json()["id"]
    id_b = res_b.json()["id"]
    assert id_a != id_b

    # Provider A search sees only A's record
    list_a = _search(client, token_a, provider_a.id, "Shared").json()
    assert len(list_a) == 1
    assert list_a[0]["patient_reference_id"] == id_a

    # Provider B search sees only B's record
    list_b = _search(client, token_b, provider_b.id, "Shared").json()
    assert len(list_b) == 1
    assert list_b[0]["patient_reference_id"] == id_b

    cross_search = _search(client, token_a, provider_b.id, "Shared")
    assert cross_search.status_code == 404

    # Cross-provider single get returns 404
    cross_get = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/patient-references/{id_b}",
        headers=_auth(token_a),
    )
    assert cross_get.status_code == 404, cross_get.text


def test_search_unauthenticated_fails_closed(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, _ = _setup_clinician(client, private_key, organizations, "ClinicUnauth")

    list_response = client.get(f"/exchange/v1/organizations/{provider.id}/patient-references")
    search_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/patient-references/search",
        json={"query": "patient"},
    )
    assert list_response.status_code == 401
    assert search_response.status_code == 401


def test_search_non_clinician_role_refused_403(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    org = make_provider(organizations, name="ClinicNonClinician")
    identity_id = _bootstrap_identity(client, private_key, "member-admin-only")
    _grant_roles(client, private_key, org.id, identity_id, "member-admin-only", ["ORG_ADMIN"])
    token = make_token(private_key, subject="member-admin-only")

    list_response = client.get(
        f"/exchange/v1/organizations/{org.id}/patient-references",
        headers=_auth(token),
    )
    search_response = client.post(
        f"/exchange/v1/organizations/{org.id}/patient-references/search",
        headers=_auth(token),
        json={"query": "patient"},
    )
    assert list_response.status_code == 403
    assert search_response.status_code == 403


def test_search_non_member_404_anti_enumeration(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider_a, _ = _setup_clinician(client, private_key, organizations, "ClinicOrgA")
    _, token_b = _setup_clinician(client, private_key, organizations, "ClinicOrgB")

    # Member of Org B tries to access Org A's patients -> anti-enumeration 404
    resp = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/patient-references",
        headers=_auth(token_b),
    )
    assert resp.status_code == 404


def test_query_bounds_validation_422(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "ClinicBounds")

    # limit < 1
    assert (
        client.get(
            f"/exchange/v1/organizations/{provider.id}/patient-references?limit=0",
            headers=_auth(token),
        ).status_code
        == 422
    )

    # limit > 100
    assert (
        client.get(
            f"/exchange/v1/organizations/{provider.id}/patient-references?limit=101",
            headers=_auth(token),
        ).status_code
        == 422
    )

    assert _search(client, token, provider.id, "a" * 101).status_code == 422
    assert _search(client, token, provider.id, "valid", limit=101).status_code == 422


def test_get_single_patient_reference_by_id(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "ClinicSingle")

    bind_res = _bind(
        client,
        token,
        provider.id,
        source_identity_value="SINGLE-001",
        display_name="Single Patient",
        contact_channel_type="PHONE",
        contact_value="+25377998877",
    )
    pat_id = bind_res.json()["id"]

    resp = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-references/{pat_id}",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["patient_reference_id"] == pat_id
    assert data["local_reference"] == "SINGLE-001"
    assert data["display_name"] == "Single Patient"
    assert data["contact_value"] == "+25377998877"
    assert "source_identity_system" not in data
    assert "source_system" not in data

    # Nonexistent ID -> 404
    nonexistent = uuid.uuid4()
    assert (
        client.get(
            f"/exchange/v1/organizations/{provider.id}/patient-references/{nonexistent}",
            headers=_auth(token),
        ).status_code
        == 404
    )


def test_patient_detail_requires_exact_provider_clinician_relationship(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, clinician_token = _setup_clinician(client, private_key, organizations, "DetailAuth")
    reference_id = _bind(client, clinician_token, provider.id).json()["id"]

    unauthenticated = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-references/{reference_id}"
    )
    assert unauthenticated.status_code == 401

    _, unrelated_token = _setup_clinician(client, private_key, organizations, "DetailUnrelated")
    unrelated = client.get(
        f"/exchange/v1/organizations/{provider.id}/patient-references/{reference_id}",
        headers=_auth(unrelated_token),
    )
    assert unrelated.status_code == 404

    provider_admin, admin_token = _setup_member(
        client, private_key, organizations, "DetailAdmin", roles=["ORG_ADMIN"]
    )
    admin = client.get(
        f"/exchange/v1/organizations/{provider_admin.id}/patient-references/{uuid.uuid4()}",
        headers=_auth(admin_token),
    )
    assert admin.status_code == 403
