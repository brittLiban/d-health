"""DH-LAB-004: Native controlled result entry, completion, and fingerprint-gated release.

Covers: first-write template pinning (and its survival across later template activation
changes), the Native batch value endpoint's replay/conflict/type-check discipline, the raw
free-structure observation API's refusal to mutate a template-bound container, required-
definition-gated completion, the result-entry workspace read, the release queue, and the
release-fingerprint race close.

Same harness pattern as ``test_exchange_results_api.py``/``test_exchange_result_templates.py``:
a bare ``FastAPI()`` app with ``build_exchange_router`` mounted directly.
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
from daher_adapter.exchange.models import LabObservation, LabOrder, LabResult
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
    client,
    private_key,
    organizations,
    catalog,
    directory,
    patients,
    *,
    name: str,
    test_count: int = 1,
):
    """Mirrors ``test_exchange_results_api.py``'s own helper of the same name, extended to also
    return the ordered catalog entries -- this ticket's tests need the entry id to resolve each
    item's ``LabOffering`` for template configuration."""
    provider, clinician_token = _setup_clinician(client, private_key, organizations, name)
    patient_id = _create_patient(client, private_key, patients, provider.id, name)
    entries = []
    for i in range(test_count):
        entry, _ = catalog.create_entry(
            code=f"DHEALTH-N-{name}-{i}", display_name=f"Native Test {i}"
        )
        entries.append(entry)
    lab = _price_lab(
        catalog,
        directory,
        organizations,
        name=f"NativeLab-{name}",
        tests=[(e.id, Decimal("1000") * (i + 1)) for i, e in enumerate(entries)],
    )
    create_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_id),
            "catalog_entry_ids": [str(e.id) for e in entries],
        },
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

    accept_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/accept",
        headers=_auth(lab_tech_token),
    )
    assert accept_response.status_code == 200, accept_response.text
    collection_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/record-collection",
        headers=_auth(lab_tech_token),
    )
    assert collection_response.status_code == 201, collection_response.text
    start_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/start-processing",
        headers=_auth(lab_tech_token),
    )
    assert start_response.status_code == 200, start_response.text

    return provider, clinician_token, lab, lab_tech_token, order_id, entries


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
    client,
    admin_token,
    lab_id,
    offering_id,
    *,
    version: int = 1,
    observations: list[dict] | None = None,
    expected_current_template_id: str | None = None,
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
        json={"expected_current_template_id": expected_current_template_id},
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


def _workspace_url(lab_id, order_id) -> str:
    return f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/result-entry-workspace"


def _results_url(lab_id, order_id) -> str:
    return f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/results"


def _release_url(lab_id, order_id) -> str:
    return f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/release-results"


def _release_queue_url(lab_id) -> str:
    return f"/exchange/v1/organizations/{lab_id}/release-queue"


def _observation_url(lab_id, order_id, item_id) -> str:
    return f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}/observations"


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


# ==================================================================================================
# N-group: Native controlled batch value entry
# ==================================================================================================


def test_n1_first_write_with_no_active_template_is_refused(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N1"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    response = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": str(uuid.uuid4()),
            "values": [{"definition_id": str(uuid.uuid4()), "numeric_value": "1"}],
        },
    )
    assert response.status_code == 409, response.text


def test_n2_first_write_pins_container_and_derives_structure_server_side(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N2"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N2")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    response = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "12.5"}],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["template_id"] == template["id"]
    assert body["values"][0]["created"] is True
    with sessions() as session:
        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item_id))
        assert str(container.result_template_id) == template["id"]
        observation = session.scalar(
            select(LabObservation).where(LabObservation.lab_result_id == container.id)
        )
        assert observation.source_code == "NUM_A"
        assert observation.display_label_snapshot == "Numeric Measure A"
        assert observation.numeric_unit == "mg/dL"
        assert observation.source_system == "dhealth-exchange"
        assert str(observation.template_observation_definition_id) == numeric_def_id


def test_n3_browser_cannot_submit_result_structure_fields(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """The request schema forbids extra fields -- a caller attempting to also send
    ``source_code``/``display_label``/``result_type``/``numeric_unit``/``source_system`` is
    refused at the schema boundary, never silently ignored."""
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N3"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N3")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    response = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [
                {
                    "definition_id": numeric_def_id,
                    "numeric_value": "12.5",
                    "source_code": "SHOULD_NOT_BE_ACCEPTED",
                }
            ],
        },
    )
    assert response.status_code == 422, response.text


