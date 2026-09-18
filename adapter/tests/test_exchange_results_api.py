"""DH-EX-007: lab result recording, order completion, provider result access, and doctor review --
the API-layer authorization/lifecycle/tenant-isolation matrix for
``exchange/results.py``/``exchange/api.py``'s new routes.

Same harness pattern as ``test_exchange_lab_workflow.py``: a bare ``FastAPI()`` app with
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
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import (
    DoctorReview,
    ExchangeSecurityAuditEvent,
    LabObservation,
    LabOrder,
    LabOrderItem,
    LabResult,
    LabResultAmendment,
    LabResultAmendmentStatus,
    LabResultRelease,
    LabResultReleaseManifestEntry,
    LabResultReleaseVersion,
    LabResultType,
    PatientReference,
    ResultDelivery,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
from daher_adapter.models import utc_now

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


def _bootstrap_identity(client: TestClient, private_key, subject: str, **token_kwargs) -> str:
    token = make_token(private_key, subject=subject, **token_kwargs)
    response = client.get("/exchange/v1/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()["identity_id"]


def _release_results(client, private_key, lab_id, order_id, name: str) -> dict:
    """DH-EX-012/ADR-044: bootstraps a fresh LAB_RESULT_RELEASER identity -- a *different* human
    from whichever LAB_TECH entered the results, exercising the un-forced two-person path ADR-044
    permits without requiring -- and releases the order's results. The sole supported production
    way to reach COMPLETED after this ticket."""
    releaser_id = _bootstrap_identity(client, private_key, f"releaser-{name}")
    _grant_roles(
        client, private_key, lab_id, releaser_id, f"releaser-{name}", ["LAB_RESULT_RELEASER"]
    )
    releaser_token = make_token(private_key, subject=f"releaser-{name}")
    fingerprint = _current_result_fingerprint(client, releaser_token, lab_id, order_id)
    response = client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/release-results",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": fingerprint},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _current_result_fingerprint(client: TestClient, token: str, lab_id, order_id) -> str:
    """DH-LAB-004 §57-62: fetches the exact ``result_fingerprint`` a release request must echo
    back -- the inspection step every releaser is now required to actually perform."""
    response = client.get(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/results",
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    return response.json()["result_fingerprint"]


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


def _setup_lab_tech(client, private_key, organizations, name: str, *, roles=None):
    lab = make_lab(organizations, name=f"Lab-{name}")
    identity_id = _bootstrap_identity(client, private_key, f"labtech-{name}")
    _grant_roles(client, private_key, lab.id, identity_id, f"labtech-{name}", roles or ["LAB_TECH"])
    token = make_token(private_key, subject=f"labtech-{name}")
    return lab, token, identity_id


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
    """Full flow through the real API up to IN_PROGRESS: order created, patient selects the lab,
    a fresh LAB_TECH accepts and starts processing. Returns (provider, clinician_token, lab,
    lab_tech_token, order_id, item_ids)."""
    provider, clinician_token = _setup_clinician(client, private_key, organizations, name)
    patient_id = _create_patient(client, private_key, patients, provider.id, name)
    entries = []
    for i in range(test_count):
        entry, _ = catalog.create_entry(
            code=f"DHEALTH-RES-{name}-{i}", display_name=f"Res Test {i}"
        )
        entries.append(entry)
    lab = _price_lab(
        catalog,
        directory,
        organizations,
        name=f"ResLab-{name}",
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

    # LAB_TECH on the *already-selected* lab -- not `_setup_lab_tech`, which creates its own
    # unrelated lab.
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
    # DH-EX-011: start-processing now requires a recorded collection milestone.
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

    return provider, clinician_token, lab, lab_tech_token, order_id


def _order_item_ids(sessions: sessionmaker[Session], order_id: uuid.UUID) -> list[uuid.UUID]:
    with sessions() as session:
        order = session.get(LabOrder, order_id)
        return [item.id for item in order.items]


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


# -- golden journey: result -> release -> completion -> provider read -> review ------------------


def test_golden_numeric_result_release_completion_provider_read_and_review(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="Golden"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    submit_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "numeric_value": "13.5",
            "numeric_unit": "g/dL",
            "reference_range_low": "12.0",
            "reference_range_high": "15.5",
        },
    )
    assert submit_response.status_code == 201, submit_response.text
    body = submit_response.json()
    # DH-EX-012/ADR-044: even the order's sole result no longer completes it -- only an explicit
    # release does. `ready_for_release` reports the same "every item now has a result" fact.
    assert body["order_status"] == "IN_PROGRESS"
    assert body["ready_for_release"] is True
    assert body["result_type"] == "NUMERIC"
    assert set(body.keys()) == {
        "order_id",
        "item_id",
        "result_id",
        "observation_id",
        "result_type",
        "completed_at",
        "entry_completed_at",
        "order_status",
        "ready_for_release",
    }

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"

    # Pre-release: provider cannot read yet, but the lab's own releaser can inspect first --
    # "a releaser must not be forced to release results they cannot inspect."
    pre_release_provider = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    )
    assert pre_release_provider.status_code == 409, pre_release_provider.text

    inspection_response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/results",
        headers=_auth(lab_tech_token),
    )
    assert inspection_response.status_code == 200, inspection_response.text
    inspection_body = inspection_response.json()
    assert inspection_body["all_items_entry_complete"] is True
    assert inspection_body["released"] is False
    assert inspection_body["released_at"] is None
    assert inspection_body["results"][0]["entry_completed"] is True
    assert Decimal(inspection_body["results"][0]["observations"][0]["numeric_value"]) == Decimal(
        "13.5"
    )

    release_body = _release_results(client, private_key, lab.id, order_id, "Golden")
    assert release_body["order_id"] == str(order_id)
    assert release_body["released_at"] is not None
    releaser_identity_id = release_body["released_by_identity_id"]

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "COMPLETED"

    post_release_inspection = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/results",
        headers=_auth(lab_tech_token),
    )
    assert post_release_inspection.json()["released"] is True

    results_response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    )
    assert results_response.status_code == 200, results_response.text
    results_body = results_response.json()
    assert results_body["order_status"] == "COMPLETED"
    # DH-EX-014A/ADR-048: an explicit-release order reads its exact release-version manifest,
    # not live current-effective state -- version 1, never an amendment (none exists yet).
    assert results_body["release_version_id"] is not None
    assert results_body["release_version_number"] == 1
    assert results_body["is_amendment"] is False
    assert len(results_body["results"]) == 1
    result_line = results_body["results"][0]
    assert len(result_line["observations"]) == 1
    observation_line = result_line["observations"][0]
    # FastAPI/Pydantic's actual response wire contract for Decimal is JSON string,
    # not a JSON number; keep the frontend contract honest and precision-preserving.
    assert all(
        isinstance(observation_line[field], str)
        for field in ("numeric_value", "reference_range_low", "reference_range_high")
    )
    assert Decimal(observation_line["numeric_value"]) == Decimal("13.5")
    assert Decimal(observation_line["reference_range_low"]) == Decimal("12.0")
    assert Decimal(observation_line["reference_range_high"]) == Decimal("15.5")
    assert observation_line["numeric_unit"] == "g/dL"

    review_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/no-follow-up-required",
        headers=_auth(clinician_token),
    )
    assert review_response.status_code == 200, review_response.text
    review_body = review_response.json()
    assert review_body["status"] == "NO_FOLLOW_UP_REQUIRED"
    assert review_body["reviewed_by_practitioner_reference_id"] is not None
    # DH-EX-014A/ADR-048: the review is bound to exactly the release version the provider read.
    assert review_body["release_version_id"] == results_body["release_version_id"]

    with sessions() as session:
        delivery = session.scalar(
            select(ResultDelivery).where(ResultDelivery.lab_order_id == order_id)
        )
        assert delivery.status == "PENDING"  # DH-EX-007/012 never touch ResultDelivery
        assert delivery.delivered_at is None

    # DH-EX-012/ADR-044: `lab_order_completed`'s actor is now the releaser, never the LAB_TECH who
    # entered the result -- a semantic change from DH-EX-007, disclosed in PROJECT_STATE.md.
    # DH-EX-012C: the backward-compatible single-observation convenience path writes both
    # `lab_observation_recorded` and `lab_result_entry_completed` atomically -- `lab_result_
    # recorded` is retired, no code path writes it any more.
    observation_events = _audit_events(sessions, "lab_observation_recorded")
    entry_completed_events = _audit_events(sessions, "lab_result_entry_completed")
    released_events = _audit_events(sessions, "lab_results_released")
    completed_events = _audit_events(sessions, "lab_order_completed")
    review_events = _audit_events(sessions, "doctor_review_recorded")
    assert len(observation_events) == 1
    assert len(entry_completed_events) == 1
    assert len(released_events) == 1
    assert len(completed_events) == 1
    assert len(review_events) == 1
    assert observation_events[0]["organization_id"] == str(lab.id)
    assert entry_completed_events[0]["organization_id"] == str(lab.id)
    assert released_events[0]["organization_id"] == str(lab.id)
    assert completed_events[0]["organization_id"] == str(lab.id)
    assert review_events[0]["organization_id"] == str(provider.id)
    with sessions() as session:
        completed_audit = session.scalar(
            select(ExchangeSecurityAuditEvent).where(
                ExchangeSecurityAuditEvent.event_type == "lab_order_completed"
            )
        )
        assert str(completed_audit.actor_identity_id) == releaser_identity_id


def test_golden_text_result(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="TextResult"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "TEXT", "text_value": "No growth after 48h"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["result_type"] == "TEXT"


# -- lifecycle ------------------------------------------------------------------------------------


def test_result_before_in_progress_refused(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Selected-but-not-yet-accepted order: result submission must be refused, not silently
    accepted."""
    private_key, _ = keypair
    provider, clinician_token = _setup_clinician(client, private_key, organizations, "NotStarted")
    patient_id = _create_patient(client, private_key, patients, provider.id, "NotStarted")
    entry, _ = catalog.create_entry(code="DHEALTH-RES-NotStarted", display_name="Not Started Test")
    lab = _price_lab(
        catalog, directory, organizations, name="NotStartedLab", tests=[(entry.id, Decimal("500"))]
    )
    create_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders",
        headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
        json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(entry.id)]},
    )
    order_id = uuid.UUID(create_response.json()["id"])
    access_token = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    ).json()["access_token"]
    options = client.get(
        "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
    ).json()["options"]
    price_ids = [line["current_price_id"] for line in options[0]["prices"]]
    client.post(
        "/exchange/v1/patient/order/selection",
        headers=_auth(access_token),
        json={"lab_organization_id": str(lab.id), "expected_price_ids": price_ids},
    )
    lab_tech_id = _bootstrap_identity(client, private_key, "labtech-notstarted")
    _grant_roles(client, private_key, lab.id, lab_tech_id, "labtech-notstarted", ["LAB_TECH"])
    lab_tech_token = make_token(private_key, subject="labtech-notstarted")
    item_id = _order_item_ids(sessions, order_id)[0]

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert response.status_code == 409, response.text


def test_result_after_completed_refused(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="AfterComplete"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    first = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert first.json()["ready_for_release"] is True
    # DH-EX-012/ADR-044: an explicit release is required to reach COMPLETED now.
    _release_results(client, private_key, lab.id, order_id, "AfterComplete")

    second = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "2"},
    )
    assert second.status_code == 409, second.text


