"""DHEALTH-LAB-REPORT-001/ADR-072: the version-bound printable released laboratory report.

What this file proves, and the shape of each proof:

- **RP-A** a report exists only for a released version. An entered, completed, *unreleased* result
  has no `LabResultReleaseVersion` to name, so there is nothing to ask for and nothing to guess.
- **RP-B** Version 1's report carries the exact released observations, and `released_at` is the
  *same stored value* as `LabResultRelease.released_at` -- asserted against the database, so the
  word "authoritative" in the DTO docstring is a checkable fact rather than a citation.
- **RP-C** an amendment draft changes nothing. The V1 report is captured as a whole dict before the
  amendment and compared field-for-field after it.
- **RP-D** V2's report is exactly V2 and V1's report is still exactly V1, byte-for-byte against the
  pre-amendment capture, with `is_current_version` as the only thing that moved.
- **RP-E** authority is reused, never widened: entry-only authority suffices to read, release
  authority is not required, and billing / facility-configuration / platform-admin / rival-tenant
  actors are all refused.
- **RP-F** `Cache-Control: no-store`, an exact key projection, and the absence of every forbidden
  field -- asserted on the key sets rather than on substrings, because a substring assertion over
  `catalog_code_snapshot` would pass or fail for reasons unrelated to what it claims.
- **RP-G/H** a laboratory that later stops offering the analysis, and a clinician who later records
  a review disposition, both leave the report unchanged.
- **RP-I** the one honest limitation: the header's organization and patient names are read live. The
  test renames the laboratory and asserts the clinical content did not move while the name did.

Same harness pattern as `test_dhlab005_native_correction_and_amendments.py` -- a bare `FastAPI()`
with `build_exchange_router` mounted directly, helpers duplicated rather than imported, per that
file's own self-contained-per-ticket convention.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
from daher_adapter.exchange.models import (
    LabOrder,
    LabResultAmendment,
    LabResultRelease,
    LabResultReleaseVersion,
    Organization,
)
from daher_adapter.exchange.organizations import OrganizationRepository

TTL_HOURS = 168

NUMERIC_REQUIRED = {
    "source_code": "NUM_A",
    "display_label": "Mesure numérique A",
    "result_type": "NUMERIC",
    "numeric_unit": "mg/dL",
    "required": True,
    "sort_order": 0,
}
TEXT_OPTIONAL = {
    "source_code": "TXT_B",
    "display_label": "Mesure textuelle B",
    "result_type": "TEXT",
    "numeric_unit": None,
    "required": False,
    "sort_order": 1,
}
DEFAULT_OBSERVATIONS = [NUMERIC_REQUIRED, TEXT_OPTIONAL]

# The exact projection. Any key added to or removed from `ReleasedReportResponse` fails here first.
REPORT_KEYS = {
    "order_id",
    "release_version_id",
    "release_version_number",
    "is_amendment",
    "released_at",
    "is_current_version",
    "current_release_version_number",
    "laboratory",
    "ordering_organization",
    "patient",
    "results",
}
ORGANIZATION_KEYS = {"organization_id", "name"}
PATIENT_KEYS = {"patient_id", "display_name", "date_of_birth"}
ITEM_KEYS = {
    "item_id",
    "catalog_code_snapshot",
    "catalog_display_name_snapshot",
    "observations",
}
OBSERVATION_KEYS = {
    "observation_id",
    "display_label_snapshot",
    "result_type",
    "numeric_value",
    "numeric_unit",
    "reference_range_low",
    "reference_range_high",
    "text_value",
}

#: Every key the ticket forbids, checked recursively at every depth of the response. Keys, not
#: substrings: `catalog_code_snapshot` contains "code" and `display_label_snapshot` contains
#: "label", so a substring sweep over the serialized body would redden for the wrong reason.
FORBIDDEN_KEYS = frozenset(
    {
        # DoctorReview -- clinician workflow metadata, not laboratory report content.
        "review_status",
        "reviewed_at",
        "reviewed_by_practitioner_reference_id",
        "review_id",
        "legacy_unversioned",
        # Visit / consultation context.
        "visit_id",
        "reason_for_visit",
        "stated_reason",
        "note",
        "notes",
        "body",
        # Laboratory-internal workflow.
        "active_block",
        "block_reason",
        "entry_completed",
        "entry_completed_at",
        "recorded_at",
        "result_fingerprint",
        "all_items_entry_complete",
        "has_correction_history",
        "template_observation_definition_id",
        "source_code",
        "source_system",
        # Money.
        "selected_price_amount",
        "selected_price_currency",
        "amount",
        "currency",
        "invoice_id",
        "balance",
        "payment_id",
        # Specimen -- D-3's participant half is open; the report introduces no specimen semantics.
        "specimen_id",
        "specimen_type",
        "collection_recorded_at",
        "collected_at",
        # Identity / authorization internals.
        "released_by_identity_id",
        "started_by_identity_id",
        "practitioner_reference_id",
        "patient_reference_id",
        "idempotency_key",
        "contact_channel_type",
        "contact_value",
        "capabilities",
        "roles",
        "memberships",
        # Interpretation. None of these exists anywhere in this codebase; asserted so that adding
        # one cannot pass unnoticed.
        "flag",
        "abnormal",
        "is_abnormal",
        "normal",
        "interpretation",
        "severity",
        "urgency",
        "verdict",
        "summary",
    }
)


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def client(sessions: sessionmaker[Session], keypair, tmp_path: Path) -> TestClient:
    _, public_key = keypair
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=build_validator(public_key),
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


# ==================================================================================================
# harness
# ==================================================================================================


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
    admin_token = make_token(
        private_key, subject=f"setup-admin-{subject}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text


def _member_token(client, private_key, org_id, subject: str, roles: list[str]) -> str:
    identity_id = _bootstrap_identity(client, private_key, subject)
    _grant_roles(client, private_key, org_id, identity_id, subject, roles)
    return make_token(private_key, subject=subject)


def _member_with_membership(
    client, private_key, org_id, subject: str, roles: list[str]
) -> tuple[str, str]:
    """Same as `_member_token`, but keeps the membership id the override endpoint needs."""
    identity_id = _bootstrap_identity(client, private_key, subject)
    admin_token = make_token(
        private_key, subject=f"setup-admin-{subject}", roles=[PLATFORM_ADMIN_ROLE]
    )
    response = client.post(
        f"/exchange/v1/organizations/{org_id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": identity_id, "roles": roles},
    )
    assert response.status_code in (200, 201), response.text
    return make_token(private_key, subject=subject), response.json()["membership_id"]


def _set_override(client, private_key, org_id, membership_id: str, capability: str, effect: str):
    """ADR-067's per-member override. Applied by an ORG_ADMIN of that organization."""
    admin_token = _member_token(
        client, private_key, org_id, f"capadmin-{membership_id[:8]}", ["ORG_ADMIN"]
    )
    response = client.put(
        f"/exchange/v1/organizations/{org_id}/memberships/{membership_id}"
        f"/capabilities/{capability}",
        headers=_auth(admin_token),
        json={"effect": effect},
    )
    assert response.status_code == 200, response.text
    return response


