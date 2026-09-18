"""DH-LAB-005: template-aware pre-release correction and amendment lifecycle completion.

Covers: the Native controlled correction endpoint (browser submits only the new value, every
structural field derived server-side from the predecessor's own pinned template definition) for
both the pre-release path and the post-release amendment path; the newly-hardened raw amendment
correction endpoint's refusal of a template-bound predecessor; the amendment release fingerprint
gate (mirroring the original release's own gate); amendment discovery; the amendment release
queue's no-empty-amendment-release invariant; and the lab-facing exact-version-content read.

Same harness pattern as ``test_exchange_native_result_entry.py``: a bare ``FastAPI()`` app with
``build_exchange_router`` mounted directly. Helpers are duplicated rather than imported, matching
that file's own convention of being self-contained per ticket.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
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
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import LabObservation, LabOrder, LabResult, LabResultAmendment
from daher_adapter.exchange.organizations import OrganizationRepository

TTL_HOURS = 168

NUMERIC_REQUIRED = {
    "source_code": "NUM_A",
    "display_label": "Numeric Measure A",
    "result_type": "NUMERIC",
    "numeric_unit": "mg/dL",
    "required": True,
    "sort_order": 0,
}
TEXT_OPTIONAL = {
    "source_code": "TXT_B",
    "display_label": "Text Measure B",
    "result_type": "TEXT",
    "numeric_unit": None,
    "required": False,
    "sort_order": 1,
}
DEFAULT_OBSERVATIONS = [NUMERIC_REQUIRED, TEXT_OPTIONAL]


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


def _create_patient(client, private_key, patients, provider_id, name: str) -> uuid.UUID:
    record = patients.get_or_create(
        source_organization_id=provider_id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-{name}",
        display_name=f"Synthetic Patient {name}",
    )
    return record.id


def _new_key() -> str:
    return str(uuid.uuid4())


def _price_lab(
    catalog, directory, organizations, *, name: str, tests: list[tuple[uuid.UUID, Decimal]]
):
    from exchange_support import make_lab

    lab = make_lab(organizations, name=name)
    for entry_id, amount in tests:
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_id)
        catalog.set_price(
            offering_id=offering.id, amount=amount, currency="DJF", expected_current_price_id=None
        )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address=f"{name} Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    return lab


def _create_in_progress_order(
    client, private_key, organizations, catalog, directory, patients, *, name: str
):
    provider, clinician_token = _setup_clinician(client, private_key, organizations, name)
    patient_id = _create_patient(client, private_key, patients, provider.id, name)
    entry, _ = catalog.create_entry(code=f"DHEALTH-N-{name}", display_name=f"Native Test {name}")
    lab = _price_lab(
        catalog,
        directory,
        organizations,
        name=f"NativeLab-{name}",
        tests=[(entry.id, Decimal("1000"))],
    )
    create_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(entry.id)]},
    )
    assert create_response.status_code == 201, create_response.text
    order_id = uuid.UUID(create_response.json()["id"])

    access_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    assert access_response.status_code == 201, access_response.text
    access_token = access_response.json()["access_token"]

    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()["options"]
    matching = [opt for opt in options if opt["lab"]["id"] == str(lab.id)]
    price_ids = [line["current_price_id"] for line in matching[0]["prices"]]

    selection_response = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert selection_response.status_code == 201, selection_response.text

    tech_identity_id = _bootstrap_identity(client, private_key, f"labtech-actual-{name}")
    _grant_roles(
        client, private_key, lab.id, tech_identity_id, f"labtech-actual-{name}", ["LAB_TECH"]
    )
    lab_tech_token = make_token(private_key, subject=f"labtech-actual-{name}")

    assert (
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
            headers=_auth(lab_tech_token),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
            headers=_auth(lab_tech_token),
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
            headers=_auth(lab_tech_token),
        ).status_code
        == 200
    )

    return provider, clinician_token, lab, lab_tech_token, order_id, entry


def _order_item_ids(sessions: sessionmaker[Session], order_id: uuid.UUID) -> list[uuid.UUID]:
    with sessions() as session:
        order = session.get(LabOrder, order_id)
        return [item.id for item in order.items]


def _offering_id_for_entry(catalog: CatalogRepository, lab_id, entry_id) -> uuid.UUID:
    offerings = catalog.get_offerings_for_lab_and_entries(lab_id, [entry_id])
    return offerings[entry_id].id


def _setup_org_admin(client, private_key, org_id, name: str) -> str:
    identity_id = _bootstrap_identity(client, private_key, f"orgadmin-{name}")
    _grant_roles(client, private_key, org_id, identity_id, f"orgadmin-{name}", ["ORG_ADMIN"])
    return make_token(private_key, subject=f"orgadmin-{name}")


def _configure_and_activate_template(
    client, admin_token, lab_id, offering_id, *, version: int = 1, observations=None
) -> dict:
    observations = observations if observations is not None else DEFAULT_OBSERVATIONS
    created = client.post(
        f"/exchange/v1/organizations/{lab_id}/offerings/{offering_id}/result-templates",
        headers=_auth(admin_token),
        json={"version": version, "observations": observations},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    activated = client.post(
        f"/exchange/v1/organizations/{lab_id}/offerings/{offering_id}/result-templates/"
        f"{body['id']}/activate",
        headers=_auth(admin_token),
        json={"expected_current_template_id": None},
    )
    assert activated.status_code == 200, activated.text
    return body


def _definition_id(template_body: dict, source_code: str) -> str:
    for observation in template_body["observations"]:
        if observation["source_code"] == source_code:
            return observation["id"]
    raise AssertionError(f"no {source_code!r} definition in {template_body}")


def _native_values_url(lab_id, order_id, item_id) -> str:
    return (
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}"
        f"/native-result-values"
    )


def _native_correct_url(lab_id, order_id, item_id, observation_id) -> str:
    return (
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}"
        f"/observations/{observation_id}/native-correct"
    )


def _correction_url(lab_id, order_id, item_id, observation_id) -> str:
    return (
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}"
        f"/observations/{observation_id}/correct"
    )


def _observation_url(lab_id, order_id, item_id) -> str:
    return f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}/observations"


def _history_url(lab_id, order_id, item_id) -> str:
    return (
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}"
        f"/observations/history"
    )


def _completion_url(lab_id, order_id, item_id) -> str:
    return (
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}"
        f"/complete-result-entry"
    )


def _results_url(lab_id, order_id) -> str:
    return f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/results"


def _release_url(lab_id, order_id) -> str:
    return f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/release-results"


def _bootstrap_releaser(client, private_key, lab_id, name: str) -> str:
    releaser_id = _bootstrap_identity(client, private_key, f"releaser-{name}")
    _grant_roles(
        client, private_key, lab_id, releaser_id, f"releaser-{name}", ["LAB_RESULT_RELEASER"]
    )
    return make_token(private_key, subject=f"releaser-{name}")


def _current_fingerprint(client, token, lab_id, order_id) -> str:
    response = client.get(_results_url(lab_id, order_id), headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["result_fingerprint"]


def _release_results(client, private_key, lab_id, order_id, name: str) -> dict:
    releaser_token = _bootstrap_releaser(client, private_key, lab_id, name)
    fingerprint = _current_fingerprint(client, releaser_token, lab_id, order_id)
    response = client.post(
        _release_url(lab_id, order_id),
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": fingerprint},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _amendment_url(lab_id, order_id, amendment_id=None) -> str:
    base = f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/amendments"
    return base if amendment_id is None else f"{base}/{amendment_id}"


def _amendment_native_correct_url(lab_id, order_id, amendment_id, item_id, observation_id) -> str:
    return (
        f"{_amendment_url(lab_id, order_id, amendment_id)}/items/{item_id}"
        f"/observations/{observation_id}/native-correct"
    )


def _amendment_correction_url(lab_id, order_id, amendment_id, item_id, observation_id) -> str:
    return (
        f"{_amendment_url(lab_id, order_id, amendment_id)}/items/{item_id}"
        f"/observations/{observation_id}/correct"
    )


def _amendment_releaser_token(client, private_key, lab_id, name: str) -> str:
    identity_id = _bootstrap_identity(client, private_key, f"amend-releaser-{name}")
    _grant_roles(
        client, private_key, lab_id, identity_id, f"amend-releaser-{name}", ["LAB_RESULT_RELEASER"]
    )
    return make_token(private_key, subject=f"amend-releaser-{name}")


def _current_amendment_fingerprint(client, token, lab_id, order_id, amendment_id) -> str:
    response = client.get(_amendment_url(lab_id, order_id, amendment_id), headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["result_fingerprint"]


def _release_amendment(client, private_key, lab_id, order_id, amendment_id, name: str) -> dict:
    releaser_token = _amendment_releaser_token(client, private_key, lab_id, name)
    fingerprint = _current_amendment_fingerprint(
        client, releaser_token, lab_id, order_id, amendment_id
    )
    response = client.post(
        f"{_amendment_url(lab_id, order_id, amendment_id)}/release",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": fingerprint},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _amendment_release_queue_url(lab_id) -> str:
    return f"/exchange/v1/organizations/{lab_id}/amendment-release-queue"


def _release_version_content_url(lab_id, order_id, release_version_id) -> str:
    return (
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}"
        f"/release-versions/{release_version_id}"
    )


def _setup_native_item(
    client, private_key, organizations, catalog, directory, patients, sessions, name: str
):
    """Builds an IN_PROGRESS order with one Native-template-bound item, returning everything a
    correction/amendment test needs (including the item id and numeric definition id)."""
    provider, clinician_token, lab, lab_tech_token, order_id, entry = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name=name
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, name)
    offering_id = _offering_id_for_entry(catalog, lab.id, entry.id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]
    return (
        provider,
        clinician_token,
        lab,
        lab_tech_token,
        order_id,
        item_id,
        template,
        numeric_def_id,
    )


def _record_native_value(
    client, lab_tech_token, lab_id, order_id, item_id, template_id, definition_id, value: str
):
    response = client.post(
        _native_values_url(lab_id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template_id,
            "values": [{"definition_id": definition_id, "numeric_value": value}],
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["values"][0]["observation_id"]


# ==================================================================================================
# PC-group: pre-release Native controlled correction
# ==================================================================================================


def test_pc1_native_correction_derives_structure_and_preserves_provenance(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _p, _c, lab, tech, order_id, item_id, template, numeric_def_id = _setup_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "PC1"
    )
    original_obs_id = _record_native_value(
        client, tech, lab.id, order_id, item_id, template["id"], numeric_def_id, "10.0"
    )
    response = client.post(
        _native_correct_url(lab.id, order_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "11.5"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["supersedes_observation_id"] == original_obs_id
    assert body["numeric_value"] == "11.5"
    assert body["template_observation_definition_id"] == numeric_def_id
    assert body["source_code"] == "NUM_A"
    assert body["display_label_snapshot"] == "Numeric Measure A"
    assert body["numeric_unit"] == "mg/dL"

    with sessions() as session:
        predecessor = session.get(LabObservation, uuid.UUID(original_obs_id))
        assert predecessor.superseded_at is not None
        assert predecessor.numeric_value == Decimal("10.0")
        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item_id))
        assert str(container.result_template_id) == template["id"]


def test_pc2_browser_cannot_submit_structural_fields(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _p, _c, lab, tech, order_id, item_id, template, numeric_def_id = _setup_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "PC2"
    )
    original_obs_id = _record_native_value(
        client, tech, lab.id, order_id, item_id, template["id"], numeric_def_id, "10.0"
    )
    response = client.post(
        _native_correct_url(lab.id, order_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "11.5", "source_code": "SHOULD_NOT_BE_ACCEPTED"},
    )
    assert response.status_code == 422, response.text


def test_pc3_correcting_non_template_bound_observation_via_native_correct_refused(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _p, _c, lab, tech, order_id, entry = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="PC3"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    raw = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(tech),
        json={
            "result_type": "NUMERIC",
            "source_code": "X",
            "display_label": "X",
            "numeric_value": "1",
        },
    )
    assert raw.status_code == 201, raw.text
    response = client.post(
        _native_correct_url(lab.id, order_id, item_id, raw.json()["observation_id"]),
        headers=_auth(tech),
        json={"numeric_value": "2"},
    )
    assert response.status_code == 409, response.text


def test_pc4_value_type_mismatch_rejected(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _p, _c, lab, tech, order_id, item_id, template, numeric_def_id = _setup_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "PC4"
    )
    original_obs_id = _record_native_value(
        client, tech, lab.id, order_id, item_id, template["id"], numeric_def_id, "10.0"
    )
    response = client.post(
        _native_correct_url(lab.id, order_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"text_value": "not-a-number"},
    )
    assert response.status_code == 422, response.text


def test_pc5_correcting_stale_predecessor_rejected(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _p, _c, lab, tech, order_id, item_id, template, numeric_def_id = _setup_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "PC5"
    )
    original_obs_id = _record_native_value(
        client, tech, lab.id, order_id, item_id, template["id"], numeric_def_id, "10.0"
    )
    first = client.post(
        _native_correct_url(lab.id, order_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "11.5"},
    )
    assert first.status_code == 201, first.text

    stale_retry = client.post(
        _native_correct_url(lab.id, order_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "99.9"},
    )
    assert stale_retry.status_code == 409, stale_retry.text


def test_pc6_correction_does_not_reopen_completion(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _p, _c, lab, tech, order_id, item_id, template, numeric_def_id = _setup_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "PC6"
    )
    original_obs_id = _record_native_value(
        client, tech, lab.id, order_id, item_id, template["id"], numeric_def_id, "10.0"
    )
    complete = client.post(_completion_url(lab.id, order_id, item_id), headers=_auth(tech))
    assert complete.status_code == 201, complete.text

    with sessions() as session:
        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item_id))
        completed_at_before = container.entry_completed_at
        completed_by_before = container.entry_completed_by_identity_id
        assert completed_at_before is not None

    corrected = client.post(
        _native_correct_url(lab.id, order_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "11.5"},
    )
    assert corrected.status_code == 201, corrected.text

    with sessions() as session:
        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item_id))
        assert container.entry_completed_at == completed_at_before
        assert container.entry_completed_by_identity_id == completed_by_before


def test_pc7_raw_correction_endpoint_still_refuses_template_bound_container(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """Regression confirm: DH-LAB-004's hardening of the raw pre-release correction endpoint
    against a template-bound container is unweakened by this ticket's new native-correct path."""
    private_key, _ = keypair
    _p, _c, lab, tech, order_id, item_id, template, numeric_def_id = _setup_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "PC7"
    )
    original_obs_id = _record_native_value(
        client, tech, lab.id, order_id, item_id, template["id"], numeric_def_id, "10.0"
    )
    response = client.post(
        _correction_url(lab.id, order_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={
            "result_type": "NUMERIC",
            "source_code": "NUM_A",
            "display_label": "Numeric Measure A",
            "numeric_value": "11.5",
        },
    )
    assert response.status_code == 409, response.text


def test_pc8_history_endpoint_shows_predecessor_and_successor_unambiguously(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _p, _c, lab, tech, order_id, item_id, template, numeric_def_id = _setup_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "PC8"
    )
    original_obs_id = _record_native_value(
        client, tech, lab.id, order_id, item_id, template["id"], numeric_def_id, "10.0"
    )
    corrected = client.post(
        _native_correct_url(lab.id, order_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "11.5"},
    )
    assert corrected.status_code == 201, corrected.text
    successor_id = corrected.json()["observation_id"]

    history = client.get(_history_url(lab.id, order_id, item_id), headers=_auth(tech))
    assert history.status_code == 200, history.text
    by_id = {o["observation_id"]: o for o in history.json()["observations"]}
    assert by_id[original_obs_id]["superseded_at"] is not None
    assert by_id[successor_id]["superseded_at"] is None
    # A fresh GET re-reads through SQLite's float-backed Numeric(18,4) column, which quantizes
    # to 4 decimal places on the way out -- unlike the same-session correction response above,
    # which reflects the exact in-memory Decimal before that round trip.
    assert by_id[original_obs_id]["numeric_value"] == "10.0000"
    assert by_id[successor_id]["numeric_value"] == "11.5000"


def test_pc9_native_correct_is_lab_tech_only(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _p, _c, lab, tech, order_id, item_id, template, numeric_def_id = _setup_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "PC9"
    )
    original_obs_id = _record_native_value(
        client, tech, lab.id, order_id, item_id, template["id"], numeric_def_id, "10.0"
    )
    releaser_token = _bootstrap_releaser(client, private_key, lab.id, "PC9")
    response = client.post(
        _native_correct_url(lab.id, order_id, item_id, original_obs_id),
        headers=_auth(releaser_token),
        json={"numeric_value": "11.5"},
    )
    assert response.status_code == 403, response.text


def test_pc10_pre_release_correction_invalidates_stale_release_fingerprint(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """The core safety property, exercised via the new Native path: a releaser inspects, a tech
    then native-corrects a value, and the releaser's stale fingerprint must be refused."""
    private_key, _ = keypair
    _p, _c, lab, tech, order_id, item_id, template, numeric_def_id = _setup_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "PC10"
    )
    original_obs_id = _record_native_value(
        client, tech, lab.id, order_id, item_id, template["id"], numeric_def_id, "10.0"
    )
    assert (
        client.post(_completion_url(lab.id, order_id, item_id), headers=_auth(tech)).status_code
        == 201
    )
    releaser_token = _bootstrap_releaser(client, private_key, lab.id, "PC10")
    stale_fingerprint = _current_fingerprint(client, releaser_token, lab.id, order_id)

    corrected = client.post(
        _native_correct_url(lab.id, order_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "22.0"},
    )
    assert corrected.status_code == 201, corrected.text

    stale_attempt = client.post(
        _release_url(lab.id, order_id),
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": stale_fingerprint},
    )
    assert stale_attempt.status_code == 409, stale_attempt.text

    fresh_fingerprint = _current_fingerprint(client, releaser_token, lab.id, order_id)
    assert fresh_fingerprint != stale_fingerprint
    fresh_release = client.post(
        _release_url(lab.id, order_id),
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": fresh_fingerprint},
    )
    assert fresh_release.status_code == 201, fresh_release.text


def _setup_released_native_item(
    client, private_key, organizations, catalog, directory, patients, sessions, name: str
):
    """Builds a COMPLETED, Version-1-released order with one Native-template-bound item,
    returning everything an amendment test needs."""
    _p, _c, lab, tech, order_id, item_id, template, numeric_def_id = _setup_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, name
    )
    original_obs_id = _record_native_value(
        client, tech, lab.id, order_id, item_id, template["id"], numeric_def_id, "10.0"
    )
    assert (
        client.post(_completion_url(lab.id, order_id, item_id), headers=_auth(tech)).status_code
        == 201
    )
    _release_results(client, private_key, lab.id, order_id, name)
    return lab, tech, order_id, item_id, template, numeric_def_id, original_obs_id


