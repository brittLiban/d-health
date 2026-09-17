"""DH-EX-010: ``POST /organizations/{provider_id}/orders/{order_id}/assign-lab`` -- provider/
facility-assigned lab routing, ADR-043's pre-authorized launch option (a).

Same harness pattern as ``test_exchange_lab_workflow.py``: a bare ``FastAPI()`` app with
``build_exchange_router`` mounted directly, a ``StaticKeySource``-backed ``TokenValidator`` (local
RSA keypair, zero network). Repository-level tests for ``assign_by_provider`` itself live in
``test_exchange_provider_assignment.py``.
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
    """A one-item order with no lab, no offering, no price -- returns (provider, order_id,
    entry_id, clinician_token)."""
    provider, clinician_token = _setup_clinician(client, private_key, organizations, name)
    patient_record = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-{name}",
        display_name=f"Synthetic Patient {name}",
    )
    entry, _ = catalog.create_entry(code=f"DHEALTH-PA-{name}", display_name=f"PA Test {name}")
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


def _assign_lab(client, token, provider_id, order_id, lab_id):
    return client.post(
        f"/exchange/v1/organizations/{provider_id}/orders/{order_id}/assign-lab",
        headers=_auth(token),
        json={"lab_organization_id": str(lab_id)},
    )


def _referral_lab(
    organizations,
    directory,
    catalog,
    *,
    name: str,
    entry_id,
    listed: bool = False,
    client: TestClient | None = None,
    private_key=None,
    provider_id=None,
):
    """An ACTIVE lab, accepting orders, offering the given test -- deliberately zero LabPrice
    rows and (by default) unlisted, matching the provider-assignment-eligible shape.

    DH-CLINIC-006: when ``client``/``private_key``/``provider_id`` are given, also configures an
    active ``ProviderLabRoute`` from ``provider_id`` to the new lab -- without one,
    ``assign_by_provider`` now refuses with ``ProviderLabRouteNotActive`` regardless of how
    eligible the lab otherwise is (§18). Omitted (the default) for tests that never call
    assign-lab for this lab, or that specifically exercise the no-route-configured refusal."""
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
    if client is not None:
        _configure_route(client, private_key, provider_id, lab.id)
    return lab


def _configure_route(client, private_key, provider_id, lab_id, *, active: bool = True) -> None:
    """DH-CLINIC-006 §8/§9: route configuration is platform-admin-only."""
    platform_admin_token = make_token(
        private_key, subject=f"route-admin-{uuid.uuid4()}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.put(
        f"/exchange/v1/organizations/{provider_id}/lab-routes/{lab_id}",
        headers=_auth(platform_admin_token),
        json={"active": active},
    )
    assert response.status_code in (200, 201), response.text


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


# -- golden provider-assignment journey, including DH-EX-006 lab-queue integration ---------------


def test_clinician_assigns_lab_and_lab_tech_can_accept_it(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="Golden"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="GoldenReferralLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )

    response = _assign_lab(client, clinician_token, provider.id, order_id, lab.id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["lab_order_id"] == str(order_id)
    assert body["lab_organization_id"] == str(lab.id)
    assert "selected_price_amount" not in body

    events = _audit_events(sessions, "lab_order_assigned")
    assert len(events) == 1
    assert events[0]["outcome"] == "updated"
    assert events[0]["resource_type"] == "lab_order"
    assert events[0]["resource_id"] == str(order_id)
    assert events[0]["organization_id"] == str(provider.id)

    lab_tech_identity = _bootstrap_identity(client, private_key, "labtech-golden")
    _grant_roles(client, private_key, lab.id, lab_tech_identity, "labtech-golden", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-golden")

    list_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders", headers=_auth(lab_tech_token)
    )
    assert list_response.status_code == 200, list_response.text
    assert any(o["order_id"] == str(order_id) for o in list_response.json())

    accept_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_response.status_code == 200, accept_response.text
    accepted = accept_response.json()
    assert accepted["status"] == "ACCEPTED"
    assert accepted["requested_tests"][0]["selected_price_amount"] is None
    assert accepted["requested_tests"][0]["selected_price_currency"] is None


# -- authorization -------------------------------------------------------------------------------


def test_org_admin_without_clinician_role_denied(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, _clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="OrgAdminOnly"
    )
    lab = _referral_lab(organizations, directory, catalog, name="OALab", entry_id=entry_id)
    org_admin_identity = _bootstrap_identity(client, private_key, "org-admin-only")
    _grant_roles(
        client, private_key, provider.id, org_admin_identity, "org-admin-only", ["ORG_ADMIN"]
    )
    token = make_token(private_key, subject="org-admin-only")

    response = _assign_lab(client, token, provider.id, order_id, lab.id)
    assert response.status_code == 403, response.text


def test_lab_tech_cannot_assign_lab(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    _provider, order_id, entry_id, _clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="LTNotAllowed"
    )
    lab = _referral_lab(organizations, directory, catalog, name="LTLab", entry_id=entry_id)
    lab_tech_identity = _bootstrap_identity(client, private_key, "lt-not-allowed")
    _grant_roles(client, private_key, lab.id, lab_tech_identity, "lt-not-allowed", ["LAB_TECH"])
    token = make_token(private_key, subject="lt-not-allowed")

    # LAB_TECH's own membership is at the LAB, not the PROVIDER -- calling with the lab's own id
    # as `provider_id` must still be refused (`require_clinician` requires CLINICIAN specifically).
    response = _assign_lab(client, token, lab.id, order_id, lab.id)
    assert response.status_code == 403, response.text


def test_platform_admin_alone_denied(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, _clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="AdminOnly"
    )
    lab = _referral_lab(organizations, directory, catalog, name="AdminOnlyLab", entry_id=entry_id)
    admin_token = make_token(private_key, subject="platform-admin-pa", roles=[PLATFORM_ADMIN_ROLE])

    response = _assign_lab(client, admin_token, provider.id, order_id, lab.id)
    assert response.status_code == 403, response.text


def test_cross_tenant_clinician_denied(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    _provider_a, order_id, entry_id, _token_a = _create_order(
        client, private_key, organizations, catalog, patients, name="TenantA"
    )
    provider_b, token_b = _setup_clinician(client, private_key, organizations, "TenantB")
    lab = _referral_lab(organizations, directory, catalog, name="TenantLab", entry_id=entry_id)

    response = _assign_lab(client, token_b, provider_b.id, order_id, lab.id)
    assert response.status_code == 404, response.text


def test_unknown_order_denied(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, token = _setup_clinician(client, private_key, organizations, "UnknownOrder")
    entry, _ = catalog.create_entry(code="DH-UNKNOWN-ORDER", display_name="X")
    lab = _referral_lab(
        organizations, directory, catalog, name="UnknownOrderLab", entry_id=entry.id
    )

    response = _assign_lab(client, token, provider.id, uuid.uuid4(), lab.id)
    assert response.status_code == 404, response.text


def test_source_organization_id_in_body_is_rejected(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, token = _create_order(
        client, private_key, organizations, catalog, patients, name="ForgedBody"
    )
    lab = _referral_lab(organizations, directory, catalog, name="ForgedBodyLab", entry_id=entry_id)

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/assign-lab",
        headers=_auth(token),
        json={"lab_organization_id": str(lab.id), "provider_id": str(uuid.uuid4())},
    )
    assert response.status_code == 422, response.text


# -- idempotency / conflict -----------------------------------------------------------------------


def test_idempotent_replay_returns_200_no_second_audit(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, token = _create_order(
        client, private_key, organizations, catalog, patients, name="Replay"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="ReplayLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )

    first = _assign_lab(client, token, provider.id, order_id, lab.id)
    second = _assign_lab(client, token, provider.id, order_id, lab.id)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert first.json()["lab_order_id"] == second.json()["lab_order_id"]
    events = _audit_events(sessions, "lab_order_assigned")
    assert len(events) == 1, "an idempotent replay must never write a second audit row"


def test_different_lab_already_selected_is_conflict(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, token = _create_order(
        client, private_key, organizations, catalog, patients, name="DiffLab"
    )
    lab_a = _referral_lab(
        organizations,
        directory,
        catalog,
        name="DiffLabA",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    # lab_b deliberately has no configured route -- irrelevant here, since the existing-different-
    # lab-selection conflict is detected before the route check ever runs for the second call.
    lab_b = _referral_lab(organizations, directory, catalog, name="DiffLabB", entry_id=entry_id)

    first = _assign_lab(client, token, provider.id, order_id, lab_a.id)
    assert first.status_code == 201, first.text
    second = _assign_lab(client, token, provider.id, order_id, lab_b.id)
    assert second.status_code == 409, second.text


# -- Law 1 evidence: directory/marketplace separation ---------------------------------------------


def test_unlisted_no_price_lab_is_provider_assignable_but_not_a_marketplace_option(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """The single scenario the ticket names as proof Law 1 is encoded in software, not merely
    documented: an ACTIVE lab, `listed=False`, `accepting_orders=True`, offering every requested
    test, with zero LabPrice rows. Provider assignment succeeds; the lab never appears in the
    patient marketplace; and a patient cannot bypass the marketplace/price requirements to select
    it directly either."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="Separation"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="SeparationLab",
        entry_id=entry_id,
        listed=False,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )

    assign_response = _assign_lab(client, clinician_token, provider.id, order_id, lab.id)
    assert assign_response.status_code == 201, assign_response.text

    marketplace_response = client.post(
        "/exchange/v1/marketplace/lab-options",
        headers=_auth(clinician_token),
        json={"catalog_entry_ids": [str(entry_id)]},
    )
    assert marketplace_response.status_code == 200, marketplace_response.text
    option_lab_ids = {opt["lab"]["id"] for opt in marketplace_response.json()["options"]}
    assert str(lab.id) not in option_lab_ids, "an unlisted lab must never appear as an option"