def _clear_override(client, private_key, org_id, membership_id: str, capability: str):
    admin_token = _member_token(
        client, private_key, org_id, f"capclear-{membership_id[:8]}", ["ORG_ADMIN"]
    )
    response = client.delete(
        f"/exchange/v1/organizations/{org_id}/memberships/{membership_id}"
        f"/capabilities/{capability}",
        headers=_auth(admin_token),
    )
    assert response.status_code == 200, response.text
    return response


def _priced_lab(catalog, directory, organizations, *, name: str, entry_id: uuid.UUID):
    lab = make_lab(organizations, name=name)
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
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
    return lab, offering


def _configure_template(client, admin_token, lab_id, offering_id) -> dict:
    created = client.post(
        f"/exchange/v1/organizations/{lab_id}/offerings/{offering_id}/result-templates",
        headers=_auth(admin_token),
        json={"version": 1, "observations": DEFAULT_OBSERVATIONS},
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


def _lab_report_url(lab_id, order_id, release_version_id) -> str:
    return (
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}"
        f"/release-versions/{release_version_id}/report"
    )


def _clinic_report_url(provider_id, order_id, release_version_id) -> str:
    return (
        f"/exchange/v1/organizations/{provider_id}/orders/{order_id}"
        f"/release-versions/{release_version_id}/report"
    )


class Scenario:
    """One IN_PROGRESS order with one template-bound item, plus every token a test needs."""

    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def _scenario(
    client, private_key, organizations, catalog, directory, patients, sessions, name: str
) -> Scenario:
    provider = make_provider(organizations, name=f"Clinique-{name}")
    clinician_token = _member_token(
        client, private_key, provider.id, f"clinician-{name}", ["CLINICIAN"]
    )
    patient_reference = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value=f"SYNTH-{name}",
        display_name=f"Patient Synthétique {name}",
    )
    entry, _ = catalog.create_entry(
        code=f"DHEALTH-RP-{name}", display_name=f"Analyse de démonstration {name}"
    )
    lab, offering = _priced_lab(
        catalog, directory, organizations, name=f"Laboratoire-{name}", entry_id=entry.id
    )

    created = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
        json={
            "patient_reference_id": str(patient_reference.id),
            "catalog_entry_ids": [str(entry.id)],
        },
    )
    assert created.status_code == 201, created.text
    order_id = uuid.UUID(created.json()["id"])

    access = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    assert access.status_code == 201, access.text
    access_token = access.json()["access_token"]
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()["options"]
    matching = [option for option in options if option["lab"]["id"] == str(lab.id)]
    price_ids = [line["current_price_id"] for line in matching[0]["prices"]]
    selected = client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    assert selected.status_code == 201, selected.text

    tech_token = _member_token(client, private_key, lab.id, f"labtech-{name}", ["LAB_TECH"])
    for path, expected in (
        ("accept", 200),
        ("record-collection", 201),
        ("start-processing", 200),
    ):
        response = client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/{path}",
            headers=_auth(tech_token),
        )
        assert response.status_code == expected, response.text

    admin_token = _member_token(client, private_key, lab.id, f"labadmin-{name}", ["ORG_ADMIN"])
    template = _configure_template(client, admin_token, lab.id, offering.id)
    with sessions() as session:
        item_id = session.get(LabOrder, order_id).items[0].id

    return Scenario(
        name=name,
        provider=provider,
        provider_id=provider.id,
        clinician_token=clinician_token,
        lab=lab,
        lab_id=lab.id,
        offering_id=offering.id,
        entry_id=entry.id,
        tech_token=tech_token,
        admin_token=admin_token,
        order_id=order_id,
        item_id=item_id,
        template=template,
        numeric_definition_id=_definition_id(template, "NUM_A"),
    )