# ==================================================================================================
# AC-group: amendment Native controlled correction
# ==================================================================================================


def test_ac1_amendment_native_correction_sets_amendment_and_template_ids(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, item_id, template, numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "AC1"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]

    corrected = client.post(
        _amendment_native_correct_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "12.0"},
    )
    assert corrected.status_code == 201, corrected.text
    body = corrected.json()
    assert body["supersedes_observation_id"] == original_obs_id
    assert body["template_observation_definition_id"] == numeric_def_id
    assert body["numeric_value"] == "12.0"

    with sessions() as session:
        successor = session.get(LabObservation, uuid.UUID(body["observation_id"]))
        assert str(successor.amendment_id) == amendment_id


def test_ac2_raw_amendment_correction_refuses_template_bound_predecessor(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """DH-LAB-005 §33: mirrors DH-LAB-004's pre-release hardening -- the raw, free-structure
    amendment correction endpoint must refuse a template-bound predecessor exactly like the raw
    pre-release endpoint does (test_pc7 above)."""
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "AC2"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]

    response = client.post(
        _amendment_correction_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={
            "result_type": "NUMERIC",
            "source_code": "NUM_A",
            "display_label": "Numeric Measure A",
            "numeric_value": "12.0",
        },
    )
    assert response.status_code == 409, response.text


