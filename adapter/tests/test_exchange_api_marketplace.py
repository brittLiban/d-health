"""DH-EX-003's HTTP-level required matrix: catalog/directory/offering/price authorization,
tenant-isolation negative controls, marketplace read-only-query guarantees, and transactional
audit atomicity for the new mutation types.

Same harness pattern as ``test_exchange_api.py``: a bare ``FastAPI()`` app with
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
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    LabOffering,
    TestCatalogEntry,
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
def directory(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> LabDirectoryProfileRepository:
    return LabDirectoryProfileRepository(sessions, organizations)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bootstrap_identity(client: TestClient, private_key, subject: str, **token_kwargs) -> str:
    token = make_token(private_key, subject=subject, **token_kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"]


def _db_events_of_type(sessions: sessionmaker[Session], event_type: str) -> list[dict]:
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


def _grant_org_admin(client: TestClient, private_key, org_id, identity_id, subject: str) -> None:
    """Set up via the API's own admin-membership machinery, not a shortcut -- a platform-admin
    token creates the org and grants the target ORG_ADMIN, matching real onboarding flow."""
    platform_admin_token = make_token(
        private_key, subject=f"setup-admin-{subject}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(platform_admin_token),
        json={"identity_id": identity_id, "roles": ["ORG_ADMIN"]},
    )
    assert response.status_code in (200, 201), response.text


def _create_org_via_api(client: TestClient, private_key, *, name: str, org_type: str) -> dict:
    admin_token = make_token(
        private_key, subject=f"platform-admin-{name}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.post(
        "/exchange/v1/organizations",
        headers=_auth(admin_token),
        json={"name": name, "organization_type": org_type},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _setup_lab_admin(
    client: TestClient, private_key, organizations: OrganizationRepository, name: str
):
    lab = make_lab(organizations, name=name)
    identity_id = _bootstrap_identity(client, private_key, f"lab-admin-{name}")
    _grant_org_admin(client, private_key, lab.id, identity_id, f"lab-admin-{name}")
    token = make_token(private_key, subject=f"lab-admin-{name}")
    return lab, token


def _setup_lab_tech(
    client: TestClient, private_key, organizations: OrganizationRepository, lab, name: str
) -> str:
    """A LAB_TECH member of an *existing* lab -- has an ACTIVE membership (so network reads are
    permitted) but never ORG_ADMIN, so lab-management mutations must stay refused."""
    platform_admin_token = make_token(
        private_key, subject=f"setup-tech-{name}", roles=[PLATFORM_ADMIN_ROLE]
    )
    identity_id = _bootstrap_identity(client, private_key, f"lab-tech-{name}")
    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/memberships",
        headers=_auth(platform_admin_token),
        json={"identity_id": identity_id, "roles": ["LAB_TECH"]},
    )
    assert response.status_code in (200, 201), response.text
    return make_token(private_key, subject=f"lab-tech-{name}")


# -- catalog: platform-admin only, synthetic-only, idempotent ------------------------------------


def test_catalog_create_requires_platform_admin(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    lab, lab_admin_token = _setup_lab_admin(client, private_key, organizations, "A")
    response = client.post(
        "/exchange/v1/catalog/tests",
        headers=_auth(lab_admin_token),
        json={"code": "DHEALTH-DEMO-A", "display_name": "Demo Test A"},
    )
    assert response.status_code == 403


def test_catalog_create_is_synthetic_only_and_cannot_be_overridden(
    client: TestClient, keypair
) -> None:
    private_key, _ = keypair
    admin_token = make_token(private_key, subject="platform-admin-1", roles=[PLATFORM_ADMIN_ROLE])
    response = client.post(
        "/exchange/v1/catalog/tests",
        headers=_auth(admin_token),
        json={"code": "DHEALTH-DEMO-A", "display_name": "Demo Test A", "is_synthetic": False},
    )
    # `is_synthetic` is not a field on the request schema at all -- extra="forbid" rejects it.
    assert response.status_code == 422


def test_catalog_create_replay_is_idempotent_over_http(
    client: TestClient, keypair, sessions
) -> None:
    private_key, _ = keypair
    admin_token = make_token(private_key, subject="platform-admin-1", roles=[PLATFORM_ADMIN_ROLE])
    body = {"code": "DHEALTH-DEMO-A", "display_name": "Demo Test A"}
    first = client.post("/exchange/v1/catalog/tests", headers=_auth(admin_token), json=body)
    second = client.post("/exchange/v1/catalog/tests", headers=_auth(admin_token), json=body)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    events = _db_events_of_type(sessions, "catalog_entry_created")
    outcomes = [e["outcome"] for e in events]
    assert outcomes.count("created") == 1
    assert outcomes.count("returned_existing") == 1
    assert events[0]["resource_type"] == "catalog_entry"
    assert events[0]["organization_id"] is None  # platform-level mutation, no owning org


def test_catalog_list_hides_inactive_and_non_synthetic_from_ordinary_member(
    client: TestClient, keypair, organizations, catalog, sessions
) -> None:
    """DH-EX-003R Part E: `GET /catalog/tests` originally returned every row unfiltered to any
    authenticated network member, with no platform-admin distinction -- an ordinary member could
    see an inactive or non-synthetic entry as though it were selectable pilot inventory merely
    because it exists in the DB. Proves the fix: ordinary member sees only ACTIVE+SYNTHETIC;
    platform admin sees everything."""
    from daher_adapter.exchange.models import TestCatalogEntry

    visible, _ = catalog.create_entry(code="DHEALTH-VISIBLE", display_name="Visible")
    inactive, _ = catalog.create_entry(code="DHEALTH-INACTIVE", display_name="Inactive")
    non_synthetic, _ = catalog.create_entry(
        code="DHEALTH-REAL", display_name="Not Synthetic", is_synthetic=False
    )
    with sessions() as session:
        session.get(TestCatalogEntry, inactive.id).active = False
        session.commit()

    private_key, _ = keypair
    _lab, member_token = _setup_lab_admin(client, private_key, organizations, "A")
    member_ids = {
        entry["id"]
        for entry in client.get("/exchange/v1/catalog/tests", headers=_auth(member_token)).json()
    }
    assert member_ids == {str(visible.id)}

    admin_token = make_token(private_key, subject="platform-admin-1", roles=[PLATFORM_ADMIN_ROLE])
    admin_ids = {
        entry["id"]
        for entry in client.get("/exchange/v1/catalog/tests", headers=_auth(admin_token)).json()
    }
    assert admin_ids == {str(visible.id), str(inactive.id), str(non_synthetic.id)}


def test_catalog_list_requires_active_membership(client: TestClient, keypair) -> None:
    private_key, _ = keypair
    token = make_token(private_key, subject="zero-membership-sub")
    response = client.get("/exchange/v1/catalog/tests", headers=_auth(token))
    assert response.status_code == 403


def test_catalog_list_unauthenticated_is_401(client: TestClient) -> None:
    response = client.get("/exchange/v1/catalog/tests")
    assert response.status_code == 401


# -- lab directory profile: tenant isolation, safety defaults -------------------------------------


def test_provider_org_admin_cannot_set_a_lab_directory_profile(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations)
    identity_id = _bootstrap_identity(client, private_key, "provider-admin")
    _grant_org_admin(client, private_key, provider.id, identity_id, "provider-admin")
    token = make_token(private_key, subject="provider-admin")

    response = client.put(
        f"/exchange/v1/organizations/{provider.id}/directory-profile",
        headers=_auth(token),
        json={"public_address": "X", "listed": True, "accepting_orders": True},
    )
    assert response.status_code == 422


def test_lab_admin_cannot_manage_a_different_labs_directory_profile(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    lab_a, lab_a_token = _setup_lab_admin(client, private_key, organizations, "A")
    lab_b, _ = _setup_lab_admin(client, private_key, organizations, "B")

    response = client.put(
        f"/exchange/v1/organizations/{lab_b.id}/directory-profile",
        headers=_auth(lab_a_token),
        json={"public_address": "X", "listed": True, "accepting_orders": True},
    )
    assert response.status_code == 404


def test_directory_profile_defaults_to_unlisted_not_accepting(
    client: TestClient, keypair, organizations, sessions
) -> None:
    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")
    response = client.put(
        f"/exchange/v1/organizations/{lab.id}/directory-profile",
        headers=_auth(token),
        json={"public_address": "123 Main St"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["listed"] is False
    assert body["accepting_orders"] is False


def test_directory_profile_update_and_replay_audit_sequence(
    client: TestClient, keypair, organizations, sessions
) -> None:
    """DH-EX-003R Part S: closes the disclosed gap -- `lab_directory_profile_updated` had a live
    HTTP call site but no test ever PUT the same lab's profile twice. Proves the full sequence:
    create -> real update -> no-op replay, with the audit trail matching each step exactly (no
    false 'updated' outcome on the no-op)."""
    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")

    created = client.put(
        f"/exchange/v1/organizations/{lab.id}/directory-profile",
        headers=_auth(token),
        json={"public_address": "First Address", "listed": True, "accepting_orders": True},
    )
    assert created.status_code == 201

    updated = client.put(
        f"/exchange/v1/organizations/{lab.id}/directory-profile",
        headers=_auth(token),
        json={"public_address": "Second Address", "listed": True, "accepting_orders": True},
    )
    assert updated.status_code == 200
    assert updated.json()["public_address"] == "Second Address"

    replayed = client.put(
        f"/exchange/v1/organizations/{lab.id}/directory-profile",
        headers=_auth(token),
        json={"public_address": "Second Address", "listed": True, "accepting_orders": True},
    )
    assert replayed.status_code == 200

    created_events = _db_events_of_type(sessions, "lab_directory_profile_created")
    updated_events = _db_events_of_type(sessions, "lab_directory_profile_updated")
    assert len(created_events) == 1
    assert created_events[0]["outcome"] == "created"
    assert created_events[0]["resource_type"] == "lab_directory_profile"
    assert created_events[0]["organization_id"] == str(lab.id)
    assert len(updated_events) == 2, "one real update, one no-op replay -- both hit the PUT branch"
    outcomes = [e["outcome"] for e in updated_events]
    assert outcomes.count("updated") == 1, "the real content change"
    assert outcomes.count("unchanged") == 1, "the identical replay must never report 'updated'"


def test_lat_long_must_both_be_present_or_both_absent(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")
    response = client.put(
        f"/exchange/v1/organizations/{lab.id}/directory-profile",
        headers=_auth(token),
        json={"public_address": "X", "latitude": "11.5"},
    )
    assert response.status_code == 422


def test_out_of_range_latitude_rejected(client: TestClient, keypair, organizations) -> None:
    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")
    response = client.put(
        f"/exchange/v1/organizations/{lab.id}/directory-profile",
        headers=_auth(token),
        json={"public_address": "X", "latitude": "91", "longitude": "0"},
    )
    assert response.status_code == 422


def test_unlisted_lab_not_visible_to_a_normal_member(
    client: TestClient, keypair, organizations, directory
) -> None:
    private_key, _ = keypair
    lab = make_lab(organizations, name="Hidden Lab")
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    other_lab, other_token = _setup_lab_admin(client, private_key, organizations, "Other")

    listing = client.get("/exchange/v1/labs", headers=_auth(other_token))
    assert lab.id not in [entry["lab_organization_id"] for entry in listing.json()]

    detail = client.get(f"/exchange/v1/labs/{lab.id}", headers=_auth(other_token))
    assert detail.status_code == 404


def test_platform_admin_can_see_an_unlisted_lab_detail(
    client: TestClient, keypair, organizations, directory
) -> None:
    private_key, _ = keypair
    lab = make_lab(organizations, name="Hidden Lab")
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    admin_token = make_token(private_key, subject="platform-admin-1", roles=[PLATFORM_ADMIN_ROLE])
    detail = client.get(f"/exchange/v1/labs/{lab.id}", headers=_auth(admin_token))
    assert detail.status_code == 200


# -- offerings: tenant isolation, no silent reactivation -------------------------------------------


def test_provider_org_admin_cannot_create_an_offering(
    client: TestClient, keypair, organizations, catalog
) -> None:
    private_key, _ = keypair
    provider = make_provider(organizations)
    identity_id = _bootstrap_identity(client, private_key, "provider-admin")
    _grant_org_admin(client, private_key, provider.id, identity_id, "provider-admin")
    token = make_token(private_key, subject="provider-admin")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/offerings",
        headers=_auth(token),
        json={"catalog_entry_id": str(entry.id)},
    )
    assert response.status_code == 422


def test_offering_status_update_on_wrong_lab_is_404(
    client: TestClient, keypair, organizations, catalog, sessions
) -> None:
    private_key, _ = keypair
    lab_a, lab_a_token = _setup_lab_admin(client, private_key, organizations, "A")
    lab_b, lab_b_token = _setup_lab_admin(client, private_key, organizations, "B")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab_a.id, catalog_entry_id=entry.id)

    response = client.patch(
        f"/exchange/v1/organizations/{lab_b.id}/offerings/{offering.id}",
        headers=_auth(lab_b_token),
        json={"active": False},
    )
    assert response.status_code == 404
    # DH-EX-003R Part T: a refused cross-tenant PATCH must leave zero successful-mutation audit
    # rows behind -- and the offering itself must still be active, not silently toggled.
    assert _db_events_of_type(sessions, "lab_offering_status_changed") == []
    assert catalog.get_offering(offering.id).active is True


def test_offering_status_update_full_sequence_audit(
    client: TestClient, keypair, organizations, catalog, sessions
) -> None:
    """DH-EX-003R Part T: closes the disclosed gap -- the suite's only PATCH call was the
    cross-tenant 404 above, so the successful-mutation branch (and its audit row) was never
    exercised. Proves ACTIVE -> INACTIVE -> ACTIVE, then a same-state no-op, with the audit trail
    matching each step exactly."""
    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    deactivate = client.patch(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}",
        headers=_auth(token),
        json={"active": False},
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["active"] is False

    reactivate = client.patch(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}",
        headers=_auth(token),
        json={"active": True},
    )
    assert reactivate.status_code == 200
    assert reactivate.json()["active"] is True

    no_op = client.patch(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}",
        headers=_auth(token),
        json={"active": True},
    )
    assert no_op.status_code == 200

    events = _db_events_of_type(sessions, "lab_offering_status_changed")
    assert len(events) == 3
    outcomes = [e["outcome"] for e in events]
    assert outcomes.count("updated") == 2, "deactivate, then reactivate"
    assert outcomes.count("unchanged") == 1, "the same-state no-op must never report 'updated'"
    for event in events:
        assert event["resource_type"] == "lab_offering"
        assert event["resource_id"] == str(offering.id)
        assert event["organization_id"] == str(lab.id)


def test_lab_tech_cannot_manage_offerings_profile_or_price(
    client: TestClient, keypair, organizations, catalog
) -> None:
    """DH-EX-003R Part G/Z: LAB_TECH holds an ACTIVE membership (network reads are fine) but never
    ORG_ADMIN -- every lab-management mutation must stay refused, not just untested."""
    private_key, _ = keypair
    lab, admin_token = _setup_lab_admin(client, private_key, organizations, "A")
    tech_token = _setup_lab_tech(client, private_key, organizations, lab, "A")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    assert (
        client.put(
            f"/exchange/v1/organizations/{lab.id}/directory-profile",
            headers=_auth(tech_token),
            json={"public_address": "X"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/exchange/v1/organizations/{lab.id}/offerings",
            headers=_auth(tech_token),
            json={"catalog_entry_id": str(entry.id)},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}",
            headers=_auth(tech_token),
            json={"active": False},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/prices",
            headers=_auth(tech_token),
            json={"amount": "1000", "currency": "DJF", "expected_current_price_id": None},
        ).status_code
        == 403
    )
    # Sanity: the same lab's real ORG_ADMIN can still do all of this -- the refusals above are
    # about the LAB_TECH role specifically, not a broken endpoint.
    assert (
        client.post(
            f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/prices",
            headers=_auth(admin_token),
            json={"amount": "1000", "currency": "DJF", "expected_current_price_id": None},
        ).status_code
        == 201
    )


def test_price_update_on_wrong_lab_offering_is_404(
    client: TestClient, keypair, organizations, catalog
) -> None:
    private_key, _ = keypair
    lab_a, _ = _setup_lab_admin(client, private_key, organizations, "A")
    lab_b, lab_b_token = _setup_lab_admin(client, private_key, organizations, "B")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab_a.id, catalog_entry_id=entry.id)

    response = client.post(
        f"/exchange/v1/organizations/{lab_b.id}/offerings/{offering.id}/prices",
        headers=_auth(lab_b_token),
        json={"amount": "1000", "currency": "DJF", "expected_current_price_id": None},
    )
    assert response.status_code == 404


def test_price_wrong_currency_rejected(client: TestClient, keypair, organizations, catalog) -> None:
    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/prices",
        headers=_auth(token),
        json={"amount": "1000", "currency": "USD", "expected_current_price_id": None},
    )
    assert response.status_code == 422


def test_price_amount_precision_and_scale_are_enforced(
    client: TestClient, keypair, organizations, catalog
) -> None:
    """DH-EX-003R Part I: `exchange_lab_prices.amount` is `Numeric(12, 2)` -- more than 2 decimal
    places or more than 10 integer digits must be refused at the schema boundary (422), not
    reach the database and be silently rounded or raise a raw, unfiltered DB error."""
    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    def _try(amount: str) -> int:
        return client.post(
            f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/prices",
            headers=_auth(token),
            json={"amount": amount, "currency": "DJF", "expected_current_price_id": None},
        ).status_code

    assert _try("1000.123") == 422, "more than 2 decimal places must be refused"
    assert _try("99999999999.00") == 422, "11 integer digits exceeds Numeric(12, 2)'s capacity"
    assert _try("-1") == 422, "negative amounts must be refused"
    assert _try("0") == 201, "zero is a valid price"


def test_price_conflict_maps_to_409(client: TestClient, keypair, organizations, catalog) -> None:
    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/prices",
        headers=_auth(token),
        json={"amount": "1000", "currency": "DJF", "expected_current_price_id": None},
    )
    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/prices",
        headers=_auth(token),
        json={"amount": "1500", "currency": "DJF", "expected_current_price_id": None},
    )
    assert response.status_code == 409


# -- audit atomicity for the new mutation types ---------------------------------------------------


def test_offering_and_price_mutations_each_write_an_authoritative_audit_row(
    client: TestClient, keypair, organizations, catalog, sessions
) -> None:
    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")

    offering_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings",
        headers=_auth(token),
        json={"catalog_entry_id": str(entry.id)},
    )
    assert offering_response.status_code == 201
    offering_id = offering_response.json()["id"]

    price_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering_id}/prices",
        headers=_auth(token),
        json={"amount": "1000", "currency": "DJF", "expected_current_price_id": None},
    )
    assert price_response.status_code == 201

    offering_events = _db_events_of_type(sessions, "lab_offering_created")
    price_events = _db_events_of_type(sessions, "lab_price_changed")
    assert len(offering_events) == 1
    assert offering_events[0]["resource_type"] == "lab_offering"
    assert offering_events[0]["organization_id"] == str(lab.id)
    assert len(price_events) == 1
    assert price_events[0]["resource_type"] == "lab_price"
    assert price_events[0]["outcome"] == "created"


def test_offering_create_failure_leaves_zero_mutation_and_zero_audit_row(
    client: TestClient, keypair, organizations, sessions, monkeypatch
) -> None:
    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")

    with sessions() as session:
        before_offerings = session.scalar(select(func.count()).select_from(LabOffering))
        before_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    catalog_repo = CatalogRepository(sessions, organizations)
    entry, _ = catalog_repo.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")

    with pytest.raises(RuntimeError, match="simulated audit-insert failure"):
        client.post(
            f"/exchange/v1/organizations/{lab.id}/offerings",
            headers=_auth(token),
            json={"catalog_entry_id": str(entry.id)},
        )

    with sessions() as session:
        after_offerings = session.scalar(select(func.count()).select_from(LabOffering))
        after_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))
    assert after_offerings == before_offerings
    assert after_audit == before_audit


def test_catalog_create_failure_leaves_zero_mutation_and_zero_audit_row(
    client: TestClient, keypair, sessions, monkeypatch
) -> None:
    """DH-EX-003R Part W: audit-atomicity failure injection for the remaining three mutation
    classes the existing offering-creation test did not cover -- catalog creation here."""
    from daher_adapter.exchange.models import TestCatalogEntry

    private_key, _ = keypair
    admin_token = make_token(private_key, subject="platform-admin-1", roles=[PLATFORM_ADMIN_ROLE])

    with sessions() as session:
        before_entries = session.scalar(select(func.count()).select_from(TestCatalogEntry))
        before_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit-insert failure"):
        client.post(
            "/exchange/v1/catalog/tests",
            headers=_auth(admin_token),
            json={"code": "DHEALTH-DEMO-A", "display_name": "Demo Test A"},
        )

    with sessions() as session:
        after_entries = session.scalar(select(func.count()).select_from(TestCatalogEntry))
        after_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))
    assert after_entries == before_entries
    assert after_audit == before_audit


def test_directory_profile_failure_leaves_zero_mutation_and_zero_audit_row(
    client: TestClient, keypair, organizations, sessions, monkeypatch
) -> None:
    """DH-EX-003R Part W: audit-atomicity failure injection -- directory profile mutation."""
    from daher_adapter.exchange.models import LabDirectoryProfile

    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")

    with sessions() as session:
        before_profiles = session.scalar(select(func.count()).select_from(LabDirectoryProfile))
        before_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit-insert failure"):
        client.put(
            f"/exchange/v1/organizations/{lab.id}/directory-profile",
            headers=_auth(token),
            json={"public_address": "X"},
        )

    with sessions() as session:
        after_profiles = session.scalar(select(func.count()).select_from(LabDirectoryProfile))
        after_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))
    assert after_profiles == before_profiles
    assert after_audit == before_audit


def test_offering_status_change_failure_leaves_zero_mutation_and_zero_audit_row(
    client: TestClient, keypair, organizations, catalog, sessions, monkeypatch
) -> None:
    """DH-EX-003R Part W: audit-atomicity failure injection -- offering status change."""
    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    with sessions() as session:
        before_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit-insert failure"):
        client.patch(
            f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}",
            headers=_auth(token),
            json={"active": False},
        )

    with sessions() as session:
        after_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))
    assert after_audit == before_audit
    assert catalog.get_offering(offering.id).active is True, "the toggle must not have survived"


def test_price_change_failure_leaves_zero_mutation_and_zero_audit_row(
    client: TestClient, keypair, organizations, catalog, sessions, monkeypatch
) -> None:
    """DH-EX-003R Part W: audit-atomicity failure injection -- price change."""
    from daher_adapter.exchange.models import LabPrice

    private_key, _ = keypair
    lab, token = _setup_lab_admin(client, private_key, organizations, "A")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    with sessions() as session:
        before_prices = session.scalar(select(func.count()).select_from(LabPrice))
        before_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated audit-insert failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit-insert failure"):
        client.post(
            f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/prices",
            headers=_auth(token),
            json={"amount": "1000", "currency": "DJF", "expected_current_price_id": None},
        )

    with sessions() as session:
        after_prices = session.scalar(select(func.count()).select_from(LabPrice))
        after_audit = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))
    assert after_prices == before_prices
    assert after_audit == before_audit


# -- marketplace: read-only, no anonymous access, correct exclusions ------------------------------


def _full_scenario(client: TestClient, keypair, organizations, catalog, directory):
    private_key, _ = keypair
    lab_a, lab_a_token = _setup_lab_admin(client, private_key, organizations, "A")
    entry_a, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")

    offering, _ = catalog.create_offering(lab_organization_id=lab_a.id, catalog_entry_id=entry_a.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    directory.upsert(
        lab_organization_id=lab_a.id,
        public_address="Lab A",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    return entry_a, lab_a, lab_a_token


def test_marketplace_requires_authentication(client: TestClient) -> None:
    response = client.post("/exchange/v1/marketplace/lab-options", json={"catalog_entry_ids": []})
    assert response.status_code == 401


def test_active_membership_in_inactive_organization_cannot_browse_network(
    client: TestClient, keypair, organizations, sessions
) -> None:
    """DH-EX-003R Part D BLOCKER: an ACTIVE membership inside a deactivated organization must not
    remain a standing backdoor to catalog/directory/marketplace reads -- confirms the
    `require_any_active_membership` fix actually closes it, at the HTTP layer, not just at the
    authorization-service unit level."""
    from daher_adapter.exchange.models import Organization

    private_key, _ = keypair
    provider = make_provider(organizations, name="Soon Deactivated")
    identity_id = _bootstrap_identity(client, private_key, "member-of-deactivated-org")
    _grant_org_admin(client, private_key, provider.id, identity_id, "member-of-deactivated-org")
    token = make_token(private_key, subject="member-of-deactivated-org")

    # Sanity: network reads work *before* the organization is deactivated.
    before = client.get("/exchange/v1/catalog/tests", headers=_auth(token))
    assert before.status_code == 200

    with sessions() as session:
        org_row = session.get(Organization, provider.id)
        org_row.status = "INACTIVE"
        session.commit()

    for response in (
        client.get("/exchange/v1/catalog/tests", headers=_auth(token)),
        client.get("/exchange/v1/labs", headers=_auth(token)),
        client.post(
            "/exchange/v1/marketplace/lab-options",
            headers=_auth(token),
            json={"catalog_entry_ids": [str(uuid.uuid4())]},
        ),
    ):
        assert response.status_code == 403, response.text

    # `/me` itself is unaffected -- this is a network-browsing gate, not an authentication gate.
    assert client.get("/exchange/v1/me", headers=_auth(token)).status_code == 200


def test_marketplace_requires_active_membership(client: TestClient, keypair) -> None:
    private_key, _ = keypair
    token = make_token(private_key, subject="zero-membership")
    response = client.post(
        "/exchange/v1/marketplace/lab-options",
        headers=_auth(token),
        json={"catalog_entry_ids": []},
    )
    # Schema validation (min_length=1) would also 422 an empty list -- use a real UUID to isolate
    # the authorization gate specifically.
    assert response.status_code in (403, 422)
    if response.status_code == 422:
        response = client.post(
            "/exchange/v1/marketplace/lab-options",
            headers=_auth(token),
            json={"catalog_entry_ids": [str(uuid.uuid4())]},
        )
        assert response.status_code == 403


def test_marketplace_returns_eligible_lab_with_correct_shape(
    client: TestClient, keypair, organizations, catalog, directory
) -> None:
    entry_a, lab_a, lab_a_token = _full_scenario(client, keypair, organizations, catalog, directory)
    response = client.post(
        "/exchange/v1/marketplace/lab-options",
        headers=_auth(lab_a_token),
        json={"catalog_entry_ids": [str(entry_a.id)]},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["options"]) == 1
    option = body["options"][0]
    assert option["lab"]["id"] == str(lab_a.id)
    assert option["total_amount"] == "1000.00" or Decimal(option["total_amount"]) == Decimal("1000")
    assert option["currency"] == "DJF"
    assert "as_of" in body


def test_marketplace_excludes_not_accepting_orders(
    client: TestClient, keypair, organizations, catalog, directory
) -> None:
    private_key, _ = keypair
    lab, lab_token = _setup_lab_admin(client, private_key, organizations, "A")
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
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
        accepting_orders=False,  # listed, but not accepting
    )
    response = client.post(
        "/exchange/v1/marketplace/lab-options",
        headers=_auth(lab_token),
        json={"catalog_entry_ids": [str(entry.id)]},
    )
    assert response.status_code == 200
    assert response.json()["options"] == []


def test_marketplace_non_synthetic_catalog_entry_fails_closed(
    client: TestClient, keypair, organizations, catalog, directory
) -> None:
    """A non-synthetic catalog entry must never be exposed as pilot-approved marketplace
    inventory -- even fully offered and priced, it must fail closed, matching the same 422
    treatment as an inactive entry."""
    private_key, _ = keypair
    lab, lab_token = _setup_lab_admin(client, private_key, organizations, "A")
    entry, _ = catalog.create_entry(
        code="DHEALTH-REAL-A", display_name="Not Synthetic", is_synthetic=False
    )
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
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
    response = client.post(
        "/exchange/v1/marketplace/lab-options",
        headers=_auth(lab_token),
        json={"catalog_entry_ids": [str(entry.id)]},
    )
    assert response.status_code == 422


def test_marketplace_duplicate_entry_ids_rejected(
    client: TestClient, keypair, organizations, catalog, directory
) -> None:
    entry_a, _lab_a, lab_a_token = _full_scenario(
        client, keypair, organizations, catalog, directory
    )
    response = client.post(
        "/exchange/v1/marketplace/lab-options",
        headers=_auth(lab_a_token),
        json={"catalog_entry_ids": [str(entry_a.id), str(entry_a.id)]},
    )
    assert response.status_code == 422


def test_marketplace_inactive_catalog_entry_fails_closed(
    client: TestClient, keypair, organizations, catalog, directory, sessions
) -> None:
    entry_a, _lab_a, lab_a_token = _full_scenario(
        client, keypair, organizations, catalog, directory
    )
    with sessions() as session:
        row = session.get(TestCatalogEntry, entry_a.id)
        row.active = False
        session.commit()

    response = client.post(
        "/exchange/v1/marketplace/lab-options",
        headers=_auth(lab_a_token),
        json={"catalog_entry_ids": [str(entry_a.id)]},
    )
    assert response.status_code == 422


def test_marketplace_unknown_catalog_entry_is_404(
    client: TestClient, keypair, organizations
) -> None:
    private_key, _ = keypair
    _lab, token = _setup_lab_admin(client, private_key, organizations, "A")
    response = client.post(
        "/exchange/v1/marketplace/lab-options",
        headers=_auth(token),
        json={"catalog_entry_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 404


def test_marketplace_creates_no_order_mutation(
    client: TestClient, keypair, organizations, catalog, directory, sessions
) -> None:
    """Permanent negative control: the marketplace query must never create a LabOrder,
    LabSelection, LabOrderItem, ResultDelivery, or DoctorReview."""
    from daher_adapter.exchange.models import (
        DoctorReview,
        LabOrder,
        LabOrderItem,
        LabSelection,
        ResultDelivery,
    )

    entry_a, _lab_a, lab_a_token = _full_scenario(
        client, keypair, organizations, catalog, directory
    )

    with sessions() as session:
        before = {
            model.__name__: session.scalar(select(func.count()).select_from(model))
            for model in (LabOrder, LabOrderItem, LabSelection, ResultDelivery, DoctorReview)
        }

    response = client.post(
        "/exchange/v1/marketplace/lab-options",
        headers=_auth(lab_a_token),
        json={"catalog_entry_ids": [str(entry_a.id)]},
    )
    assert response.status_code == 200

    with sessions() as session:
        after = {
            model.__name__: session.scalar(select(func.count()).select_from(model))
            for model in (LabOrder, LabOrderItem, LabSelection, ResultDelivery, DoctorReview)
        }
    assert before == after


def test_marketplace_produces_no_authoritative_audit_row(
    client: TestClient, keypair, organizations, catalog, directory, sessions
) -> None:
    entry_a, _lab_a, lab_a_token = _full_scenario(
        client, keypair, organizations, catalog, directory
    )
    with sessions() as session:
        before = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))

    client.post(
        "/exchange/v1/marketplace/lab-options",
        headers=_auth(lab_a_token),
        json={"catalog_entry_ids": [str(entry_a.id)]},
    )

    with sessions() as session:
        after = session.scalar(select(func.count()).select_from(ExchangeSecurityAuditEvent))
    assert before == after