def test_oversized_result_fields_are_refused_at_the_api_boundary_not_the_db(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """``numeric_unit``/``text_value`` exceed ``LabResult``'s DB column length (``String(32)``/
    ``String(2048)``). Without a matching ``max_length`` on ``LabResultSubmitRequest``, this
    reaches PostgreSQL's real ``VARCHAR(n)`` column and raises ``DataError``, not
    ``IntegrityError`` -- the one type `_submit_in_session` translates -- so it would otherwise
    surface as a raw, uncontrolled 500 instead of a clean 422. SQLite (this suite's engine) does
    not enforce ``VARCHAR`` length, so this defect is invisible unless the boundary itself is
    tested, not just the DB behind it."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="Oversized"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    oversized_unit = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1", "numeric_unit": "u" * 33},
    )
    assert oversized_unit.status_code == 422, oversized_unit.text

    oversized_text = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "TEXT", "text_value": "t" * 2049},
    )
    assert oversized_text.status_code == 422, oversized_text.text

    # Neither rejected request left any partial state -- a subsequent well-formed submission for
    # the same item still succeeds.
    good = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1", "numeric_unit": "u" * 32},
    )
    assert good.status_code == 201, good.text


# -- DH-EX-007A: numeric_value/reference_range_low/reference_range_high must exactly match
# LabResult's Numeric(18, 4) database columns -- the same class of gap
# test_oversized_result_fields_are_refused_at_the_api_boundary_not_the_db above already closed for
# numeric_unit/text_value. Reproduced live against real PostgreSQL before this fix (recorded in
# PROJECT_STATE.md's DH-EX-007A entry): an over-scale value (1.23456) reached the database and was
# **silently rounded to 1.2346**, returning 201 -- no error, a different clinical value stored than
# the one sent. An over-precision value (123456789012345.6789, 15 integer digits) raised
# psycopg.errors.NumericValueOutOfRange, translated by SQLAlchemy to DataError -- uncaught by
# `_submit_in_session`, reaching the caller as a raw HTTP 500. SQLite (this file's default engine)
# enforces neither, so these tests exist to prove the *API boundary* itself refuses both shapes
# before either reaches a database of any kind. ------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["numeric_value", "reference_range_low", "reference_range_high"],
)
def test_decimal_field_accepts_representable_boundary_values(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, field: str
) -> None:
    """Every value `NUMERIC(18, 4)` can represent losslessly must still be accepted, on each of
    the three Decimal fields independently -- a constraint applied to one field but accidentally
    omitted from another must fail this parametrization."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name=f"DecimalBoundary{field}",
        test_count=7,
    )
    item_ids = _order_item_ids(sessions, order_id)

    valid_values = [
        "1",  # integer
        "-1",  # negative integer -- no sign rule exists or is added by this ticket
        "13.5",  # ordinary decimal
        "1.2345",  # exactly four fractional digits
        "99999999999999.9999",  # the maximum representable positive magnitude (18 total digits)
        "-99999999999999.9999",  # the equivalent negative magnitude
        "1.10000",  # trailing zeros beyond scale 4 -- losslessly reduces to 1.1, must be accepted
    ]
    for item_id, value in zip(item_ids, valid_values, strict=True):
        body: dict[str, str] = {"result_type": "NUMERIC", "numeric_value": "1.0"}
        body[field] = value
        response = client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
            headers=_auth(lab_tech_token),
            json=body,
        )
        assert response.status_code == 201, (value, response.text)


@pytest.mark.parametrize(
    "field",
    ["numeric_value", "reference_range_low", "reference_range_high"],
)
def test_decimal_field_rejects_too_many_fractional_digits(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, field: str
) -> None:
    """A genuinely significant fifth decimal digit (not just a trailing zero -- see the boundary
    test above) must be refused with 422, on each of the three Decimal fields independently,
    before the endpoint's transaction ever opens."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name=f"TooManyDp{field}"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    body: dict[str, str] = {"result_type": "NUMERIC", "numeric_value": "1.0"}
    body[field] = "1.23456"
    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json=body,
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["type"] == "decimal_max_places"


@pytest.mark.parametrize(
    "field",
    ["numeric_value", "reference_range_low", "reference_range_high"],
)
def test_decimal_field_rejects_too_many_total_digits(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, field: str
) -> None:
    """A value exceeding NUMERIC(18, 4)'s 18-total/14-integer-digit representable domain must be
    refused with 422 -- the exact class that reached real PostgreSQL as a raw, uncontrolled 500
    before this fix -- on each of the three Decimal fields independently."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name=f"TooManyTotal{field}",
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    body: dict[str, str] = {"result_type": "NUMERIC", "numeric_value": "1.0"}
    body[field] = "123456789012345.6789"  # 15 integer digits, 19 total -- exceeds both limits
    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json=body,
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["type"] in ("decimal_max_digits", "decimal_whole_digits")


def test_rejected_decimal_result_creates_no_result_audit_row_or_order_mutation(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """A 422 from Pydantic happens before the endpoint's transaction ever opens -- prove that
    directly: no ``LabResult`` row, no ``lab_observation_recorded``/``lab_result_entry_completed``/
    ``lab_order_completed`` audit event, and no ``LabOrder.status`` change, for an over-scale
    request on a one-item order (so a real completion would otherwise have been triggered by a
    successful submission)."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="RejectedNoSideEffect",
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1.23456"},
    )
    assert response.status_code == 422, response.text

    with sessions() as session:
        stored = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item_id))
        assert stored is None
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"

    assert _audit_events(sessions, "lab_observation_recorded") == []
    assert _audit_events(sessions, "lab_result_entry_completed") == []
    assert _audit_events(sessions, "lab_order_completed") == []

    # No partial state was left behind -- a subsequent well-formed submission for the same item
    # still succeeds and leaves this one-item order ready for release (DH-EX-012: no longer
    # auto-completed).
    good = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1.2345"},
    )
    assert good.status_code == 201, good.text
    assert good.json()["order_status"] == "IN_PROGRESS"
    assert good.json()["ready_for_release"] is True


def test_text_result_flow_unaffected_by_decimal_hardening(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """The Decimal `max_digits`/`decimal_places` constraints apply only to the three Decimal
    fields -- a TEXT result (which must leave all three `None`, per the existing
    `_value_matches_declared_type` validator) must continue to work exactly as before."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="TextUnaffected"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "TEXT", "text_value": "Negative"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["result_type"] == "TEXT"


def test_duplicate_result_on_still_in_progress_order_refused(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="DupInProgress",
        test_count=2,
    )
    item_ids = _order_item_ids(sessions, order_id)
    first = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_ids[0]}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert first.json()["order_status"] == "IN_PROGRESS"

    duplicate = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_ids[0]}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "999"},
    )
    assert duplicate.status_code == 409, duplicate.text

    second = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_ids[1]}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "2"},
    )
    # DH-EX-012/ADR-044: even the last missing result leaves the order IN_PROGRESS now.
    assert second.json()["order_status"] == "IN_PROGRESS"
    assert second.json()["ready_for_release"] is True


def test_partial_results_keep_order_in_progress_via_api(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="PartialAPI",
        test_count=3,
    )
    item_ids = _order_item_ids(sessions, order_id)

    for i, item_id in enumerate(item_ids):
        response = client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
            headers=_auth(lab_tech_token),
            json={"result_type": "NUMERIC", "numeric_value": str(i + 1)},
        )
        assert response.status_code == 201, response.text
        # DH-EX-012/ADR-044: order status stays IN_PROGRESS regardless of which item this is --
        # only `ready_for_release` reflects completeness now; only explicit release reaches
        # COMPLETED.
        assert response.json()["order_status"] == "IN_PROGRESS"
        expected_ready = i == len(item_ids) - 1
        assert response.json()["ready_for_release"] is expected_ready


