"""DH-EX-010A: persisted ``PatientAccessGrant.access_mode`` (``LAB_SELECTION`` | ``ORDER_READ``) --
closing the timing hole where a clinician who assigned a lab before issuing the patient's access
link could never issue one afterward (``PatientAccessGrantRepository.issue()`` used to gate a fresh
grant to ``AWAITING_LAB_SELECTION`` only).

Same harness pattern as ``test_exchange_provider_assignment_api.py``: a bare ``FastAPI()`` app with
``build_exchange_router`` mounted directly. The provider-assignment-then-issue reproduction of the
pre-fix timing hole itself lives in ``test_exchange_provider_assignment_api.py`` (it needs that
file's ``_assign_lab``/``_referral_lab`` helpers and was written there first, before this file
existed, to keep the "reproduce before fixing" step co-located with the workflow it reproduces).
Real-thread concurrency and real-PostgreSQL qualification live in this file's repository-harness
section below and in ``tests/integration/test_dhex010a_postgres_qualification.py`` respectively.
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
from daher_adapter.exchange.models import ExchangeSecurityAuditEvent, PatientAccessGrant
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
    entry, _ = catalog.create_entry(code=f"DHEALTH-AM-{name}", display_name=f"AM Test {name}")
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
    """DH-CLINIC-006: when ``client``/``private_key``/``provider_id`` are given, also configures
    an active ``ProviderLabRoute`` from ``provider_id`` to the new lab -- without one,
    ``assign_by_provider`` refuses with ``ProviderLabRouteNotActive`` regardless of how eligible
    the lab otherwise is (§18)."""
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
    platform_admin_token = make_token(
        private_key, subject=f"route-admin-{uuid.uuid4()}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.put(
        f"/exchange/v1/organizations/{provider_id}/lab-routes/{lab_id}",
        headers=_auth(platform_admin_token),
        json={"active": active},
    )
    assert response.status_code in (200, 201), response.text


def _issue_access(client, token, provider_id, order_id):
    return client.post(
        f"/exchange/v1/organizations/{provider_id}/orders/{order_id}/patient-access",
        headers=_auth(token),
    )


# -- primary acceptance journey (ticket's own "PROVIDER-ASSIGNED FLOW") ---------------------------


def test_provider_assigned_launch_journey(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """create/bind patient -> create order -> provider assigns lab -> issue patient access ->
    patient GET /patient/order. The ticket's own named primary acceptance journey."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="Journey"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="JourneyLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )

    assign_response = _assign_lab(client, clinician_token, provider.id, order_id, lab.id)
    assert assign_response.status_code == 201, assign_response.text

    access_response = _issue_access(client, clinician_token, provider.id, order_id)
    assert access_response.status_code == 201, access_response.text
    assert access_response.json()["access_mode"] == "ORDER_READ"
    access_token = access_response.json()["access_token"]

    order_view = client.get("/exchange/v1/patient/order", headers=_auth(access_token))
    assert order_view.status_code == 200
    selection = order_view.json()["selection"]
    assert selection is not None
    assert selection["lab_organization_id"] == str(lab.id)
    assert selection["assignment_source"] == "PROVIDER"
    assert selection["total_amount"] is None
    assert selection["currency"] is None

    # Patient cannot access lab-choice functionality with this grant.
    lab_options = client.get("/exchange/v1/patient/order/lab-options", headers=_auth(access_token))
    assert lab_options.status_code == 403
    # A schema-valid body (min_length=1) is required so this actually exercises the capability
    # check rather than tripping Pydantic's own request validation first.
    reselect = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": [str(uuid.uuid4())]},
    )
    assert reselect.status_code == 403


# -- fast-lab race / later states -------------------------------------------------------------


