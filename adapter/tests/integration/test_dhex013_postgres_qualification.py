"""DH-EX-013/ADR-045 real PostgreSQL qualification: pre-release observation correction
concurrency and atomicity claims. SQLite is not sufficient evidence for production PostgreSQL
row-locking behavior -- matching the reasoning ``test_dhex012c_postgres_qualification.py`` already
established, applied here to ``exchange/results.py``'s new ``correct_observation``.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHEX013_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHEX013_PG_HOST=127.0.0.1 \\
    DHEX013_PG_PORT=5432 \\
    DHEX013_PG_USER=dhex013 \\
    DHEX013_PG_PASSWORD=dhex013 \\
    DHEX013_PG_DATABASE=dhex013_qual \\
        pytest adapter/tests/integration/test_dhex013_postgres_qualification.py -v

The target database must already have migrations applied through ``0017_observation_revisions``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.
"""

from __future__ import annotations

import os
import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import LabObservation, LabOrder, LabPrice, LabResultType
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    EntryAlreadyCompleted,
    InvalidResultSubmissionState,
    LabResultReleaseRepository,
    LabResultRepository,
    StaleObservationVersion,
)
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHEX013_PG_HOST")
PG_PORT = os.environ.get("DHEX013_PG_PORT")
PG_USER = os.environ.get("DHEX013_PG_USER")
PG_PASSWORD = os.environ.get("DHEX013_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX013_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHEX013_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _labtech_identity_id(sessions: sessionmaker, subject: str):
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer="https://pg13-qualification.example", subject=subject
    )
    return identity.id


def _seed_in_progress_order(sessions: sessionmaker, *, key_prefix: str, item_count: int = 1):
    """Identical shape to `test_dhex012c_postgres_qualification.py`'s own fixture."""
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
            code=f"{key_prefix}-TEST-{i}", display_name=f"DH-EX-013 test {i}"
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
        source_system="pg13-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-1",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="pg13-qualification",
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
    workflow.record_collection(order_id=order.id, lab_organization_id=lab.id)
    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)

    order = orders.get(order.id)
    return lab.id, order.id, [item.id for item in order.items]


def test_concurrent_corrections_of_same_observation_yield_exactly_one_winner(
    sessions: sessionmaker,
) -> None:
    """Ten concurrent `correct_observation` calls against the same effective observation: exactly
    one may succeed; every other caller must observe `StaleObservationVersion`, never create a
    second sibling revision superseding the same predecessor."""
    key_prefix = f"pg13-concurrentcorrect-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(sessions, key_prefix=key_prefix)
    item_id = item_ids[0]
    results = LabResultRepository(sessions)
    original = results.record_observation(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot="Glucose",
        numeric_value=Decimal("5.1"),
        recorded_by_identity_id=_labtech_identity_id(sessions, "pg13-seed-tech"),
    )

    n = 10
    outcomes: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def attempt(i: int) -> None:
        try:
            actor = _labtech_identity_id(sessions, f"pg13-corrector-{i}")
            barrier.wait(timeout=15)
            successor = results.correct_observation(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                observation_id=original.id,
                result_type=LabResultType.NUMERIC,
                source_code="GLU",
                display_label_snapshot="Glucose",
                numeric_value=Decimal(f"{i}.0"),
                corrected_by_identity_id=actor,
            )
            with lock:
                outcomes.append(("ok", successor.id))
        except StaleObservationVersion:
            with lock:
                outcomes.append(("stale", None))
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"correct_observation must never raise unexpectedly: {errors!r}"
    assert len(outcomes) == n
    winners = [o for o in outcomes if o[0] == "ok"]
    assert len(winners) == 1, f"exactly one correction may win the race, got {winners!r}"

    with sessions() as session:
        successor_count = session.scalar(
            select(func.count())
            .select_from(LabObservation)
            .where(LabObservation.supersedes_observation_id == original.id)
        )
        assert successor_count == 1, "no two rows may ever supersede the same predecessor"
        effective_count = session.scalar(
            select(func.count())
            .select_from(LabObservation)
            .where(
                LabObservation.lab_result_id == original.lab_result_id,
                LabObservation.superseded_at.is_(None),
            )
        )
        assert effective_count == 1, "exactly one observation must remain effective"