def test_result_for_item_belonging_to_different_order_refused(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider_a, _tok_a, lab, lab_tech_token, order_a = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CrossOrderA"
    )
    # A second, unrelated IN_PROGRESS order at the SAME lab -- its item id is real, just not on
    # order_a.
    _provider_b, _tok_b, _lab_b, _tech_b, order_b = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CrossOrderB"
    )
    foreign_item_id = _order_item_ids(sessions, order_b)[0]

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_a}/items/{foreign_item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert response.status_code == 404, response.text


# -- authorization: result submission --------------------------------------------------------------


def test_wrong_lab_cannot_submit_result_and_is_non_enumerating(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, _lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="WrongLabResult"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    impostor_lab, impostor_token, _id = _setup_lab_tech(
        client, private_key, organizations, "impostor-result"
    )

    real_response = client.post(
        f"/exchange/v1/organizations/{impostor_lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(impostor_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    fake_response = client.post(
        f"/exchange/v1/organizations/{impostor_lab.id}/lab-orders/{uuid.uuid4()}/items/{item_id}/result",
        headers=_auth(impostor_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert real_response.status_code == 404
    assert fake_response.status_code == 404
    assert real_response.json() == fake_response.json()

    with sessions() as session:
        assert session.scalar(select(LabResult)) is None


def test_provider_clinician_cannot_submit_lab_result(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, clinician_token, lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ClinicianResult"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(clinician_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert response.status_code == 404, response.text  # clinician has zero relationship to lab.id


def test_platform_admin_alone_cannot_submit_result(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="AdminResult"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    admin_token = make_token(
        private_key, subject="platform-admin-result", roles=[PLATFORM_ADMIN_ROLE]
    )

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(admin_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert response.status_code == 403, response.text


def test_lab_org_admin_alone_cannot_submit_result(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="OrgAdminResult"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    org_admin_id = _bootstrap_identity(client, private_key, "lab-org-admin-result")
    _grant_roles(client, private_key, lab.id, org_admin_id, "lab-org-admin-result", ["ORG_ADMIN"])
    org_admin_token = make_token(private_key, subject="lab-org-admin-result")

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(org_admin_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert response.status_code == 403, response.text


# -- provider result access -----------------------------------------------------------------------


def test_results_not_ready_before_completion_is_a_controlled_conflict_not_leaked(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token, _lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="NotReady"
    )

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    )
    assert response.status_code == 409, response.text
    assert "numeric_value" not in response.text
    assert "text_value" not in response.text


def test_wrong_provider_cannot_read_results_and_is_non_enumerating(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider_a, _tok_a, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ProvA"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )

    provider_b, clinician_b_token = _setup_clinician(
        client, private_key, organizations, "ProvBReader"
    )

    real_response = client.get(
        f"/exchange/v1/organizations/{provider_b.id}/orders/{order_id}/results",
        headers=_auth(clinician_b_token),
    )
    fake_response = client.get(
        f"/exchange/v1/organizations/{provider_b.id}/orders/{uuid.uuid4()}/results",
        headers=_auth(clinician_b_token),
    )
    assert real_response.status_code == 404
    assert fake_response.status_code == 404
    assert real_response.json() == fake_response.json()


def test_lab_cannot_use_provider_results_endpoint(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="LabAsProvider"
    )

    response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(lab_tech_token),
    )
    assert response.status_code == 404, response.text  # lab tech has no relationship to provider


def test_provider_results_response_excludes_internal_fields(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="LeakCheck"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    # DH-EX-012/ADR-044: provider visibility requires COMPLETED, which requires an explicit
    # release.
    _release_results(client, private_key, lab.id, order_id, "LeakCheck")

    body = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    ).json()
    forbidden = {
        "lab_organization_id",
        "patient_reference_id",
        "access_token",
        "token_hash",
        "actor_identity_id",
        "membership_id",
    }
    result_line_keys = set(body["results"][0].keys())
    assert forbidden.isdisjoint(result_line_keys)
    assert result_line_keys == {
        "item_id",
        "catalog_code_snapshot",
        "catalog_display_name_snapshot",
        "entry_completed_at",
        "observations",
    }
    observation_keys = set(body["results"][0]["observations"][0].keys())
    assert forbidden.isdisjoint(observation_keys)
    assert observation_keys == {
        "observation_id",
        "source_code",
        "source_system",
        "display_label_snapshot",
        "result_type",
        "numeric_value",
        "numeric_unit",
        "reference_range_low",
        "reference_range_high",
        "text_value",
        "recorded_at",
    }


# -- doctor review ---------------------------------------------------------------------------


def test_review_before_completion_refused(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token, _lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReviewEarly"
    )

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/no-follow-up-required",
        headers=_auth(clinician_token),
    )
    assert response.status_code == 409, response.text
    with sessions() as session:
        review = session.scalar(select(DoctorReview).where(DoctorReview.lab_order_id == order_id))
        assert review.status == "PENDING"


def test_second_terminal_review_is_a_controlled_conflict(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReviewTwice"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    # DH-EX-012/ADR-044: review requires COMPLETED, which requires an explicit release.
    _release_results(client, private_key, lab.id, order_id, "ReviewTwice")
    first = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/no-follow-up-required",
        headers=_auth(clinician_token),
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/follow-up-required",
        headers=_auth(clinician_token),
    )
    assert second.status_code == 409, second.text

    with sessions() as session:
        review = session.scalar(select(DoctorReview).where(DoctorReview.lab_order_id == order_id))
        assert review.status == "NO_FOLLOW_UP_REQUIRED"  # unchanged, never silently overwritten


def test_wrong_provider_cannot_review(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider_a, _tok_a, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReviewWrongProv"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    provider_b, clinician_b_token = _setup_clinician(
        client, private_key, organizations, "ReviewProvB"
    )

    response = client.post(
        f"/exchange/v1/organizations/{provider_b.id}/orders/{order_id}/review/no-follow-up-required",
        headers=_auth(clinician_b_token),
    )
    assert response.status_code == 404, response.text


def test_platform_admin_cannot_review(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReviewAdmin"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    admin_token = make_token(
        private_key, subject="platform-admin-review", roles=[PLATFORM_ADMIN_ROLE]
    )

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/no-follow-up-required",
        headers=_auth(admin_token),
    )
    assert response.status_code == 403, response.text


def test_reviewer_identity_is_the_authenticated_caller_not_the_ordering_clinician(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """The reviewing clinician may be a different authenticated identity than whoever created the
    order -- the recorded `reviewed_by_practitioner_reference_id` must reflect the actual caller,
    resolved via the same DH-EX-004 identity-backed binding, never the order's own
    `practitioner_reference_id`."""
    private_key, _ = keypair
    provider, ordering_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReviewerDiff"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    # DH-EX-012/ADR-044: review requires COMPLETED, which requires an explicit release.
    _release_results(client, private_key, lab.id, order_id, "ReviewerDiff")

    second_clinician_id = _bootstrap_identity(client, private_key, "second-reviewer")
    _grant_roles(
        client, private_key, provider.id, second_clinician_id, "second-reviewer", ["CLINICIAN"]
    )
    second_clinician_token = make_token(private_key, subject="second-reviewer")

    response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/no-follow-up-required",
        headers=_auth(second_clinician_token),
    )
    assert response.status_code == 200, response.text
    reviewer_ref_id = response.json()["reviewed_by_practitioner_reference_id"]

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert str(order.practitioner_reference_id) != reviewer_ref_id


# -- frozen snapshots unaffected --------------------------------------------------------------


def test_frozen_price_and_catalog_snapshots_unchanged_by_result_recording(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="FrozenSnap"
    )
    with sessions() as session:
        item = session.scalars(
            select(LabOrderItem).where(LabOrderItem.lab_order_id == order_id)
        ).first()
        price_before = item.selected_price_amount
        code_before = item.catalog_code_snapshot

    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item.id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )

    with sessions() as session:
        item_after = session.get(LabOrderItem, item.id)
        assert item_after.selected_price_amount == price_before
        assert item_after.catalog_code_snapshot == code_before


# -- audit atomicity --------------------------------------------------------------------------


def test_result_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    """The `LabResult` container/observation insert and its `lab_observation_recorded` audit row
    must roll back together when the audit insert fails, exactly matching DH-EX-006R's established
    pattern for accept/reject/start-processing. DH-EX-012C: the backward-compatible convenience
    path also writes a `lab_result_entry_completed` event in the same transaction -- since the
    monkeypatched `record` raises on its very first call, that second event never even attempts to
    write, and the whole transaction (container, observation, and completion) rolls back as one
    unit (see `test_release_and_completion_audit_atomicity` below for the release endpoint's own
    two-audit atomicity, where `lab_order_completed` fires)."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ResultAuditFail"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
            headers=_auth(lab_tech_token),
            json={"result_type": "NUMERIC", "numeric_value": "1"},
        )

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"
        assert session.scalar(select(LabResult)) is None  # container insert rolled back too

    assert _audit_events(sessions, "lab_observation_recorded") == []
    assert _audit_events(sessions, "lab_result_entry_completed") == []
    assert _audit_events(sessions, "lab_order_completed") == []


def test_granular_completion_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    """DH-EX-012C: the granular `complete-result-entry` endpoint's `entry_completed_at` write and
    its `lab_result_entry_completed` audit row must roll back together when the audit insert
    fails -- mirrors `test_result_audit_atomicity`'s identical claim for the compat endpoint,
    applied to this new endpoint specifically."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="GranularCompleteF"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    observe_response = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    )
    assert observe_response.status_code == 201, observe_response.text

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(_completion_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token))

    with sessions() as session:
        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item_id))
        assert container.entry_completed_at is None, "completion write must roll back too"
    assert _audit_events(sessions, "lab_result_entry_completed") == []


def test_release_and_completion_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    """DH-EX-012/ADR-044: the release insert, the `LabOrder.status` write to COMPLETED, the
    `lab_results_released` audit row, and the `lab_order_completed` audit row must all roll back
    together when the audit insert fails -- no split-brain state (a release row with the order
    still IN_PROGRESS, or COMPLETED with no release row) for a new DH-EX-012 order."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReleaseAuditFail"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    releaser_id = _bootstrap_identity(client, private_key, "releaser-ReleaseAuditFail")
    _grant_roles(
        client,
        private_key,
        lab.id,
        releaser_id,
        "releaser-ReleaseAuditFail",
        ["LAB_RESULT_RELEASER"],
    )
    releaser_token = make_token(private_key, subject="releaser-ReleaseAuditFail")
    fingerprint = _current_result_fingerprint(client, releaser_token, lab.id, order_id)

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
            headers=_auth(releaser_token),
            json={"expected_result_fingerprint": fingerprint},
        )

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"  # never advanced to COMPLETED
        assert session.scalar(select(LabResultRelease)) is None  # release insert rolled back too
        # DH-EX-014A/ADR-048: the version, manifest, and review-binding are all in the same
        # transaction as the release insert -- none of them may survive either.
        assert session.scalar(select(LabResultReleaseVersion)) is None
        assert session.scalar(select(LabResultReleaseManifestEntry)) is None
        review = session.scalar(select(DoctorReview).where(DoctorReview.lab_order_id == order_id))
        assert review.release_version_id is None

    assert _audit_events(sessions, "lab_results_released") == []
    assert _audit_events(sessions, "lab_order_completed") == []


def test_release_rollback_when_only_completion_audit_fails(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    """Narrower than `test_release_and_completion_audit_atomicity` above: here the FIRST audit
    write (`lab_results_released`) is allowed to succeed and only the SECOND
    (`lab_order_completed`) fails, proving the whole transaction still rolls back completely --
    no split-brain state where a `lab_results_released` audit row survives without its matching
    release row, status write, and `lab_order_completed` row."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="PartialAuditFail"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    releaser_id = _bootstrap_identity(client, private_key, "releaser-PartialAuditFail")
    _grant_roles(
        client,
        private_key,
        lab.id,
        releaser_id,
        "releaser-PartialAuditFail",
        ["LAB_RESULT_RELEASER"],
    )
    releaser_token = make_token(private_key, subject="releaser-PartialAuditFail")
    fingerprint = _current_result_fingerprint(client, releaser_token, lab.id, order_id)

    real_record = ExchangeSecurityAuditRepository.record
    call_count = {"n": 0}

    def _fail_second_call(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("simulated completion-audit failure")
        return real_record(self, *args, **kwargs)

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _fail_second_call)

    with pytest.raises(RuntimeError, match="simulated completion-audit failure"):
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
            headers=_auth(releaser_token),
            json={"expected_result_fingerprint": fingerprint},
        )

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"  # never advanced to COMPLETED
        assert session.scalar(select(LabResultRelease)) is None  # release insert rolled back too
    assert _audit_events(sessions, "lab_results_released") == []
    assert _audit_events(sessions, "lab_order_completed") == []


# -- authorization: release endpoint --------------------------------------------------------------


def test_lab_tech_alone_cannot_release_results(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="TechAloneRelease"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(lab_tech_token),
        json={"expected_result_fingerprint": "irrelevant-role-check-fails-first"},
    )
    assert response.status_code == 403, response.text
    with sessions() as session:
        assert session.scalar(select(LabResultRelease)) is None


def test_org_admin_alone_cannot_release_results(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="AdminAloneRelease"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    org_admin_id = _bootstrap_identity(client, private_key, "org-admin-release")
    _grant_roles(client, private_key, lab.id, org_admin_id, "org-admin-release", ["ORG_ADMIN"])
    org_admin_token = make_token(private_key, subject="org-admin-release")

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(org_admin_token),
        json={"expected_result_fingerprint": "irrelevant-role-check-fails-first"},
    )
    assert response.status_code == 403, response.text


def test_platform_admin_alone_cannot_release_results(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="PlatformAloneRelease",
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    admin_token = make_token(
        private_key, subject="platform-admin-release", roles=[PLATFORM_ADMIN_ROLE]
    )

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(admin_token),
        json={"expected_result_fingerprint": "irrelevant-role-check-fails-first"},
    )
    assert response.status_code == 403, response.text


def test_clinician_cannot_release_results(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ClinicianRelease"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(clinician_token),
        json={"expected_result_fingerprint": "irrelevant-role-check-fails-first"},
    )
    assert response.status_code == 404, response.text  # clinician has zero relationship to lab.id


def test_cross_lab_releaser_cannot_release_results_and_is_non_enumerating(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CrossLabRelease"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    impostor_lab, _impostor_token, _id = _setup_lab_tech(
        client, private_key, organizations, "impostor-releaser"
    )
    impostor_releaser_id = _bootstrap_identity(client, private_key, "impostor-releaser-role")
    _grant_roles(
        client,
        private_key,
        impostor_lab.id,
        impostor_releaser_id,
        "impostor-releaser-role",
        ["LAB_RESULT_RELEASER"],
    )
    impostor_releaser_token = make_token(private_key, subject="impostor-releaser-role")

    real_response = client.post(
        f"/exchange/v1/organizations/{impostor_lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(impostor_releaser_token),
        json={"expected_result_fingerprint": "irrelevant-tenant-check-fails-first"},
    )
    fake_response = client.post(
        f"/exchange/v1/organizations/{impostor_lab.id}/lab-orders/{uuid.uuid4()}/release-results",
        headers=_auth(impostor_releaser_token),
        json={"expected_result_fingerprint": "irrelevant-tenant-check-fails-first"},
    )
    assert real_response.status_code == 404
    assert fake_response.status_code == 404
    assert real_response.json() == fake_response.json()
    with sessions() as session:
        assert session.scalar(select(LabResultRelease)) is None


def test_releaser_alone_cannot_submit_result(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """`LAB_RESULT_RELEASER` grants no result-*entry* authority -- that remains `LAB_TECH`'s
    alone, symmetric to `test_lab_tech_alone_cannot_release_results` above."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReleaserAloneEnter"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    releaser_id = _bootstrap_identity(client, private_key, "releaser-alone-enter")
    _grant_roles(
        client, private_key, lab.id, releaser_id, "releaser-alone-enter", ["LAB_RESULT_RELEASER"]
    )
    releaser_token = make_token(private_key, subject="releaser-alone-enter")

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(releaser_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert response.status_code == 403, response.text
    with sessions() as session:
        assert session.scalar(select(LabResult)) is None


# -- authorization: lab-side inspection endpoint ----------------------------------------------


def test_releaser_alone_can_inspect_results(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """A `LAB_RESULT_RELEASER` with no `LAB_TECH` role must still be able to inspect what they are
    about to release."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReleaserInspect"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    releaser_id = _bootstrap_identity(client, private_key, "releaser-inspect-only")
    _grant_roles(
        client, private_key, lab.id, releaser_id, "releaser-inspect-only", ["LAB_RESULT_RELEASER"]
    )
    releaser_token = make_token(private_key, subject="releaser-inspect-only")

    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/results",
        headers=_auth(releaser_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["all_items_entry_complete"] is True


def test_org_admin_alone_cannot_inspect_results(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="AdminInspect"
    )
    org_admin_id = _bootstrap_identity(client, private_key, "org-admin-inspect")
    _grant_roles(client, private_key, lab.id, org_admin_id, "org-admin-inspect", ["ORG_ADMIN"])
    org_admin_token = make_token(private_key, subject="org-admin-inspect")

    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/results",
        headers=_auth(org_admin_token),
    )
    assert response.status_code == 403, response.text


def test_platform_admin_alone_cannot_inspect_results(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="PlatformInspect"
    )
    admin_token = make_token(
        private_key, subject="platform-admin-inspect", roles=[PLATFORM_ADMIN_ROLE]
    )

    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/results",
        headers=_auth(admin_token),
    )
    assert response.status_code == 403, response.text


def test_clinician_cannot_use_lab_inspection_endpoint(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, clinician_token, lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ClinicianInspect"
    )

    response = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/results",
        headers=_auth(clinician_token),
    )
    assert response.status_code == 404, response.text  # zero relationship to lab.id