def test_unlisted_no_price_lab_cannot_be_patient_selected_directly(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """Same lab shape as above, but proving the *other* half: even a patient who somehow already
    knows the lab id (bypassing marketplace discovery entirely) cannot select it through the
    DH-EX-005 price-consent path, because it has no price to consent to."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="SeparationPatient"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="SeparationPatientLab",
        entry_id=entry_id,
        listed=False,
    )
    access_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    assert access_response.status_code == 201, access_response.text
    access_token = access_response.json()["access_token"]

    selection_response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [str(uuid.uuid4())]},
    )
    assert selection_response.status_code == 409, selection_response.text


# -- the 0 DJF fix -------------------------------------------------------------------------------


def test_patient_response_shows_null_price_not_zero_for_provider_assignment(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """The exact bug this ticket found and fixed: `_patient_order_response` used to sum an
    all-`None` generator with `start=Decimal("0")` and default currency to `"DJF"`, producing an
    implied `0 DJF` for a provider-assigned order. This proves the fixed response instead reports
    `total_amount`/`currency` as `null` with `assignment_source: "PROVIDER"`.

    Grant issuance happens *before* assignment, matching a real finding this ticket recorded (see
    PROJECT_STATE.md): `patient_access.issue()` only allows a fresh selection-capable grant while
    the order is still `AWAITING_LAB_SELECTION` -- an already-issued grant remains fully usable
    afterward, exactly like DH-EX-006's own `test_patient_grant_revocation_does_not_remove_lab_
    assignment` already relies on for its own (patient-driven) lab assignment."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="ZeroDJF"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="ZeroDJFLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )

    access_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    assert access_response.status_code == 201, access_response.text
    access_token = access_response.json()["access_token"]

    assign_response = _assign_lab(client, clinician_token, provider.id, order_id, lab.id)
    assert assign_response.status_code == 201, assign_response.text

    patient_order_response = client.get("/exchange/v1/patient/order", headers=_auth(access_token))
    assert patient_order_response.status_code == 200, patient_order_response.text
    selection = patient_order_response.json()["selection"]
    assert selection is not None
    assert selection["assignment_source"] == "PROVIDER"
    assert selection["total_amount"] is None, "must be null, never an implied 0"
    assert selection["currency"] is None, "must be null, never a fabricated DJF"
    assert selection["items"] == []


def test_patient_response_still_shows_real_total_for_patient_selection(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """Regression guard on the other side of the same fix: an ordinary DH-EX-005 patient
    selection must still report its real, non-null total and currency exactly as before."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="RealPrice"
    )
    lab = make_lab(organizations, name="RealPriceLab")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("500.00"),
        currency="DJF",
        expected_current_price_id=None,
    )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    access_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    access_token = access_response.json()["access_token"]
    options_response = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    )
    price_ids = [
        line["current_price_id"]
        for opt in options_response.json()["options"]
        if opt["lab"]["id"] == str(lab.id)
        for line in opt["prices"]
    ]
    selection_response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert selection_response.status_code == 201, selection_response.text

    patient_order_response = client.get("/exchange/v1/patient/order", headers=_auth(access_token))
    selection = patient_order_response.json()["selection"]
    assert selection["assignment_source"] == "PATIENT"
    assert selection["total_amount"] == "500.00"
    assert selection["currency"] == "DJF"
    assert len(selection["items"]) == 1


# -- patient-vs-provider provenance protection -----------------------------------------------------


def test_patient_selection_after_provider_assignment_same_lab_is_conflict(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """The strongest Law 1 evidence: a patient POSTing arbitrary/fabricated `expected_price_ids`
    for a lab the provider already assigned (which has no real price at all) must be refused, not
    silently accepted as though the patient had consented to a price that never existed.

    Grant issued before assignment -- see the sibling `0 DJF` test's docstring for why."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="ProvenanceAttack"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="ProvenanceAttackLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )

    access_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    access_token = access_response.json()["access_token"]

    assign_response = _assign_lab(client, clinician_token, provider.id, order_id, lab.id)
    assert assign_response.status_code == 201, assign_response.text

    attack_response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [str(uuid.uuid4())]},
    )
    assert attack_response.status_code == 409, attack_response.text
    # No fabricated mutation event either -- the refused attempt never reached a mutation branch
    # of any kind, so still exactly the one original provider-assignment audit row.
    events = _audit_events(sessions, "lab_order_assigned")
    assert len(events) == 1
    assert events[0]["outcome"] == "updated"


def test_provider_assignment_after_patient_selection_preserves_patient_receipt(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """Reverse order: a patient legitimately selects and prices a lab first; a provider later
    calling assign-lab for that same lab must get a clean 200 with the patient's receipt
    completely unchanged, never a fabricated `lab_order_assigned` mutation."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="ReverseOrder"
    )
    lab = make_lab(organizations, name="ReverseOrderLab")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("750.00"),
        currency="DJF",
        expected_current_price_id=None,
    )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    access_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    access_token = access_response.json()["access_token"]
    options_response = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    )
    price_ids = [
        line["current_price_id"]
        for opt in options_response.json()["options"]
        if opt["lab"]["id"] == str(lab.id)
        for line in opt["prices"]
    ]
    patient_selection = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert patient_selection.status_code == 201, patient_selection.text

    provider_assign = _assign_lab(client, clinician_token, provider.id, order_id, lab.id)
    assert provider_assign.status_code == 200, provider_assign.text

    receipt_response = client.get("/exchange/v1/patient/order", headers=_auth(access_token))
    selection = receipt_response.json()["selection"]
    assert selection["assignment_source"] == "PATIENT"
    assert selection["total_amount"] == "750.00"
    assert selection["currency"] == "DJF"


# -- DH-EX-010A: the timing hole this ticket exists to close ---------------------------------------


def test_reproduction_issuing_access_after_provider_assignment_was_refused_pre_fix(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """DH-EX-010A's own reproduction requirement: before this ticket's fix, a clinician who
    assigned a lab *before* issuing the patient's access link could never issue one afterward --
    ``patient_access.issue()`` only permitted a fresh grant while
    ``LabOrder.status == AWAITING_LAB_SELECTION``, and provider assignment immediately transitions
    past it. This test's assertion is the fixed, correct behavior (``201``, ``ORDER_READ``); run
    against pre-fix code it observably fails with ``409`` -- see this ticket's negative control #2
    (reverting issuance to AWAITING-only) for the executed proof."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="TimingHole"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="TimingHoleLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )

    assign_response = _assign_lab(client, clinician_token, provider.id, order_id, lab.id)
    assert assign_response.status_code == 201, assign_response.text

    access_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    assert access_response.status_code == 201, (
        "DH-EX-010A: issuing patient access after a provider assignment must now succeed -- "
        f"got {access_response.status_code}: {access_response.text}"
    )
    assert access_response.json()["access_mode"] == "ORDER_READ"