def test_n4_unknown_definition_id_rejected(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N4"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N4")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    item_id = _order_item_ids(sessions, order_id)[0]

    response = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": str(uuid.uuid4()), "numeric_value": "1"}],
        },
    )
    assert response.status_code == 422, response.text


def test_n5_n6_value_type_must_match_definitions_own_type(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N56"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N56")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    text_def_id = _definition_id(template, "TXT_B")
    item_id = _order_item_ids(sessions, order_id)[0]

    # N5: NUMERIC definition submitted with text_value -- server derives type from the
    # definition, not from which field the caller populated.
    numeric_as_text = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "text_value": "x"}],
        },
    )
    assert numeric_as_text.status_code == 422, numeric_as_text.text

    # N6: TEXT definition submitted with numeric_value.
    text_as_numeric = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": text_def_id, "numeric_value": "1"}],
        },
    )
    assert text_as_numeric.status_code == 422, text_as_numeric.text


def test_n7_exact_value_replay_is_idempotent(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N7"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N7")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]
    body = {
        "template_id": template["id"],
        "values": [{"definition_id": numeric_def_id, "numeric_value": "12.5000"}],
    }

    first = client.post(
        _native_values_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token), json=body
    )
    assert first.status_code == 201, first.text
    assert first.json()["values"][0]["created"] is True

    second = client.post(
        _native_values_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token), json=body
    )
    assert second.status_code == 200, second.text
    assert second.json()["values"][0]["created"] is False
    assert (
        second.json()["values"][0]["observation_id"] == first.json()["values"][0]["observation_id"]
    )

    with sessions() as session:
        rows = session.scalars(
            select(LabObservation).where(
                LabObservation.template_observation_definition_id == uuid.UUID(numeric_def_id)
            )
        ).all()
        assert len(rows) == 1, "replay must never create a second observation row"


def test_n8_different_value_replay_is_a_conflict_never_overwritten(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N8"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N8")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    first = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "12.5"}],
        },
    )
    assert first.status_code == 201, first.text

    conflict = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "99.0"}],
        },
    )
    assert conflict.status_code == 409, conflict.text

    with sessions() as session:
        observation = session.scalar(
            select(LabObservation).where(
                LabObservation.template_observation_definition_id == uuid.UUID(numeric_def_id)
            )
        )
        assert observation.numeric_value == Decimal("12.5000"), (
            "original value must never be overwritten"
        )


def test_n9_mixed_batch_replay_and_create_is_valid(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N9"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N9")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    text_def_id = _definition_id(template, "TXT_B")
    item_id = _order_item_ids(sessions, order_id)[0]

    first = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1"}],
        },
    )
    assert first.status_code == 201, first.text

    mixed = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [
                {"definition_id": numeric_def_id, "numeric_value": "1"},
                {"definition_id": text_def_id, "text_value": "Normal"},
            ],
        },
    )
    assert mixed.status_code == 201, mixed.text  # at least one genuine creation
    values_by_definition = {v["definition_id"]: v for v in mixed.json()["values"]}
    assert values_by_definition[numeric_def_id]["created"] is False
    assert values_by_definition[text_def_id]["created"] is True


def test_n10_second_write_with_different_template_id_is_a_conflict(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N10"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N10")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template_v1 = _configure_and_activate_template(
        client, admin_token, lab.id, offering_id, version=1
    )
    numeric_def_id = _definition_id(template_v1, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    first = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template_v1["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1"}],
        },
    )
    assert first.status_code == 201, first.text

    template_v2 = _configure_and_activate_template(
        client,
        admin_token,
        lab.id,
        offering_id,
        version=2,
        expected_current_template_id=template_v1["id"],
    )
    v2_numeric_def_id = _definition_id(template_v2, "NUM_A")

    stale_pin_attempt = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template_v2["id"],
            "values": [{"definition_id": v2_numeric_def_id, "numeric_value": "1"}],
        },
    )
    assert stale_pin_attempt.status_code == 409, stale_pin_attempt.text


