"""DH-CLINIC-007 real-PostgreSQL concurrency qualification.

The version-specific review row-locking behavior itself (first-terminal-review-wins,
``ReviewAlreadyFinalized`` on every later caller, no last-write-wins, review rows never migrate
between release versions) is the exact same ``DoctorReviewRepository._mark``/``_mark_in_session``
and ``LabResultAmendmentRepository.release_amendment`` machinery already qualified against real
PostgreSQL by ``test_dhex014a_postgres_qualification.py`` and
``test_dhex014b_postgres_qualification.py`` -- DH-CLINIC-007 added no new locking or mutation code,
only the read-only provider review queue (``DoctorReviewRepository.list_queue_for_provider``).

- PG-R1 (opposite dispositions racing the same version, exactly one terminal disposition wins) is
  ``test_racing_clinician_reviews_for_same_amended_version_one_wins_one_refused`` in the 014B file.
- PG-R3 (review of Version 1 racing an amendment's Version 2 release, no review ever migrates
  between versions) is ``test_review_and_amendment_release_race_never_reviews_unseen_version`` in
  the same file.

This file adds the one scenario neither of those isolates -- PG-R2, *identical* dispositions
racing the same version, verifying the winning reviewer's identity/timestamp is never silently
overwritten by a later identical-status caller -- plus a same-provider queue-count sanity check
appended after the dust settles, run against the exact schema DH-CLINIC-007 shipped against.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHCLINIC007_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHCLINIC007_PG_HOST=127.0.0.1 \\
    DHCLINIC007_PG_PORT=5432 \\
    DHCLINIC007_PG_USER=dhclinic007_c \\
    DHCLINIC007_PG_PASSWORD=dhclinic007_c \\
    DHCLINIC007_PG_DATABASE=dhclinic007_concurrency_qual \\
        pytest adapter/tests/integration/test_dhclinic007_postgres_qualification.py -v

The target database must already have migrations applied through ``0023_provider_lab_routes``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from threading import Barrier, Lock, Thread

import pytest
from exchange_support import lab_staff
from sqlalchemy import create_engine, select
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
    DoctorReviewStatus,
    LabOrder,
    LabOrderStatus,
    LabPrice,
    LabResultType,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    DoctorReviewRepository,
    LabResultAmendmentRepository,
    LabResultReleaseRepository,
    LabResultRepository,
    ReviewAlreadyFinalized,
)
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHCLINIC007_PG_HOST")
PG_PORT = os.environ.get("DHCLINIC007_PG_PORT")
PG_USER = os.environ.get("DHCLINIC007_PG_USER")
PG_PASSWORD = os.environ.get("DHCLINIC007_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHCLINIC007_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a disposable PostgreSQL database (DHCLINIC007_PG_*) -- never in CI",
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
        issuer="https://dhclinic007-qualification.example", subject=subject
    )
    return identity.id


def _prepared_amendment_with_distinct_practitioners(
    sessions: sessionmaker, *, key_prefix: str, n: int
):
    """A released Version 1, a released Version 2 (amendment), and `n` distinct
    PractitionerReference rows under the same provider -- used to make an identical-disposition
    race's "who actually authored the terminal row" assertion meaningful rather than trivially
    true (a single shared practitioner id would prove nothing about provenance)."""
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
    entry, _ = catalog.create_entry(code=f"{key_prefix}-TEST", display_name="Synthetic test")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("100.00"),
        currency="DJF",
        expected_current_price_id=None,
    )
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
        source_system="dhclinic007-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-1",
    )
    ordering_practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="dhclinic007-qualification",
        source_identity_system=f"urn:{key_prefix}:practitioner",
        source_identity_value="D-ordering",
    )
    order = orders.create(
        idempotency_key=f"{key_prefix}-order",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=ordering_practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order
    with sessions() as session:
        price_id = session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering.id, LabPrice.superseded_at.is_(None)
            )
        )
    selections.select(
        lab_order_id=order.id,
        lab_organization_id=lab.id,
        expected_price_ids=frozenset({price_id}),
    )
    workflow.accept(order_id=order.id, lab_organization_id=lab.id)
    workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=lab_staff(organizations, lab.id),
    )
    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)
    order = orders.get(order.id)
    item_id = order.items[0].id

    results = LabResultRepository(sessions)
    tech_id = _identity_id(sessions, f"{key_prefix}-tech")
    original = results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="OBS-0",
        display_label_snapshot="Synthetic observation",
        numeric_value=Decimal("1.0"),
        recorded_by_identity_id=tech_id,
    )
    results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=tech_id,
    )
    releases = LabResultReleaseRepository(sessions)
    releases.release(
        order_id=order.id,
        lab_organization_id=lab.id,
        released_by_identity_id=_identity_id(sessions, f"{key_prefix}-releaser-v1"),
    )

    amendments = LabResultAmendmentRepository(sessions)
    amendment = amendments.start_amendment(
        order_id=order.id, lab_organization_id=lab.id, started_by_identity_id=tech_id
    )
    amendments.correct_observation(
        amendment_id=amendment.id,
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        observation_id=original.id,
        result_type=LabResultType.NUMERIC,
        source_code="OBS-0",
        display_label_snapshot="Synthetic observation",
        numeric_value=Decimal("2.0"),
        corrected_by_identity_id=tech_id,
    )
    v2, _ = amendments.release_amendment(
        amendment_id=amendment.id,
        order_id=order.id,
        lab_organization_id=lab.id,
        released_by_identity_id=_identity_id(sessions, f"{key_prefix}-releaser-v2"),
    )

    reviewer_ids = [
        practitioners.get_or_create(
            source_organization_id=provider.id,
            source_system="dhclinic007-qualification",
            source_identity_system=f"urn:{key_prefix}:reviewer",
            source_identity_value=f"D-reviewer-{i}",
        ).id
        for i in range(n)
    ]
    return provider.id, order.id, v2.id, reviewer_ids


def test_pgr2_identical_disposition_race_preserves_single_authoritative_provenance(
    sessions: sessionmaker,
) -> None:
    """PG-R2: `n` distinct clinicians concurrently submit the *same* disposition
    (NO_FOLLOW_UP_REQUIRED) for the same amended release version. Exactly one may actually author
    the terminal row; every other caller must observe `ReviewAlreadyFinalized` -- the final row's
    `reviewed_by_practitioner_reference_id`/`reviewed_at` must match only the one caller whose
    mutation actually committed, never get silently rewritten by a later identical-status replay,
    and exactly one `DoctorReview` row must exist for this version (no duplicate row from a
    race)."""
    key = f"dhclinic007-pgr2-{uuid.uuid4().hex[:8]}"
    n = 6
    _provider_id, order_id, v2_id, reviewer_ids = _prepared_amendment_with_distinct_practitioners(
        sessions, key_prefix=key, n=n
    )
    reviews = DoctorReviewRepository(sessions)

    barrier = Barrier(n)
    outcomes: list[tuple[int, str]] = []
    errors: list[BaseException] = []
    lock = Lock()

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            reviews.mark_no_follow_up_required_for_release_version(
                order_id, v2_id, reviewed_by_practitioner_reference_id=reviewer_ids[i]
            )
            with lock:
                outcomes.append((i, "ok"))
        except ReviewAlreadyFinalized:
            with lock:
                outcomes.append((i, "conflict"))
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [Thread(target=attempt, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, (
        f"identical-disposition review marking must never raise unexpectedly: {errors!r}"
    )
    assert len(outcomes) == n
    winners = [i for i, outcome in outcomes if outcome == "ok"]
    conflicts = [i for i, outcome in outcomes if outcome == "conflict"]
    assert len(winners) == 1, (
        f"exactly one identical-disposition submission may win, got {outcomes!r}"
    )
    assert len(conflicts) == n - 1
    winning_reviewer_id = reviewer_ids[winners[0]]

    with sessions() as session:
        rows = session.scalars(
            select(DoctorReview).where(DoctorReview.release_version_id == v2_id)
        ).all()
        assert len(rows) == 1, (
            "exactly one DoctorReview row may exist for this version -- no duplicates"
        )
        row = rows[0]
        assert row.status == "NO_FOLLOW_UP_REQUIRED"
        assert row.reviewed_at is not None
        assert row.reviewed_by_practitioner_reference_id == winning_reviewer_id, (
            "provenance must belong to whichever caller's mutation actually committed first, "
            "never to an arbitrary or later identical-status caller"
        )


def test_queue_count_after_pgr2_race_matches_ground_truth(sessions: sessionmaker) -> None:
    """Sanity check that DH-CLINIC-007's own read-only addition (`list_queue_for_provider`)
    reflects the exact post-race ground truth: Version 2's review is now terminal (excluded from
    the PENDING queue), and the queue is never left showing a torn or duplicated state after the
    race above resolves."""
    key = f"dhclinic007-queuecheck-{uuid.uuid4().hex[:8]}"
    provider_id, order_id, v2_id, reviewer_ids = _prepared_amendment_with_distinct_practitioners(
        sessions, key_prefix=key, n=4
    )
    reviews = DoctorReviewRepository(sessions)

    barrier = Barrier(4)
    lock = Lock()
    outcomes: list[str] = []

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            reviews.mark_no_follow_up_required_for_release_version(
                order_id, v2_id, reviewed_by_practitioner_reference_id=reviewer_ids[i]
            )
            with lock:
                outcomes.append("ok")
        except ReviewAlreadyFinalized:
            with lock:
                outcomes.append("conflict")

    threads = [Thread(target=attempt, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert outcomes.count("ok") == 1

    with sessions() as session:
        assert session.get(LabOrder, order_id).status == LabOrderStatus.COMPLETED.value

    pending = reviews.list_queue_for_provider(provider_id, status_filter=None, limit=50)
    pending_only = reviews.list_queue_for_provider(
        provider_id, status_filter=DoctorReviewStatus.PENDING, limit=50
    )
    v2_entries = [e for e in pending if e.release_version_id == v2_id]
    assert len(v2_entries) == 1, (
        "the queue must show exactly one entry for this version, never duplicated"
    )
    assert v2_entries[0].review_status == "NO_FOLLOW_UP_REQUIRED"
    assert all(e.release_version_id != v2_id for e in pending_only), (
        "a now-terminal version must never still appear under the PENDING-only filter"
    )
    # Version 1's own review is untouched and remains its own separate, independent obligation.
    v1_entries = [e for e in pending if e.release_version_id != v2_id]
    assert len(v1_entries) == 1