def test_ac3_amendment_native_correction_value_type_mismatch_rejected(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "AC3"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]

    response = client.post(
        _amendment_native_correct_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"text_value": "not-a-number"},
    )
    assert response.status_code == 422, response.text


def test_ac4_amendment_native_correct_is_lab_tech_only(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "AC4"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]

    releaser_token = _bootstrap_releaser(client, private_key, lab.id, "AC4")
    response = client.post(
        _amendment_native_correct_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(releaser_token),
        json={"numeric_value": "12.0"},
    )
    assert response.status_code == 403, response.text


def test_ac5_amendment_discovery_survives_page_refresh(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """DH-LAB-005 §27-28: GET .../amendments must let the frontend rediscover a PREPARING
    amendment without depending on the browser having remembered its id."""
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "AC5"
        )
    )
    before = client.get(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert before.status_code == 200, before.text
    assert before.json()["amendments"] == []

    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]

    rediscovered = client.get(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert rediscovered.status_code == 200, rediscovered.text
    listed = rediscovered.json()["amendments"]
    assert len(listed) == 1
    assert listed[0]["amendment_id"] == amendment_id
    assert listed[0]["status"] == "PREPARING"


def test_ac6_amendment_status_is_never_cancelled(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """No CANCELLED/DISCARDED amendment state exists in this version of DHealth -- the only
    statuses an amendment can ever report are PREPARING and RELEASED."""
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "AC6"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    assert started.json()["status"] == "PREPARING"

    with sessions() as session:
        rows = session.scalars(select(LabResultAmendment)).all()
        assert all(row.status in ("PREPARING", "RELEASED") for row in rows)


# ==================================================================================================
# AF-group: amendment release fingerprint gate
# ==================================================================================================


def test_af1_preparing_amendment_inspection_includes_stable_fingerprint(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "AF1"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]

    first = client.get(_amendment_url(lab.id, order_id, amendment_id), headers=_auth(tech))
    second = client.get(_amendment_url(lab.id, order_id, amendment_id), headers=_auth(tech))
    assert first.status_code == 200 and second.status_code == 200
    assert isinstance(first.json()["result_fingerprint"], str)
    assert len(first.json()["result_fingerprint"]) == 64
    assert first.json()["result_fingerprint"] == second.json()["result_fingerprint"]


def test_af2_amendment_release_requires_fingerprint_field(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "AF2"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    client.post(
        _amendment_native_correct_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "12.0"},
    )
    releaser_token = _amendment_releaser_token(client, private_key, lab.id, "AF2")
    response = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/release",
        headers=_auth(releaser_token),
        json={},
    )
    assert response.status_code == 422, response.text


def test_af3_stale_amendment_fingerprint_after_correction_refuses_release(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """The amendment-side counterpart of test_pc10/REL3: a releaser inspects the amendment, a
    tech then corrects a value inside it, and the releaser's stale fingerprint must be refused --
    never releasing a result set the releaser never actually saw."""
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "AF3"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    first_correction = client.post(
        _amendment_native_correct_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "12.0"},
    )
    assert first_correction.status_code == 201, first_correction.text

    releaser_token = _amendment_releaser_token(client, private_key, lab.id, "AF3")
    stale_fingerprint = _current_amendment_fingerprint(
        client, releaser_token, lab.id, order_id, amendment_id
    )

    second_correction = client.post(
        _amendment_native_correct_url(
            lab.id, order_id, amendment_id, item_id, first_correction.json()["observation_id"]
        ),
        headers=_auth(tech),
        json={"numeric_value": "13.0"},
    )
    assert second_correction.status_code == 201, second_correction.text

    stale_attempt = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/release",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": stale_fingerprint},
    )
    assert stale_attempt.status_code == 409, stale_attempt.text

    with sessions() as session:
        row = session.get(LabResultAmendment, uuid.UUID(amendment_id))
        assert row.status == "PREPARING"