def test_n11_new_value_after_completion_refused_exact_replay_harmless(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N11"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N11")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    text_def_id = _definition_id(template, "TXT_B")
    item_id = _order_item_ids(sessions, order_id)[0]

    submit = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1"}],
        },
    )
    assert submit.status_code == 201, submit.text
    complete = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/complete-result-entry",
        headers=_auth(lab_tech_token),
    )
    assert complete.status_code == 201, complete.text

    replay = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1"}],
        },
    )
    assert replay.status_code == 200, replay.text

    new_value_attempt = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": text_def_id, "text_value": "Late"}],
        },
    )
    assert new_value_attempt.status_code == 409, new_value_attempt.text
    with sessions() as session:
        rows = session.scalars(
            select(LabObservation).where(
                LabObservation.template_observation_definition_id == uuid.UUID(text_def_id)
            )
        ).all()
        assert rows == [], "a new value must never silently append after completion"


def test_n12_legacy_container_refuses_native_adoption(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N12"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N12")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    raw = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "LEGACY",
            "display_label": "Legacy value",
            "numeric_value": "1",
        },
    )
    assert raw.status_code == 201, raw.text

    native_attempt = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1"}],
        },
    )
    assert native_attempt.status_code == 409, native_attempt.text


def test_n13_pin_survives_later_template_version_activation(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N13"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N13")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template_v1 = _configure_and_activate_template(
        client, admin_token, lab.id, offering_id, version=1
    )
    numeric_def_id_v1 = _definition_id(template_v1, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    first = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template_v1["id"],
            "values": [{"definition_id": numeric_def_id_v1, "numeric_value": "1"}],
        },
    )
    assert first.status_code == 201, first.text

    _configure_and_activate_template(
        client,
        admin_token,
        lab.id,
        offering_id,
        version=2,
        expected_current_template_id=template_v1["id"],
    )

    # v1 is no longer active, but this container is pinned to it -- entering a second value
    # against the *same pinned* template must still succeed.
    text_def_id_v1 = _definition_id(template_v1, "TXT_B")
    second = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template_v1["id"],
            "values": [{"definition_id": text_def_id_v1, "text_value": "Still v1"}],
        },
    )
    assert second.status_code == 201, second.text


def test_n14_over_precision_numeric_value_rejected_at_schema(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N14"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N14")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    response = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1.00001"}],
        },
    )
    assert response.status_code == 422, response.text


def test_n15_releaser_alone_cannot_enter_native_values(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N15"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N15")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]
    releaser_token = _bootstrap_releaser(client, private_key, lab.id, "N15")

    response = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(releaser_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1"}],
        },
    )
    assert response.status_code == 403, response.text


def test_n16_raw_observation_write_refused_on_template_bound_container(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N16"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N16")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1"}],
        },
    )

    raw_attempt = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "TEXT",
            "source_code": "EXTRA",
            "display_label": "Extra free-form value",
            "text_value": "should not be allowed",
        },
    )
    assert raw_attempt.status_code == 409, raw_attempt.text


def test_n17_raw_correction_refused_on_controlled_observation(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="N17"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "N17")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    written = client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1"}],
        },
    )
    observation_id = written.json()["values"][0]["observation_id"]

    correction_attempt = client.post(
        f"{_observation_url(lab.id, order_id, item_id)}/{observation_id}/correct",
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NUM_A",
            "display_label": "Numeric Measure A",
            "numeric_value": "2",
        },
    )
    assert correction_attempt.status_code == 409, correction_attempt.text


# ==================================================================================================
# C-group: required-definition-gated completion
# ==================================================================================================


def test_c1_completion_blocked_while_required_definition_missing(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="C1"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "C1")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    text_def_id = _definition_id(template, "TXT_B")
    item_id = _order_item_ids(sessions, order_id)[0]

    client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": text_def_id, "text_value": "only optional"}],
        },
    )
    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/complete-result-entry",
        headers=_auth(lab_tech_token),
    )
    assert response.status_code == 409, response.text


def test_c2_completion_succeeds_with_required_present_optional_absent(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="C2"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "C2")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1"}],
        },
    )
    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/complete-result-entry",
        headers=_auth(lab_tech_token),
    )
    assert response.status_code == 201, response.text