def _record_value(client, scenario: Scenario, value: str) -> str:
    response = client.post(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/items/{scenario.item_id}/native-result-values",
        headers=_auth(scenario.tech_token),
        json={
            "template_id": scenario.template["id"],
            "values": [{"definition_id": scenario.numeric_definition_id, "numeric_value": value}],
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["values"][0]["observation_id"]


def _complete_entry(client, scenario: Scenario) -> None:
    response = client.post(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/items/{scenario.item_id}/complete-result-entry",
        headers=_auth(scenario.tech_token),
    )
    assert response.status_code in (200, 201), response.text


def _releaser_token(client, private_key, scenario: Scenario, suffix: str) -> str:
    return _member_token(
        client,
        private_key,
        scenario.lab_id,
        f"releaser-{scenario.name}-{suffix}",
        ["LAB_RESULT_RELEASER"],
    )


def _release_v1(client, private_key, scenario: Scenario) -> None:
    token = _releaser_token(client, private_key, scenario, "v1")
    inspection = client.get(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}/results",
        headers=_auth(token),
    )
    assert inspection.status_code == 200, inspection.text
    response = client.post(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/release-results",
        headers=_auth(token),
        json={"expected_result_fingerprint": inspection.json()["result_fingerprint"]},
    )
    assert response.status_code == 201, response.text


def _start_amendment(client, private_key, scenario: Scenario) -> str:
    # ADR-048 §8: starting an amendment is `LAB_TECH` authority (entry/correction), and releasing it
    # is `LAB_RESULT_RELEASER`. One identity holding both is legitimate (no forced second person),
    # and it keeps this helper to one token.
    token = _member_token(
        client,
        private_key,
        scenario.lab_id,
        f"amender-{scenario.name}",
        ["LAB_TECH", "LAB_RESULT_RELEASER"],
    )
    response = client.post(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}/amendments",
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    scenario.amendment_releaser_token = token
    return response.json()["amendment_id"]


def _amend_value(client, scenario: Scenario, amendment_id: str, observation_id: str, value: str):
    response = client.post(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/amendments/{amendment_id}/items/{scenario.item_id}"
        f"/observations/{observation_id}/native-correct",
        headers=_auth(scenario.amendment_releaser_token),
        json={"numeric_value": value},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _release_amendment(client, scenario: Scenario, amendment_id: str) -> None:
    token = scenario.amendment_releaser_token
    inspection = client.get(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/amendments/{amendment_id}",
        headers=_auth(token),
    )
    assert inspection.status_code == 200, inspection.text
    response = client.post(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/amendments/{amendment_id}/release",
        headers=_auth(token),
        json={"expected_result_fingerprint": inspection.json()["result_fingerprint"]},
    )
    assert response.status_code == 201, response.text


def _versions(client, scenario: Scenario) -> list[dict]:
    response = client.get(
        f"/exchange/v1/organizations/{scenario.lab_id}/lab-orders/{scenario.order_id}"
        f"/release-versions",
        headers=_auth(scenario.tech_token),
    )
    assert response.status_code == 200, response.text
    return response.json()["versions"]


def _version_id(client, scenario: Scenario, number: int) -> str:
    for version in _versions(client, scenario):
        if version["version_number"] == number:
            return version["release_version_id"]
    raise AssertionError(f"no version {number} for order {scenario.order_id}")


def _collect_keys(node, into: set[str]) -> None:
    if isinstance(node, dict):
        into.update(node.keys())
        for value in node.values():
            _collect_keys(value, into)
    elif isinstance(node, list):
        for value in node:
            _collect_keys(value, into)


def _all_keys(payload) -> set[str]:
    keys: set[str] = set()
    _collect_keys(payload, keys)
    return keys


def _ready_released_scenario(
    client, private_key, organizations, catalog, directory, patients, sessions, name: str
) -> Scenario:
    scenario = _scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, name
    )
    scenario.v1_observation_id = _record_value(client, scenario, "4.2")
    _complete_entry(client, scenario)
    _release_v1(client, private_key, scenario)
    return scenario


# ==================================================================================================
# RP-A: a report exists only for a released version
# ==================================================================================================


def test_rp_a1_no_release_version_exists_before_release(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """An entered, completed, unreleased result has nothing to report on -- not a hidden report, no
    report at all. There is no `LabResultReleaseVersion` row, so there is no id to ask for."""
    private_key, _ = keypair
    scenario = _scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "A1"
    )
    _record_value(client, scenario, "4.2")
    _complete_entry(client, scenario)

    assert _versions(client, scenario) == []
    with sessions() as session:
        assert (
            session.scalars(
                select(LabResultReleaseVersion).where(
                    LabResultReleaseVersion.lab_order_id == scenario.order_id
                )
            ).all()
            == []
        )


def test_rp_a2_guessed_version_id_is_refused_and_discloses_no_value(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """The draft value `4.2` has been entered and the entry completed. A laboratory user guessing a
    report URL gets a 404 whose body contains no clinical value at all."""
    private_key, _ = keypair
    scenario = _scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "A2"
    )
    _record_value(client, scenario, "4.2")
    _complete_entry(client, scenario)

    response = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, uuid.uuid4()),
        headers=_auth(scenario.tech_token),
    )
    assert response.status_code == 404, response.text
    assert "4.2" not in response.text
    assert response.json() == {"detail": "release version not found"}


