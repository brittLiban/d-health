"""DH-LAB-003/ADR-053: controlled, immutable, ``LabOffering``-scoped result-structure templates.

``POST/GET .../offerings/{offering_id}/result-templates``,
``POST .../result-templates/{template_id}/activate``.

Same harness pattern as ``test_exchange_sample_collection.py``: a bare ``FastAPI()`` app with
``build_exchange_router`` mounted directly.
"""

from __future__ import annotations

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


def _setup_lab_with_offering(organizations, directory, catalog, *, name: str):
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
    entry, _ = catalog.create_entry(code=f"DEMO-{name}", display_name=f"Demo Test {name}")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    return lab, entry, offering


def _setup_org_admin_for(client, private_key, org_id, name: str):
    identity_id = _bootstrap_identity(client, private_key, f"orgadmin-{name}")
    _grant_roles(client, private_key, org_id, identity_id, f"orgadmin-{name}", ["ORG_ADMIN"])
    return make_token(private_key, subject=f"orgadmin-{name}")


def _setup_lab_tech_for(client, private_key, lab_id, name: str):
    identity_id = _bootstrap_identity(client, private_key, f"labtech-{name}")
    _grant_roles(client, private_key, lab_id, identity_id, f"labtech-{name}", ["LAB_TECH"])
    return make_token(private_key, subject=f"labtech-{name}")


def _create_template(client, token, lab_id, offering_id, *, version=1, observations=None):
    if observations is None:
        observations = [DEMO_OBSERVATION]
    return client.post(
        f"/exchange/v1/organizations/{lab_id}/offerings/{offering_id}/result-templates",
        headers=_auth(token),
        json={"version": version, "observations": observations},
    )


def _audit_events(sessions, event_type: str) -> list[dict]:
    with sessions() as session:
        rows = session.scalars(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == event_type
            )
        ).all()
        return [{"outcome": row.outcome, "resource_id": str(row.resource_id)} for row in rows]


# -- T-group: create ---------------------------------------------------------------------------


def test_t1_create_one_valid_template(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="T1")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "t1")

    response = _create_template(client, admin_token, lab.id, offering.id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"] == 1
    assert body["active"] is False
    assert len(body["observations"]) == 1
    assert body["observations"][0]["source_code"] == "DEMO_A"


def test_t2_at_least_one_observation_required(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="T2")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "t2")

    response = _create_template(client, admin_token, lab.id, offering.id, observations=[])
    assert response.status_code == 422, response.text


def test_t3_at_least_one_required_observation(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="T3")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "t3")

    optional_only = {**DEMO_OBSERVATION, "required": False}
    response = _create_template(
        client, admin_token, lab.id, offering.id, observations=[optional_only]
    )
    assert response.status_code == 422, response.text


def test_t4_version_must_be_at_least_one(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="T4")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "t4")

    response = _create_template(client, admin_token, lab.id, offering.id, version=0)
    assert response.status_code == 422, response.text


def test_t5_duplicate_source_code_rejected(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="T5")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "t5")

    dup = {**DEMO_OBSERVATION, "sort_order": 1}
    response = _create_template(
        client, admin_token, lab.id, offering.id, observations=[DEMO_OBSERVATION, dup]
    )
    assert response.status_code == 422, response.text


def test_t6_duplicate_sort_order_rejected(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="T6")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "t6")

    second = {**DEMO_OBSERVATION, "source_code": "DEMO_B"}
    response = _create_template(
        client, admin_token, lab.id, offering.id, observations=[DEMO_OBSERVATION, second]
    )
    assert response.status_code == 422, response.text


def test_t7_text_with_numeric_unit_rejected(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="T7")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "t7")

    bad = {**DEMO_OBSERVATION, "result_type": "TEXT"}
    response = _create_template(client, admin_token, lab.id, offering.id, observations=[bad])
    assert response.status_code == 422, response.text


def test_t8_numeric_with_null_unit_allowed(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="T8")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "t8")

    no_unit = {**DEMO_OBSERVATION, "numeric_unit": None}
    response = _create_template(client, admin_token, lab.id, offering.id, observations=[no_unit])
    assert response.status_code == 201, response.text


def test_t9_exact_replay_returns_same_template(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="T9")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "t9")

    first = _create_template(client, admin_token, lab.id, offering.id)
    assert first.status_code == 201, first.text
    second = _create_template(client, admin_token, lab.id, offering.id)
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]


def test_t10_same_version_different_content_conflicts(
    client, keypair, organizations, directory, catalog
):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="T10")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "t10")

    first = _create_template(client, admin_token, lab.id, offering.id)
    assert first.status_code == 201, first.text
    different = {**DEMO_OBSERVATION, "display_label": "Different label"}
    second = _create_template(client, admin_token, lab.id, offering.id, observations=[different])
    assert second.status_code == 409, second.text


