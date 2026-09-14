"""DH-EX-014A/ADR-048 real PostgreSQL qualification: release-version manifest and versioned
``DoctorReview`` concurrency/atomicity claims. SQLite is not sufficient evidence for production
PostgreSQL row-locking behavior -- matching the reasoning ``test_dhex013_postgres_qualification.py``
already established, applied here to ``exchange/results.py``'s new
``LabResultReleaseRepository.release`` (version + manifest + review-binding) and
``DoctorReviewRepository``'s version-aware resolution.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHEX014A_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHEX014A_PG_HOST=127.0.0.1 \\
    DHEX014A_PG_PORT=5432 \\
    DHEX014A_PG_USER=dhex014a_c \\
    DHEX014A_PG_PASSWORD=dhex014a_c \\
    DHEX014A_PG_DATABASE=dhex014a_concurrency_qual \\
        pytest adapter/tests/integration/test_dhex014a_postgres_qualification.py -v

The target database must already have migrations applied through ``0018_exchange_release_versions``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.
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
    LabOrder,
    LabPrice,
    LabResultRelease,
    LabResultReleaseVersion,
    LabResultType,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    DoctorReviewRepository,
    InvalidResultSubmissionState,
    LabResultReleaseRepository,
    LabResultRepository,
    ReviewAlreadyFinalized,
)
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHEX014A_PG_HOST")
PG_PORT = os.environ.get("DHEX014A_PG_PORT")
PG_USER = os.environ.get("DHEX014A_PG_USER")
PG_PASSWORD = os.environ.get("DHEX014A_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHEX014A_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHEX014A_PG_* env vars) -- never in CI",
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
        issuer="https://pg14a-qualification.example", subject=subject
    )
    return identity.id


def _seed_in_progress_order(sessions: sessionmaker, *, key_prefix: str, item_count: int = 1):
    """Identical shape to `test_dhex013_postgres_qualification.py`'s own fixture."""
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
            code=f"{key_prefix}-TEST-{i}", display_name=f"DH-EX-014A test {i}"
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
        source_system="pg14a-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-1",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="pg14a-qualification",
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