def test_correction_vs_release_race_never_lets_release_ship_stale_value(
    sessions: sessionmaker,
) -> None:
    """Races `correct_observation` against `LabResultReleaseRepository.release` on a single-item,
    already-entry-completed order. Whichever the shared LabOrder lock admits first determines the
    outcome; if correction wins, release must see the corrected value; if release wins, the
    correction must be refused as post-release -- never a released order whose provider-visible
    value silently changes afterward."""
    key_prefix = f"pg13-correctvsrelease-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(sessions, key_prefix=key_prefix)
    item_id = item_ids[0]
    results = LabResultRepository(sessions)
    releases = LabResultReleaseRepository(sessions)
    original = results.record_observation(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        result_type=LabResultType.NUMERIC,
        source_code="NA",
        display_label_snapshot="Sodium",
        numeric_value=Decimal("140"),
        recorded_by_identity_id=_labtech_identity_id(sessions, "pg13-cvr-tech"),
    )
    results.complete_entry(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        completed_by_identity_id=_labtech_identity_id(sessions, "pg13-cvr-completer"),
    )
    corrector = _labtech_identity_id(sessions, "pg13-cvr-corrector")
    releaser = _labtech_identity_id(sessions, "pg13-cvr-releaser")

    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def do_correct() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["correct"] = results.correct_observation(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                observation_id=original.id,
                result_type=LabResultType.NUMERIC,
                source_code="NA",
                display_label_snapshot="Sodium",
                numeric_value=Decimal("141"),
                corrected_by_identity_id=corrector,
            )
        except InvalidResultSubmissionState as refused:
            outcomes["correct"] = refused
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    def do_release() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["release"] = releases.release(
                order_id=order_id, lab_organization_id=lab_id, released_by_identity_id=releaser
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    t1 = threading.Thread(target=do_correct)
    t2 = threading.Thread(target=do_release)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, errors
    assert "correct" in outcomes and "release" in outcomes

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order.status == "COMPLETED"
        effective = session.scalar(
            select(LabObservation).where(
                LabObservation.lab_result_id == original.lab_result_id,
                LabObservation.superseded_at.is_(None),
            )
        )
        if isinstance(outcomes["correct"], InvalidResultSubmissionState):
            # Release won -- correction was refused; the original value is still effective and
            # is exactly what release saw.
            assert effective.id == original.id
            assert effective.numeric_value == Decimal("140")
        else:
            # Correction won -- it committed before release's own completeness re-check ran, so
            # release saw (and the provider will see) the corrected value.
            assert effective.numeric_value == Decimal("141")


def test_concurrent_corrections_on_different_observations_both_succeed(
    sessions: sessionmaker,
) -> None:
    """Two concurrent corrections targeting two *different* observations under the same order
    (different items) do not spuriously conflict -- only same-predecessor races are refused."""
    key_prefix = f"pg13-differentobs-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(
        sessions, key_prefix=key_prefix, item_count=2
    )
    results = LabResultRepository(sessions)
    originals = [
        results.record_observation(
            order_id=order_id,
            lab_order_item_id=item_id,
            lab_organization_id=lab_id,
            result_type=LabResultType.NUMERIC,
            source_code=f"OBS-{i}",
            display_label_snapshot=f"Observation {i}",
            numeric_value=Decimal("1"),
            recorded_by_identity_id=_labtech_identity_id(sessions, f"pg13-diff-seed-{i}"),
        )
        for i, item_id in enumerate(item_ids)
    ]

    outcomes: dict[int, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def attempt(i: int) -> None:
        try:
            actor = _labtech_identity_id(sessions, f"pg13-diff-corrector-{i}")
            barrier.wait(timeout=15)
            outcomes[i] = results.correct_observation(
                order_id=order_id,
                lab_order_item_id=item_ids[i],
                lab_organization_id=lab_id,
                observation_id=originals[i].id,
                result_type=LabResultType.NUMERIC,
                source_code=f"OBS-{i}",
                display_label_snapshot=f"Observation {i}",
                numeric_value=Decimal("2"),
                corrected_by_identity_id=actor,
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(outcomes) == 2
    assert all(outcomes[i].numeric_value == Decimal("2") for i in range(2))


def test_correction_does_not_reopen_the_ordinary_add_observation_freeze(
    sessions: sessionmaker,
) -> None:
    """Confirms `correct_observation` never accidentally clears the freeze `record_observation`
    already established once entry is complete -- correcting an effective observation must not
    let a brand-new, unrelated observation slip in afterward."""
    key_prefix = f"pg13-nofreezereopen-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(sessions, key_prefix=key_prefix)
    item_id = item_ids[0]
    results = LabResultRepository(sessions)
    original = results.record_observation(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        result_type=LabResultType.NUMERIC,
        source_code="K",
        display_label_snapshot="Potassium",
        numeric_value=Decimal("4.0"),
        recorded_by_identity_id=_labtech_identity_id(sessions, "pg13-nofreeze-tech"),
    )
    results.complete_entry(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        completed_by_identity_id=_labtech_identity_id(sessions, "pg13-nofreeze-completer"),
    )
    results.correct_observation(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        observation_id=original.id,
        result_type=LabResultType.NUMERIC,
        source_code="K",
        display_label_snapshot="Potassium",
        numeric_value=Decimal("4.5"),
        corrected_by_identity_id=_labtech_identity_id(sessions, "pg13-nofreeze-corrector"),
    )

    with pytest.raises(EntryAlreadyCompleted):
        results.record_observation(
            order_id=order_id,
            lab_order_item_id=item_id,
            lab_organization_id=lab_id,
            result_type=LabResultType.NUMERIC,
            source_code="NEW",
            display_label_snapshot="Unrelated new observation",
            numeric_value=Decimal("1"),
            recorded_by_identity_id=_labtech_identity_id(sessions, "pg13-nofreeze-sneaky"),
        )