def test_t11_no_edit_api_exists(client, keypair, organizations, directory, catalog):
    """AC10: there is no PATCH/PUT for a template -- only create (new version) and activate."""
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="T11")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "t11")
    created = _create_template(client, admin_token, lab.id, offering.id)
    template_id = created.json()["id"]

    response = client.patch(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates/{template_id}",
        headers=_auth(admin_token),
        json={"version": 1, "observations": [DEMO_OBSERVATION]},
    )
    assert response.status_code in (404, 405), response.text


def test_t12_template_belongs_to_exact_offering(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab_a, _entry_a, offering_a = _setup_lab_with_offering(
        organizations, directory, catalog, name="T12A"
    )
    lab_b, _entry_b, offering_b = _setup_lab_with_offering(
        organizations, directory, catalog, name="T12B"
    )
    admin_a = _setup_org_admin_for(client, private_key, lab_a.id, "t12a")
    admin_b = _setup_org_admin_for(client, private_key, lab_b.id, "t12b")

    created = _create_template(client, admin_a, lab_a.id, offering_a.id)
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]

    # Lab B's own admin, using Lab B's own offering path, cannot see Lab A's template.
    listing = client.get(
        f"/exchange/v1/organizations/{lab_b.id}/offerings/{offering_b.id}/result-templates",
        headers=_auth(admin_b),
    )
    assert listing.status_code == 200, listing.text
    assert all(t["id"] != template_id for t in listing.json())


# -- AUTH-group: create/list/activate authorization matrix -------------------------------------


def test_auth1_owning_lab_org_admin_can_create(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="A1")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "a1")
    response = _create_template(client, admin_token, lab.id, offering.id)
    assert response.status_code == 201, response.text


def test_auth2_owning_lab_org_admin_can_list(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="A2")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "a2")
    _create_template(client, admin_token, lab.id, offering.id)
    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates",
        headers=_auth(admin_token),
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1


def test_auth3_owning_lab_org_admin_can_activate(
    client, keypair, organizations, directory, catalog
):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="A3")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "a3")
    created = _create_template(client, admin_token, lab.id, offering.id)
    template_id = created.json()["id"]
    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates/{template_id}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["active"] is True


def test_auth4_lab_tech_without_admin_cannot_configure(
    client, keypair, organizations, directory, catalog
):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="A4")
    tech_token = _setup_lab_tech_for(client, private_key, lab.id, "a4")
    response = _create_template(client, tech_token, lab.id, offering.id)
    assert response.status_code == 403, response.text


def test_auth5_releaser_without_admin_cannot_configure(
    client, keypair, organizations, directory, catalog
):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="A5")
    identity_id = _bootstrap_identity(client, private_key, "releaser-a5")
    _grant_roles(client, private_key, lab.id, identity_id, "releaser-a5", ["LAB_RESULT_RELEASER"])
    releaser_token = make_token(private_key, subject="releaser-a5")
    response = _create_template(client, releaser_token, lab.id, offering.id)
    assert response.status_code == 403, response.text


def test_auth6_provider_org_admin_cannot_configure_lab_offering(
    client, keypair, organizations, directory, catalog
):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="A6")
    provider = make_provider(organizations, name="A6Provider")
    provider_admin_token = _setup_org_admin_for(client, private_key, provider.id, "a6")
    response = _create_template(client, provider_admin_token, lab.id, offering.id)
    assert response.status_code == 404, response.text


def test_auth7_other_lab_admin_cannot_access_this_offering(
    client, keypair, organizations, directory, catalog
):
    private_key, _ = keypair
    lab_a, _entry_a, offering_a = _setup_lab_with_offering(
        organizations, directory, catalog, name="A7A"
    )
    lab_b, _entry_b, _offering_b = _setup_lab_with_offering(
        organizations, directory, catalog, name="A7B"
    )
    admin_b = _setup_org_admin_for(client, private_key, lab_b.id, "a7b")
    response = _create_template(client, admin_b, lab_b.id, offering_a.id)
    assert response.status_code == 404, response.text


# -- A-group: activation semantics ---------------------------------------------------------------


def test_a1_initial_activation_succeeds(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="Act1")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "act1")
    template_id = _create_template(client, admin_token, lab.id, offering.id).json()["id"]

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates/{template_id}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["active"] is True


def test_a2_same_target_activation_replay_unchanged(
    client, keypair, organizations, directory, catalog, sessions
):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="Act2")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "act2")
    template_id = _create_template(client, admin_token, lab.id, offering.id).json()["id"]

    activate_url = (
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}"
        f"/result-templates/{template_id}/activate"
    )
    first = client.post(
        activate_url, headers=_auth(admin_token), json={"expected_current_template_id": None}
    )
    assert first.status_code == 200, first.text
    second = client.post(
        activate_url, headers=_auth(admin_token), json={"expected_current_template_id": None}
    )
    assert second.status_code == 200, second.text
    assert second.json()["active"] is True
    # No duplicate audit event for the replay.
    events = _audit_events(sessions, "lab_result_template_activated")
    assert len(events) == 1