def test_original_release_race_yields_one_version_one_manifest_one_bound_review(
    sessions: sessionmaker,
) -> None:
    """Ten concurrent `release()` calls for the same, freshly-completed order: exactly one
    `LabResultRelease`, one Release Version 1, one exact manifest, and one bound `DoctorReview`
    result -- every other caller must observe a safe idempotent replay (`created=False`,
    identical provenance), never a duplicate version or a second manifest."""
    key_prefix = f"pg14a-releaserace-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(sessions, key_prefix=key_prefix)
    results = LabResultRepository(sessions)
    releases = LabResultReleaseRepository(sessions)
    reviews = DoctorReviewRepository(sessions)
    results.record_observation(
        order_id=order_id,
        lab_order_item_id=item_ids[0],
        lab_organization_id=lab_id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot="Glucose",
        numeric_value=Decimal("5.1"),
        recorded_by_identity_id=_identity_id(sessions, "pg14a-seed-tech"),
    )
    results.complete_entry(
        order_id=order_id,
        lab_order_item_id=item_ids[0],
        lab_organization_id=lab_id,
        completed_by_identity_id=_identity_id(sessions, "pg14a-seed-tech"),
    )

    n = 10
    outcomes: list[tuple[uuid.UUID, bool, uuid.UUID]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def attempt(i: int) -> None:
        try:
            releaser = _identity_id(sessions, f"pg14a-releaser-{i}")
            barrier.wait(timeout=15)
            record, created = releases.release(
                order_id=order_id, lab_organization_id=lab_id, released_by_identity_id=releaser
            )
            with lock:
                outcomes.append((record.released_by_identity_id, created, record.id))
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"release must never raise unexpectedly under a pure race: {errors!r}"
    assert len(outcomes) == n
    winners = [o for o in outcomes if o[1] is True]
    assert len(winners) == 1, f"exactly one release call may create the release, got {winners!r}"
    release_ids = {o[2] for o in outcomes}
    assert len(release_ids) == 1, "every caller must observe the identical release id"
    releaser_ids = {o[0] for o in outcomes}
    assert len(releaser_ids) == 1, (
        "every caller (winner and idempotent replays alike) must see the same original releaser"
    )

    with sessions() as session:
        version_count = session.scalar(
            select(func.count())
            .select_from(LabResultReleaseVersion)
            .where(LabResultReleaseVersion.lab_order_id == order_id)
        )
        assert version_count == 1, "exactly one release version may exist for this order"

    manifest = releases.get_manifest_for_order(order_id)
    assert manifest is not None
    assert manifest.version.version_number == 1
    assert len(manifest.items) == 1
    assert len(manifest.items[0].observations) == 1

    review = reviews.get_for_order(order_id)
    assert review.release_version_id == manifest.version.id


def test_correction_vs_release_race_manifest_reflects_whichever_won(
    sessions: sessionmaker,
) -> None:
    """DH-EX-013's own correction-vs-release race, re-qualified against DH-EX-014A's manifest: if
    correction wins, the manifest (once release eventually succeeds) must contain the *corrected*
    observation id; if release wins first, the correction must be refused as post-release and the
    manifest must contain the *original* observation id -- never a manifest silently mismatched
    from whichever observation was actually effective when release ran."""
    key_prefix = f"pg14a-correctvsrelease-{uuid.uuid4().hex[:8]}"
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
        recorded_by_identity_id=_identity_id(sessions, "pg14a-seed-tech"),
    )
    results.complete_entry(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        completed_by_identity_id=_identity_id(sessions, "pg14a-seed-tech"),
    )

    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}
    lock = threading.Lock()

    def do_correct() -> None:
        try:
            barrier.wait(timeout=15)
            successor = results.correct_observation(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                observation_id=original.id,
                result_type=LabResultType.NUMERIC,
                source_code="NA",
                display_label_snapshot="Sodium",
                numeric_value=Decimal("141"),
                corrected_by_identity_id=_identity_id(sessions, "pg14a-corrector"),
            )
            with lock:
                outcomes["correct"] = ("ok", successor.id)
        except InvalidResultSubmissionState:
            with lock:
                outcomes["correct"] = ("refused", None)

    def do_release() -> None:
        try:
            barrier.wait(timeout=15)
            record, created = releases.release(
                order_id=order_id,
                lab_organization_id=lab_id,
                released_by_identity_id=_identity_id(sessions, "pg14a-releaser"),
            )
            with lock:
                outcomes["release"] = ("ok", record.id, created)
        except BaseException as error:  # noqa: BLE001
            with lock:
                outcomes["release"] = ("error", error, None)

    t1 = threading.Thread(target=do_correct)
    t2 = threading.Thread(target=do_release)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert "correct" in outcomes and "release" in outcomes
    assert outcomes["release"][0] == "ok", f"release must not error: {outcomes['release']!r}"

    manifest = releases.get_manifest_for_order(order_id)
    assert manifest is not None
    manifest_observation_ids = {o.id for o in manifest.items[0].observations}
    assert len(manifest_observation_ids) == 1

    if outcomes["correct"][0] == "ok":
        # Correction won the race -- release must have seen and manifested the corrected value.
        assert manifest_observation_ids == {outcomes["correct"][1]}
        assert manifest_observation_ids != {original.id}
    else:
        # Release won the race -- correction was refused, manifest names the original observation.
        assert outcomes["correct"][0] == "refused"
        assert manifest_observation_ids == {original.id}


