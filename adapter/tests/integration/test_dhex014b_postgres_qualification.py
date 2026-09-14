"""DH-EX-014B real-PostgreSQL row-locking, idempotency, and atomicity qualification.

Opt-in only. The target must be a disposable database migrated through
``0019_exchange_amendments``; never point these tests at a facility database.
"""

from __future__ import annotations

import os
import threading
import time
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
from daher_adapter.exchange.models import (
    DoctorReview,
    LabObservation,
    LabPrice,
    LabResultAmendment,
    LabResultReleaseManifestEntry,
    LabResultReleaseVersion,
    LabResultType,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    AmendmentAlreadyInProgress,
    AmendmentNotPreparing,
    DoctorReviewRepository,
    ExplicitReviewVersionRequired,
    LabResultAmendmentRepository,
    LabResultReleaseRepository,
    LabResultRepository,
    StaleObservationVersion,
)
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHEX014B_PG_HOST")
PG_PORT = os.environ.get("DHEX014B_PG_PORT")
PG_USER = os.environ.get("DHEX014B_PG_USER")
PG_PASSWORD = os.environ.get("DHEX014B_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX014B_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a disposable PostgreSQL database (DHEX014B_PG_*) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _identity_id(sessions: sessionmaker, subject: str):
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer="https://pg14b-qualification.example", subject=subject
    )
    return identity.id


def _seed_in_progress_order(sessions: sessionmaker, *, key_prefix: str, item_count: int = 1):
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
    for index in range(item_count):
        entry, _ = catalog.create_entry(
            code=f"{key_prefix}-TEST-{index}", display_name=f"Synthetic test {index}"
        )
        offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("100.00") * (index + 1),
            currency="DJF",
            expected_current_price_id=None,
        )
        entries.append(entry)
        offering_ids.append(offering.id)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="Synthetic address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="pg14b-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-1",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="pg14b-qualification",
        source_identity_system=f"urn:{key_prefix}:practitioner",
        source_identity_value="D-1",
    )
    order = orders.create(
        idempotency_key=f"{key_prefix}-order",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id) for entry in entries],
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
    return lab.id, order.id, practitioner.id, [item.id for item in order.items]


def _release_version_one(sessions: sessionmaker, *, key_prefix: str, item_count: int = 1):
    lab_id, order_id, practitioner_id, item_ids = _seed_in_progress_order(
        sessions, key_prefix=key_prefix, item_count=item_count
    )
    results = LabResultRepository(sessions)
    tech_id = _identity_id(sessions, f"{key_prefix}-tech")
    observations = []
    for index, item_id in enumerate(item_ids):
        observation = results.record_observation(
            order_id=order_id,
            lab_order_item_id=item_id,
            lab_organization_id=lab_id,
            result_type=LabResultType.NUMERIC,
            source_code=f"OBS-{index}",
            display_label_snapshot=f"Synthetic observation {index}",
            numeric_value=Decimal(index + 1),
            recorded_by_identity_id=tech_id,
        )
        observations.append(observation)
        results.complete_entry(
            order_id=order_id,
            lab_order_item_id=item_id,
            lab_organization_id=lab_id,
            completed_by_identity_id=tech_id,
        )
    releases = LabResultReleaseRepository(sessions)
    releases.release(
        order_id=order_id,
        lab_organization_id=lab_id,
        released_by_identity_id=_identity_id(sessions, f"{key_prefix}-releaser-v1"),
    )
    version_one = releases.get_latest_release_version(order_id)
    assert version_one is not None
    return lab_id, order_id, practitioner_id, item_ids, observations, version_one


def _prepared_amendment(sessions: sessionmaker, *, key_prefix: str):
    lab_id, order_id, practitioner_id, item_ids, originals, version_one = _release_version_one(
        sessions, key_prefix=key_prefix
    )
    amendments = LabResultAmendmentRepository(sessions)
    tech_id = _identity_id(sessions, f"{key_prefix}-amend-tech")
    amendment = amendments.start_amendment(
        order_id=order_id,
        lab_organization_id=lab_id,
        started_by_identity_id=tech_id,
    )
    value_two = amendments.correct_observation(
        amendment_id=amendment.id,
        order_id=order_id,
        lab_order_item_id=item_ids[0],
        lab_organization_id=lab_id,
        observation_id=originals[0].id,
        result_type=LabResultType.NUMERIC,
        source_code="OBS-0",
        display_label_snapshot="Synthetic observation 0",
        numeric_value=Decimal("2"),
        corrected_by_identity_id=tech_id,
    )
    return (
        amendments,
        lab_id,
        order_id,
        practitioner_id,
        item_ids[0],
        version_one,
        amendment,
        value_two,
    )