def test_cross_lab_cannot_inspect_results_and_is_non_enumerating(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, _lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CrossLabInspect"
    )
    impostor_lab, impostor_token, _id = _setup_lab_tech(
        client, private_key, organizations, "impostor-inspect"
    )

    real_response = client.get(
        f"/exchange/v1/organizations/{impostor_lab.id}/lab-orders/{order_id}/results",
        headers=_auth(impostor_token),
    )
    fake_response = client.get(
        f"/exchange/v1/organizations/{impostor_lab.id}/lab-orders/{uuid.uuid4()}/results",
        headers=_auth(impostor_token),
    )
    assert real_response.status_code == 404
    assert fake_response.status_code == 404
    assert real_response.json() == fake_response.json()


# -- dual-role, incomplete release, and legacy-order handling ---------------------------------


def test_dual_role_identity_may_enter_and_release_the_same_order(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """ADR-044's central permission: the SAME human may hold both `LAB_TECH` and
    `LAB_RESULT_RELEASER` and use both on the same order -- no forced second person. This is the
    one path negative control 4 ("accidentally add a second-person restriction") must break."""
    private_key, _ = keypair
    provider, clinician_token, lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="DualRole"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    dual_identity_id = _bootstrap_identity(client, private_key, "dual-role-human")
    _grant_roles(
        client,
        private_key,
        lab.id,
        dual_identity_id,
        "dual-role-human",
        ["LAB_TECH", "LAB_RESULT_RELEASER"],
    )
    dual_token = make_token(private_key, subject="dual-role-human")

    submit = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(dual_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert submit.status_code == 201, submit.text

    fingerprint = _current_result_fingerprint(client, dual_token, lab.id, order_id)
    release = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(dual_token),
        json={"expected_result_fingerprint": fingerprint},
    )
    assert release.status_code == 201, release.text
    assert release.json()["released_by_identity_id"] == dual_identity_id

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "COMPLETED"

    results_response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    )
    assert results_response.status_code == 200, results_response.text


def test_release_refused_when_results_incomplete(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """A two-item order with only one result entered must refuse release with a controlled
    conflict, never silently completing a half-resulted order."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="IncompleteRelease",
        test_count=2,
    )
    item_ids = _order_item_ids(sessions, order_id)
    submit = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_ids[0]}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert submit.json()["ready_for_release"] is False

    releaser_id = _bootstrap_identity(client, private_key, "releaser-incomplete")
    _grant_roles(
        client, private_key, lab.id, releaser_id, "releaser-incomplete", ["LAB_RESULT_RELEASER"]
    )
    releaser_token = make_token(private_key, subject="releaser-incomplete")

    response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": "irrelevant-incompleteness-check-fails-first"},
    )
    assert response.status_code == 409, response.text
    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"
        assert session.scalar(select(LabResultRelease)) is None


def test_release_on_legacy_completed_order_is_refused_not_fabricated(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """A pre-DH-EX-012 order that reached COMPLETED via the old automatic-completion behavior has
    no `LabResultRelease` row -- manufactured here with a raw status write (there is no longer any
    live code path that can produce this shape). ADR-044's grandfather rule: release must refuse
    with a distinguishable conflict rather than fabricating a release record, while provider read
    and clinician review remain completely unaffected."""
    private_key, _ = keypair
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="LegacyCompleted"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    with sessions() as session:
        order = session.get(LabOrder, order_id)
        order.status = "COMPLETED"  # simulates a pre-DH-EX-012 auto-completed order
        session.commit()

    releaser_id = _bootstrap_identity(client, private_key, "releaser-legacy")
    _grant_roles(
        client, private_key, lab.id, releaser_id, "releaser-legacy", ["LAB_RESULT_RELEASER"]
    )
    releaser_token = make_token(private_key, subject="releaser-legacy")

    release_attempt = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": "irrelevant-legacy-check-fails-first"},
    )
    assert release_attempt.status_code == 409, release_attempt.text
    with sessions() as session:
        assert session.scalar(select(LabResultRelease)) is None  # never fabricated

    # Provider visibility and clinician review are unaffected -- both gate on LabOrder.status
    # alone, which this legacy order already satisfies.
    results_response = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    )
    assert results_response.status_code == 200, results_response.text
    # DH-EX-014A: a grandfathered order has no release version -- all three version-metadata
    # fields stay None together, and reads the unchanged legacy live-effective-observation path.
    results_body = results_response.json()
    assert results_body["release_version_id"] is None
    assert results_body["release_version_number"] is None
    assert results_body["is_amendment"] is None
    assert len(results_body["results"]) == 1

    review_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/no-follow-up-required",
        headers=_auth(clinician_token),
    )
    assert review_response.status_code == 200, review_response.text


# -- idempotent release replay ------------------------------------------------------------------


def test_release_replay_by_same_releaser_returns_identical_provenance(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReplaySameReleaser"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    releaser_id = _bootstrap_identity(client, private_key, "releaser-ReplaySameReleaser")
    _grant_roles(
        client,
        private_key,
        lab.id,
        releaser_id,
        "releaser-ReplaySameReleaser",
        ["LAB_RESULT_RELEASER"],
    )
    releaser_token = make_token(private_key, subject="releaser-ReplaySameReleaser")
    fingerprint = _current_result_fingerprint(client, releaser_token, lab.id, order_id)

    first = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": fingerprint},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": "irrelevant-replay-returns-first-committer-fact"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["released_at"].rstrip("Z") == first.json()["released_at"].rstrip("Z")
    assert second.json()["released_by_identity_id"] == first.json()["released_by_identity_id"]
    with sessions() as session:
        from sqlalchemy import func

        count = session.scalar(select(func.count()).select_from(LabResultRelease))
    assert count == 1


def test_release_replay_by_a_different_releaser_never_rewrites_provenance(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """A *different*, equally-authorized `LAB_RESULT_RELEASER` retrying the same order must see the
    first committer's provenance unchanged -- the fact recorded is who actually released it first,
    never rewritten by whoever asks most recently."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReplayDiffReleaser"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    first_body = _release_results(client, private_key, lab.id, order_id, "ReplayDiffReleaserFirst")

    second_releaser_id = _bootstrap_identity(client, private_key, "releaser-ReplayDiffSecond")
    _grant_roles(
        client,
        private_key,
        lab.id,
        second_releaser_id,
        "releaser-ReplayDiffSecond",
        ["LAB_RESULT_RELEASER"],
    )
    second_releaser_token = make_token(private_key, subject="releaser-ReplayDiffSecond")

    second_response = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(second_releaser_token),
        json={"expected_result_fingerprint": "irrelevant-replay-returns-first-committer-fact"},
    )
    assert second_response.status_code == 200, second_response.text
    assert (
        second_response.json()["released_by_identity_id"] == first_body["released_by_identity_id"]
    )
    assert second_response.json()["released_by_identity_id"] != second_releaser_id
    assert second_response.json()["released_at"].rstrip("Z") == first_body["released_at"].rstrip(
        "Z"
    )


def test_doctor_review_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    private_key, _ = keypair
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReviewAuditFail"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    # DH-EX-012/ADR-044: review requires COMPLETED, which requires an explicit release -- done
    # here, before the audit repository is monkeypatched below, so the release itself succeeds.
    _release_results(client, private_key, lab.id, order_id, "ReviewAuditFail")

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/no-follow-up-required",
            headers=_auth(clinician_token),
        )

    with sessions() as session:
        review = session.scalar(select(DoctorReview).where(DoctorReview.lab_order_id == order_id))
        assert review.status == "PENDING"
        assert review.reviewed_at is None
        assert review.reviewed_by_practitioner_reference_id is None
    assert _audit_events(sessions, "doctor_review_recorded") == []


# -- DH-EX-012C/ADR-047: granular multi-observation entry / explicit completion ----------------


def _observation_url(lab_id, order_id, item_id) -> str:
    return f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}/observations"


def _completion_url(lab_id, order_id, item_id) -> str:
    return (
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}"
        f"/complete-result-entry"
    )


def test_multi_observation_golden_flow_release_and_provider_read(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="MultiObs"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    first = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "WBC",
            "display_label": "White blood cell count",
            "numeric_value": "6.2",
            "numeric_unit": "10^9/L",
        },
    )
    assert first.status_code == 201, first.text
    assert first.json()["source_system"] == "dhealth-exchange"

    second = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "TEXT",
            "source_code": "MORPH",
            "display_label": "Morphology",
            "text_value": "Normal",
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["result_id"] == first.json()["result_id"]

    # Not yet complete -- release must refuse.
    premature_release = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(_bootstrap_and_grant_releaser(client, private_key, lab.id, "MultiObs")),
        json={"expected_result_fingerprint": "irrelevant-incompleteness-check-fails-first"},
    )
    assert premature_release.status_code == 409, premature_release.text

    inspection = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/results",
        headers=_auth(lab_tech_token),
    ).json()
    assert inspection["all_items_entry_complete"] is False
    assert len(inspection["results"][0]["observations"]) == 2
    assert inspection["results"][0]["entry_completed"] is False

    complete = client.post(
        _completion_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token)
    )
    assert complete.status_code == 201, complete.text
    assert complete.json()["entry_completed_by_identity_id"] is not None

    release_body = _release_results(client, private_key, lab.id, order_id, "MultiObs-2")
    assert release_body["released_at"] is not None

    provider_body = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    ).json()
    observations = provider_body["results"][0]["observations"]
    assert {o["source_code"] for o in observations} == {"WBC", "MORPH"}


def _bootstrap_and_grant_releaser(client, private_key, lab_id, name: str) -> str:
    releaser_id = _bootstrap_identity(client, private_key, f"releaser-{name}")
    _grant_roles(
        client, private_key, lab_id, releaser_id, f"releaser-{name}", ["LAB_RESULT_RELEASER"]
    )
    return make_token(private_key, subject=f"releaser-{name}")


def test_completion_rejected_with_zero_observations_via_api(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ZeroObsApi"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    response = client.post(
        _completion_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token)
    )
    assert response.status_code == 409, response.text

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"


def test_observation_after_completion_refused_via_api(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="FreezeApi"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    )
    complete = client.post(
        _completion_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token)
    )
    assert complete.status_code == 201, complete.text

    late = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "K",
            "display_label": "Potassium",
            "numeric_value": "4.0",
        },
    )
    assert late.status_code == 409, late.text


def test_completion_replay_is_idempotent_via_api(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CompleteReplay"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "GLU",
            "display_label": "Glucose",
            "numeric_value": "5.1",
        },
    )

    first = client.post(_completion_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token))
    assert first.status_code == 201, first.text

    replay = client.post(_completion_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token))
    assert replay.status_code == 200, replay.text
    assert replay.json()["entry_completed_at"].rstrip("Z") == first.json()[
        "entry_completed_at"
    ].rstrip("Z")
    assert (
        replay.json()["entry_completed_by_identity_id"]
        == first.json()["entry_completed_by_identity_id"]
    )

    entry_completed_events = _audit_events(sessions, "lab_result_entry_completed")
    assert len(entry_completed_events) == 1