def test_lab_can_accept_before_patient_link_issuance_read_link_still_issuable(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """A lab may accept the order before clinic staff get around to printing/sending the link --
    issuance must key off the existence of the canonical ``LabSelection``, never
    ``status == SELECTED`` specifically, so a fast-moving lab can never make a read link
    unissuable."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="FastLab"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="FastLabLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assign_response = _assign_lab(client, clinician_token, provider.id, order_id, lab.id)
    assert assign_response.status_code == 201, assign_response.text

    lab_tech_identity = _bootstrap_identity(client, private_key, "fastlab-tech")
    _grant_roles(client, private_key, lab.id, lab_tech_identity, "fastlab-tech", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="fastlab-tech")
    accept_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_response.status_code == 200, accept_response.text
    assert accept_response.json()["status"] == "ACCEPTED"

    access_response = _issue_access(client, clinician_token, provider.id, order_id)
    assert access_response.status_code == 201, (
        "issuance must succeed even though the order has already moved past SELECTED -- "
        f"got {access_response.status_code}: {access_response.text}"
    )
    assert access_response.json()["access_mode"] == "ORDER_READ"

    order_view = client.get(
        "/exchange/v1/patient/order", headers=_auth(access_response.json()["access_token"])
    )
    assert order_view.status_code == 200
    assert order_view.json()["status"] == "ACCEPTED"


# -- replacement links after revoke/expiry ------------------------------------------------------


def test_expired_grant_replacement_after_selection_is_order_read(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    import datetime as dt

    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="ExpireReplace"
    )
    lab = make_lab(organizations, name="ExpireReplaceLab")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("300.00"),
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
    first = _issue_access(client, clinician_token, provider.id, order_id)
    assert first.json()["access_mode"] == "LAB_SELECTION"
    first_token = first.json()["access_token"]
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(first_token)
    ).json()
    price_ids = [line["current_price_id"] for line in options["options"][0]["prices"]]
    selection = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(first_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert selection.status_code == 201

    with sessions() as session:
        grant = session.scalar(select(PatientAccessGrant))
        grant.issued_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=TTL_HOURS + 2)
        grant.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
        session.commit()

    assert client.get("/exchange/v1/patient/order", headers=_auth(first_token)).status_code == 401

    replacement = _issue_access(client, clinician_token, provider.id, order_id)
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["access_mode"] == "ORDER_READ"
    replacement_token = replacement.json()["access_token"]

    receipt = client.get("/exchange/v1/patient/order", headers=_auth(replacement_token))
    assert receipt.json()["selection"]["total_amount"] == "300.00"
    assert receipt.json()["selection"]["currency"] == "DJF"

    denied = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(replacement_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert denied.status_code == 403


# -- one-live-grant still enforced for ORDER_READ issuance ---------------------------------------


def test_one_live_grant_enforced_for_order_read_reissuance(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="OneLiveOrderRead"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="OneLiveOrderReadLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201

    first = _issue_access(client, clinician_token, provider.id, order_id)
    assert first.status_code == 201
    second = _issue_access(client, clinician_token, provider.id, order_id)
    assert second.status_code == 409


# -- issuance authorization, DH-EX-010A qualification items 15/17 --------------------------------


def test_cross_provider_clinician_cannot_issue_access_for_another_providers_order(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    private_key, _ = keypair
    provider_a, order_id, entry_id, _token_a = _create_order(
        client, private_key, organizations, catalog, patients, name="CrossIssueA"
    )
    _provider_b, token_b = _setup_clinician(client, private_key, organizations, "CrossIssueB")
    response = _issue_access(client, token_b, provider_a.id, order_id)
    assert response.status_code in (403, 404)


def test_lab_tech_cannot_issue_provider_patient_access(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """LAB_TECH is only a valid role at a LAB organization (never PROVIDER), so this proves the
    stronger, more relevant fact directly: a LAB_TECH -- even one genuinely holding that role at
    the assigned lab itself -- has no clinician standing at the *provider* organization and cannot
    issue that provider's patient access grants."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="LabTechIssue"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="LabTechIssueLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201

    lab_tech_identity = _bootstrap_identity(client, private_key, "labtech-issue-attempt")
    _grant_roles(
        client, private_key, lab.id, lab_tech_identity, "labtech-issue-attempt", ["LAB_TECH"]
    )
    lab_tech_token = make_token(private_key, subject="labtech-issue-attempt")
    response = _issue_access(client, lab_tech_token, provider.id, order_id)
    assert response.status_code in (403, 404)


# -- audit atomicity on the ORDER_READ issuance path ----------------------------------------------


def test_order_read_issuance_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    """The mirror of ``test_exchange_patient_access.py``'s ``test_issuance_audit_atomicity``,
    specifically for the new ORDER_READ issuance path this ticket adds -- a failure writing the
    audit row must roll back the grant insert here too, not just on the pre-existing
    LAB_SELECTION path."""
    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="OrderReadAuditAtomic"
    )
    lab = _referral_lab(
        organizations,
        directory,
        catalog,
        name="OrderReadAuditAtomicLab",
        entry_id=entry_id,
        client=client,
        private_key=private_key,
        provider_id=provider.id,
    )
    assert _assign_lab(client, clinician_token, provider.id, order_id, lab.id).status_code == 201

    with sessions() as session:
        before_grants = session.scalar(select(func.count()).select_from(PatientAccessGrant))
        before_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit-insert failure"):
        _issue_access(client, clinician_token, provider.id, order_id)

    with sessions() as session:
        after_grants = session.scalar(select(func.count()).select_from(PatientAccessGrant))
        after_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))
    assert after_grants == before_grants
    assert after_audit == before_audit