def test_concurrent_amendment_start_yields_one_preparing_episode(
    sessions: sessionmaker,
) -> None:
    key = f"pg14b-start-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, _practitioner_id, _item_ids, _observations, _version = _release_version_one(
        sessions, key_prefix=key
    )
    amendments = LabResultAmendmentRepository(sessions)
    actor_ids = [_identity_id(sessions, f"{key}-starter-{index}") for index in range(8)]
    barrier = threading.Barrier(len(actor_ids))
    outcomes: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt(actor_id) -> None:
        try:
            barrier.wait(timeout=15)
            amendments.start_amendment(
                order_id=order_id,
                lab_organization_id=lab_id,
                started_by_identity_id=actor_id,
            )
            outcome = "created"
        except AmendmentAlreadyInProgress:
            outcome = "conflict"
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)
            return
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=attempt, args=(actor_id,)) for actor_id in actor_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors
    assert outcomes.count("created") == 1
    assert outcomes.count("conflict") == 7
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(LabResultAmendment)
                .where(
                    LabResultAmendment.lab_order_id == order_id,
                    LabResultAmendment.status == "PREPARING",
                )
            )
            == 1
        )


def test_concurrent_corrections_yield_one_effective_successor(sessions: sessionmaker) -> None:
    key = f"pg14b-correct-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, _practitioner_id, item_ids, originals, _version = _release_version_one(
        sessions, key_prefix=key
    )
    amendments = LabResultAmendmentRepository(sessions)
    tech_id = _identity_id(sessions, f"{key}-tech")
    amendment = amendments.start_amendment(
        order_id=order_id, lab_organization_id=lab_id, started_by_identity_id=tech_id
    )
    barrier = threading.Barrier(6)
    outcomes: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt(index: int) -> None:
        try:
            barrier.wait(timeout=15)
            amendments.correct_observation(
                amendment_id=amendment.id,
                order_id=order_id,
                lab_order_item_id=item_ids[0],
                lab_organization_id=lab_id,
                observation_id=originals[0].id,
                result_type=LabResultType.NUMERIC,
                source_code="OBS-0",
                display_label_snapshot="Synthetic observation 0",
                numeric_value=Decimal(index + 2),
                corrected_by_identity_id=tech_id,
            )
            outcome = "created"
        except StaleObservationVersion:
            outcome = "stale"
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)
            return
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors
    assert outcomes.count("created") == 1
    assert outcomes.count("stale") == 5
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(LabObservation)
                .where(
                    LabObservation.amendment_id == amendment.id,
                    LabObservation.superseded_at.is_(None),
                )
            )
            == 1
        )