def test_clinician_cannot_record_observation_or_complete_entry(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, clinician_token, lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ClinicianObs"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    # Anti-enumeration: the clinician has zero relationship to lab.id, matching
    # `test_provider_clinician_cannot_submit_lab_result`'s identical 404 (not 403).
    observation_response = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(clinician_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    )
    assert observation_response.status_code == 404, observation_response.text

    completion_response = client.post(
        _completion_url(lab.id, order_id, item_id), headers=_auth(clinician_token)
    )
    assert completion_response.status_code == 404, completion_response.text


def test_releaser_alone_cannot_complete_entry(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Entry completion is LAB_TECH authority, deliberately not LAB_RESULT_RELEASER -- entry and
    release stay separate authorities at the item level, mirroring ADR-044's order-level split."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="ReleaserComplete"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    )
    releaser_token = _bootstrap_and_grant_releaser(client, private_key, lab.id, "ReleaserComplete")

    response = client.post(
        _completion_url(lab.id, order_id, item_id), headers=_auth(releaser_token)
    )
    assert response.status_code == 403, response.text


def test_wrong_lab_cannot_record_observation_or_complete_entry_and_is_non_enumerating(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Mirrors `test_wrong_lab_cannot_submit_result_and_is_non_enumerating`: a real LAB_TECH of a
    genuinely different lab, calling with their *own* lab id, is refused identically (404, never
    disclosing whether the order exists) whether the order id is real or fabricated."""
    private_key, _ = keypair
    _provider, _clinician_token, _lab, _lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="WrongLabObs"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    impostor_lab, impostor_token, _id = _setup_lab_tech(
        client, private_key, organizations, "impostor-observation"
    )

    real_response = client.post(
        _observation_url(impostor_lab.id, order_id, item_id),
        headers=_auth(impostor_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    )
    fake_response = client.post(
        _observation_url(impostor_lab.id, uuid.uuid4(), item_id),
        headers=_auth(impostor_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    )
    assert real_response.status_code == 404
    assert fake_response.status_code == 404
    assert real_response.json() == fake_response.json()

    completion_response = client.post(
        _completion_url(impostor_lab.id, order_id, item_id), headers=_auth(impostor_token)
    )
    assert completion_response.status_code == 404, completion_response.text


# -- DH-EX-013/ADR-045: pre-release observation correction -------------------------------------


def _correction_url(lab_id, order_id, item_id, observation_id) -> str:
    return (
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}"
        f"/observations/{observation_id}/correct"
    )


def _history_url(lab_id, order_id, item_id) -> str:
    return (
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_id}"
        f"/observations/history"
    )


def _release_versions_url(lab_id, order_id) -> str:
    return f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/release-versions"


def test_correction_golden_flow_history_and_release(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CorrectGolden"
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    original = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "GLU",
            "display_label": "Glucose",
            "numeric_value": "5.1",
        },
    )
    assert original.status_code == 201, original.text
    original_id = original.json()["observation_id"]

    corrected = client.post(
        _correction_url(lab.id, order_id, item_id, original_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "GLU",
            "display_label": "Glucose",
            "numeric_value": "5.9",
        },
    )
    assert corrected.status_code == 201, corrected.text
    corrected_body = corrected.json()
    assert corrected_body["supersedes_observation_id"] == original_id
    assert corrected_body["superseded_at"] is None
    assert Decimal(corrected_body["numeric_value"]) == Decimal("5.9")

    # Lab inspection shows only the effective (corrected) value, with a history indicator.
    inspection = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/results",
        headers=_auth(lab_tech_token),
    ).json()
    item_line = inspection["results"][0]
    assert item_line["has_correction_history"] is True
    assert len(item_line["observations"]) == 1
    assert Decimal(item_line["observations"][0]["numeric_value"]) == Decimal("5.9")

    # History endpoint reconstructs both versions.
    history = client.get(_history_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token))
    assert history.status_code == 200, history.text
    history_observations = history.json()["observations"]
    assert [Decimal(o["numeric_value"]) for o in history_observations] == [
        Decimal("5.1"),
        Decimal("5.9"),
    ]
    assert history_observations[0]["superseded_at"] is not None
    assert history_observations[1]["superseded_at"] is None

    complete = client.post(
        _completion_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token)
    )
    assert complete.status_code == 201, complete.text
    release_body = _release_results(client, private_key, lab.id, order_id, "CorrectGolden")
    assert release_body["released_at"] is not None

    provider_body = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    ).json()
    provider_observations = provider_body["results"][0]["observations"]
    assert len(provider_observations) == 1
    assert Decimal(provider_observations[0]["numeric_value"]) == Decimal("5.9")


def test_correction_after_entry_completion_before_release_works(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CorrectPostComplete"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    original = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "CA",
            "display_label": "Calcium",
            "numeric_value": "9.5",
        },
    ).json()
    complete = client.post(
        _completion_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token)
    )
    assert complete.status_code == 201, complete.text
    entry_completed_at = complete.json()["entry_completed_at"]

    corrected = client.post(
        _correction_url(lab.id, order_id, item_id, original["observation_id"]),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "CA",
            "display_label": "Calcium",
            "numeric_value": "9.8",
        },
    )
    assert corrected.status_code == 201, corrected.text

    inspection = client.get(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/results",
        headers=_auth(lab_tech_token),
    ).json()
    assert inspection["results"][0]["entry_completed"] is True
    assert inspection["results"][0]["entry_completed_at"].rstrip("Z") == entry_completed_at.rstrip(
        "Z"
    )


def test_correction_after_release_refused(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CorrectPostRelease"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    original = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "CL",
            "display_label": "Chloride",
            "numeric_value": "100",
        },
    ).json()
    client.post(_completion_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token))
    _release_results(client, private_key, lab.id, order_id, "CorrectPostRelease")

    response = client.post(
        _correction_url(lab.id, order_id, item_id, original["observation_id"]),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "CL",
            "display_label": "Chloride",
            "numeric_value": "101",
        },
    )
    assert response.status_code == 409, response.text


def test_correcting_stale_observation_refused_via_api(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CorrectStale"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    original = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    ).json()
    first_correction = client.post(
        _correction_url(lab.id, order_id, item_id, original["observation_id"]),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "141",
        },
    )
    assert first_correction.status_code == 201, first_correction.text

    stale_correction = client.post(
        _correction_url(lab.id, order_id, item_id, original["observation_id"]),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "999",
        },
    )
    assert stale_correction.status_code == 409, stale_correction.text


def test_clinician_cannot_correct_or_view_history(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CorrectClinician"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    original = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    ).json()

    correction_response = client.post(
        _correction_url(lab.id, order_id, item_id, original["observation_id"]),
        headers=_auth(clinician_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "141",
        },
    )
    assert correction_response.status_code == 404, correction_response.text

    history_response = client.get(
        _history_url(lab.id, order_id, item_id), headers=_auth(clinician_token)
    )
    assert history_response.status_code == 404, history_response.text


def test_releaser_alone_cannot_correct(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """LAB_TECH is entry authority (ADR-045's Decision 2) -- a LAB_RESULT_RELEASER with no
    LAB_TECH role may inspect and view history, but not correct."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CorrectReleaser"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    original = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    ).json()
    releaser_token = _bootstrap_and_grant_releaser(client, private_key, lab.id, "CorrectReleaser")

    correction_response = client.post(
        _correction_url(lab.id, order_id, item_id, original["observation_id"]),
        headers=_auth(releaser_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "141",
        },
    )
    assert correction_response.status_code == 403, correction_response.text

    # But the releaser CAN view history, matching the ordinary pre-release inspection auth.
    history_response = client.get(
        _history_url(lab.id, order_id, item_id), headers=_auth(releaser_token)
    )
    assert history_response.status_code == 200, history_response.text


def test_wrong_lab_cannot_correct_or_view_history_and_is_non_enumerating(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CorrectWrongLab"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    original = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    ).json()
    impostor_lab, impostor_token, _id = _setup_lab_tech(
        client, private_key, organizations, "impostor-correction"
    )

    correction_response = client.post(
        _correction_url(impostor_lab.id, order_id, item_id, original["observation_id"]),
        headers=_auth(impostor_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "141",
        },
    )
    assert correction_response.status_code == 404, correction_response.text

    history_response = client.get(
        _history_url(impostor_lab.id, order_id, item_id), headers=_auth(impostor_token)
    )
    assert history_response.status_code == 404, history_response.text


def test_correction_audit_atomicity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions, monkeypatch
) -> None:
    """The new observation insert, the predecessor's `superseded_at` write, and the
    `lab_observation_corrected` audit row must roll back together when the audit insert fails."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CorrectAuditFail"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    original = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    ).json()

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            _correction_url(lab.id, order_id, item_id, original["observation_id"]),
            headers=_auth(lab_tech_token),
            json={
                "result_type": "NUMERIC",
                "source_code": "NA",
                "display_label": "Sodium",
                "numeric_value": "141",
            },
        )

    with sessions() as session:
        original_row = session.get(LabObservation, uuid.UUID(original["observation_id"]))
        assert original_row.superseded_at is None
        observation_count = session.scalar(
            select(func.count())
            .select_from(LabObservation)
            .where(LabObservation.lab_result_id == original_row.lab_result_id)
        )
        assert observation_count == 1, "no successor observation may have been left behind"
    assert _audit_events(sessions, "lab_observation_corrected") == []


def test_patient_bearer_never_sees_correction_or_history(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Regression: correction/history must never leak through the patient bearer-access surface,
    which excludes every result field by construction regardless of correction activity."""
    private_key, _ = keypair
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="CorrectPatient"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    original = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "140",
        },
    ).json()
    client.post(
        _correction_url(lab.id, order_id, item_id, original["observation_id"]),
        headers=_auth(lab_tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "NA",
            "display_label": "Sodium",
            "numeric_value": "141",
        },
    )
    client.post(_completion_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token))
    _release_results(client, private_key, lab.id, order_id, "CorrectPatient")

    # `_create_in_progress_order` already issued and consumed one patient-access grant to drive
    # the patient-selection step -- revoke it before issuing a fresh one for this assertion.
    revoke_response = client.delete(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    assert revoke_response.status_code == 204, revoke_response.text

    access_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/patient-access",
        headers=_auth(clinician_token),
    )
    assert access_response.status_code == 201, access_response.text
    access_token = access_response.json()["access_token"]

    patient_body = client.get("/exchange/v1/patient/order", headers=_auth(access_token)).json()
    body_text = str(patient_body)
    assert "140" not in body_text
    assert "141" not in body_text
    assert "numeric_value" not in patient_body
    assert "observations" not in patient_body
    # DH-EX-014A/ADR-048: the release-version manifest must never become reachable through the
    # patient bearer surface either -- this order has a real release version by this point.
    assert "release_version" not in patient_body
    assert "manifest" not in body_text


# -- DH-EX-014A/ADR-048: release-version manifest + versioned DoctorReview API surface -----------