def test_rp_a3_clinic_cannot_obtain_a_report_before_release(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """The clinic's own route refuses on the order's status before any version id is even looked
    up -- the same 409 wording the sibling version-content read already uses."""
    private_key, _ = keypair
    scenario = _scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "A3"
    )
    _record_value(client, scenario, "4.2")
    _complete_entry(client, scenario)

    response = client.get(
        _clinic_report_url(scenario.provider_id, scenario.order_id, uuid.uuid4()),
        headers=_auth(scenario.clinician_token),
    )
    assert response.status_code == 409, response.text
    assert "4.2" not in response.text


# ==================================================================================================
# RP-B: Version 1
# ==================================================================================================


def test_rp_b1_version_one_report_carries_the_exact_released_content(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "B1"
    )
    version_id = _version_id(client, scenario, 1)

    response = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(scenario.tech_token),
    )
    assert response.status_code == 200, response.text
    report = response.json()

    assert report["release_version_id"] == version_id
    assert report["release_version_number"] == 1
    assert report["is_amendment"] is False
    assert report["is_current_version"] is True
    assert report["current_release_version_number"] == 1
    assert report["order_id"] == str(scenario.order_id)
    assert report["laboratory"] == {
        "organization_id": str(scenario.lab_id),
        "name": f"Laboratoire-{scenario.name}",
    }
    assert report["ordering_organization"] == {
        "organization_id": str(scenario.provider_id),
        "name": f"Clinique-{scenario.name}",
    }
    assert report["patient"]["display_name"] == f"Patient Synthétique {scenario.name}"

    assert len(report["results"]) == 1
    item = report["results"][0]
    assert item["catalog_code_snapshot"] == f"DHEALTH-RP-{scenario.name}"
    assert item["catalog_display_name_snapshot"] == f"Analyse de démonstration {scenario.name}"
    assert len(item["observations"]) == 1
    observation = item["observations"][0]
    assert observation["observation_id"] == scenario.v1_observation_id
    assert observation["display_label_snapshot"] == "Mesure numérique A"
    assert observation["result_type"] == "NUMERIC"
    assert Decimal(observation["numeric_value"]) == Decimal("4.2")
    assert observation["numeric_unit"] == "mg/dL"