def test_c3_legacy_container_completion_unaffected(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="C3"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "LEGACY",
            "display_label": "Legacy",
            "numeric_value": "1",
        },
    )
    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/complete-result-entry",
        headers=_auth(lab_tech_token),
    )
    assert response.status_code == 201, response.text


# ==================================================================================================
# W-group: result-entry workspace
# ==================================================================================================


def test_w1_unstarted_item_shows_active_template_unpinned(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="W1"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "W1")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)

    response = client.get(_workspace_url(lab.id, order_id), headers=_auth(lab_tech_token))
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["template"]["template_id"] == template["id"]
    assert item["template_pinned"] is False
    assert item["entry_mode"] == "NATIVE_TEMPLATE"


def test_w2_unstarted_item_with_no_active_template_blocks_entry(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="W2"
    )
    response = client.get(_workspace_url(lab.id, order_id), headers=_auth(lab_tech_token))
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["template"] is None
    assert item["entry_mode"] == "UNSTARTED_NO_TEMPLATE"


def test_w3_pinned_template_shown_even_after_deactivation(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="W3"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "W3")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template_v1 = _configure_and_activate_template(
        client, admin_token, lab.id, offering_id, version=1
    )
    numeric_def_id = _definition_id(template_v1, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template_v1["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1"}],
        },
    )
    _configure_and_activate_template(
        client,
        admin_token,
        lab.id,
        offering_id,
        version=2,
        expected_current_template_id=template_v1["id"],
    )

    response = client.get(_workspace_url(lab.id, order_id), headers=_auth(lab_tech_token))
    item = response.json()["items"][0]
    assert item["template"]["template_id"] == template_v1["id"]
    assert item["template_pinned"] is True
    assert item["entry_mode"] == "NATIVE_TEMPLATE"


def test_w4_legacy_container_represented_truthfully(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="W4"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "LEGACY",
            "display_label": "Legacy",
            "numeric_value": "1",
        },
    )
    response = client.get(_workspace_url(lab.id, order_id), headers=_auth(lab_tech_token))
    item = response.json()["items"][0]
    assert item["entry_mode"] == "LEGACY_OR_EXTERNAL"
    assert item["template"] is None


def test_w5_entered_values_reflected_in_workspace(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="W5"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "W5")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]

    client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "7.5"}],
        },
    )
    response = client.get(_workspace_url(lab.id, order_id), headers=_auth(lab_tech_token))
    item = response.json()["items"][0]
    assert len(item["entered_values"]) == 1
    assert item["entered_values"][0]["definition_id"] == numeric_def_id
    assert item["entered_values"][0]["numeric_value"] == "7.5000"


def test_w6_releaser_alone_cannot_read_workspace(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, _lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="W6"
    )
    releaser_token = _bootstrap_releaser(client, private_key, lab.id, "W6")
    response = client.get(_workspace_url(lab.id, order_id), headers=_auth(releaser_token))
    assert response.status_code == 403, response.text


def test_w7_cross_lab_workspace_is_non_enumerating(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, _lab, _lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="W7"
    )
    from exchange_support import make_lab

    impostor_lab = make_lab(organizations, name="W7-impostor")
    impostor_id = _bootstrap_identity(client, private_key, "w7-impostor")
    _grant_roles(client, private_key, impostor_lab.id, impostor_id, "w7-impostor", ["LAB_TECH"])
    impostor_token = make_token(private_key, subject="w7-impostor")

    real_response = client.get(
        _workspace_url(impostor_lab.id, order_id), headers=_auth(impostor_token)
    )
    fake_response = client.get(
        _workspace_url(impostor_lab.id, uuid.uuid4()), headers=_auth(impostor_token)
    )
    assert real_response.status_code == 404
    assert fake_response.status_code == 404
    assert real_response.json() == fake_response.json()


def test_w8_workspace_only_available_in_progress(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="W8"
    )
    admin_token = _setup_org_admin(client, private_key, lab.id, "W8")
    offering_id = _offering_id_for_entry(catalog, lab.id, entries[0].id)
    template = _configure_and_activate_template(client, admin_token, lab.id, offering_id)
    numeric_def_id = _definition_id(template, "NUM_A")
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        _native_values_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "template_id": template["id"],
            "values": [{"definition_id": numeric_def_id, "numeric_value": "1"}],
        },
    )
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/complete-result-entry",
        headers=_auth(lab_tech_token),
    )
    _release_results(client, private_key, lab.id, order_id, "W8")

    response = client.get(_workspace_url(lab.id, order_id), headers=_auth(lab_tech_token))
    assert response.status_code == 409, response.text


