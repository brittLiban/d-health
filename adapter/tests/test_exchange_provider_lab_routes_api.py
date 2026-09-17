"""DH-CLINIC-006: HTTP-level tests for the route management API
(``PUT .../lab-routes/{lab_id}``), the provider's own route directory read
(``GET .../lab-routes``), and the order-specific eligible-labs read
(``GET .../orders/{order_id}/eligible-labs``) -- authorization, idempotency, audit, anti-
enumeration, and the §19 revalidate-at-commit guarantees (A7-A9). Same harness pattern as
``test_exchange_provider_assignment_api.py``.
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
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
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
def directory(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> LabDirectoryProfileRepository:
    return LabDirectoryProfileRepository(sessions, organizations)


@pytest.fixture
def patients(sessions: sessionmaker[Session]) -> PatientReferenceRepository:
    return PatientReferenceRepository(sessions)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _new_key() -> str:
    return str(uuid.uuid4())


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


def _create_order(client, private_key, organizations, catalog, patients, *, name: str):
    provider, clinician_token = _setup_clinician(client, private_key, organizations, name)
    patient_record = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-{name}",
        display_name=f"Synthetic Patient {name}",
    )
    entry, _ = catalog.create_entry(code=f"DHEALTH-PLR-{name}", display_name=f"PLR Test {name}")
    create_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_record.id),
            "catalog_entry_ids": [str(entry.id)],
        },
    )
    assert create_response.status_code == 201, create_response.text
    order_id = uuid.UUID(create_response.json()["id"])
    return provider, order_id, entry.id, clinician_token


def _platform_admin_token(private_key, subject: str) -> str:
    return make_token(private_key, subject=subject, roles=[PLATFORM_ADMIN_ROLE])


def _configure_route(client, private_key, provider_id, lab_id, *, active: bool = True):
    token = _platform_admin_token(private_key, f"route-admin-{uuid.uuid4()}")
    return client.put(
        f"/exchange/v1/organizations/{provider_id}/lab-routes/{lab_id}",
        headers=_auth(token),
        json={"active": active},
    )


def _referral_lab(organizations, directory, catalog, *, name: str, entry_id, listed: bool = False):
    lab = make_lab(organizations, name=name)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address=f"{name} Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=listed,
        accepting_orders=True,
    )
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_id)
    return lab


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
                "actor_identity_id": str(row.actor_identity_id),
            }
            for row in rows
        ]


# -- PUT .../lab-routes/{lab_id}: platform-admin-only route management ---------------------------


def test_put_route_platform_admin_creates_and_returns_201(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations, name="PutRouteProvider")
    lab = make_lab(organizations, name="PutRouteLab")

    response = _configure_route(client, private_key, provider.id, lab.id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider_organization_id"] == str(provider.id)
    assert body["lab_organization_id"] == str(lab.id)
    assert body["lab_organization_name"] == "PutRouteLab"
    assert body["active"] is True

    events = _audit_events(sessions, "provider_lab_route_created")
    assert len(events) == 1
    assert events[0]["outcome"] == "created"
    assert events[0]["resource_type"] == "provider_lab_route"
    assert events[0]["organization_id"] == str(provider.id)


def test_put_route_idempotent_same_state_records_unchanged_outcome(
    client: TestClient, keypair, organizations, sessions
) -> None:
    """Mirrors ``put_directory_profile``'s own established convention: every PUT call is audited,
    including a no-op replay -- ``outcome`` distinguishes ``"created"``/``"updated"``/
    ``"unchanged"`` rather than suppressing the row entirely (unlike ``assign_lab_to_order``'s own
    different idempotent-replay convention, which is about a *clinical* mutation, not a platform-
    admin configuration change)."""
    private_key, _ = keypair
    provider = make_provider(organizations, name="PutRouteReplayProvider")
    lab = make_lab(organizations, name="PutRouteReplayLab")

    first = _configure_route(client, private_key, provider.id, lab.id)
    second = _configure_route(client, private_key, provider.id, lab.id)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    created_events = _audit_events(sessions, "provider_lab_route_created")
    updated_events = _audit_events(sessions, "provider_lab_route_updated")
    assert len(created_events) == 1
    assert len(updated_events) == 1
    assert updated_events[0]["outcome"] == "unchanged"


def test_put_route_changing_active_returns_200_with_updated_audit(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations, name="PutRouteChangeProvider")
    lab = make_lab(organizations, name="PutRouteChangeLab")
    _configure_route(client, private_key, provider.id, lab.id, active=True)

    response = _configure_route(client, private_key, provider.id, lab.id, active=False)

    assert response.status_code == 200, response.text
    assert response.json()["active"] is False
    events = _audit_events(sessions, "provider_lab_route_updated")
    assert len(events) == 1
    assert events[0]["outcome"] == "updated"


def test_put_route_non_platform_admin_denied(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, clinician_token = _setup_clinician(
        client, private_key, organizations, "PutRouteDenied"
    )
    lab = make_lab(organizations, name="PutRouteDeniedLab")

    response = client.put(
        f"/exchange/v1/organizations/{provider.id}/lab-routes/{lab.id}",
        headers=_auth(clinician_token),
        json={"active": True},
    )
    assert response.status_code == 403, response.text


def test_put_route_anonymous_is_401(client: TestClient, organizations) -> None:
    provider = make_provider(organizations, name="PutRouteAnonProvider")
    lab = make_lab(organizations, name="PutRouteAnonLab")
    response = client.put(
        f"/exchange/v1/organizations/{provider.id}/lab-routes/{lab.id}", json={"active": True}
    )
    assert response.status_code == 401, response.text


def test_put_route_wrong_organization_types_rejected(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations, name="PutRouteWrongTypeProvider")
    other_provider = make_provider(organizations, name="PutRouteWrongTypeOther")

    response = _configure_route(client, private_key, provider.id, other_provider.id)
    assert response.status_code == 422, response.text


def test_put_route_nonexistent_lab_is_404(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations, name="PutRouteMissingLabProvider")

    response = _configure_route(client, private_key, provider.id, uuid.uuid4())
    assert response.status_code == 404, response.text


def test_put_route_nonexistent_provider_is_404(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    lab = make_lab(organizations, name="PutRouteMissingProviderLab")

    response = _configure_route(client, private_key, uuid.uuid4(), lab.id)
    assert response.status_code == 404, response.text


# -- GET .../lab-routes: provider's own routing directory (CLINICIAN) -----------------------------


def test_get_lab_routes_clinician_sees_own_active_routes(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, clinician_token = _setup_clinician(client, private_key, organizations, "GetRoutes")
    lab = make_lab(organizations, name="GetRoutesLab")
    _configure_route(client, private_key, provider.id, lab.id)

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/lab-routes", headers=_auth(clinician_token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["lab_organization_id"] == str(lab.id)
    assert "price" not in str(body[0]).lower()


def test_get_lab_routes_inactive_routes_hidden_by_default(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider, clinician_token = _setup_clinician(
        client, private_key, organizations, "GetRoutesInactive"
    )
    lab = make_lab(organizations, name="GetRoutesInactiveLab")
    _configure_route(client, private_key, provider.id, lab.id, active=False)

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/lab-routes", headers=_auth(clinician_token)
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_get_lab_routes_non_clinician_denied(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations, name="GetRoutesDeniedProvider")
    identity_id = _bootstrap_identity(client, private_key, "GetRoutesDeniedLabTech")
    lab_org = make_lab(organizations, name="GetRoutesDeniedLab")
    _grant_roles(
        client, private_key, lab_org.id, identity_id, "GetRoutesDeniedLabTech", ["LAB_TECH"]
    )
    token = make_token(private_key, subject="GetRoutesDeniedLabTech")

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/lab-routes", headers=_auth(token)
    )
    assert response.status_code in (403, 404), response.text


def test_get_lab_routes_cross_provider_no_configuration_leak(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider_a = make_provider(organizations, name="GetRoutesCrossA")
    lab = make_lab(organizations, name="GetRoutesCrossLab")
    _configure_route(client, private_key, provider_a.id, lab.id)
    _provider_b, token_b = _setup_clinician(client, private_key, organizations, "GetRoutesCrossB")

    response = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/lab-routes", headers=_auth(token_b)
    )
    assert response.status_code in (403, 404), response.text


# -- GET .../orders/{order_id}/eligible-labs -------------------------------------------------------


def test_eligible_labs_returns_configured_capable_lab(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, token = _create_order(
        client, private_key, organizations, catalog, patients, name="Eligible"
    )
    lab = _referral_lab(organizations, directory, catalog, name="EligibleLab", entry_id=entry_id)
    _configure_route(client, private_key, provider.id, lab.id)

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/eligible-labs",
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["lab_organization_id"] == str(lab.id)
    assert "price" not in body[0]


def test_eligible_labs_zero_eligible_is_a_valid_empty_list(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    """DH-CLINIC-006 §15: zero eligible labs is real, valid state -- never an error, never a
    fabricated fallback lab."""
    private_key, _ = keypair
    provider, order_id, _entry_id, token = _create_order(
        client, private_key, organizations, catalog, patients, name="NoEligible"
    )

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/eligible-labs",
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_eligible_labs_unknown_order_is_404(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "EligibleUnknown")

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{uuid.uuid4()}/eligible-labs",
        headers=_auth(token),
    )
    assert response.status_code == 404, response.text


def test_eligible_labs_cross_provider_order_anti_enumeration(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider_a, order_id, _entry_id, _token_a = _create_order(
        client, private_key, organizations, catalog, patients, name="EligCrossA"
    )
    _provider_b, token_b = _setup_clinician(client, private_key, organizations, "EligCrossB")

    response = client.get(
        f"/exchange/v1/organizations/{provider_a.id}/orders/{order_id}/eligible-labs",
        headers=_auth(token_b),
    )
    assert response.status_code == 404, response.text


def test_eligible_labs_non_clinician_denied(
    client: TestClient, keypair, organizations, catalog, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, _entry_id, _token = _create_order(
        client, private_key, organizations, catalog, patients, name="EligNonClin"
    )
    identity_id = _bootstrap_identity(client, private_key, "EligNonClinLabTech")
    lab_org = make_lab(organizations, name="EligNonClinLab")
    _grant_roles(client, private_key, lab_org.id, identity_id, "EligNonClinLabTech", ["LAB_TECH"])
    token = make_token(private_key, subject="EligNonClinLabTech")

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/eligible-labs",
        headers=_auth(token),
    )
    assert response.status_code in (403, 404), response.text


# -- §19 revalidate-at-commit: A7-A9 ---------------------------------------------------------------


def test_a7_route_deactivated_after_eligible_read_assignment_refuses(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, token = _create_order(
        client, private_key, organizations, catalog, patients, name="A7"
    )
    lab = _referral_lab(organizations, directory, catalog, name="A7Lab", entry_id=entry_id)
    _configure_route(client, private_key, provider.id, lab.id)

    eligible = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/eligible-labs",
        headers=_auth(token),
    )
    assert any(o["lab_organization_id"] == str(lab.id) for o in eligible.json())

    _configure_route(client, private_key, provider.id, lab.id, active=False)

    assign_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/assign-lab",
        headers=_auth(token),
        json={"lab_organization_id": str(lab.id)},
    )
    assert assign_response.status_code == 409, assign_response.text

    with sessions() as session:
        from daher_adapter.exchange.models import LabOrder, LabSelection

        order_row = session.get(LabOrder, order_id)
        assert order_row.status == "AWAITING_LAB_SELECTION"
        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        assert selection is None


def test_a8_accepting_orders_flipped_after_eligible_read_assignment_refuses(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, token = _create_order(
        client, private_key, organizations, catalog, patients, name="A8"
    )
    lab = _referral_lab(organizations, directory, catalog, name="A8Lab", entry_id=entry_id)
    _configure_route(client, private_key, provider.id, lab.id)

    eligible = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/eligible-labs",
        headers=_auth(token),
    )
    assert any(o["lab_organization_id"] == str(lab.id) for o in eligible.json())

    directory.upsert(
        lab_organization_id=lab.id,
        public_address="A8Lab Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=False,
    )

    assign_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/assign-lab",
        headers=_auth(token),
        json={"lab_organization_id": str(lab.id)},
    )
    assert assign_response.status_code == 409, assign_response.text


def test_a9_offering_deactivated_after_eligible_read_assignment_refuses(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, token = _create_order(
        client, private_key, organizations, catalog, patients, name="A9"
    )
    lab = _referral_lab(organizations, directory, catalog, name="A9Lab", entry_id=entry_id)
    _configure_route(client, private_key, provider.id, lab.id)

    eligible = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/eligible-labs",
        headers=_auth(token),
    )
    assert any(o["lab_organization_id"] == str(lab.id) for o in eligible.json())

    with_offering = catalog.list_offerings_for_lab(lab.id)
    catalog.set_offering_active(with_offering[0].id, False)

    assign_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/assign-lab",
        headers=_auth(token),
        json={"lab_organization_id": str(lab.id)},
    )
    assert assign_response.status_code == 409, assign_response.text