# -- domain-layer defense-in-depth: unreachable via HTTP, proven directly against the repository ---


def test_select_refuses_order_read_grant_bypassing_the_route_layer(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """DH-EX-010A's second, independent enforcement layer:
    ``PatientAccessGrantLacksSelectionAuthority`` inside ``select()`` itself. The normal HTTP path
    (``POST /patient/order/selection``) always refuses an ``ORDER_READ`` grant at the route layer
    (``_require_lab_selection_capability``) before ``select()`` is ever called, so no HTTP-level
    test can exercise this branch -- it exists for a caller that bypasses the route entirely,
    exactly the same reasoning ``assign_by_provider``'s tenant check inside its own locked mutation
    has. Proven the same way ``test_exchange_patient_access.py``'s ``test_grant_bound_to_a_
    different_order_cannot_authorize_this_orders_selection`` proves its own route-bypassing IDOR
    check: call the repository directly.

    Uses a *patient-selected* order (not provider-assigned) and names the *same* lab the patient
    already chose -- the one scenario where ``select()``'s other guards
    (``DifferentLabAlreadySelected``, ``LabAlreadyAssignedByProvider``) do not also fire, so this
    test isolates the capability check itself rather than being coincidentally saved by an
    unrelated guard. Without the capability check, this exact call falls through to the same-lab
    replay branch and returns the existing selection successfully -- the unmasked version of
    negative control #1b."""
    from daher_adapter.exchange.selection import (
        LabSelectionRepository,
        PatientAccessGrantLacksSelectionAuthority,
    )

    private_key, _ = keypair
    provider, order_id, entry_id, clinician_token = _create_order(
        client, private_key, organizations, catalog, patients, name="DomainCapability"
    )
    lab = make_lab(organizations, name="DomainCapabilityLab")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("100.00"),
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

    first_access = _issue_access(client, clinician_token, provider.id, order_id)
    assert first_access.json()["access_mode"] == "LAB_SELECTION"
    first_token = first_access.json()["access_token"]
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(first_token)
    ).json()
    price_ids = [line["current_price_id"] for line in options["options"][0]["prices"]]
    selection = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(first_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert selection.status_code == 201

    client.delete(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    replacement = _issue_access(client, clinician_token, provider.id, order_id)
    assert replacement.status_code == 201
    assert replacement.json()["access_mode"] == "ORDER_READ"

    with sessions() as session:
        grant = session.scalar(
            select(PatientAccessGrant).where(
                PatientAccessGrant.lab_order_id == order_id,
                PatientAccessGrant.revoked_at.is_(None),
            )
        )
        grant_id = grant.id

    selections = LabSelectionRepository(sessions, organizations)
    with pytest.raises(PatientAccessGrantLacksSelectionAuthority):
        selections.select(
            lab_order_id=order_id,
            lab_organization_id=lab.id,
            expected_price_ids=frozenset(uuid.UUID(p) for p in price_ids),
            patient_access_grant_id=grant_id,
        )


# -- repository-level fail-closed proof (no HTTP route reaches this state today) ------------------


def test_issuance_fails_closed_for_cancelled_order_with_no_selection(sessions) -> None:
    """DH-EX-010A's explicit "do not guess" instruction: an order cancelled directly from
    AWAITING_LAB_SELECTION (no lab ever chosen) has no truthful access mode -- neither
    LAB_SELECTION (there is nothing left to choose) nor ORDER_READ (no LabSelection exists to
    read). No HTTP route in this codebase drives an order to CANCELLED without a prior selection
    today, so this is exercised directly against the repository, matching this suite's own
    established pattern for defensive branches with no HTTP path (e.g.
    ``test_exchange_provider_assignment.py``'s repository-only tests)."""
    from exchange_support import make_patient_reference, make_practitioner_reference, make_provider

    from daher_adapter.exchange.identity import (
        PatientReferenceRepository,
        PractitionerReferenceRepository,
    )
    from daher_adapter.exchange.models import LabOrderStatus
    from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
    from daher_adapter.exchange.patient_access import OrderNotIssuable, PatientAccessGrantRepository

    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients_repo = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    access = PatientAccessGrantRepository(sessions, ttl_hours=TTL_HOURS)

    provider = make_provider(organizations, name="CancelledNoSelection")
    entry, _ = catalog.create_entry(code="DHEALTH-AM-CANCEL", display_name="Cancel Test")
    patient = make_patient_reference(
        patients_repo, provider_org_id=provider.id, source_identity_value="cancel-1"
    )
    practitioner = make_practitioner_reference(
        practitioners, provider_org_id=provider.id, source_identity_value="cancel-doc-1"
    )
    order = orders.create(
        idempotency_key="cancel-no-selection-1",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order
    orders.transition(order.id, LabOrderStatus.CANCELLED)

    with pytest.raises(OrderNotIssuable):
        access.issue(lab_order_id=order.id)


# -- concurrency: the genuinely new race this ticket introduces -----------------------------------


def test_concurrent_assignment_and_issuance_race_yields_one_coherent_mode(tmp_path: Path) -> None:
    """The race that actually proves this design: ``assign_by_provider`` and ``issue()`` both lock
    the same ``LabOrder`` row, and whichever commits first decides the *other*'s outcome --
    issuing first must yield a `LAB_SELECTION` grant (and the later assignment must still succeed,
    since no lab is chosen yet when it starts); assigning first must yield an `ORDER_READ` grant.
    Both are correct; the assertion is internal coherence between the grant's mode and whether a
    `LabSelection` existed at the moment issuance actually committed, never a predetermined winner
    -- the same shape as DH-EX-010's own patient-vs-provider race proof."""
    import threading

    from sqlalchemy import create_engine, event

    from daher_adapter.exchange.identity import (
        PatientReferenceRepository,
        PractitionerReferenceRepository,
    )
    from daher_adapter.exchange.models import Base
    from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
    from daher_adapter.exchange.patient_access import PatientAccessGrantRepository
    from daher_adapter.exchange.provider_lab_routes import ProviderLabRouteRepository
    from daher_adapter.exchange.selection import LabSelectionRepository

    database = tmp_path / "assign-vs-issue-race.sqlite"
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
    local_access = PatientAccessGrantRepository(local_sessions, ttl_hours=TTL_HOURS)
    local_provider_lab_routes = ProviderLabRouteRepository(local_sessions, local_organizations)

    provider = make_provider(local_organizations, name="RaceProvider")
    entry, _ = local_catalog.create_entry(code="DHEALTH-AM-RACE", display_name="Race Test")
    lab = _referral_lab(
        local_organizations, local_directory, local_catalog, name="RaceLab", entry_id=entry.id
    )
    local_provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )
    patient = local_patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="SYNTH-RACE",
        display_name="Synthetic Patient",
    )
    practitioner = local_practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-practitioner-id",
        source_identity_value="SYNTH-RACE-DOC",
        display_name="Dr Synthetic",
    )
    order = local_orders.create(
        idempotency_key="assign-vs-issue-race-1",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order

    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def assign_attempt() -> None:
        try:
            barrier.wait(timeout=10)
            record, created = local_selections.assign_by_provider(
                lab_order_id=order.id,
                lab_organization_id=lab.id,
                provider_organization_id=provider.id,
            )
            results["assign"] = ("ok", created)
        except BaseException as error:  # noqa: BLE001
            results["assign"] = ("error", type(error).__name__)

    def issue_attempt() -> None:
        try:
            barrier.wait(timeout=10)
            issued = local_access.issue(lab_order_id=order.id)
            results["issue"] = ("ok", issued.access_mode.value)
        except BaseException as error:  # noqa: BLE001
            results["issue"] = ("error", type(error).__name__)

    t_assign = threading.Thread(target=assign_attempt)
    t_issue = threading.Thread(target=issue_attempt)
    t_assign.start()
    t_issue.start()
    t_assign.join(timeout=30)
    t_issue.join(timeout=30)

    assert results["assign"][0] == "ok", results
    assert results["issue"][0] == "ok", results

    with local_sessions() as session:
        from daher_adapter.exchange.models import LabSelection

        selection_count = session.scalar(select(func.count()).select_from(LabSelection))
        grant = session.scalar(select(PatientAccessGrant))
    assert selection_count == 1

    # Internal coherence, not a predetermined winner: whichever thread's mutation committed first
    # is what the other actually observed.
    if results["issue"][1] == "LAB_SELECTION":
        # issue() committed while no LabSelection existed yet -- assignment then proceeded
        # against an order still AWAITING_LAB_SELECTION and created the selection afterward.
        assert results["assign"] == ("ok", True)
    else:
        # assign_by_provider() committed first -- issue() observed the now-existing LabSelection.
        assert results["issue"][1] == "ORDER_READ"
    assert grant.access_mode == results["issue"][1]
    engine.dispose()