def test_rp_b2_released_at_is_the_stored_release_timestamp_not_an_approximation(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """The DTO claims `released_at` is the authoritative release instant. That is only true because
    `release()` assigns the version's `created_at` from the very same `released_at` value it writes
    to `LabResultRelease`. Asserted against both rows rather than taken on trust."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "B2"
    )
    version_id = _version_id(client, scenario, 1)
    report = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(scenario.tech_token),
    ).json()

    with sessions() as session:
        release = session.scalars(
            select(LabResultRelease).where(LabResultRelease.lab_order_id == scenario.order_id)
        ).one()
        version = session.get(LabResultReleaseVersion, uuid.UUID(version_id))
        assert version.created_at == release.released_at
        assert release.lab_organization_id == scenario.lab_id
        stored = version.created_at

    # SQLite hands these back naive; PostgreSQL hands them back aware. Normalising here keeps the
    # assertion about the instant rather than about the driver -- the real PostgreSQL qualification
    # in `tests/integration/` asserts the same equality against a tz-aware column.
    served = datetime.fromisoformat(report["released_at"])
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    assert served == stored


def test_rp_b3_the_clinic_reads_the_same_document_under_its_own_authority(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "B3"
    )
    version_id = _version_id(client, scenario, 1)

    lab_report = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(scenario.tech_token),
    )
    clinic_report = client.get(
        _clinic_report_url(scenario.provider_id, scenario.order_id, version_id),
        headers=_auth(scenario.clinician_token),
    )
    assert lab_report.status_code == 200, lab_report.text
    assert clinic_report.status_code == 200, clinic_report.text
    # One document, one projection, one builder -- not two shapes that happen to agree today.
    assert lab_report.json() == clinic_report.json()


# ==================================================================================================
# RP-C: an amendment draft changes nothing
# ==================================================================================================


def test_rp_c1_amendment_draft_does_not_touch_the_version_one_report(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "C1"
    )
    version_id = _version_id(client, scenario, 1)
    before = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(scenario.tech_token),
    ).json()

    amendment_id = _start_amendment(client, private_key, scenario)
    _amend_value(client, scenario, amendment_id, scenario.v1_observation_id, "9.9")

    after = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(scenario.tech_token),
    )
    assert after.status_code == 200, after.text
    assert after.json() == before
    # The draft's proposed value must not be reachable through the report at all.
    assert "9.9" not in after.text
    # And no second version exists to report on yet.
    assert [v["version_number"] for v in _versions(client, scenario)] == [1]


# ==================================================================================================
# RP-D: Version 2, and Version 1 after it
# ==================================================================================================


def test_rp_d1_version_two_is_exactly_version_two_and_version_one_is_still_version_one(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "D1"
    )
    v1_id = _version_id(client, scenario, 1)
    v1_before = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, v1_id),
        headers=_auth(scenario.tech_token),
    ).json()

    amendment_id = _start_amendment(client, private_key, scenario)
    _amend_value(client, scenario, amendment_id, scenario.v1_observation_id, "9.9")
    _release_amendment(client, scenario, amendment_id)

    v2_id = _version_id(client, scenario, 2)
    v2 = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, v2_id),
        headers=_auth(scenario.tech_token),
    ).json()
    assert v2["release_version_number"] == 2
    assert v2["is_amendment"] is True
    assert v2["is_current_version"] is True
    assert v2["current_release_version_number"] == 2
    assert Decimal(v2["results"][0]["observations"][0]["numeric_value"]) == Decimal("9.9")

    v1_after = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, v1_id),
        headers=_auth(scenario.tech_token),
    ).json()
    assert Decimal(v1_after["results"][0]["observations"][0]["numeric_value"]) == Decimal("4.2")
    # Every released clinical field is identical to the pre-amendment capture. The only fields
    # permitted to move are the two derived currentness ones.
    assert v1_after["results"] == v1_before["results"]
    assert v1_after["released_at"] == v1_before["released_at"]
    assert v1_after["release_version_id"] == v1_before["release_version_id"]
    assert v1_after["is_current_version"] is False
    assert v1_after["current_release_version_number"] == 2
    moved = {key for key in v1_before if v1_before[key] != v1_after[key]}
    assert moved == {"is_current_version", "current_release_version_number"}


def test_rp_d2_version_two_released_at_is_the_stored_amendment_timestamp(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "D2"
    )
    amendment_id = _start_amendment(client, private_key, scenario)
    _amend_value(client, scenario, amendment_id, scenario.v1_observation_id, "9.9")
    _release_amendment(client, scenario, amendment_id)
    v2_id = _version_id(client, scenario, 2)

    with sessions() as session:
        amendment = session.get(LabResultAmendment, uuid.UUID(amendment_id))
        version = session.get(LabResultReleaseVersion, uuid.UUID(v2_id))
        assert version.created_at == amendment.released_at


def test_rp_d3_a_version_id_from_another_order_is_not_served_under_this_one(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """A real, existing release version id -- just not this order's. The exact-version read must
    refuse it rather than fall back to this order's own current release."""
    private_key, _ = keypair
    mine = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "D3A"
    )
    theirs = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "D3B"
    )
    foreign_version_id = _version_id(client, theirs, 1)

    response = client.get(
        _lab_report_url(mine.lab_id, mine.order_id, foreign_version_id),
        headers=_auth(mine.tech_token),
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "release version not found"}


# ==================================================================================================
# RP-E: authority is reused, never widened
# ==================================================================================================


def test_rp_e1_entry_authority_alone_can_read_a_released_report(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """Release authority and read authority are different concepts. `LAB_TECH` holds
    `lab.queue.work` and no release authority, and may read the report it may already inspect."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "E1"
    )
    version_id = _version_id(client, scenario, 1)
    tech_only = _member_token(client, private_key, scenario.lab_id, "e1-tech-only", ["LAB_TECH"])
    response = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(tech_only),
    )
    assert response.status_code == 200, response.text


def test_rp_e2_release_authority_alone_can_also_read_it(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "E2"
    )
    version_id = _version_id(client, scenario, 1)
    releaser_only = _member_token(
        client, private_key, scenario.lab_id, "e2-releaser-only", ["LAB_RESULT_RELEASER"]
    )
    response = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(releaser_only),
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("roles", [["BILLING_CLERK"], ["ORG_ADMIN"]])
def test_rp_e3_money_and_configuration_authority_grant_no_report_access(
    client, keypair, organizations, catalog, directory, patients, sessions, roles
):
    """`billing.operate` and `facility.configure` are not clinical read. Both are refused on the
    laboratory route, in the laboratory that actually performed the test."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, f"E3{roles[0]}"
    )
    version_id = _version_id(client, scenario, 1)
    token = _member_token(client, private_key, scenario.lab_id, f"e3-{roles[0].lower()}", roles)
    response = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id), headers=_auth(token)
    )
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("roles", [["BILLING_CLERK"], ["ORG_ADMIN"]])
def test_rp_e4_money_and_configuration_authority_are_refused_on_the_clinic_route_too(
    client, keypair, organizations, catalog, directory, patients, sessions, roles
):
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, f"E4{roles[0]}"
    )
    version_id = _version_id(client, scenario, 1)
    token = _member_token(
        client, private_key, scenario.provider_id, f"e4-{roles[0].lower()}", roles
    )
    response = client.get(
        _clinic_report_url(scenario.provider_id, scenario.order_id, version_id),
        headers=_auth(token),
    )
    assert response.status_code == 403, response.text


