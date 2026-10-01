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
from exchange_support import lab_staff
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
    LabResult,
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
    ReviewAlreadyFinalized,
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
    workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=lab_staff(organizations, lab.id),
    )
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


def test_concurrent_independent_observation_corrections_both_succeed(
    sessions: sessionmaker,
) -> None:
    """DH-EX-014B Concurrency Matrix Scenario A:

    Concurrent independent observation corrections in the same amendment episode both succeed
    and are both reflected in the resulting released version manifest.
    """
    key = f"pg14b-indep-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, _practitioner_id, item_ids, originals, _v1 = _release_version_one(
        sessions, key_prefix=key, item_count=2
    )
    amendments = LabResultAmendmentRepository(sessions)
    tech_id = _identity_id(sessions, f"{key}-tech")
    amendment = amendments.start_amendment(
        order_id=order_id, lab_organization_id=lab_id, started_by_identity_id=tech_id
    )

    barrier = threading.Barrier(2)
    outcomes: dict[int, str] = {}
    errors: list[BaseException] = []
    lock = threading.Lock()

    def correct(index: int, item_id: uuid.UUID, orig_obs_id: uuid.UUID, val: Decimal) -> None:
        try:
            barrier.wait(timeout=15)
            amendments.correct_observation(
                amendment_id=amendment.id,
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                observation_id=orig_obs_id,
                result_type=LabResultType.NUMERIC,
                source_code=f"OBS-{index}",
                display_label_snapshot=f"Synthetic observation {index}",
                numeric_value=val,
                corrected_by_identity_id=tech_id,
            )
            with lock:
                outcomes[index] = "created"
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [
        threading.Thread(target=correct, args=(0, item_ids[0], originals[0].id, Decimal("100"))),
        threading.Thread(target=correct, args=(1, item_ids[1], originals[1].id, Decimal("200"))),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    assert outcomes == {0: "created", 1: "created"}

    # Release amendment
    version, created = amendments.release_amendment(
        amendment_id=amendment.id,
        order_id=order_id,
        lab_organization_id=lab_id,
        released_by_identity_id=_identity_id(sessions, f"{key}-releaser"),
    )
    assert created is True
    assert version.version_number == 2

    manifest = LabResultReleaseRepository(sessions).get_manifest_for_version(version.id)
    assert len(manifest.items) == 2
    item0_obs = manifest.items[0].observations[0]
    item1_obs = manifest.items[1].observations[0]
    assert item0_obs.numeric_value == Decimal("100")
    assert item1_obs.numeric_value == Decimal("200")
    assert item0_obs.amendment_id == amendment.id
    assert item1_obs.amendment_id == amendment.id


def test_second_amendment_start_racing_first_amendment_release_sequences_properly(
    sessions: sessionmaker,
) -> None:
    """DH-EX-014B Concurrency Matrix Scenario B:

    A second amendment start racing the release of an existing preparing amendment either gets
    cleanly rejected with AmendmentAlreadyInProgress (if it runs before release commits) or
    cleanly succeeds in starting the next episode based on Version 2 (if it runs after release
    commits). At no point do two preparing episodes coexist.
    """
    key = f"pg14b-seq-race-{uuid.uuid4().hex[:8]}"
    amendments, lab_id, order_id, _practitioner_id, _item_id, _v1, amendment, _value_two = (
        _prepared_amendment(sessions, key_prefix=key)
    )
    starter_tech_id = _identity_id(sessions, f"{key}-starter-tech")
    releaser_id = _identity_id(sessions, f"{key}-releaser")

    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}
    errors: list[BaseException] = []
    lock = threading.Lock()

    def do_release() -> None:
        try:
            barrier.wait(timeout=15)
            amendments.release_amendment(
                amendment_id=amendment.id,
                order_id=order_id,
                lab_organization_id=lab_id,
                released_by_identity_id=releaser_id,
            )
            with lock:
                outcomes["release"] = "released"
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    def do_start() -> None:
        try:
            barrier.wait(timeout=15)
            second_amend = amendments.start_amendment(
                order_id=order_id,
                lab_organization_id=lab_id,
                started_by_identity_id=starter_tech_id,
            )
            with lock:
                outcomes["start"] = f"created:{second_amend.id}"
        except AmendmentAlreadyInProgress:
            with lock:
                outcomes["start"] = "conflict"
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=do_release), threading.Thread(target=do_start)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    assert outcomes["release"] == "released"
    assert outcomes["start"] == "conflict" or outcomes["start"].startswith("created:")

    with sessions() as session:
        # Exactly one amendment is RELEASED (amendment 1)
        assert (
            session.scalar(
                select(func.count())
                .select_from(LabResultAmendment)
                .where(
                    LabResultAmendment.lab_order_id == order_id,
                    LabResultAmendment.status == "RELEASED",
                )
            )
            == 1
        )
        # PREPARING count is either 0 (if conflict) or 1 (if created after release)
        prep_count = session.scalar(
            select(func.count())
            .select_from(LabResultAmendment)
            .where(
                LabResultAmendment.lab_order_id == order_id,
                LabResultAmendment.status == "PREPARING",
            )
        )
        if outcomes["start"] == "conflict":
            assert prep_count == 0
        else:
            assert prep_count == 1
            # If created, its base_release_version_id must be Version 2
            v2 = session.scalar(
                select(LabResultReleaseVersion).where(
                    LabResultReleaseVersion.lab_order_id == order_id,
                    LabResultReleaseVersion.version_number == 2,
                )
            )
            assert v2 is not None
            created_amend_id = uuid.UUID(outcomes["start"].split(":")[1])
            created_amend = session.get(LabResultAmendment, created_amend_id)
            assert created_amend.base_release_version_id == v2.id