def test_provider_manifest_read_never_observes_partial_release(sessions: sessionmaker) -> None:
    """While a multi-item order's release is being created, a concurrent reader polling
    `get_manifest_for_order` must never observe a release version whose manifest is missing an
    item -- PostgreSQL's own transaction isolation must make the four-part release commit
    atomic from a reader's point of view, exactly as `LabResultReleaseRepository.release`'s own
    docstring claims."""
    key_prefix = f"pg14a-partialread-{uuid.uuid4().hex[:8]}"
    item_count = 5
    lab_id, order_id, item_ids = _seed_in_progress_order(
        sessions, key_prefix=key_prefix, item_count=item_count
    )
    results = LabResultRepository(sessions)
    releases = LabResultReleaseRepository(sessions)
    for i, item_id in enumerate(item_ids):
        results.record_observation(
            order_id=order_id,
            lab_order_item_id=item_id,
            lab_organization_id=lab_id,
            result_type=LabResultType.NUMERIC,
            source_code=f"OBS{i}",
            display_label_snapshot=f"Observation {i}",
            numeric_value=Decimal(f"{i}.0"),
            recorded_by_identity_id=_identity_id(sessions, "pg14a-seed-tech"),
        )
        results.complete_entry(
            order_id=order_id,
            lab_order_item_id=item_id,
            lab_organization_id=lab_id,
            completed_by_identity_id=_identity_id(sessions, "pg14a-seed-tech"),
        )

    stop = threading.Event()
    violations: list[str] = []
    read_count = 0
    lock = threading.Lock()

    def poll_reader() -> None:
        nonlocal read_count
        while not stop.is_set():
            manifest = releases.get_manifest_for_order(order_id)
            with lock:
                read_count += 1
            if manifest is not None and len(manifest.items) != item_count:
                with lock:
                    violations.append(
                        f"observed a release version with {len(manifest.items)} manifest "
                        f"items, expected 0 (no version yet) or exactly {item_count}"
                    )
            time.sleep(0.001)

    reader = threading.Thread(target=poll_reader)
    reader.start()
    time.sleep(0.02)
    releases.release(
        order_id=order_id,
        lab_organization_id=lab_id,
        released_by_identity_id=_identity_id(sessions, "pg14a-releaser"),
    )
    time.sleep(0.02)
    stop.set()
    reader.join(timeout=15)

    assert read_count > 0, "the polling reader must have actually run at least once"
    assert not violations, f"reader observed a partially-written manifest: {violations!r}"

    manifest = releases.get_manifest_for_order(order_id)
    assert manifest is not None
    assert len(manifest.items) == item_count


def test_two_clinicians_racing_the_same_version_review_preserve_one_terminal_disposition(
    sessions: sessionmaker,
) -> None:
    """Two concurrent review submissions for the same order (bound to the same Version 1) --
    exactly one may succeed; the other must observe `ReviewAlreadyFinalized`, never two terminal
    dispositions or a silently overwritten one."""
    key_prefix = f"pg14a-reviewrace-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_ids = _seed_in_progress_order(sessions, key_prefix=key_prefix)
    results = LabResultRepository(sessions)
    releases = LabResultReleaseRepository(sessions)
    reviews = DoctorReviewRepository(sessions)
    results.record_observation(
        order_id=order_id,
        lab_order_item_id=item_ids[0],
        lab_organization_id=lab_id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot="Glucose",
        numeric_value=Decimal("5.1"),
        recorded_by_identity_id=_identity_id(sessions, "pg14a-seed-tech"),
    )
    results.complete_entry(
        order_id=order_id,
        lab_order_item_id=item_ids[0],
        lab_organization_id=lab_id,
        completed_by_identity_id=_identity_id(sessions, "pg14a-seed-tech"),
    )
    releases.release(
        order_id=order_id,
        lab_organization_id=lab_id,
        released_by_identity_id=_identity_id(sessions, "pg14a-releaser"),
    )
    with sessions() as session:
        practitioner_reference_id = session.get(LabOrder, order_id).practitioner_reference_id

    n = 8
    outcomes: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            if i % 2 == 0:
                reviews.mark_no_follow_up_required(
                    order_id, reviewed_by_practitioner_reference_id=practitioner_reference_id
                )
            else:
                reviews.mark_follow_up_required(
                    order_id, reviewed_by_practitioner_reference_id=practitioner_reference_id
                )
            with lock:
                outcomes.append("ok")
        except ReviewAlreadyFinalized:
            with lock:
                outcomes.append("already_finalized")
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"review marking must never raise unexpectedly: {errors!r}"
    assert len(outcomes) == n
    winners = [o for o in outcomes if o == "ok"]
    assert len(winners) == 1, f"exactly one review submission may win the race, got {outcomes!r}"

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        release = session.scalar(
            select(LabResultRelease).where(LabResultRelease.lab_order_id == order.id)
        )
        assert release is not None
    review = reviews.get_for_order(order_id)
    assert review.status != "PENDING"