def test_rp_e5_a_rival_laboratory_cannot_read_the_report(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """Both shapes of the attempt: under the rival's own laboratory id (the order is not theirs ->
    404, indistinguishable from a non-existent order) and under the performing laboratory's id
    (no membership there -> 403). Neither discloses that a report exists."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "E5"
    )
    version_id = _version_id(client, scenario, 1)
    rival = make_lab(organizations, name="Laboratoire Rival E5")
    rival_token = _member_token(client, private_key, rival.id, "e5-rival-tech", ["LAB_TECH"])

    # Under their own laboratory id the membership gate passes and the *order-scope* check refuses:
    # this is the assertion that reddens if the report route stops scoping the order to the lab.
    own_id = client.get(
        _lab_report_url(rival.id, scenario.order_id, version_id), headers=_auth(rival_token)
    )
    assert own_id.status_code == 404, own_id.text
    assert own_id.json() == {"detail": "order not found"}

    # Under the performing laboratory's id the membership gate refuses first, and the repository's
    # existing non-enumerating policy answers "organization not found" rather than confirming that
    # an organization they have no membership in exists. Pinned as it actually behaves, not as a
    # 403 one might expect.
    victim_id = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(rival_token),
    )
    assert victim_id.status_code == 404, victim_id.text
    assert victim_id.json() == {"detail": "organization not found"}
    assert "4.2" not in victim_id.text


def test_rp_e6_an_unrelated_clinic_cannot_read_the_report(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "E6"
    )
    version_id = _version_id(client, scenario, 1)
    other = make_provider(organizations, name="Clinique Étrangère E6")
    other_token = _member_token(client, private_key, other.id, "e6-other-clinician", ["CLINICIAN"])

    own_id = client.get(
        _clinic_report_url(other.id, scenario.order_id, version_id), headers=_auth(other_token)
    )
    assert own_id.status_code == 404, own_id.text

    # Same non-enumerating policy as RP-E5's second half.
    victim_id = client.get(
        _clinic_report_url(scenario.provider_id, scenario.order_id, version_id),
        headers=_auth(other_token),
    )
    assert victim_id.status_code == 404, victim_id.text
    assert victim_id.json() == {"detail": "organization not found"}
    assert "4.2" not in victim_id.text


def test_rp_e7_platform_admin_holds_no_blanket_clinical_report_read(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """Platform administration is network authority, not a membership. It does not bypass
    `_require_lab_tech_or_releaser` or `_require_clinician`, exactly as it does not bypass them for
    the existing exact-version content reads."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "E7"
    )
    version_id = _version_id(client, scenario, 1)
    admin_token = make_token(private_key, subject="e7-platform-admin", roles=[PLATFORM_ADMIN_ROLE])
    _bootstrap_identity(client, private_key, "e7-platform-admin", roles=[PLATFORM_ADMIN_ROLE])

    lab_side = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(admin_token),
    )
    clinic_side = client.get(
        _clinic_report_url(scenario.provider_id, scenario.order_id, version_id),
        headers=_auth(admin_token),
    )
    assert lab_side.status_code == 403, lab_side.text
    assert clinic_side.status_code == 403, clinic_side.text


def test_rp_e8_an_unauthenticated_request_is_refused(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "E8"
    )
    version_id = _version_id(client, scenario, 1)
    assert (
        client.get(_lab_report_url(scenario.lab_id, scenario.order_id, version_id)).status_code
        == 401
    )
    assert (
        client.get(
            _clinic_report_url(scenario.provider_id, scenario.order_id, version_id)
        ).status_code
        == 401
    )


def test_rp_e9_a_deny_override_on_the_read_capability_refuses_the_report(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """ADR-067 per-member overrides reach the report because the gate is a capability gate.

    The report adds no authority of its own, so this is not a new boundary -- it is evidence
    that the boundary it reuses is the real one. A `LAB_TECH` who may read the report loses it
    when an administrator denies `lab.queue.work` for that one member, **on the next request**,
    with no logout and no token refresh: the same bearer token is used before and after.
    """
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "E9"
    )
    version_id = _version_id(client, scenario, 1)
    token, membership_id = _member_with_membership(
        client, private_key, scenario.lab_id, "e9-tech", ["LAB_TECH"]
    )

    before = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id), headers=_auth(token)
    )
    assert before.status_code == 200, before.text

    _set_override(client, private_key, scenario.lab_id, membership_id, "lab.queue.work", "DENY")

    # Same token, no re-authentication.
    after = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id), headers=_auth(token)
    )
    assert after.status_code == 403, after.text
    assert "4.2" not in after.text


def test_rp_e10_a_grant_override_alone_is_enough_to_read_the_report(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """The other direction: a membership whose role template carries no laboratory clinical
    authority at all reads the report once `lab.queue.work` is granted to that one member. The
    report's gate is the capability, never the role name."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "E10"
    )
    version_id = _version_id(client, scenario, 1)
    # BILLING_CLERK: money authority and nothing clinical (ADR-066).
    token, membership_id = _member_with_membership(
        client, private_key, scenario.lab_id, "e10-clerk", ["BILLING_CLERK"]
    )

    refused = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id), headers=_auth(token)
    )
    assert refused.status_code == 403, refused.text

    _set_override(client, private_key, scenario.lab_id, membership_id, "lab.queue.work", "GRANT")

    granted = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id), headers=_auth(token)
    )
    assert granted.status_code == 200, granted.text


def test_rp_e11_clearing_the_override_returns_the_member_to_their_role(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """Revocation is not a separate mechanism, and it takes effect on the same token. Granted,
    readable; cleared, refused again -- because the member's role template never carried it."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "E11"
    )
    version_id = _version_id(client, scenario, 1)
    token, membership_id = _member_with_membership(
        client, private_key, scenario.lab_id, "e11-clerk", ["BILLING_CLERK"]
    )

    _set_override(client, private_key, scenario.lab_id, membership_id, "lab.queue.work", "GRANT")
    assert (
        client.get(
            _lab_report_url(scenario.lab_id, scenario.order_id, version_id), headers=_auth(token)
        ).status_code
        == 200
    )

    _clear_override(client, private_key, scenario.lab_id, membership_id, "lab.queue.work")
    revoked = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id), headers=_auth(token)
    )
    assert revoked.status_code == 403, revoked.text
    assert "4.2" not in revoked.text