def test_sequential_amendment_chain_through_version_three(
    sessions: sessionmaker,
) -> None:
    """DH-EX-014B Concurrency Matrix Scenario C:

    Sequential amendment chain through Version 3 (V1 -> V2 -> V3) demonstrates:
    1. Base version linkage integrity (V2.base == V1, V3.base == V2).
    2. Exact manifest reconstruction for all 3 versions.
    3. Unchanged observations reused across versions without row duplication.
    4. Exact DoctorReview binding to each version in the chain.
    """
    key = f"pg14b-chain-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, _practitioner_id, item_ids, originals, v1 = _release_version_one(
        sessions, key_prefix=key, item_count=2
    )
    amendments = LabResultAmendmentRepository(sessions)
    releases = LabResultReleaseRepository(sessions)
    tech_id = _identity_id(sessions, f"{key}-tech")
    releaser_id = _identity_id(sessions, f"{key}-releaser")

    obs1_0 = originals[0].id
    obs1_1 = originals[1].id

    # Amendment 1: correct item 0 only -> releases Version 2
    amend_1 = amendments.start_amendment(
        order_id=order_id, lab_organization_id=lab_id, started_by_identity_id=tech_id
    )
    assert amend_1.base_release_version_id == v1.id
    corr_item0 = amendments.correct_observation(
        amendment_id=amend_1.id,
        order_id=order_id,
        lab_order_item_id=item_ids[0],
        lab_organization_id=lab_id,
        observation_id=obs1_0,
        result_type=LabResultType.NUMERIC,
        source_code="OBS-0",
        display_label_snapshot="Synthetic observation 0",
        numeric_value=Decimal("50"),
        corrected_by_identity_id=tech_id,
    )
    obs2_0 = corr_item0.id
    v2, _ = amendments.release_amendment(
        amendment_id=amend_1.id,
        order_id=order_id,
        lab_organization_id=lab_id,
        released_by_identity_id=releaser_id,
    )
    assert v2.version_number == 2

    # Amendment 2: correct item 1 only -> releases Version 3
    amend_2 = amendments.start_amendment(
        order_id=order_id, lab_organization_id=lab_id, started_by_identity_id=tech_id
    )
    assert amend_2.base_release_version_id == v2.id
    corr_item1 = amendments.correct_observation(
        amendment_id=amend_2.id,
        order_id=order_id,
        lab_order_item_id=item_ids[1],
        lab_organization_id=lab_id,
        observation_id=obs1_1,
        result_type=LabResultType.NUMERIC,
        source_code="OBS-1",
        display_label_snapshot="Synthetic observation 1",
        numeric_value=Decimal("75"),
        corrected_by_identity_id=tech_id,
    )
    obs3_1 = corr_item1.id
    v3, _ = amendments.release_amendment(
        amendment_id=amend_2.id,
        order_id=order_id,
        lab_organization_id=lab_id,
        released_by_identity_id=releaser_id,
    )
    assert v3.version_number == 3

    # Verify manifest reconstructions
    m1 = releases.get_manifest_for_version(v1.id)
    m2 = releases.get_manifest_for_version(v2.id)
    m3 = releases.get_manifest_for_version(v3.id)

    # Verify manifest reconstructions by item_id
    m1_by_item = {item.item_id: [o.id for o in item.observations] for item in m1.items}
    m2_by_item = {item.item_id: [o.id for o in item.observations] for item in m2.items}
    m3_by_item = {item.item_id: [o.id for o in item.observations] for item in m3.items}

    # V1: [obs1_0, obs1_1]
    assert m1_by_item[item_ids[0]] == [obs1_0]
    assert m1_by_item[item_ids[1]] == [obs1_1]

    # V2: [obs2_0 (new), obs1_1 (reused from V1)]
    assert m2_by_item[item_ids[0]] == [obs2_0]
    assert m2_by_item[item_ids[1]] == [obs1_1]

    # V3: [obs2_0 (reused from V2), obs3_1 (new)]
    assert m3_by_item[item_ids[0]] == [obs2_0]
    assert m3_by_item[item_ids[1]] == [obs3_1]

    # Check total observation rows in database: exactly 4 (2 original + 2 corrections),
    # ZERO duplication
    with sessions() as session:
        all_obs = session.scalars(
            select(LabObservation.id)
            .join(LabResult, LabObservation.lab_result_id == LabResult.id)
            .where(LabResult.lab_order_item_id.in_(item_ids))
        ).all()
        assert len(all_obs) == 4
        assert set(all_obs) == {obs1_0, obs1_1, obs2_0, obs3_1}

    # Verify history with reviews returns 3 versions and each has its review
    history_with_reviews = releases.get_release_version_history_with_reviews(order_id)
    assert len(history_with_reviews) == 3
    for ver, rev in history_with_reviews:
        assert rev is not None
        assert rev.release_version_id == ver.id


