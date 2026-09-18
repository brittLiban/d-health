"""DH-LAB-003 §34-§39: the order-scoped, read-only Native Lab result-entry-plan --
``GET /organizations/{lab_id}/lab-orders/{order_id}/result-entry-plan``.

Same harness pattern as ``test_exchange_result_templates.py``/
``test_exchange_sample_collection.py``: a bare ``FastAPI()`` app with ``build_exchange_router``
mounted directly.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_lab, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.organizations import OrganizationRepository

TTL_HOURS = 168

DEMO_OBSERVATION = {
    "source_code": "DEMO_A",
    "display_label": "Demo Measure A",
    "result_type": "NUMERIC",
    "numeric_unit": "demo-unit",
    "required": True,
    "sort_order": 0,
}


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


def _bootstrap_identity(client, private_key, subject: str, **token_kwargs) -> str:
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


def _setup_lab_tech_for(client, private_key, lab_id, name: str):
    identity_id = _bootstrap_identity(client, private_key, f"labtech-{name}")
    _grant_roles(client, private_key, lab_id, identity_id, f"labtech-{name}", ["LAB_TECH"])
    return make_token(private_key, subject=f"labtech-{name}")


def _setup_org_admin_for(client, private_key, org_id, name: str):
    identity_id = _bootstrap_identity(client, private_key, f"orgadmin-{name}")
    _grant_roles(client, private_key, org_id, identity_id, f"orgadmin-{name}", ["ORG_ADMIN"])
    return make_token(private_key, subject=f"orgadmin-{name}")


def _configure_route(client, private_key, provider_id, lab_id) -> None:
    platform_admin_token = make_token(
        private_key, subject=f"route-admin-{uuid.uuid4()}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.put(
        f"/exchange/v1/organizations/{provider_id}/lab-routes/{lab_id}",
        headers=_auth(platform_admin_token),
        json={"active": True},
    )
    assert response.status_code in (200, 201), response.text


def _build_in_progress_order(
    client,
    private_key,
    organizations,
    catalog,
    directory,
    patients,
    *,
    name: str,
    item_count: int = 1,
    configure_templates: bool = True,
):
    """Full journey: N catalog entries, one lab with N offerings (each with an active template
    unless `configure_templates=False`), a provider order requesting all N, provider-assigned
    routing, accept, collection, start-processing -> IN_PROGRESS. Returns (lab, order_id,
    entries, offerings, lab_tech_token, admin_token)."""
    provider, clinician_token = _setup_clinician(client, private_key, organizations, name)
    patient_record = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-{name}",
        display_name=f"Synthetic Patient {name}",
    )
    entries = []
    for i in range(item_count):
        entry, _ = catalog.create_entry(
            code=f"PLAN-{name}-{i}", display_name=f"Plan Test {name} {i}"
        )
        entries.append(entry)

    lab = make_lab(organizations, name=name)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address=f"{name} Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    admin_token = _setup_org_admin_for(client, private_key, lab.id, name)
    offerings = []
    for entry in entries:
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        offerings.append(offering)
        if configure_templates:
            created = client.post(
                f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates",
                headers=_auth(admin_token),
                json={"version": 1, "observations": [DEMO_OBSERVATION]},
            )
            assert created.status_code == 201, created.text
            template_id = created.json()["id"]
            activated = client.post(
                f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}"
                f"/result-templates/{template_id}/activate",
                headers=_auth(admin_token),
                json={"expected_current_template_id": None},
            )
            assert activated.status_code == 200, activated.text

    _configure_route(client, private_key, provider.id, lab.id)

    create_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_record.id),
            "catalog_entry_ids": [str(e.id) for e in entries],
        },
    )
    assert create_response.status_code == 201, create_response.text
    order_id = create_response.json()["id"]

    assign = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/assign-lab",
        headers=_auth(clinician_token),
        json={"lab_organization_id": str(lab.id)},
    )
    assert assign.status_code == 201, assign.text

    lab_tech_token = _setup_lab_tech_for(client, private_key, lab.id, name)
    accept = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept.status_code == 200, accept.text
    collect = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert collect.status_code == 201, collect.text
    start = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert start.status_code == 200, start.text
    assert start.json()["status"] == "IN_PROGRESS"

    return lab, order_id, entries, offerings, lab_tech_token, admin_token


# -- P-group: functional correctness -------------------------------------------------------------


def test_p1_in_progress_order_returns_plan(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    lab, order_id, *_rest, lab_tech_token, _admin = _build_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="P1"
    )
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(lab_tech_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["order_id"] == str(order_id)


def test_p2_one_configured_item_returns_active_template(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    lab, order_id, *_rest, lab_tech_token, _admin = _build_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="P2"
    )
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(lab_tech_token),
    )
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["template"] is not None
    assert body["items"][0]["template"]["observations"][0]["source_code"] == "DEMO_A"


def test_p3_multiple_items_each_resolve_own_template(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    lab, order_id, entries, offerings, lab_tech_token, _admin = _build_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="P3", item_count=3
    )
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(lab_tech_token),
    )
    body = response.json()
    assert len(body["items"]) == 3
    assert all(item["template"] is not None for item in body["items"])
    assert {item["catalog_entry_id"] for item in body["items"]} == {str(e.id) for e in entries}


def test_p4_missing_template_returns_null(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    lab, order_id, *_rest, lab_tech_token, _admin = _build_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="P4",
        configure_templates=False,
    )
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(lab_tech_token),
    )
    body = response.json()
    assert body["items"][0]["template"] is None


def test_p5_all_items_configured_false_when_any_missing(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    lab, order_id, *_rest, lab_tech_token, _admin = _build_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="P5",
        configure_templates=False,
    )
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(lab_tech_token),
    )
    assert response.json()["all_items_configured"] is False


def test_p6_all_templates_present_true(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    lab, order_id, *_rest, lab_tech_token, _admin = _build_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="P6", item_count=2
    )
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(lab_tech_token),
    )
    assert response.json()["all_items_configured"] is True


def test_p7_p8_inactive_historical_template_not_current(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    lab, order_id, entries, offerings, lab_tech_token, admin_token = _build_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="P7"
    )
    offering = offerings[0]
    v2_obs = {**DEMO_OBSERVATION, "source_code": "DEMO_V2"}
    v2 = client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates",
        headers=_auth(admin_token),
        json={"version": 2, "observations": [v2_obs]},
    ).json()["id"]
    v1_listing = client.get(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates",
        headers=_auth(admin_token),
    ).json()
    v1_id = next(t["id"] for t in v1_listing if t["version"] == 1)
    client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates/{v2}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": v1_id},
    )

    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(lab_tech_token),
    )
    body = response.json()
    # P7: the historical (now inactive) v1 is not what's returned.
    # P8: only the active template (v2) is returned.
    assert body["items"][0]["template"]["version"] == 2
    assert body["items"][0]["template"]["observations"][0]["source_code"] == "DEMO_V2"


def test_p9_offering_becoming_inactive_does_not_invalidate_plan(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    lab, order_id, entries, offerings, lab_tech_token, admin_token = _build_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="P9"
    )
    offering = offerings[0]
    deactivate = client.patch(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}",
        headers=_auth(admin_token),
        json={"active": False},
    )
    assert deactivate.status_code == 200, deactivate.text

    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(lab_tech_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["template"] is not None


# -- PS-group: security -----------------------------------------------------------------------


def test_ps1_correct_lab_tech_can_read(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    lab, order_id, *_rest, lab_tech_token, _admin = _build_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="PS1"
    )
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(lab_tech_token),
    )
    assert response.status_code == 200, response.text


def test_ps2_wrong_lab_anti_enumerates(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    lab, order_id, *_rest = _build_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="PS2A"
    )
    other_lab = make_lab(organizations, name="PS2B")
    directory.upsert(
        lab_organization_id=other_lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    other_tech = _setup_lab_tech_for(client, private_key, other_lab.id, "ps2other")
    response = client.get(
        f"/exchange/v1/organizations/{other_lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(other_tech),
    )
    assert response.status_code == 404, response.text


def test_ps3_provider_clinician_denied(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    lab, order_id, *_rest = _build_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="PS3"
    )
    identity_id = _bootstrap_identity(client, private_key, "ps3-clinician")
    _grant_roles(client, private_key, lab.id, identity_id, "ps3-clinician", [])
    clinician_token = make_token(private_key, subject="ps3-clinician")
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(clinician_token),
    )
    assert response.status_code in (403, 404), response.text


def test_ps4_releaser_only_denied(client, keypair, organizations, catalog, directory, patients):
    private_key, _ = keypair
    lab, order_id, *_rest = _build_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="PS4"
    )
    identity_id = _bootstrap_identity(client, private_key, "ps4-releaser")
    _grant_roles(client, private_key, lab.id, identity_id, "ps4-releaser", ["LAB_RESULT_RELEASER"])
    releaser_token = make_token(private_key, subject="ps4-releaser")
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(releaser_token),
    )
    assert response.status_code == 403, response.text


def test_ps5_order_not_in_progress_returns_conflict(
    client, keypair, organizations, catalog, directory, patients
):
    private_key, _ = keypair
    provider, clinician_token = _setup_clinician(client, private_key, organizations, "PS5")
    patient_record = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="SYNTH-PS5",
        display_name="Synthetic Patient PS5",
    )
    entry, _ = catalog.create_entry(code="PS5-ENTRY", display_name="PS5 Test")
    lab = make_lab(organizations, name="PS5Lab")
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    _configure_route(client, private_key, provider.id, lab.id)
    create_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_record.id), "catalog_entry_ids": [str(entry.id)]},
    )
    order_id = create_response.json()["id"]
    client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/assign-lab",
        headers=_auth(clinician_token),
        json={"lab_organization_id": str(lab.id)},
    )
    lab_tech_token = _setup_lab_tech_for(client, private_key, lab.id, "ps5")
    # Order is SELECTED, never accepted -- not IN_PROGRESS.
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/result-entry-plan",
        headers=_auth(lab_tech_token),
    )
    assert response.status_code == 409, response.text


# -- query-count (§39/§63): no N+1 --------------------------------------------------------------


def _count_queries(sessions, fn) -> int:
    query_count = {"n": 0}

    def _count(*_args, **_kwargs) -> None:
        query_count["n"] += 1

    engine = sessions.kw["bind"]
    event.listen(engine, "after_cursor_execute", _count)
    try:
        fn()
    finally:
        event.remove(engine, "after_cursor_execute", _count)
    return query_count["n"]


def test_result_entry_plan_query_count_is_flat_regardless_of_item_count(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """DH-LAB-003 §39/§63: query count for the plan read must not scale with item count -- one
    order+items read, one batched offering lookup, one batched catalog-entry lookup, and
    `get_active_templates_for_offerings`'s own two batched queries. Never one query pair per
    item."""
    private_key, _ = keypair
    lab_small, order_small, *_rest_small, tech_small, _admin_small = _build_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="NQSmall",
        item_count=1,
    )
    lab_large, order_large, *_rest_large, tech_large, _admin_large = _build_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="NQLarge",
        item_count=5,
    )

    def _read_small():
        r = client.get(
            f"/exchange/v1/organizations/{lab_small.id}/lab-orders/{order_small}/result-entry-plan",
            headers=_auth(tech_small),
        )
        assert r.status_code == 200, r.text

    def _read_large():
        r = client.get(
            f"/exchange/v1/organizations/{lab_large.id}/lab-orders/{order_large}/result-entry-plan",
            headers=_auth(tech_large),
        )
        assert r.status_code == 200, r.text

    small_count = _count_queries(sessions, _read_small)
    large_count = _count_queries(sessions, _read_large)

    assert large_count - small_count <= 1, (
        f"query count grew {large_count - small_count} between a 1-item and a 5-item order "
        f"({small_count} -> {large_count}) -- the result-entry plan should be flat/bounded, not "
        f"scaling with item count"
    )
