"""DH-EX-007R: real PostgreSQL qualification of the results-workflow's concurrency and atomicity
claims. SQLite is not sufficient evidence for production PostgreSQL row-locking behavior --
matching the reasoning ``test_dhex001r_postgres_qualification.py`` already established for the
Exchange core, applied here to ``exchange/results.py``'s ``LabOrder FOR UPDATE`` discipline.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHEX007_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHEX007_PG_HOST=127.0.0.1 \\
    DHEX007_PG_PORT=5432 \\
    DHEX007_PG_USER=dhex007 \\
    DHEX007_PG_PASSWORD=dhex007 \\
    DHEX007_PG_DATABASE=dhex007_qual \\
        pytest adapter/tests/integration/test_dhex007_postgres_qualification.py -v

The target database must already have migrations applied through ``0010_exchange_results_workflow``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.
"""

from __future__ import annotations

import os
import tempfile
import threading
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    DoctorReview,
    ExchangeSecurityAuditEvent,
    LabObservation,
    LabOrder,
    LabPrice,
    LabResult,
    ResultDelivery,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    DuplicateResult,
    InvalidResultSubmissionState,
    LabResultRepository,
)
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHEX007_PG_HOST")
PG_PORT = os.environ.get("DHEX007_PG_PORT")
PG_USER = os.environ.get("DHEX007_PG_USER")
PG_PASSWORD = os.environ.get("DHEX007_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX007_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHEX007_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed_in_progress_order(sessions: sessionmaker, *, key_prefix: str, item_count: int = 1):
    """One provider, one lab, an `item_count`-item order, selected and driven to IN_PROGRESS via
    the real API-equivalent repository calls. Returns (order_id, [item_id, ...])."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)
    workflow = LabWorkflowRepository(sessions, organizations)

    provider = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")
    lab = organizations.create(org_type="LAB", name=f"{key_prefix}-lab")
    entries = []
    offering_ids = []
    for i in range(item_count):
        entry, _ = catalog.create_entry(
            code=f"{key_prefix}-TEST-{i}", display_name=f"DH-EX-007R test {i}"
        )
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("100.00") * (i + 1),
            currency="DJF",
            expected_current_price_id=None,
        )
        entries.append(entry)
        offering_ids.append(offering.id)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="addr",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-1",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:practitioner",
        source_identity_value="D-1",
    )
    order = orders.create(
        idempotency_key=f"{key_prefix}-order",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=e.id) for e in entries],
    ).order

    with sessions() as session:
        price_ids = session.scalars(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id.in_(offering_ids), LabPrice.superseded_at.is_(None)
            )
        ).all()

    selections.select(
        lab_order_id=order.id,
        lab_organization_id=lab.id,
        expected_price_ids=frozenset(price_ids),
    )
    workflow.accept(order_id=order.id, lab_organization_id=lab.id)
    # DH-EX-011: start_processing now requires a recorded collection milestone -- this fixture
    # predates that ticket and was never updated (this file is skipped by default in CI, so the
    # staleness went unnoticed until DH-EX-012C's real qualification actually ran it again).
    workflow.record_collection(order_id=order.id, lab_organization_id=lab.id)
    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)

    order = orders.get(order.id)
    return lab.id, order.id, [item.id for item in order.items]


def _labtech_identity_id(sessions: sessionmaker, subject: str):
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer="https://pg-qualification.example", subject=subject
    )
    return identity.id


def test_concurrent_same_item_submission_yields_exactly_one_result(sessions: sessionmaker) -> None:
    """Single-item order: the LabOrder FOR UPDATE lock serializes every concurrent submission for
    this order down to one winner. DH-EX-012/ADR-044: unlike DH-EX-007's original behavior (where
    this single winner also completed a one-item order, so the nine losers hit
    `InvalidResultSubmissionState` before ever reaching the duplicate check), recording a result no
    longer completes the order by itself -- the order stays `IN_PROGRESS` for every loser, so all
    nine now correctly observe the real `DuplicateResult` conflict instead, identical to the
    two-item companion test below. Both are controlled conflicts; neither is a raw DB error."""
    key_prefix = f"pg7sameitem-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(sessions, key_prefix=key_prefix)
    item_id = item_ids[0]
    results = LabResultRepository(sessions)

    winners: list[int] = []
    losers: list[int] = []
    unexpected_errors: list[BaseException] = []
    lock = threading.Lock()
    n = 10
    barrier = threading.Barrier(n)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            results.record_numeric(
                recorded_by_identity_id=_labtech_identity_id(sessions, f"pg7-actor-1-{i}"),
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                value=Decimal(i + 1),
            )
            with lock:
                winners.append(i)
        except (DuplicateResult, InvalidResultSubmissionState):
            with lock:
                losers.append(i)
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                unexpected_errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not unexpected_errors, (
        f"no raw DB error may leak to a caller -- got: {unexpected_errors!r}"
    )
    assert len(winners) == 1, f"exactly one winner required, got {winners!r}"
    assert len(losers) == n - 1

    with sessions() as session:
        result_count = session.scalar(
            select(func.count())
            .select_from(LabResult)
            .where(LabResult.lab_order_item_id == item_id)
        )
        assert result_count == 1
        recorded_events_for_this_order = session.scalar(
            select(func.count())
            .select_from(ExchangeSecurityAuditEvent)
            .where(
                ExchangeSecurityAuditEvent.event_type == "lab_observation_recorded",
                ExchangeSecurityAuditEvent.resource_id == order_id,
            )
        )
        # This test calls the repository directly (no API layer, no audit call) -- 0 is expected
        # for *this order* here; audit-count correctness under concurrency is proven at the API
        # layer in test_exchange_results_api.py's own (non-concurrent) audit-atomicity tests.
        # Scoped by resource_id, not a bare global count -- this scratch database is reused
        # across this session's qualification steps and may hold unrelated orders' audit rows.
        assert recorded_events_for_this_order == 0
        order = session.get(LabOrder, order_id)
        # DH-EX-012/ADR-044: never auto-completed -- stays IN_PROGRESS regardless of the winner.
        assert order.status == "IN_PROGRESS"


def test_concurrent_same_item_submission_on_still_in_progress_order_yields_duplicate_result(
    sessions: sessionmaker,
) -> None:
    """Companion to the one-item test above, isolating `DuplicateResult` specifically: a two-item
    order racing on only one of its items never completes (the second item has no result), so
    every loser observes the real `DuplicateResult` conflict, not the lifecycle gate."""
    key_prefix = f"pg7sameitem2-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(
        sessions, key_prefix=key_prefix, item_count=2
    )
    item_id = item_ids[0]
    results = LabResultRepository(sessions)

    winners: list[int] = []
    losers: list[int] = []
    unexpected_errors: list[BaseException] = []
    lock = threading.Lock()
    n = 10
    barrier = threading.Barrier(n)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            results.record_numeric(
                recorded_by_identity_id=_labtech_identity_id(sessions, f"pg7-actor-2-{i}"),
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                value=Decimal(i + 1),
            )
            with lock:
                winners.append(i)
        except DuplicateResult:
            with lock:
                losers.append(i)
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                unexpected_errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not unexpected_errors, (
        f"no raw DB error may leak to a caller -- got: {unexpected_errors!r}"
    )
    assert len(winners) == 1
    assert len(losers) == n - 1

    with sessions() as session:
        result_count = session.scalar(
            select(func.count())
            .select_from(LabResult)
            .where(LabResult.lab_order_item_id == item_id)
        )
        assert result_count == 1
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"  # second item still has no result


def test_concurrent_final_two_results_converge_to_ready_for_release_with_no_lost_write(
    sessions: sessionmaker,
) -> None:
    """DH-EX-012/ADR-044: renamed from `..._converge_to_completed_...` -- recording even the last
    missing result no longer completes the order by itself; `ready_for_release` reports the same
    DB-grounded "every item now has a result" fact, but the order stays `IN_PROGRESS` until an
    explicit, authorized release (see `test_dhex012_postgres_qualification.py`)."""
    key_prefix = f"pg7finaltwo-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(
        sessions, key_prefix=key_prefix, item_count=2
    )
    results = LabResultRepository(sessions)

    outcomes: dict[int, bool] = {}
    unexpected_errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def attempt(i: int, item_id) -> None:
        try:
            barrier.wait(timeout=15)
            outcome = results.record_numeric(
                recorded_by_identity_id=_labtech_identity_id(sessions, f"pg7-actor-3-{i}"),
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                value=Decimal(i + 1),
            )
            with lock:
                outcomes[i] = outcome.ready_for_release
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                unexpected_errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i, item_ids[i])) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not unexpected_errors, f"no lost write / no raw DB error: {unexpected_errors!r}"
    assert len(outcomes) == 2, "both concurrent submissions must succeed -- no lost write"
    assert sorted(outcomes.values()) == [False, True], (
        "exactly one of the two must be the ready-for-release-triggering submission"
    )

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"  # DH-EX-012: never auto-completed by result entry
        result_count = session.scalar(
            select(func.count())
            .select_from(LabResult)
            .where(LabResult.lab_order_item_id.in_(item_ids))
        )
        assert result_count == 2, "both results must persist -- no lost write"
        review = session.scalar(select(DoctorReview).where(DoctorReview.lab_order_id == order_id))
        assert review.status == "PENDING"
        delivery = session.scalar(
            select(ResultDelivery).where(ResultDelivery.lab_order_id == order_id)
        )
        assert delivery.status == "PENDING"


# -- DH-EX-007A: the tests above drive `LabResultRepository` directly, below the FastAPI/Pydantic
# validation layer entirely -- they prove nothing about whether an over-scale or over-precision
# Decimal is rejected *before* reaching this real PostgreSQL database, only what the database
# itself does once a value already arrives. That gap is exactly why this ticket exists: reproduced
# live before the fix (recorded in PROJECT_STATE.md's DH-EX-007A entry) against this exact
# database engine, `numeric_value=1.23456` reached `NUMERIC(18, 4)` and was **silently rounded to
# `1.2346`**, HTTP 201, no error at all; `numeric_value=123456789012345.6789` (15 integer digits)
# raised `psycopg.errors.NumericValueOutOfRange` ("numeric field overflow"), translated by
# SQLAlchemy to `DataError`, uncaught, reaching the caller as a raw HTTP 500. The tests below
# build the real FastAPI router (`build_exchange_router`, the identical production wiring
# `test_exchange_results_api.py` uses) over this file's own real-Postgres `sessions` fixture and
# drive the actual HTTP endpoint, so the property proven is specifically "the HTTP boundary now
# refuses these before PostgreSQL ever sees them, on this real engine" -- not re-proving what
# `test_exchange_results_api.py`'s SQLite-backed suite already covers for the validation rule
# itself.


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_client(sessions: sessionmaker, tmp_path: Path):
    """A real FastAPI TestClient wired to this file's own real-PostgreSQL `sessions` factory --
    the identical `build_exchange_router` production wiring, a fresh local RSA keypair (zero
    network), and `raise_server_exceptions=False` so an uncaught exception (the exact pre-fix
    over-precision behavior) surfaces as an observed HTTP 500 in the test rather than a pytest
    error, matching how a real deployed server would actually respond to a caller."""
    private_key, public_key = generate_keypair()
    validator = build_validator(public_key)
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=validator,
        audit_writer=ExchangeAuditWriter(Path(tempfile.mkdtemp()) / "audit.jsonl"),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
        patient_access_ttl_hours=168,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False), private_key


def _seed_in_progress_order_with_lab_tech_token(
    sessions: sessionmaker, client: TestClient, private_key, *, key_prefix: str, item_count: int
):
    """`_seed_in_progress_order` gets an order to IN_PROGRESS via direct repository calls (fast,
    already proven correct by the tests above); this additionally mints a real bearer token for a
    genuine LAB_TECH membership on the selected lab, via the real `/me` + `/memberships` HTTP
    routes, so the HTTP result-submission endpoint under test is exercised exactly as a real
    caller would reach it -- not with a hand-constructed principal."""
    lab_id, order_id, item_ids = _seed_in_progress_order(
        sessions, key_prefix=key_prefix, item_count=item_count
    )
    identity_token = make_token(private_key, subject=f"{key_prefix}-labtech")
    identity_response = client.get("/exchange/v1/me", headers=_auth(identity_token))
    assert identity_response.status_code == 200, identity_response.text
    identity_id = identity_response.json()["identity_id"]

    admin_token = make_token(
        private_key, subject=f"{key_prefix}-admin", roles=[PLATFORM_ADMIN_ROLE]
    )
    membership_response = client.post(
        f"/exchange/v1/organizations/{lab_id}/memberships",
        headers=_auth(admin_token),
        json={"identity_id": identity_id, "roles": ["LAB_TECH"]},
    )
    assert membership_response.status_code in (200, 201), membership_response.text

    return lab_id, order_id, item_ids, identity_token


def test_over_scale_decimal_rejected_before_reaching_real_postgres(
    sessions: sessionmaker, api_client
) -> None:
    """The exact pre-fix reproduction, over-scale: before this ticket, this request returned 201
    with the value silently rounded from `1.23456` to `1.2346` in this real database. Now it must
    be refused at the HTTP boundary, with no row ever reaching PostgreSQL."""
    client, private_key = api_client
    key_prefix = f"pg7ascale-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids, lab_tech_token = _seed_in_progress_order_with_lab_tech_token(
        sessions, client, private_key, key_prefix=key_prefix, item_count=1
    )

    response = client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_ids[0]}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "1.23456"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["type"] == "decimal_max_places"

    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(LabResult)
                .where(LabResult.lab_order_item_id == item_ids[0])
            )
            == 0
        )
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"


def test_over_precision_decimal_rejected_before_reaching_real_postgres(
    sessions: sessionmaker, api_client
) -> None:
    """The exact pre-fix reproduction, over-precision: before this ticket, this request raised
    `psycopg.errors.NumericValueOutOfRange` uncaught in this real database, surfacing as a raw
    HTTP 500. Now it must be refused at the HTTP boundary, with no row ever reaching PostgreSQL and
    no raw 500 reaching the caller."""
    client, private_key = api_client
    key_prefix = f"pg7aprec-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids, lab_tech_token = _seed_in_progress_order_with_lab_tech_token(
        sessions, client, private_key, key_prefix=key_prefix, item_count=1
    )

    response = client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_ids[0]}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "123456789012345.6789"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["type"] in ("decimal_max_digits", "decimal_whole_digits")

    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(LabResult)
                .where(LabResult.lab_order_item_id == item_ids[0])
            )
            == 0
        )
        order = session.get(LabOrder, order_id)
        assert order.status == "IN_PROGRESS"


def test_boundary_valid_decimal_persists_exactly_in_real_postgres(
    sessions: sessionmaker, api_client
) -> None:
    """The maximum representable magnitude must reach and persist in real `NUMERIC(18, 4)`
    unchanged -- proving the fix does not over-reject the exact boundary the database can hold."""
    client, private_key = api_client
    key_prefix = f"pg7abound-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids, lab_tech_token = _seed_in_progress_order_with_lab_tech_token(
        sessions, client, private_key, key_prefix=key_prefix, item_count=1
    )

    response = client.post(
        f"/exchange/v1/organizations/{lab_id}/lab-orders/{order_id}/items/{item_ids[0]}/result",
        headers=_auth(lab_tech_token),
        json={"result_type": "NUMERIC", "numeric_value": "99999999999999.9999"},
    )
    assert response.status_code == 201, response.text
    # DH-EX-012/ADR-044: even this one-item order's sole result no longer completes it.
    assert response.json()["order_status"] == "IN_PROGRESS"
    assert response.json()["ready_for_release"] is True

    with sessions() as session:
        container = session.scalar(
            select(LabResult).where(LabResult.lab_order_item_id == item_ids[0])
        )
        observation = session.scalar(
            select(LabObservation).where(LabObservation.lab_result_id == container.id)
        )
        assert observation.numeric_value == Decimal("99999999999999.9999")