def test_lab_tech_and_releaser_can_read_release_version_history(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="VersionHistory"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    _release_results(client, private_key, lab.id, order_id, "VersionHistory")

    lab_tech_response = client.get(
        _release_versions_url(lab.id, order_id), headers=_auth(lab_tech_token)
    )
    assert lab_tech_response.status_code == 200, lab_tech_response.text
    body = lab_tech_response.json()
    assert body["order_id"] == str(order_id)
    assert len(body["versions"]) == 1
    assert body["versions"][0]["version_number"] == 1
    assert body["versions"][0]["is_amendment"] is False
    assert body["versions"][0]["lab_result_release_id"] is not None

    releaser_id = _bootstrap_identity(client, private_key, "releaser-versionhistory-reader")
    _grant_roles(
        client,
        private_key,
        lab.id,
        releaser_id,
        "releaser-versionhistory-reader",
        ["LAB_RESULT_RELEASER"],
    )
    releaser_token = make_token(private_key, subject="releaser-versionhistory-reader")
    releaser_response = client.get(
        _release_versions_url(lab.id, order_id), headers=_auth(releaser_token)
    )
    assert releaser_response.status_code == 200, releaser_response.text


def test_clinician_cannot_read_lab_release_version_history(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="VersionHistoryAuth"
    )
    response = client.get(_release_versions_url(lab.id, order_id), headers=_auth(clinician_token))
    assert response.status_code == 404, response.text  # clinician has zero relationship to lab.id


def test_cross_lab_release_version_history_is_non_enumerating(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="VersionHistoryCross"
    )
    impostor_lab, impostor_token, _id = _setup_lab_tech(
        client, private_key, organizations, "impostor-versionhistory"
    )
    real_response = client.get(
        _release_versions_url(impostor_lab.id, order_id), headers=_auth(impostor_token)
    )
    fake_response = client.get(
        _release_versions_url(impostor_lab.id, uuid.uuid4()), headers=_auth(impostor_token)
    )
    assert real_response.status_code == 404
    assert fake_response.status_code == 404
    assert real_response.json() == fake_response.json()


def test_release_version_history_for_grandfathered_order_is_empty(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """A grandfathered COMPLETED-without-LabResultRelease order has no release version at all --
    the history endpoint must return an empty list, never a fabricated one."""
    private_key, _ = keypair
    _provider, _clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="VersionHistoryGF"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    with sessions() as session:
        order = session.get(LabOrder, order_id)
        order.status = "COMPLETED"
        session.commit()

    response = client.get(_release_versions_url(lab.id, order_id), headers=_auth(lab_tech_token))
    assert response.status_code == 200, response.text
    assert response.json()["versions"] == []


# -- DH-EX-014B/ADR-048: post-release amendment API ----------------------------------------------


def _amendment_url(lab_id, order_id, amendment_id=None) -> str:
    base = f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/amendments"
    return base if amendment_id is None else f"{base}/{amendment_id}"


def _amendment_releaser_token(client, private_key, lab_id, name: str) -> str:
    identity_id = _bootstrap_identity(client, private_key, f"amend-releaser-{name}")
    _grant_roles(
        client,
        private_key,
        lab_id,
        identity_id,
        f"amend-releaser-{name}",
        ["LAB_RESULT_RELEASER"],
    )
    return make_token(private_key, subject=f"amend-releaser-{name}")


def _current_amendment_fingerprint(
    client: TestClient, token: str, lab_id, order_id, amendment_id
) -> str:
    """DH-LAB-005 §37-42: fetches the exact ``result_fingerprint`` an amendment release request
    must echo back -- the inspection step every releaser is now required to actually perform."""
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


def test_amendment_golden_flow_provider_history_and_explicit_review(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token, lab, tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="AmendGolden",
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    original = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "SYN-OBS",
            "display_label": "Synthetic observation",
            "numeric_value": "1",
        },
    )
    assert original.status_code == 201, original.text
    assert (
        client.post(
            _completion_url(lab.id, order_id, item_id), headers=_auth(tech_token)
        ).status_code
        == 201
    )
    _release_results(client, private_key, lab.id, order_id, "AmendGolden-v1")
    version_one = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    ).json()["release_version_id"]

    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech_token))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    corrected = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/items/{item_id}/observations/"
        f"{original.json()['observation_id']}/correct",
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "SYN-OBS",
            "display_label": "Synthetic observation",
            "numeric_value": "2",
        },
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["supersedes_observation_id"] == original.json()["observation_id"]

    releaser_token = _amendment_releaser_token(client, private_key, lab.id, "AmendGolden-v2")
    fingerprint = _current_amendment_fingerprint(
        client, releaser_token, lab.id, order_id, amendment_id
    )
    released = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/release",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": fingerprint},
    )
    assert released.status_code == 201, released.text
    version_two = released.json()["release_version_id"]
    assert released.json()["version_number"] == 2
    assert released.json()["is_amendment"] is True

    replay = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/release",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": "irrelevant-replay-returns-first-committer-fact"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == released.json()
    assert len(_audit_events(sessions, "lab_result_amendment_released")) == 1

    latest = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    )
    assert latest.status_code == 200, latest.text
    assert latest.json()["release_version_id"] == version_two
    assert latest.json()["release_version_number"] == 2
    assert Decimal(latest.json()["results"][0]["observations"][0]["numeric_value"]) == Decimal("2")

    history_url = f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/release-versions"
    history = client.get(history_url, headers=_auth(clinician_token))
    assert history.status_code == 200, history.text
    assert [version["release_version_number"] for version in history.json()["versions"]] == [1, 2]
    assert [version["review_status"] for version in history.json()["versions"]] == [
        "PENDING",
        "PENDING",
    ]

    version_one_content = client.get(f"{history_url}/{version_one}", headers=_auth(clinician_token))
    assert version_one_content.status_code == 200, version_one_content.text
    assert Decimal(
        version_one_content.json()["results"][0]["observations"][0]["numeric_value"]
    ) == Decimal("1")
    version_two_content = client.get(f"{history_url}/{version_two}", headers=_auth(clinician_token))
    assert Decimal(
        version_two_content.json()["results"][0]["observations"][0]["numeric_value"]
    ) == Decimal("2")

    legacy_review = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/no-follow-up-required",
        headers=_auth(clinician_token),
    )
    assert legacy_review.status_code == 409, legacy_review.text
    version_one_review = client.post(
        f"{history_url}/{version_one}/review/no-follow-up-required",
        headers=_auth(clinician_token),
    )
    assert version_one_review.status_code == 200, version_one_review.text
    assert version_one_review.json()["release_version_id"] == version_one
    assert (
        client.get(f"{history_url}/{version_two}", headers=_auth(clinician_token)).json()[
            "review_status"
        ]
        == "PENDING"
    )


def test_amendment_and_version_routes_preserve_role_and_tenant_boundaries(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token, lab, tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="AmendBoundary",
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    result = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(tech_token),
        json={"result_type": "TEXT", "text_value": "Synthetic result"},
    )
    assert result.status_code == 201, result.text
    _release_results(client, private_key, lab.id, order_id, "AmendBoundary-v1")

    releaser_token = _amendment_releaser_token(client, private_key, lab.id, "AmendBoundaryReleaser")
    releaser_start = client.post(_amendment_url(lab.id, order_id), headers=_auth(releaser_token))
    assert releaser_start.status_code == 403

    clinician_start = client.post(_amendment_url(lab.id, order_id), headers=_auth(clinician_token))
    assert clinician_start.status_code == 404
    other_lab, other_tech_token, _identity = _setup_lab_tech(
        client, private_key, organizations, "AmendBoundaryOther"
    )
    cross_lab = client.post(_amendment_url(other_lab.id, order_id), headers=_auth(other_tech_token))
    assert cross_lab.status_code == 404

    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech_token))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    second_start = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech_token))
    assert second_start.status_code == 409

    # Releaser alone cannot correct observation (requires LAB_TECH)
    releaser_correct = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/items/{item_id}/observations/"
        f"{result.json()['observation_id']}/correct",
        headers=_auth(releaser_token),
        json={
            "result_type": "TEXT",
            "source_code": "SYN-OBS",
            "display_label": "Synthetic observation",
            "text_value": "Attempted by releaser",
        },
    )
    assert releaser_correct.status_code == 403

    # Tech alone cannot release amendment (requires LAB_RESULT_RELEASER)
    tech_release = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/release",
        headers=_auth(tech_token),
        json={"expected_result_fingerprint": "irrelevant-role-check-fails-first"},
    )
    assert tech_release.status_code == 403

    other_provider, other_clinician = _setup_clinician(
        client, private_key, organizations, "AmendBoundaryOtherProvider"
    )
    own_history = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/release-versions",
        headers=_auth(clinician_token),
    )
    version_id = own_history.json()["versions"][0]["release_version_id"]
    cross_provider = client.get(
        f"/exchange/v1/organizations/{other_provider.id}/orders/{order_id}/release-versions/"
        f"{version_id}",
        headers=_auth(other_clinician),
    )
    assert cross_provider.status_code == 404


def test_provider_release_version_history_query_count_does_not_scale_with_version_count(
    client: TestClient,
    keypair,
    organizations,
    catalog,
    directory,
    patients,
    sessions: sessionmaker[Session],
    engine: Engine,
) -> None:
    """DH-EX-014B: GET .../release-versions query count must be constant (O(1) in release versions).

    A single joined query retrieves release versions and doctor reviews together, replacing
    the per-version DoctorReview lookup loop.
    """
    private_key, _ = keypair
    provider, clinician_token, lab, tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="AmendHistQC",
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    orig_res = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "SYN-OBS",
            "display_label": "Synthetic observation",
            "numeric_value": "10",
        },
    )
    assert orig_res.status_code == 201
    assert (
        client.post(
            _completion_url(lab.id, order_id, item_id), headers=_auth(tech_token)
        ).status_code
        == 201
    )
    _release_results(client, private_key, lab.id, order_id, "AmendHistQC-v1")

    history_url = f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/release-versions"

    def _measure_history_statements() -> tuple[int, int]:
        statements: list[str] = []

        def _record(conn, cursor, statement, *args) -> None:  # noqa: ARG001
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            resp = client.get(history_url, headers=_auth(clinician_token))
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        assert resp.status_code == 200, resp.text
        selects = [s for s in statements if s.strip().startswith("SELECT")]
        version_selects = [s for s in statements if "exchange_release_versions" in s]
        return len(selects), len(version_selects), len(statements)

    selects_1, ver_selects_1, total_1 = _measure_history_statements()

    releaser_token = _amendment_releaser_token(client, private_key, lab.id, "AmendHistQCReleaser")
    current_obs_id = orig_res.json()["observation_id"]

    # Create 3 amendments (so total versions = 4)
    for v in range(2, 5):
        started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech_token))
        assert started.status_code == 201, started.text
        amend_id = started.json()["amendment_id"]
        corrected = client.post(
            f"{_amendment_url(lab.id, order_id, amend_id)}/items/{item_id}/observations/"
            f"{current_obs_id}/correct",
            headers=_auth(tech_token),
            json={
                "result_type": "NUMERIC",
                "source_code": "SYN-OBS",
                "display_label": f"Correction v{v}",
                "numeric_value": str(v * 10),
            },
        )
        assert corrected.status_code == 201, corrected.text
        current_obs_id = corrected.json()["observation_id"]
        amend_fingerprint = _current_amendment_fingerprint(
            client, releaser_token, lab.id, order_id, amend_id
        )
        rel = client.post(
            f"{_amendment_url(lab.id, order_id, amend_id)}/release",
            headers=_auth(releaser_token),
            json={"expected_result_fingerprint": amend_fingerprint},
        )
        assert rel.status_code == 201, rel.text

    selects_4, ver_selects_4, total_4 = _measure_history_statements()

    # The single joined query means release-version history query count is constant:
    assert ver_selects_1 == 1
    assert ver_selects_4 == 1
    # Total endpoint queries do not scale with version count:
    assert selects_1 == 7
    assert selects_4 == 7
    assert selects_4 == selects_1
    assert total_4 == total_1 == 15