def test_concurrent_amendment_release_is_one_version_one_review_one_manifest(
    sessions: sessionmaker,
) -> None:
    key = f"pg14b-release-{uuid.uuid4().hex[:8]}"
    amendments, lab_id, order_id, _practitioner_id, _item_id, _v1, amendment, _value_two = (
        _prepared_amendment(sessions, key_prefix=key)
    )
    actor_ids = [_identity_id(sessions, f"{key}-releaser-{index}") for index in range(8)]
    barrier = threading.Barrier(len(actor_ids))
    outcomes: list[tuple[uuid.UUID, bool]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt(actor_id) -> None:
        try:
            barrier.wait(timeout=15)
            version, created = amendments.release_amendment(
                amendment_id=amendment.id,
                order_id=order_id,
                lab_organization_id=lab_id,
                released_by_identity_id=actor_id,
            )
            with lock:
                outcomes.append((version.id, created))
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(actor_id,)) for actor_id in actor_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors
    assert len({outcome[0] for outcome in outcomes}) == 1
    assert sum(outcome[1] for outcome in outcomes) == 1
    with sessions() as session:
        version_id = outcomes[0][0]
        assert (
            session.scalar(
                select(func.count())
                .select_from(LabResultReleaseVersion)
                .where(LabResultReleaseVersion.lab_order_id == order_id)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(DoctorReview)
                .where(DoctorReview.release_version_id == version_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(LabResultReleaseManifestEntry)
                .where(LabResultReleaseManifestEntry.release_version_id == version_id)
            )
            == 1
        )


def test_correction_racing_release_manifest_matches_the_winner(sessions: sessionmaker) -> None:
    key = f"pg14b-correct-release-{uuid.uuid4().hex[:8]}"
    amendments, lab_id, order_id, _practitioner_id, item_id, _v1, amendment, value_two = (
        _prepared_amendment(sessions, key_prefix=key)
    )
    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}
    lock = threading.Lock()

    def correct() -> None:
        try:
            barrier.wait(timeout=15)
            successor = amendments.correct_observation(
                amendment_id=amendment.id,
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                observation_id=value_two.id,
                result_type=LabResultType.NUMERIC,
                source_code="OBS-0",
                display_label_snapshot="Synthetic observation 0",
                numeric_value=Decimal("3"),
                corrected_by_identity_id=_identity_id(sessions, f"{key}-corrector"),
            )
            outcome = ("created", successor.id)
        except AmendmentNotPreparing:
            outcome = ("released", None)
        with lock:
            outcomes["correct"] = outcome

    def release() -> None:
        barrier.wait(timeout=15)
        version, _created = amendments.release_amendment(
            amendment_id=amendment.id,
            order_id=order_id,
            lab_organization_id=lab_id,
            released_by_identity_id=_identity_id(sessions, f"{key}-releaser"),
        )
        with lock:
            outcomes["release"] = version.id

    threads = [threading.Thread(target=correct), threading.Thread(target=release)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert set(outcomes) == {"correct", "release"}
    manifest = LabResultReleaseRepository(sessions).get_manifest_for_version(outcomes["release"])
    manifested = manifest.items[0].observations[0].id
    if outcomes["correct"][0] == "created":
        assert manifested == outcomes["correct"][1]
    else:
        assert manifested == value_two.id


def test_review_and_amendment_release_race_never_reviews_unseen_version(
    sessions: sessionmaker,
) -> None:
    key = f"pg14b-review-release-{uuid.uuid4().hex[:8]}"
    amendments, lab_id, order_id, practitioner_id, _item_id, v1, amendment, _value_two = (
        _prepared_amendment(sessions, key_prefix=key)
    )
    reviews = DoctorReviewRepository(sessions)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def legacy_review() -> None:
        try:
            barrier.wait(timeout=15)
            reviews.mark_no_follow_up_required(
                order_id, reviewed_by_practitioner_reference_id=practitioner_id
            )
            outcome = "reviewed-v1"
        except ExplicitReviewVersionRequired:
            outcome = "explicit-required"
        with lock:
            outcomes.append(outcome)

    def release() -> None:
        barrier.wait(timeout=15)
        amendments.release_amendment(
            amendment_id=amendment.id,
            order_id=order_id,
            lab_organization_id=lab_id,
            released_by_identity_id=_identity_id(sessions, f"{key}-releaser"),
        )
        with lock:
            outcomes.append("released-v2")

    threads = [threading.Thread(target=legacy_review), threading.Thread(target=release)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert "released-v2" in outcomes
    assert set(outcomes) <= {"released-v2", "reviewed-v1", "explicit-required"}
    history = reviews.get_history_for_order(order_id)
    assert len(history) == 2
    assert history[0].release_version_id == v1.id
    assert history[1].status == "PENDING"


def test_reader_never_observes_partial_amendment_manifest(sessions: sessionmaker) -> None:
    key = f"pg14b-reader-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, _practitioner_id, item_ids, originals, _v1 = _release_version_one(
        sessions, key_prefix=key, item_count=2
    )
    amendments = LabResultAmendmentRepository(sessions)
    tech_id = _identity_id(sessions, f"{key}-tech")
    amendment = amendments.start_amendment(
        order_id=order_id, lab_organization_id=lab_id, started_by_identity_id=tech_id
    )
    amendments.correct_observation(
        amendment_id=amendment.id,
        order_id=order_id,
        lab_order_item_id=item_ids[0],
        lab_organization_id=lab_id,
        observation_id=originals[0].id,
        result_type=LabResultType.NUMERIC,
        source_code="OBS-0",
        display_label_snapshot="Synthetic observation 0",
        numeric_value=Decimal("3"),
        corrected_by_identity_id=tech_id,
    )
    releases = LabResultReleaseRepository(sessions)
    stop = threading.Event()
    seen_counts: list[tuple[int, int]] = []

    def read() -> None:
        while not stop.is_set():
            manifest = releases.get_manifest_for_order(order_id)
            seen_counts.append((manifest.version.version_number, len(manifest.items)))
            time.sleep(0.001)

    reader = threading.Thread(target=read)
    reader.start()
    time.sleep(0.02)
    amendments.release_amendment(
        amendment_id=amendment.id,
        order_id=order_id,
        lab_organization_id=lab_id,
        released_by_identity_id=_identity_id(sessions, f"{key}-releaser"),
    )
    time.sleep(0.02)
    stop.set()
    reader.join(timeout=15)
    assert seen_counts
    assert all(item_count == 2 for _version, item_count in seen_counts)
    assert {version for version, _count in seen_counts} <= {1, 2}