def test_w9_workspace_query_count_does_not_scale_with_item_count(
    client, keypair, organizations, catalog, directory, patients, sessions, engine
):
    private_key, _ = keypair
    _provider, _clinician, lab_one, lab_tech_one, order_one, entries_one = (
        _create_in_progress_order(
            client,
            private_key,
            organizations,
            catalog,
            directory,
            patients,
            name="W9-1",
            test_count=1,
        )
    )
    _provider2, _clinician2, lab_five, lab_tech_five, order_five, entries_five = (
        _create_in_progress_order(
            client,
            private_key,
            organizations,
            catalog,
            directory,
            patients,
            name="W9-5",
            test_count=5,
        )
    )
    admin_one = _setup_org_admin(client, private_key, lab_one.id, "W9-1")
    admin_five = _setup_org_admin(client, private_key, lab_five.id, "W9-5")
    _configure_and_activate_template(
        client,
        admin_one,
        lab_one.id,
        _offering_id_for_entry(catalog, lab_one.id, entries_one[0].id),
    )
    for entry in entries_five:
        _configure_and_activate_template(
            client, admin_five, lab_five.id, _offering_id_for_entry(catalog, lab_five.id, entry.id)
        )

    from sqlalchemy import event

    def _count_queries(token, lab_id, order_id) -> int:
        counter = {"n": 0}

        def _on_execute(*_a, **_kw):
            counter["n"] += 1

        event.listen(engine, "before_cursor_execute", _on_execute)
        try:
            response = client.get(_workspace_url(lab_id, order_id), headers=_auth(token))
            assert response.status_code == 200, response.text
        finally:
            event.remove(engine, "before_cursor_execute", _on_execute)
        return counter["n"]

    one_count = _count_queries(lab_tech_one, lab_one.id, order_one)
    five_count = _count_queries(lab_tech_five, lab_five.id, order_five)
    assert five_count == one_count, (
        f"query count must not scale with item count: 1 item={one_count}, 5 items={five_count}"
    )


# ==================================================================================================
# RQ-group: release queue
# ==================================================================================================


def test_rq1_rq2_only_ready_unreleased_orders_appear(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="RQ12", test_count=2
    )
    releaser_token = _bootstrap_releaser(client, private_key, lab.id, "RQ12")

    before = client.get(_release_queue_url(lab.id), headers=_auth(releaser_token))
    assert before.status_code == 200, before.text
    assert order_id not in {uuid.UUID(o["order_id"]) for o in before.json()["orders"]}

    item_ids = _order_item_ids(sessions, order_id)
    for item_id in item_ids:
        client.post(
            _observation_url(lab.id, order_id, item_id),
            headers=_auth(lab_tech_token),
            json={
                "result_type": "NUMERIC",
                "source_code": "X",
                "display_label": "X",
                "numeric_value": "1",
            },
        )
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}"
            f"/complete-result-entry",
            headers=_auth(lab_tech_token),
        )

    after = client.get(_release_queue_url(lab.id), headers=_auth(releaser_token))
    assert order_id in {uuid.UUID(o["order_id"]) for o in after.json()["orders"]}


def test_rq3_released_order_disappears_from_queue(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="RQ3"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "X",
            "display_label": "X",
            "numeric_value": "1",
        },
    )
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/complete-result-entry",
        headers=_auth(lab_tech_token),
    )
    _release_results(client, private_key, lab.id, order_id, "RQ3")

    releaser_token = _bootstrap_releaser(client, private_key, lab.id, "RQ3-check")
    response = client.get(_release_queue_url(lab.id), headers=_auth(releaser_token))
    assert order_id not in {uuid.UUID(o["order_id"]) for o in response.json()["orders"]}