def test_amendment_release_route_fails_closed_on_integrity_violation(
    client: TestClient,
    keypair,
    organizations,
    catalog,
    directory,
    patients,
    sessions: sessionmaker[Session],
) -> None:
    """DH-EX-014B: amendment release endpoint returns 409 Conflict if an effective observation
    has no attribution or foreign attribution not belonging to the amendment.
    """
    private_key, _ = keypair
    provider, clinician_token, lab, tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="AmendLineageAPI",
        test_count=2,
    )
    item_ids = _order_item_ids(sessions, order_id)
    assert len(item_ids) == 2

    # Record results for both items
    res0 = client.post(
        _observation_url(lab.id, order_id, item_ids[0]),
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "SYN-0",
            "display_label": "Synthetic 0",
            "numeric_value": "10",
        },
    )
    assert res0.status_code == 201
    res1 = client.post(
        _observation_url(lab.id, order_id, item_ids[1]),
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "SYN-1",
            "display_label": "Synthetic 1",
            "numeric_value": "20",
        },
    )
    assert res1.status_code == 201
    for i_id in item_ids:
        assert (
            client.post(
                _completion_url(lab.id, order_id, i_id), headers=_auth(tech_token)
            ).status_code
            == 201
        )
    obs_id_0 = res0.json()["observation_id"]
    obs_id_1 = res1.json()["observation_id"]

    _release_results(client, private_key, lab.id, order_id, "AmendLineageAPI-v1")

    # Start amendment
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech_token))
    assert started.status_code == 201
    amendment_id = started.json()["amendment_id"]

    # Tech corrects item 0 observation (so has_changes is True)
    corrected = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/items/{item_ids[0]}/observations/"
        f"{obs_id_0}/correct",
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "SYN-0",
            "display_label": "Corrected 0",
            "numeric_value": "15",
        },
    )
    assert corrected.status_code == 201

    # Simulate direct DB mutation on item 1: observation superseded unattributed (amendment_id=None)
    with sessions() as session:
        result_id = session.scalar(
            select(LabObservation.lab_result_id).where(LabObservation.id == uuid.UUID(obs_id_1))
        )
        unattributed_obs = LabObservation(
            id=uuid.uuid4(),
            lab_result_id=result_id,
            source_code="SYN-1",
            source_system="dhealth-exchange",
            display_label_snapshot="Unattributed Obs",
            result_type=LabResultType.NUMERIC.value,
            numeric_value=Decimal("99.9"),
            supersedes_observation_id=uuid.UUID(obs_id_1),
            amendment_id=None,
        )
        orig = session.get(LabObservation, uuid.UUID(obs_id_1))
        orig.superseded_at = utc_now()
        session.add(unattributed_obs)
        session.commit()

    releaser_token = _amendment_releaser_token(client, private_key, lab.id, "AmendLineageReleaser")
    fingerprint = _current_amendment_fingerprint(
        client, releaser_token, lab.id, order_id, amendment_id
    )

    # Attempt to release amendment -> must fail closed with 409 Conflict (the fingerprint itself
    # matches the current, still-malformed effective set -- this test exercises the deeper
    # attribution/integrity check inside the manifest-building loop, not the fingerprint gate).
    release_resp = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/release",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": fingerprint},
    )
    assert release_resp.status_code == 409, release_resp.text
    detail = release_resp.json()["detail"]
    assert "differs from base release manifest" in detail or "is not attributed" in detail

    # Verify amendment remains PREPARING and no release version 2 was created
    with sessions() as session:
        stored_amend = session.get(LabResultAmendment, uuid.UUID(amendment_id))
        assert stored_amend.status == LabResultAmendmentStatus.PREPARING.value
        versions = session.scalars(
            select(LabResultReleaseVersion).where(LabResultReleaseVersion.lab_order_id == order_id)
        ).all()
        assert len(versions) == 1


def test_provider_exact_version_content_route_returns_target_manifest_and_review(
    client: TestClient,
    keypair,
    organizations,
    catalog,
    directory,
    patients,
    sessions: sessionmaker[Session],
) -> None:
    """DH-EX-014B NC 10: provider exact-version content route returns the target version's
    immutable manifest and exact review status, never leaking latest order state or another version.
    """
    private_key, _ = keypair
    provider, clinician_token, lab, tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="ExactVerNC10",
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    # Record and release V1
    orig = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "SYN-10",
            "display_label": "Synthetic 10",
            "numeric_value": "10",
        },
    )
    assert orig.status_code == 201
    assert (
        client.post(
            _completion_url(lab.id, order_id, item_id), headers=_auth(tech_token)
        ).status_code
        == 201
    )
    _release_results(client, private_key, lab.id, order_id, "ExactVerNC10-v1")
    v1_id = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    ).json()["release_version_id"]

    # Amend and release V2 with value "20"
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech_token))
    amendment_id = started.json()["amendment_id"]
    corrected = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/items/{item_id}/observations/"
        f"{orig.json()['observation_id']}/correct",
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "SYN-10",
            "display_label": "Synthetic 10 Corrected",
            "numeric_value": "20",
        },
    )
    assert corrected.status_code == 201
    releaser_token = _amendment_releaser_token(client, private_key, lab.id, "ExactVerNC10Releaser")
    v2_fingerprint = _current_amendment_fingerprint(
        client, releaser_token, lab.id, order_id, amendment_id
    )
    v2_rel = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/release",
        headers=_auth(releaser_token),
        json={"expected_result_fingerprint": v2_fingerprint},
    )
    assert v2_rel.status_code == 201
    v2_id = v2_rel.json()["release_version_id"]

    # Clinician reviews V1 with NO_FOLLOW_UP_REQUIRED
    history_url = f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/release-versions"
    rev_resp = client.post(
        f"{history_url}/{v1_id}/review/no-follow-up-required",
        headers=_auth(clinician_token),
    )
    assert rev_resp.status_code == 200, rev_resp.text
    assert rev_resp.json()["release_version_id"] == v1_id

    # Read exact version V1 -> must return value "10" and review status "NO_FOLLOW_UP_REQUIRED"
    v1_content = client.get(
        f"{history_url}/{v1_id}",
        headers=_auth(clinician_token),
    )
    assert v1_content.status_code == 200, v1_content.text
    v1_body = v1_content.json()
    assert v1_body["release_version_id"] == v1_id
    assert v1_body["release_version_number"] == 1
    assert v1_body["is_amendment"] is False
    assert v1_body["review_status"] == "NO_FOLLOW_UP_REQUIRED"
    assert Decimal(v1_body["results"][0]["observations"][0]["numeric_value"]) == Decimal("10")

    # Read exact version V2 -> must return value "20" and review status "PENDING"
    v2_content = client.get(
        f"{history_url}/{v2_id}",
        headers=_auth(clinician_token),
    )
    assert v2_content.status_code == 200, v2_content.text
    v2_body = v2_content.json()
    assert v2_body["release_version_id"] == v2_id
    assert v2_body["release_version_number"] == 2
    assert v2_body["is_amendment"] is True
    assert v2_body["review_status"] == "PENDING"
    assert Decimal(v2_body["results"][0]["observations"][0]["numeric_value"]) == Decimal("20")


def test_amendment_release_audit_atomicity(
    client: TestClient,
    keypair,
    organizations,
    catalog,
    directory,
    patients,
    sessions: sessionmaker[Session],
    monkeypatch,
) -> None:
    """DH-EX-014B NC 11: amendment release, version row creation, manifest entries, new review
    row, and the lab_result_amendment_released audit event must all commit or roll back
    together atomically. An audit recording failure rolls back the entire transaction --
    no split brain.
    """
    private_key, _ = keypair
    provider, clinician_token, lab, tech_token, order_id = _create_in_progress_order(
        client,
        private_key,
        organizations,
        catalog,
        directory,
        patients,
        name="AmendAuditFail",
    )
    item_id = _order_item_ids(sessions, order_id)[0]

    # Create and release V1
    orig = client.post(
        _observation_url(lab.id, order_id, item_id),
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "SYN-AUD",
            "display_label": "Synthetic Audit",
            "numeric_value": "10",
        },
    )
    assert orig.status_code == 201
    assert (
        client.post(
            _completion_url(lab.id, order_id, item_id), headers=_auth(tech_token)
        ).status_code
        == 201
    )
    _release_results(client, private_key, lab.id, order_id, "AmendAuditFail-v1")

    # Start amendment and correct observation
    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech_token))
    amendment_id = started.json()["amendment_id"]
    corrected = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/items/{item_id}/observations/"
        f"{orig.json()['observation_id']}/correct",
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "SYN-AUD",
            "display_label": "Synthetic Audit Corrected",
            "numeric_value": "15",
        },
    )
    assert corrected.status_code == 201

    releaser_token = _amendment_releaser_token(client, private_key, lab.id, "AmendAuditReleaser")
    fingerprint = _current_amendment_fingerprint(
        client, releaser_token, lab.id, order_id, amendment_id
    )

    # Simulate audit write failure
    original_record = ExchangeSecurityAuditRepository.record

    def _failing_record(self, session, *args, **kwargs):
        if kwargs.get("event_type") == "lab_result_amendment_released":
            raise RuntimeError("simulated amendment audit failure")
        return original_record(self, session, *args, **kwargs)

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _failing_record)

    # Attempt to release -> must fail with simulated audit failure
    with pytest.raises(RuntimeError, match="simulated amendment audit failure"):
        client.post(
            f"{_amendment_url(lab.id, order_id, amendment_id)}/release",
            headers=_auth(releaser_token),
            json={"expected_result_fingerprint": fingerprint},
        )

    # Verify complete atomicity rollback:
    with sessions() as session:
        # Amendment remains PREPARING, not RELEASED
        stored_amend = session.get(LabResultAmendment, uuid.UUID(amendment_id))
        assert stored_amend.status == LabResultAmendmentStatus.PREPARING.value
        assert stored_amend.released_at is None
        assert stored_amend.released_by_identity_id is None

        # No version 2 created
        versions = session.scalars(
            select(LabResultReleaseVersion).where(LabResultReleaseVersion.lab_order_id == order_id)
        ).all()
        assert len(versions) == 1
        assert versions[0].version_number == 1

        # No review for version 2 created
        reviews = session.scalars(
            select(DoctorReview).where(DoctorReview.lab_order_id == order_id)
        ).all()
        assert len(reviews) == 1
        assert reviews[0].release_version_id == versions[0].id

    # No amendment release audit event was committed
    assert _audit_events(sessions, "lab_result_amendment_released") == []


# -- DH-CLINIC-005: human patient-identity projection on the results-read API (RI1-RI6) ------------


def test_ri1_ri2_released_result_includes_patient_identity_via_directory(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """RI1: the released-result response carries a Patient identity. RI2: it is the order's own
    ``patient_reference_id -> PatientReference.patient_id -> Patient`` chain, not any client-
    supplied value (this endpoint takes no patient input at all)."""
    private_key, _ = keypair
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="RI1"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    submit = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    assert submit.status_code == 201, submit.text
    _release_results(client, private_key, lab.id, order_id, "RI1")

    results = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    )
    assert results.status_code == 200, results.text
    body = results.json()
    assert body["patient"]["display_name"] == "Synthetic Patient RI1"
    with sessions() as session:
        order = session.get(LabOrder, order_id)
        reference = session.get(PatientReference, order.patient_reference_id)
        assert body["patient"]["patient_id"] == str(reference.patient_id)


def test_ri3_legacy_reference_snapshot_disagreement_does_not_override_directory_identity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """§4/RI3: even if a legacy ``PatientReference.display_name`` snapshot were to drift from the
    current ``Patient`` directory record (a real historical-data possibility -- snapshots are never
    rewritten), the results response must keep reading the live directory value, never the stale
    provenance snapshot."""
    private_key, _ = keypair
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="RI3"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    _release_results(client, private_key, lab.id, order_id, "RI3")

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        reference = session.get(PatientReference, order.patient_reference_id)
        reference.display_name = "Stale Snapshot Name"
        session.commit()

    results = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    )
    assert results.status_code == 200, results.text
    assert results.json()["patient"]["display_name"] == "Synthetic Patient RI3"
    assert results.json()["patient"]["display_name"] != "Stale Snapshot Name"


def test_ri4_ri5_cross_provider_result_read_is_404_and_leaks_no_patient_data(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """RI4: cross-provider result reads stay denied/anti-enumerated exactly as before this ticket.
    RI5: neither the 404 status nor its body ever carries the real patient's name/contact."""
    private_key, _ = keypair
    _provider_a, _tok_a, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="RI4"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1"},
    )
    _release_results(client, private_key, lab.id, order_id, "RI4")

    provider_b, clinician_b_token = _setup_clinician(client, private_key, organizations, "RI4B")

    real_response = client.get(
        f"/exchange/v1/organizations/{provider_b.id}/orders/{order_id}/results",
        headers=_auth(clinician_b_token),
    )
    fake_response = client.get(
        f"/exchange/v1/organizations/{provider_b.id}/orders/{uuid.uuid4()}/results",
        headers=_auth(clinician_b_token),
    )
    assert real_response.status_code == 404
    assert fake_response.status_code == 404
    assert real_response.json() == fake_response.json()
    assert "Synthetic Patient RI4" not in real_response.text
    assert "patient" not in real_response.json()


def test_ri6_release_and_result_semantics_unchanged_alongside_patient_identity(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """RI6: every pre-existing workflow fact (order status, release version metadata, observation
    values) is byte-identical to the pre-DH-CLINIC-005 contract -- ``patient`` is a pure addition,
    not a reshape of anything that already existed."""
    private_key, _ = keypair
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="RI6"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    submit = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/items/{item_id}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "13.5", "numeric_unit": "g/dL"},
    )
    assert submit.status_code == 201, submit.text
    _release_results(client, private_key, lab.id, order_id, "RI6")

    results = client.get(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/results",
        headers=_auth(clinician_token),
    )
    assert results.status_code == 200, results.text
    body = results.json()
    assert body["order_status"] == "COMPLETED"
    assert body["release_version_number"] == 1
    assert body["is_amendment"] is False
    assert len(body["results"]) == 1
    observation = body["results"][0]["observations"][0]
    assert Decimal(observation["numeric_value"]) == Decimal("13.5")
    assert observation["numeric_unit"] == "g/dL"
    # The new field is additive -- every pre-existing top-level key is still exactly as before.
    assert set(body.keys()) == {
        "order_id",
        "order_status",
        "patient",
        "release_version_id",
        "release_version_number",
        "is_amendment",
        "results",
    }