def test_af4_fresh_fingerprint_after_correction_succeeds(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "AF4"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    client.post(
        _amendment_native_correct_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "12.0"},
    )
    released = _release_amendment(client, private_key, lab.id, order_id, amendment_id, "AF4")
    assert released["version_number"] == 2
    assert released["is_amendment"] is True


def test_af5_released_amendment_inspection_returns_fingerprint(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "AF5"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    client.post(
        _amendment_native_correct_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "12.0"},
    )
    _release_amendment(client, private_key, lab.id, order_id, amendment_id, "AF5")

    tech_read = client.get(_amendment_url(lab.id, order_id, amendment_id), headers=_auth(tech))
    assert tech_read.status_code == 200, tech_read.text
    assert isinstance(tech_read.json()["result_fingerprint"], str)
    assert len(tech_read.json()["result_fingerprint"]) == 64


# ==================================================================================================
# RQ-group: amendment release queue
# ==================================================================================================


def test_rq1_empty_amendment_does_not_appear_in_queue(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """No-empty-amendment-release invariant: a PREPARING amendment with zero revisions must not
    appear as a release candidate."""
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, _original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "RQ1"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text

    releaser_token = _amendment_releaser_token(client, private_key, lab.id, "RQ1")
    response = client.get(_amendment_release_queue_url(lab.id), headers=_auth(releaser_token))
    assert response.status_code == 200, response.text
    assert order_id not in {uuid.UUID(a["order_id"]) for a in response.json()["amendments"]}


def test_rq2_amendment_with_revision_appears_in_queue(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "RQ2"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    client.post(
        _amendment_native_correct_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "12.0"},
    )
    releaser_token = _amendment_releaser_token(client, private_key, lab.id, "RQ2")
    response = client.get(_amendment_release_queue_url(lab.id), headers=_auth(releaser_token))
    assert response.status_code == 200, response.text
    entries = {a["order_id"]: a for a in response.json()["amendments"]}
    assert str(order_id) in entries
    assert entries[str(order_id)]["amendment_id"] == amendment_id


def test_rq3_released_amendment_disappears_from_queue(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "RQ3"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    client.post(
        _amendment_native_correct_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "12.0"},
    )
    _release_amendment(client, private_key, lab.id, order_id, amendment_id, "RQ3")

    releaser_token = _amendment_releaser_token(client, private_key, lab.id, "RQ3-check")
    response = client.get(_amendment_release_queue_url(lab.id), headers=_auth(releaser_token))
    assert order_id not in {uuid.UUID(a["order_id"]) for a in response.json()["amendments"]}


def test_rq4_amendment_queue_cross_lab_isolation(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab_a, tech_a, order_a, item_a, _t, _d, obs_a = _setup_released_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "RQ4A"
    )
    lab_b, _tech_b, _order_b, _item_b, _t_b, _d_b, _obs_b = _setup_released_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "RQ4B"
    )
    started = client.post(_amendment_url(lab_a.id, order_a), headers=_auth(tech_a))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    client.post(
        _amendment_native_correct_url(lab_a.id, order_a, amendment_id, item_a, obs_a),
        headers=_auth(tech_a),
        json={"numeric_value": "12.0"},
    )
    releaser_b = _amendment_releaser_token(client, private_key, lab_b.id, "RQ4B")
    response = client.get(_amendment_release_queue_url(lab_b.id), headers=_auth(releaser_b))
    assert order_a not in {uuid.UUID(a["order_id"]) for a in response.json()["amendments"]}


