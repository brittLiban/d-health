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
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import PatientReferenceRepository
from daher_adapter.exchange.models import (
    DoctorReview,
    ExchangeSecurityAuditEvent,
    LabOrder,
    LabOrderItem,
    LabResult,
    LabResultRelease,
    ResultDelivery,
)
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
    response = client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/release-results",
        headers=_auth(releaser_token),
    )
    assert response.status_code == 201, response.text
    return response.json()


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
        "result_type",
        "completed_at",
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
    assert inspection_body["all_results_entered"] is True
    assert inspection_body["released"] is False
    assert inspection_body["released_at"] is None
    assert Decimal(inspection_body["results"][0]["numeric_value"]) == Decimal("13.5")

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
    assert len(results_body["results"]) == 1
    result_line = results_body["results"][0]
    assert Decimal(result_line["numeric_value"]) == Decimal("13.5")
    assert result_line["numeric_unit"] == "g/dL"

    review_response = client.post(
        f"/exchange/v1/organizations/{provider.id}/orders/{order_id}/review/no-follow-up-required",
        headers=_auth(clinician_token),
    )
    assert review_response.status_code == 200, review_response.text
    review_body = review_response.json()
    assert review_body["status"] == "NO_FOLLOW_UP_REQUIRED"
    assert review_body["reviewed_by_practitioner_reference_id"] is not None

    with sessions() as session:
        delivery = session.scalar(
            select(ResultDelivery).where(ResultDelivery.lab_order_id == order_id)
        )
        assert delivery.status == "PENDING"  # DH-EX-007/012 never touch ResultDelivery
        assert delivery.delivered_at is None

    # DH-EX-012/ADR-044: `lab_order_completed`'s actor is now the releaser, never the LAB_TECH who
    # entered the result -- a semantic change from DH-EX-007, disclosed in PROJECT_STATE.md.
    result_events = _audit_events(sessions, "lab_result_recorded")
    released_events = _audit_events(sessions, "lab_results_released")
    completed_events = _audit_events(sessions, "lab_order_completed")
    review_events = _audit_events(sessions, "doctor_review_recorded")
    assert len(result_events) == 1
    assert len(released_events) == 1
    assert len(completed_events) == 1
    assert len(review_events) == 1
    assert result_events[0]["organization_id"] == str(lab.id)
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

    result_events = _audit_events(sessions, "lab_result_recorded")
    completed_events = _audit_events(sessions, "lab_order_completed")
    review_events = _audit_events(sessions, "doctor_review_recorded")
    assert len(result_events) == 1
    assert len(completed_events) == 1
    assert len(review_events) == 1
    assert result_events[0]["organization_id"] == str(lab.id)
    assert completed_events[0]["organization_id"] == str(lab.id)
    assert review_events[0]["organization_id"] == str(provider.id)


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
    directly: no ``LabResult`` row, no ``lab_result_recorded``/``lab_order_completed`` audit event,
    and no ``LabOrder.status`` change, for an over-scale request on a one-item order (so a real
    completion would otherwise have been triggered by a successful submission)."""
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

    assert _audit_events(sessions, "lab_result_recorded") == []
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
        "result_type",
        "numeric_value",
        "numeric_unit",
        "reference_range_low",
        "reference_range_high",
        "text_value",
        "completed_at",
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
    """The `LabResult` insert and its `lab_result_recorded` audit row must roll back together when
    the audit insert fails, exactly matching DH-EX-006R's established pattern for accept/reject/
    start-processing. DH-EX-012/ADR-044: renamed from `..._and_completion_audit_atomicity` --
    result submission no longer completes anything by itself (see
    `test_release_and_completion_audit_atomicity` below for the release endpoint's own two-audit
    atomicity, where `lab_order_completed` now actually fires)."""
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
        assert session.scalar(select(LabResult)) is None  # result insert rolled back too

    assert _audit_events(sessions, "lab_result_recorded") == []
    assert _audit_events(sessions, "lab_order_completed") == []


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

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(ExchangeSecurityAuditRepository, "record", _raise)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
            headers=_auth(releaser_token),
        )

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"  # never advanced to COMPLETED
        assert session.scalar(select(LabResultRelease)) is None  # release insert rolled back too

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
    )
    fake_response = client.post(
        f"/exchange/v1/organizations/{impostor_lab.id}/lab-orders/{uuid.uuid4()}/release-results",
        headers=_auth(impostor_releaser_token),
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
    assert response.json()["all_results_entered"] is True


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

    release = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(dual_token),
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

    first = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(releaser_token),
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/exchange/v1/organizations/{lab.id}/lab-orders/{order_id}/release-results",
        headers=_auth(releaser_token),
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