# ==================================================================================================
# RP-F: projection, privacy and cache policy
# ==================================================================================================


def test_rp_f1_the_projection_is_exactly_the_report_facts(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "F1"
    )
    version_id = _version_id(client, scenario, 1)
    report = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(scenario.tech_token),
    ).json()

    assert set(report) == REPORT_KEYS
    assert set(report["laboratory"]) == ORGANIZATION_KEYS
    assert set(report["ordering_organization"]) == ORGANIZATION_KEYS
    assert set(report["patient"]) == PATIENT_KEYS
    for item in report["results"]:
        assert set(item) == ITEM_KEYS
        for observation in item["observations"]:
            assert set(observation) == OBSERVATION_KEYS


@pytest.mark.parametrize("side", ["lab", "clinic"])
def test_rp_f2_no_forbidden_field_appears_at_any_depth(
    client, keypair, organizations, catalog, directory, patients, sessions, side
):
    """Run on both routes. The clinic's own version-content read carries `review_status`,
    `reviewed_at`, `reviewed_by_practitioner_reference_id` and the wider
    `PatientIdentitySummaryResponse` (with contact channel and value); the report must carry none of
    them."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, f"F2{side}"
    )
    version_id = _version_id(client, scenario, 1)
    url = (
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id)
        if side == "lab"
        else _clinic_report_url(scenario.provider_id, scenario.order_id, version_id)
    )
    token = scenario.tech_token if side == "lab" else scenario.clinician_token
    report = client.get(url, headers=_auth(token)).json()

    present = _all_keys(report)
    assert present & FORBIDDEN_KEYS == set(), sorted(present & FORBIDDEN_KEYS)


@pytest.mark.parametrize("side", ["lab", "clinic"])
def test_rp_f3_report_responses_are_never_shared_or_publicly_cacheable(
    client, keypair, organizations, catalog, directory, patients, sessions, side
):
    """Scoped to the two routes this ticket introduces. It says nothing about the repository's
    broader clinical cache-control work, which remains as it was."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, f"F3{side}"
    )
    version_id = _version_id(client, scenario, 1)
    url = (
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id)
        if side == "lab"
        else _clinic_report_url(scenario.provider_id, scenario.order_id, version_id)
    )
    token = scenario.tech_token if side == "lab" else scenario.clinician_token
    response = client.get(url, headers=_auth(token))
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"


# ==================================================================================================
# RP-G/H: what must not change the report
# ==================================================================================================


def test_rp_g1_disabling_the_offering_does_not_hide_a_historical_report(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """DHEALTH-LAB-OFFERINGS-001 governs new ordering. It does not rewrite released truth: a report
    for a release that happened while the analysis was offered stays readable after it is switched
    off."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "G1"
    )
    version_id = _version_id(client, scenario, 1)
    before = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(scenario.tech_token),
    ).json()

    disabled = client.patch(
        f"/exchange/v1/organizations/{scenario.lab_id}/offerings/{scenario.offering_id}",
        headers=_auth(scenario.admin_token),
        json={"active": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["active"] is False

    after = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(scenario.tech_token),
    )
    assert after.status_code == 200, after.text
    assert after.json() == before

    clinic = client.get(
        _clinic_report_url(scenario.provider_id, scenario.order_id, version_id),
        headers=_auth(scenario.clinician_token),
    )
    assert clinic.status_code == 200, clinic.text


def test_rp_h1_a_clinician_review_disposition_does_not_change_the_report(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """ "The clinician reviewed Version 1" is workflow metadata. The report is identical before and
    after it is recorded, which is the behavioural half of the projection assertion in RP-F2."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "H1"
    )
    version_id = _version_id(client, scenario, 1)
    before = client.get(
        _clinic_report_url(scenario.provider_id, scenario.order_id, version_id),
        headers=_auth(scenario.clinician_token),
    ).json()

    reviewed = client.post(
        f"/exchange/v1/organizations/{scenario.provider_id}/orders/{scenario.order_id}"
        f"/release-versions/{version_id}/review/no-follow-up-required",
        headers=_auth(scenario.clinician_token),
    )
    assert reviewed.status_code == 200, reviewed.text

    after = client.get(
        _clinic_report_url(scenario.provider_id, scenario.order_id, version_id),
        headers=_auth(scenario.clinician_token),
    )
    assert after.status_code == 200, after.text
    assert after.json() == before