# -- DH-CLINIC-007: GET .../result-reviews (the provider-scoped review queue) ----------------------


def _complete_and_release_order(
    client, private_key, organizations, catalog, directory, patients, sessions, *, name: str
):
    """Full HTTP flow through IN_PROGRESS -> one recorded+completed observation per item ->
    released (COMPLETED). Returns (provider, clinician_token, lab, order_id)."""
    provider, clinician_token, lab, lab_tech_token, order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name=name
    )
    for item_id in _order_item_ids(sessions, order_id):
        obs = client.post(
            _observation_url(lab.id, order_id, item_id),
            headers=_auth(lab_tech_token),
            json={
                "result_type": "NUMERIC",
                "source_code": "SYN-OBS",
                "display_label": "Synthetic observation",
                "numeric_value": "1",
            },
        )
        assert obs.status_code == 201, obs.text
        completed = client.post(
            _completion_url(lab.id, order_id, item_id), headers=_auth(lab_tech_token)
        )
        assert completed.status_code == 201, completed.text
    _release_results(client, private_key, lab.id, order_id, name)
    return provider, clinician_token, lab, order_id


def _queue_url(provider_id, *, status: str | None = None, limit: int | None = None) -> str:
    url = f"/exchange/v1/organizations/{provider_id}/result-reviews"
    params = []
    if status is not None:
        params.append(f"status={status}")
    if limit is not None:
        params.append(f"limit={limit}")
    if params:
        url += "?" + "&".join(params)
    return url


def test_queue_default_shows_pending_review(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _complete_and_release_order(
        client, private_key, organizations, catalog, directory, patients, sessions, name="QDefault"
    )

    response = client.get(_queue_url(provider.id), headers=_auth(clinician_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider_organization_id"] == str(provider.id)
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry["order_id"] == str(order_id)
    assert entry["review_status"] == "PENDING"
    assert entry["release_version_number"] == 1
    assert entry["is_amendment"] is False
    assert entry["legacy_unversioned"] is False
    assert entry["patient"]["display_name"] is not None
    # Q12: no result/observation value of any kind in the queue -- metadata only.
    assert "results" not in entry
    assert "observations" not in entry


def test_queue_in_progress_order_is_absent(
    client: TestClient, keypair, organizations, catalog, directory, patients
) -> None:
    """Q1/the critical exclusion: an order still in progress (never released) must never appear,
    even though its DoctorReview row already exists, PENDING, unbound, from order-creation time."""
    private_key, _ = keypair
    provider, clinician_token, _lab, _tech_token, _order_id = _create_in_progress_order(
        client, private_key, organizations, catalog, directory, patients, name="QInProgress"
    )

    response = client.get(_queue_url(provider.id), headers=_auth(clinician_token))
    assert response.status_code == 200, response.text
    assert response.json()["entries"] == []


def test_queue_finalized_review_excluded_by_default_but_visible_with_status_all(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    private_key, _ = keypair
    provider, clinician_token, _lab, order_id = _complete_and_release_order(
        client, private_key, organizations, catalog, directory, patients, sessions, name="QFinal"
    )
    review_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/no-follow-up-required",
        headers=_auth(clinician_token),
    )
    assert review_response.status_code == 200, review_response.text

    pending_only = client.get(_queue_url(provider.id), headers=_auth(clinician_token))
    assert pending_only.json()["entries"] == []

    everything = client.get(_queue_url(provider.id, status="ALL"), headers=_auth(clinician_token))
    assert len(everything.json()["entries"]) == 1
    assert everything.json()["entries"][0]["review_status"] == "NO_FOLLOW_UP_REQUIRED"
    assert everything.json()["entries"][0]["reviewed_at"] is not None


def test_queue_two_pending_versions_on_one_order_are_two_entries(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Q6/AM6: an amendment on top of an already-released order must surface as a second,
    independent queue entry -- never collapsed into the first, never hiding it."""
    private_key, _ = keypair
    provider, clinician_token, lab, order_id = _complete_and_release_order(
        client, private_key, organizations, catalog, directory, patients, sessions, name="QAmend"
    )
    item_id = _order_item_ids(sessions, order_id)[0]
    tech_token = make_token(private_key, subject="labtech-actual-QAmend")

    started = client.post(_amendment_url(lab.id, order_id), headers=_auth(tech_token))
    assert started.status_code == 201, started.text
    amendment_id = started.json()["amendment_id"]
    with sessions() as session:
        original_obs_id = session.scalar(
            select(LabObservation.id).where(
                LabObservation.lab_result_id.in_(
                    select(LabResult.id).where(LabResult.lab_order_item_id == item_id)
                )
            )
        )
    corrected = client.post(
        f"{_amendment_url(lab.id, order_id, amendment_id)}/items/{item_id}/observations/"
        f"{original_obs_id}/correct",
        headers=_auth(tech_token),
        json={
            "result_type": "NUMERIC",
            "source_code": "SYN-OBS",
            "display_label": "Synthetic observation",
            "numeric_value": "2",
        },
    )
    assert corrected.status_code == 201, corrected.text
    _release_amendment(client, private_key, lab.id, order_id, amendment_id, "QAmend-v2")

    response = client.get(_queue_url(provider.id, status="ALL"), headers=_auth(clinician_token))
    entries = response.json()["entries"]
    assert len(entries) == 2
    version_numbers = {entry["release_version_number"] for entry in entries}
    assert version_numbers == {1, 2}
    assert all(entry["order_id"] == str(order_id) for entry in entries)
    amendment_flags = {entry["release_version_number"]: entry["is_amendment"] for entry in entries}
    assert amendment_flags == {1: False, 2: True}


def test_queue_provider_isolation(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Q2: a provider must never see another provider's review obligations."""
    private_key, _ = keypair
    provider_a, _clinician_a, _lab, _order_id = _complete_and_release_order(
        client, private_key, organizations, catalog, directory, patients, sessions, name="QIsoA"
    )
    _provider_b, clinician_b_token = _setup_clinician(client, private_key, organizations, "QIsoB")

    response = client.get(_queue_url(provider_a.id), headers=_auth(clinician_b_token))
    # Anti-enumeration: clinician B has no membership at all in provider A -- the existing
    # `_require_clinician` tenant gate refuses before the queue query ever runs.
    assert response.status_code in (403, 404), response.text


def test_queue_lab_user_cannot_access(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Q3: a LAB_TECH/LAB_RESULT_RELEASER has no clinician standing at any provider organization
    and cannot call the provider-scoped review queue at all."""
    private_key, _ = keypair
    provider, _clinician_token, lab, _order_id = _complete_and_release_order(
        client, private_key, organizations, catalog, directory, patients, sessions, name="QLabUser"
    )
    lab_tech_token = make_token(private_key, subject="labtech-actual-QLabUser")

    response = client.get(_queue_url(provider.id), headers=_auth(lab_tech_token))
    assert response.status_code in (403, 404), response.text


def test_queue_patient_identity_from_directory_not_reference_snapshot(
    client: TestClient, keypair, organizations, catalog, directory, patients, sessions
) -> None:
    """Q8: the queue's patient projection is the same server-resolved Patient-directory identity
    every other order/result read already uses -- never a raw reference id."""
    private_key, _ = keypair
    provider, clinician_token, _lab, _order_id = _complete_and_release_order(
        client, private_key, organizations, catalog, directory, patients, sessions, name="QPatient"
    )

    entry = client.get(_queue_url(provider.id), headers=_auth(clinician_token)).json()["entries"][0]
    assert entry["patient"]["display_name"] == "Synthetic Patient QPatient"
    assert "patient_reference_id" not in entry


def test_queue_resolution_is_not_n_plus_1(
    client: TestClient,
    keypair,
    organizations,
    catalog,
    directory,
    patients,
    sessions,
    engine: Engine,
) -> None:
    """Q9: mirrors DH-CLINIC-005's own N+1 proof exactly -- the actual SQL SELECT count for a
    5-entry queue must equal the count for a 1-entry queue."""
    private_key, _ = keypair
    provider, clinician_token, _lab, _order_id = _complete_and_release_order(
        client, private_key, organizations, catalog, directory, patients, sessions, name="QN1-seed"
    )

    def _measure_select_count() -> int:
        statements: list[str] = []

        def _record(conn, cursor, statement, *args) -> None:  # noqa: ARG001
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            resp = client.get(_queue_url(provider.id, status="ALL"), headers=_auth(clinician_token))
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        assert resp.status_code == 200, resp.text
        return len([s for s in statements if s.strip().startswith("SELECT")])

    selects_for_one = _measure_select_count()

    # 4 more orders/patients/labs, all released under the SAME provider queue as the seed above.
    for suffix in ("2", "3", "4", "5"):
        entry, _ = catalog.create_entry(code=f"DHEALTH-QN1-{suffix}", display_name=f"QN1 {suffix}")
        lab_n = _price_lab(
            catalog,
            directory,
            organizations,
            name=f"QN1Lab-{suffix}",
            tests=[(entry.id, Decimal("500"))],
        )
        patient_id = _create_patient(client, private_key, patients, provider.id, f"QN1-{suffix}")
        create_response = client.post(
            f"/exchange/v1/organizations/{provider.id}/orders",
            headers={**_auth(clinician_token), "Idempotency-Key": _new_key()},
            json={"patient_reference_id": str(patient_id), "catalog_entry_ids": [str(entry.id)]},
        )
        assert create_response.status_code == 201, create_response.text
        order_n_id = uuid.UUID(create_response.json()["id"])
        access = client.post(
            f"/exchange/v1/organizations/{provider.id}/orders/{order_n_id}/patient-access",
            headers=_auth(clinician_token),
        ).json()
        access_token = access["access_token"]
        options = client.get(
            "/exchange/v1/patient/order/lab-options", headers=_auth(access_token)
        ).json()["options"]
        matching = [opt for opt in options if opt["lab"]["id"] == str(lab_n.id)]
        price_ids = [line["current_price_id"] for line in matching[0]["prices"]]
        client.post(
            "/exchange/v1/patient/order/selection",
            headers=_auth(access_token),
            json={"lab_organization_id": str(lab_n.id), "expected_price_ids": price_ids},
        )
        tech_id = _bootstrap_identity(client, private_key, f"labtech-qn1-{suffix}")
        _grant_roles(client, private_key, lab_n.id, tech_id, f"labtech-qn1-{suffix}", ["LAB_TECH"])
        tech_token = make_token(private_key, subject=f"labtech-qn1-{suffix}")
        client.post(
            f"/exchange/v1/organizations/{lab_n.id}/lab-orders/{order_n_id}/accept",
            headers=_auth(tech_token),
        )
        client.post(
            f"/exchange/v1/organizations/{lab_n.id}/lab-orders/{order_n_id}/record-collection",
            headers=_auth(tech_token),
        )
        client.post(
            f"/exchange/v1/organizations/{lab_n.id}/lab-orders/{order_n_id}/start-processing",
            headers=_auth(tech_token),
        )
        item_id = _order_item_ids(sessions, order_n_id)[0]
        client.post(
            _observation_url(lab_n.id, order_n_id, item_id),
            headers=_auth(tech_token),
            json={
                "result_type": "NUMERIC",
                "source_code": "SYN-OBS",
                "display_label": "Synthetic observation",
                "numeric_value": "1",
            },
        )
        client.post(_completion_url(lab_n.id, order_n_id, item_id), headers=_auth(tech_token))
        _release_results(client, private_key, lab_n.id, order_n_id, f"qn1-{suffix}")

    selects_for_five = _measure_select_count()
    assert selects_for_five == selects_for_one