def test_rq5_lab_tech_alone_cannot_access_amendment_queue(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, _order_id, _item_id, _t, _d, _obs = _setup_released_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "RQ5"
    )
    response = client.get(_amendment_release_queue_url(lab.id), headers=_auth(tech))
    assert response.status_code == 403, response.text


# ==================================================================================================
# VH-group: lab-facing exact release-version content
# ==================================================================================================


def test_vh1_version_one_content_immutable_after_version_two_release(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "VH1"
        )
    )
    history = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-versions",
        headers=_auth(tech),
    )
    assert history.status_code == 200, history.text
    version_one_id = history.json()["versions"][0]["release_version_id"]

    before = client.get(
        _release_version_content_url(lab.id, order_id, version_one_id), headers=_auth(tech)
    )
    assert before.status_code == 200, before.text
    before_body = before.json()
    assert before_body["is_amendment"] is False
    assert before_body["results"][0]["observations"][0]["numeric_value"] == "10.0000"

    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    client.post(
        _amendment_native_correct_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "999.0"},
    )
    _release_amendment(client, private_key, lab.id, order_id, amendment_id, "VH1")

    after = client.get(
        _release_version_content_url(lab.id, order_id, version_one_id), headers=_auth(tech)
    )
    assert after.status_code == 200, after.text
    assert after.json() == before_body


def test_vh2_amendment_version_marked_is_amendment_true(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, item_id, _template, _numeric_def_id, original_obs_id = (
        _setup_released_native_item(
            client, private_key, organizations, catalog, directory, patients, sessions, "VH2"
        )
    )
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    client.post(
        _amendment_native_correct_url(lab.id, order_id, amendment_id, item_id, original_obs_id),
        headers=_auth(tech),
        json={"numeric_value": "20.0"},
    )
    released = _release_amendment(client, private_key, lab.id, order_id, amendment_id, "VH2")
    version_two_id = released["release_version_id"]

    content = client.get(
        _release_version_content_url(lab.id, order_id, version_two_id), headers=_auth(tech)
    )
    assert content.status_code == 200, content.text
    assert content.json()["is_amendment"] is True
    assert content.json()["results"][0]["observations"][0]["numeric_value"] == "20.0000"


def test_vh3_nonexistent_version_content_404(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    lab, tech, order_id, _item_id, _t, _d, _obs = _setup_released_native_item(
        client, private_key, organizations, catalog, directory, patients, sessions, "VH3"
    )
    response = client.get(
        _release_version_content_url(lab.id, order_id, uuid.uuid4()), headers=_auth(tech)
    )
    assert response.status_code == 404, response.text