def test_rq4_cross_lab_queue_isolation(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab_a, lab_tech_a, order_a, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="RQ4A"
    )
    _provider_b, _clinician_b, lab_b, _lab_tech_b, order_b, _entries_b = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="RQ4B"
    )
    releaser_b = _bootstrap_releaser(client, private_key, lab_b.id, "RQ4B")
    response = client.get(_release_queue_url(lab_b.id), headers=_auth(releaser_b))
    assert order_a not in {uuid.UUID(o["order_id"]) for o in response.json()["orders"]}


def test_rq5_lab_tech_alone_cannot_access_release_queue(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, _order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="RQ5"
    )
    response = client.get(_release_queue_url(lab.id), headers=_auth(lab_tech_token))
    assert response.status_code == 403, response.text


def test_rq6_releaser_only_user_needs_no_lab_tech_to_see_queue(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, _lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="RQ6"
    )
    releaser_only_token = _bootstrap_releaser(client, private_key, lab.id, "RQ6")
    response = client.get(_release_queue_url(lab.id), headers=_auth(releaser_only_token))
    assert response.status_code == 200, response.text


# ==================================================================================================
# REL-group: release inspection fingerprint and the release-vs-correction race close
# ==================================================================================================


def test_rel1_inspection_response_includes_patient_provider_and_fingerprint(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    provider, _clinician, lab, lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="REL1"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "X",
            "display_label": "X",
            "numeric_value": "1",
        },
    )
    response = client.get(_results_url(lab.id, order_id), headers=_auth(lab_tech_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["patient"]["display_name"] is not None
    assert body["ordering_provider_name"] == provider.name
    assert isinstance(body["result_fingerprint"], str) and len(body["result_fingerprint"]) == 64


def test_rel2_release_succeeds_with_correct_fingerprint(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="REL2"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "X",
            "display_label": "X",
            "numeric_value": "1",
        },
    )
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/complete-result-entry",
        headers=_auth(lab_tech_token),
    )
    releaser_token = _bootstrap_releaser(client, private_key, lab.id, "REL2")
    fingerprint = _current_fingerprint(client, releaser_token, lab.id, order_id)
    response = client.post(
        _release_url(lab.id, order_id),
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": fingerprint},
    )
    assert response.status_code == 201, response.text


def test_rel3_stale_fingerprint_after_correction_refuses_release(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """The core safety property: a releaser inspects, a tech then corrects a value, and the
    releaser's stale fingerprint must be refused -- never releasing an observation set the
    releaser never actually saw."""
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="REL3"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    submitted = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "X",
            "display_label": "X",
            "numeric_value": "1",
        },
    )
    observation_id = submitted.json()["observation_id"]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/complete-result-entry",
        headers=_auth(lab_tech_token),
    )
    releaser_token = _bootstrap_releaser(client, private_key, lab.id, "REL3")
    stale_fingerprint = _current_fingerprint(client, releaser_token, lab.id, order_id)

    # A tech corrects the value AFTER the releaser's inspection -- this creates a new
    # observation id, which must change the fingerprint.
    correction = client.post(
        f"{_observation_url(lab.id, order_id, item_id)}/{observation_id}/correct",
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "X",
            "display_label": "X",
            "numeric_value": "2",
        },
    )
    assert correction.status_code == 201, correction.text

    stale_release_attempt = client.post(
        _release_url(lab.id, order_id),
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": stale_fingerprint},
    )
    assert stale_release_attempt.status_code == 409, stale_release_attempt.text
    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"
        from daher_adapter.exchange.models import LabResultRelease

        assert session.scalar(select(LabResultRelease)) is None

    fresh_fingerprint = _current_fingerprint(client, releaser_token, lab.id, order_id)
    assert fresh_fingerprint != stale_fingerprint
    fresh_release = client.post(
        _release_url(lab.id, order_id),
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": fresh_fingerprint},
    )
    assert fresh_release.status_code == 201, fresh_release.text


def test_rel4_fingerprint_is_stable_when_nothing_changed(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    _provider, _clinician, lab, lab_tech_token, order_id, _entries = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="REL4"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "X",
            "display_label": "X",
            "numeric_value": "1",
        },
    )
    first = _current_fingerprint(client, lab_tech_token, lab.id, order_id)
    second = _current_fingerprint(client, lab_tech_token, lab.id, order_id)
    assert first == second


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