def test_racing_clinician_reviews_for_same_amended_version_one_wins_one_refused(
    sessions: sessionmaker,
) -> None:
    """DH-EX-014B Concurrency Matrix Scenario D:

    Two clinician review attempts racing for the same amended release version (Version 2):
    exactly one wins and finalizes the review, the second is refused with ReviewAlreadyFinalized.
    """
    key = f"pg14b-review-race-{uuid.uuid4().hex[:8]}"
    amendments, lab_id, order_id, practitioner_id, _item_id, _v1, amendment, _value_two = (
        _prepared_amendment(sessions, key_prefix=key)
    )
    v2, _ = amendments.release_amendment(
        amendment_id=amendment.id,
        order_id=order_id,
        lab_organization_id=lab_id,
        released_by_identity_id=_identity_id(sessions, f"{key}-releaser"),
    )
    reviews = DoctorReviewRepository(sessions)

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def review_no_follow_up() -> None:
        try:
            barrier.wait(timeout=15)
            reviews.mark_no_follow_up_required_for_release_version(
                order_id,
                v2.id,
                reviewed_by_practitioner_reference_id=practitioner_id,
            )
            with lock:
                outcomes.append("reviewed_no_follow_up")
        except ReviewAlreadyFinalized:
            with lock:
                outcomes.append("conflict_already_finalized")
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    def review_follow_up_required() -> None:
        try:
            barrier.wait(timeout=15)
            reviews.mark_follow_up_required_for_release_version(
                order_id,
                v2.id,
                reviewed_by_practitioner_reference_id=practitioner_id,
            )
            with lock:
                outcomes.append("reviewed_follow_up_required")
        except ReviewAlreadyFinalized:
            with lock:
                outcomes.append("conflict_already_finalized")
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [
        threading.Thread(target=review_no_follow_up),
        threading.Thread(target=review_follow_up_required),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    assert len(outcomes) == 2
    # Exactly one winner, exactly one conflict
    winners = [o for o in outcomes if o.startswith("reviewed_")]
    conflicts = [o for o in outcomes if o == "conflict_already_finalized"]
    assert len(winners) == 1
    assert len(conflicts) == 1

    # Check database: exactly one review row for V2, and its status matches the winning action
    with sessions() as session:
        v2_review = session.scalar(
            select(DoctorReview).where(DoctorReview.release_version_id == v2.id)
        )
        assert v2_review is not None
        if winners[0] == "reviewed_no_follow_up":
            assert v2_review.status == "NO_FOLLOW_UP_REQUIRED"
        else:
            assert v2_review.status == "FOLLOW_UP_REQUIRED"
