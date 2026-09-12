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
from daher_adapter.exchange.models import ExchangeSecurityAuditEvent, PatientReference
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