# ==================================================================================================
# RP-I: snapshot truth versus current reference data -- the documented limitation, tested
# ==================================================================================================


def test_rp_i1_header_organization_name_is_read_live_while_clinical_content_is_frozen(
    client, keypair, organizations, catalog, directory, patients, sessions
):
    """ADR-072 §5. The report does not claim header immutability it cannot deliver: a laboratory
    renamed after the release reprints under its new name. The released clinical content, and the
    catalogue snapshots frozen onto the order, do not move."""
    private_key, _ = keypair
    scenario = _ready_released_scenario(
        client, private_key, organizations, catalog, directory, patients, sessions, "I1"
    )
    version_id = _version_id(client, scenario, 1)
    before = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(scenario.tech_token),
    ).json()

    with sessions() as session:
        organization = session.get(Organization, scenario.lab_id)
        organization.name = "Laboratoire Renommé I1"
        session.commit()

    after = client.get(
        _lab_report_url(scenario.lab_id, scenario.order_id, version_id),
        headers=_auth(scenario.tech_token),
    ).json()

    assert before["laboratory"]["name"] == f"Laboratoire-{scenario.name}"
    assert after["laboratory"]["name"] == "Laboratoire Renommé I1"
    # Everything the release actually froze is untouched by the rename.
    assert after["results"] == before["results"]
    assert after["released_at"] == before["released_at"]
    assert after["release_version_id"] == before["release_version_id"]


# ==================================================================================================
# RP-S: structural guards
#
# Narrow, and only where a behavioural test is weaker. The central invariant -- clinical content
# comes from the immutable manifest -- is proven behaviourally by RP-C and RP-D. What those cannot
# catch is a *future* edit that reintroduces a forbidden source, so these assertions read the report
# code itself.
#
# They read **code, not prose**: the three functions are parsed, every docstring is removed, and the
# result is round-tripped through `ast.unparse`, which discards comments. Asserting over raw source
# would have matched this ticket's own explanatory comments -- which is exactly what the first draft
# of this section did, and why it is written this way.
# ==================================================================================================

REPORT_FUNCTION_NAMES = (
    "_released_report_response",
    "get_lab_order_release_version_report",
    "get_provider_release_version_report",
)


def _report_code() -> str:
    import ast
    import inspect

    from daher_adapter.exchange import api as api_module

    module_source = inspect.getsource(api_module)
    tree = ast.parse(module_source)
    bodies: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in REPORT_FUNCTION_NAMES:
            stripped = [
                statement
                for statement in node.body
                if not (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                )
            ]
            bodies[node.name] = ast.unparse(ast.Module(body=stripped, type_ignores=[]))
    missing = set(REPORT_FUNCTION_NAMES) - set(bodies)
    assert not missing, f"report functions not found in api.py: {sorted(missing)}"
    return "\n".join(bodies[name] for name in REPORT_FUNCTION_NAMES)


def test_rp_s1_the_guard_actually_read_the_report_code():
    # Without this, every `not in` assertion below would pass vacuously against an empty string --
    # the exact failure `patientPrintStyles.test.ts` already hit once in this repository.
    code = _report_code()
    assert len(code) > 1500
    assert "get_manifest_for_version" in code
    # Proof the docstring/comment stripping actually happened: `_released_report_response`'s own
    # docstring names DoctorReview while explaining why it reads none, and the next test asserts
    # that word is absent from the code. Without this line, a stripper that returned nothing
    # useful would make that assertion pass for the wrong reason.
    import inspect

    from daher_adapter.exchange import api as api_module

    assert "DoctorReview" in inspect.getsource(api_module)


def test_rp_s2_the_report_reads_only_the_exact_version_manifest():
    """Not `get_manifest_for_order` ("latest" -- which would let an old version silently become the
    current one), not the amendment inspection (a draft), not the inspection record's own
    current-effective item content."""
    code = _report_code()
    assert "get_manifest_for_order" not in code
    assert "get_amendment_inspection" not in code
    assert "inspection.items" not in code
    assert "_effective_observation_rows" not in code
    assert "get_manifest_for_version" in code


def test_rp_s3_the_report_consults_no_doctor_review_and_no_role_string():
    """Report availability is a release fact, never a review fact (ADR-072 §4). Authorization is a
    capability gate reused from the sibling exact-version reads -- never a role string compared
    here."""
    code = _report_code()
    for forbidden in ("reviews.", "review_", "DoctorReview", "_provider_review_for_version"):
        assert forbidden not in code, forbidden
    for role in ("LAB_TECH", "LAB_RESULT_RELEASER", "CLINICIAN", "ORG_ADMIN", "BILLING_CLERK"):
        assert role not in code, role
    assert "_require_lab_tech_or_releaser" in code
    assert "_require_clinician" in code
    # The cache policy, on both routes. `ast.unparse` normalises to single quotes.
    assert code.count("response.headers['Cache-Control'] = 'no-store'") == 2


def test_rp_s4_no_signature_certification_or_interpretation_vocabulary():
    """ADR-072 §7: the report states who released it and when. It never claims a signature, a seal,
    a certification or an accreditation, and it derives no clinical meaning from a value."""
    code = _report_code().lower()
    for word in ("signature", "signed", "certif", "accredit", "seal", "abnormal", "interpret"):
        assert word not in code, word