def test_a3_switching_active_version(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="Act3")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "act3")
    v1 = _create_template(client, admin_token, lab.id, offering.id, version=1).json()["id"]
    v2_obs = {**DEMO_OBSERVATION, "source_code": "DEMO_V2"}
    v2 = _create_template(
        client, admin_token, lab.id, offering.id, version=2, observations=[v2_obs]
    ).json()["id"]

    base = f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates"
    r1 = client.post(
        f"{base}/{v1}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": None},
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        f"{base}/{v2}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": v1},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["active"] is True

    listing = client.get(base, headers=_auth(admin_token)).json()
    by_id = {t["id"]: t for t in listing}
    assert by_id[v1]["active"] is False
    assert by_id[v2]["active"] is True


def test_a4_never_two_active_templates(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="Act4")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "act4")
    v1 = _create_template(client, admin_token, lab.id, offering.id, version=1).json()["id"]
    v2_obs = {**DEMO_OBSERVATION, "source_code": "DEMO_V2"}
    v2 = _create_template(
        client, admin_token, lab.id, offering.id, version=2, observations=[v2_obs]
    ).json()["id"]

    base = f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates"
    client.post(
        f"{base}/{v1}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": None},
    )
    client.post(
        f"{base}/{v2}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": v1},
    )

    listing = client.get(base, headers=_auth(admin_token)).json()
    assert sum(1 for t in listing if t["active"]) == 1


def test_a5_stale_expected_current_id_conflicts(client, keypair, organizations, directory, catalog):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="Act5")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "act5")
    v1 = _create_template(client, admin_token, lab.id, offering.id, version=1).json()["id"]
    v2_obs = {**DEMO_OBSERVATION, "source_code": "DEMO_V2"}
    v2 = _create_template(
        client, admin_token, lab.id, offering.id, version=2, observations=[v2_obs]
    ).json()["id"]
    v3_obs = {**DEMO_OBSERVATION, "source_code": "DEMO_V3"}
    v3 = _create_template(
        client, admin_token, lab.id, offering.id, version=3, observations=[v3_obs]
    ).json()["id"]

    base = f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates"
    client.post(
        f"{base}/{v1}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": None},
    )
    client.post(
        f"{base}/{v2}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": v1},
    )
    # Admin A still believes v1 is current; v2 is actually current now.
    stale = client.post(
        f"{base}/{v3}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": v1},
    )
    assert stale.status_code == 409, stale.text

    listing = client.get(base, headers=_auth(admin_token)).json()
    by_id = {t["id"]: t for t in listing}
    assert by_id[v2]["active"] is True
    assert by_id[v3]["active"] is False


def test_a6_cannot_activate_template_from_another_offering(
    client, keypair, organizations, directory, catalog
):
    private_key, _ = keypair
    lab_a, _entry_a, offering_a = _setup_lab_with_offering(
        organizations, directory, catalog, name="Act6A"
    )
    lab_b, _entry_b, offering_b = _setup_lab_with_offering(
        organizations, directory, catalog, name="Act6B"
    )
    admin_a = _setup_org_admin_for(client, private_key, lab_a.id, "act6a")
    admin_b = _setup_org_admin_for(client, private_key, lab_b.id, "act6b")
    template_a = _create_template(client, admin_a, lab_a.id, offering_a.id).json()["id"]

    response = client.post(
        f"/exchange/v1/organizations/{lab_b.id}/offerings/{offering_b.id}"
        f"/result-templates/{template_a}/activate",
        headers=_auth(admin_b),
        json={"expected_current_template_id": None},
    )
    assert response.status_code == 404, response.text


def test_a7_old_inactive_versions_remain_readable(
    client, keypair, organizations, directory, catalog
):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="Act7")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "act7")
    v1 = _create_template(client, admin_token, lab.id, offering.id, version=1).json()["id"]
    v2_obs = {**DEMO_OBSERVATION, "source_code": "DEMO_V2"}
    v2 = _create_template(
        client, admin_token, lab.id, offering.id, version=2, observations=[v2_obs]
    ).json()["id"]

    base = f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}/result-templates"
    client.post(
        f"{base}/{v1}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": None},
    )
    client.post(
        f"{base}/{v2}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": v1},
    )

    listing = client.get(base, headers=_auth(admin_token)).json()
    assert {t["id"] for t in listing} == {v1, v2}


def test_a8_activation_never_changes_template_content(
    client, keypair, organizations, directory, catalog
):
    private_key, _ = keypair
    lab, _entry, offering = _setup_lab_with_offering(organizations, directory, catalog, name="Act8")
    admin_token = _setup_org_admin_for(client, private_key, lab.id, "act8")
    created = _create_template(client, admin_token, lab.id, offering.id)
    template_id = created.json()["id"]
    before_observations = created.json()["observations"]

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/offerings/{offering.id}"
        f"/result-templates/{template_id}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": None},
    )
    assert response.json()["observations"] == before_observations
