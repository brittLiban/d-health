from dataclasses import dataclass
from decimal import Decimal

import pytest
from exchange_support import (
    make_lab,
    make_patient_reference,
    make_practitioner_reference,
    make_priced_offering,
    make_provider,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import LabOrderNotAssigned, LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import DoctorReviewStatus, LabPrice, ResultDeliveryStatus
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    DoctorReviewRepository,
    DuplicateResult,
    InvalidResultSubmissionState,
    LabResultReleaseRepository,
    LabResultRepository,
    LegacyCompletedOrderHasNoRelease,
    OrderItemNotFound,
    ResultDeliveryRepository,
    ResultsNotReadyForRelease,
    ReviewAlreadyFinalized,
    ReviewNotAllowedYet,
)
from daher_adapter.exchange.selection import LabSelectionRepository


@dataclass(frozen=True, slots=True)
class Repos:
    organizations: OrganizationRepository
    catalog: CatalogRepository
    patients: PatientReferenceRepository
    practitioners: PractitionerReferenceRepository
    orders: LabOrderRepository
    selections: LabSelectionRepository
    workflow: LabWorkflowRepository
    results: LabResultRepository
    releases: LabResultReleaseRepository
    deliveries: ResultDeliveryRepository
    reviews: DoctorReviewRepository
    directory: LabDirectoryProfileRepository
    identities: ExchangeIdentityRepository


def _build(sessions: sessionmaker[Session]) -> Repos:
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    return Repos(
        organizations=organizations,
        catalog=catalog,
        patients=PatientReferenceRepository(sessions),
        practitioners=PractitionerReferenceRepository(sessions),
        orders=LabOrderRepository(sessions, organizations, catalog),
        selections=LabSelectionRepository(sessions, organizations),
        workflow=LabWorkflowRepository(sessions, organizations),
        results=LabResultRepository(sessions),
        releases=LabResultReleaseRepository(sessions),
        deliveries=ResultDeliveryRepository(sessions),
        reviews=DoctorReviewRepository(sessions),
        directory=LabDirectoryProfileRepository(sessions, organizations),
        identities=ExchangeIdentityRepository(sessions),
    )


def _release(repos: Repos, *, order_id, lab_id, key: str):
    """DH-EX-012/ADR-044: bootstraps a fresh LAB_RESULT_RELEASER identity and releases the order's
    results -- the sole supported production way to reach COMPLETED after this ticket. A separate
    identity from whichever LAB_TECH entered the results, exercising the un-forced two-person path
    ADR-044 permits without requiring (the dual-role same-human case has its own dedicated test)."""
    releaser, _ = repos.identities.get_or_create(
        issuer="https://test.example", subject=f"releaser-{key}"
    )
    return repos.releases.release(
        order_id=order_id, lab_organization_id=lab_id, released_by_identity_id=releaser.id
    )


def _current_price_id(sessions: sessionmaker[Session], offering_id) -> object:
    with sessions() as session:
        return session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering_id, LabPrice.superseded_at.is_(None)
            )
        )


def _create_and_select(
    repos: Repos,
    sessions: sessionmaker[Session],
    *,
    lab_id,
    key: str = "req-1",
    advance_to_in_progress: bool = True,
):
    """Creates one priced offering, a one-item order against it, and immediately selects
    `lab_id`. By default also drives the order through ACCEPTED -> IN_PROGRESS via the exact
    DH-EX-006 workflow repository, since DH-EX-007 requires IN_PROGRESS before any result may be
    recorded -- a test that wants an earlier status passes `advance_to_in_progress=False` and
    stops at SELECTED. Returns (refreshed order, ordering practitioner record)."""
    repos.directory.upsert(
        lab_organization_id=lab_id,
        public_address="Test Lab Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    entry, offering = make_priced_offering(
        repos.catalog, lab_organization_id=lab_id, amount=Decimal("500.00"), code=f"DH-{key}"
    )
    provider = make_provider(repos.organizations, name=f"Clinic-{key}")
    patient = make_patient_reference(
        repos.patients, provider_org_id=provider.id, source_identity_value=key
    )
    practitioner = make_practitioner_reference(
        repos.practitioners, provider_org_id=provider.id, source_identity_value=f"doc-{key}"
    )
    order = repos.orders.create(
        idempotency_key=key,
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order
    price_id = _current_price_id(sessions, offering.id)
    repos.selections.select(
        lab_order_id=order.id, lab_organization_id=lab_id, expected_price_ids=frozenset({price_id})
    )
    if advance_to_in_progress:
        repos.workflow.accept(order_id=order.id, lab_organization_id=lab_id)
        # DH-EX-011: start_processing now requires a recorded collection milestone.
        repos.workflow.record_collection(order_id=order.id, lab_organization_id=lab_id)
        repos.workflow.start_processing(order_id=order.id, lab_organization_id=lab_id)
    return repos.orders.get(order.id), practitioner


def _create_and_select_two_items(
    repos: Repos, sessions: sessionmaker[Session], *, lab_id, key: str
):
    """Same shape as `_create_and_select`, but a two-item order -- needed by any test that wants
    to observe `ready_for_release=False` after only one of two results has been recorded (a
    one-item order is always `ready_for_release=True` the instant its only result is recorded)."""
    repos.directory.upsert(
        lab_organization_id=lab_id,
        public_address="Test Lab Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    entry_1, offering_1 = make_priced_offering(
        repos.catalog, lab_organization_id=lab_id, amount=Decimal("100"), code=f"DH-{key}-1"
    )
    entry_2, offering_2 = make_priced_offering(
        repos.catalog, lab_organization_id=lab_id, amount=Decimal("200"), code=f"DH-{key}-2"
    )
    provider = make_provider(repos.organizations, name=f"Clinic-{key}")
    patient = make_patient_reference(
        repos.patients, provider_org_id=provider.id, source_identity_value=key
    )
    practitioner = make_practitioner_reference(
        repos.practitioners, provider_org_id=provider.id, source_identity_value=f"doc-{key}"
    )
    order = repos.orders.create(
        idempotency_key=key,
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[
            OrderItemInput(catalog_entry_id=entry_1.id),
            OrderItemInput(catalog_entry_id=entry_2.id),
        ],
    ).order
    price_1 = _current_price_id(sessions, offering_1.id)
    price_2 = _current_price_id(sessions, offering_2.id)
    repos.selections.select(
        lab_order_id=order.id,
        lab_organization_id=lab_id,
        expected_price_ids=frozenset({price_1, price_2}),
    )
    repos.workflow.accept(order_id=order.id, lab_organization_id=lab_id)
    # DH-EX-011: start_processing now requires a recorded collection milestone.
    repos.workflow.record_collection(order_id=order.id, lab_organization_id=lab_id)
    repos.workflow.start_processing(order_id=order.id, lab_organization_id=lab_id)
    return repos.orders.get(order.id), practitioner


def test_result_binds_to_the_correct_order_item_and_lab(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id)
    item_id = order.items[0].id

    outcome = repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        value=Decimal("13.5"),
        unit="g/dL",
    )

    assert outcome.result.lab_order_item_id == item_id
    assert outcome.result.lab_organization_id == lab.id
    # DH-EX-012/ADR-044: recording even a one-item order's sole result no longer completes it --
    # `ready_for_release` reports the same "every item now has a result" fact, but only an
    # explicit `LabResultReleaseRepository.release` call may act on it.
    assert outcome.ready_for_release is True
    assert outcome.order_status == "IN_PROGRESS"
    assert repos.results.get_for_item(item_id) is not None


def test_result_from_a_different_lab_than_selected_fails_closed(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab_selected = make_lab(repos.organizations, name="Lab Selected")
    lab_impostor = make_lab(repos.organizations, name="Lab Impostor")
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab_selected.id)
    item_id = order.items[0].id

    with pytest.raises(LabOrderNotAssigned):
        repos.results.record_numeric(
            order_id=order.id,
            lab_order_item_id=item_id,
            lab_organization_id=lab_impostor.id,
            value=Decimal("13.5"),
        )


def test_result_before_any_lab_selected_fails_closed(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    entry, _offering = make_priced_offering(repos.catalog, lab_organization_id=lab.id)
    provider = make_provider(repos.organizations)
    patient = make_patient_reference(repos.patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(repos.practitioners, provider_org_id=provider.id)
    order = repos.orders.create(
        idempotency_key="req-no-selection",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order

    with pytest.raises(LabOrderNotAssigned):
        repos.results.record_numeric(
            order_id=order.id,
            lab_order_item_id=order.items[0].id,
            lab_organization_id=lab.id,
            value=Decimal("1"),
        )


def test_result_before_in_progress_fails_closed(sessions: sessionmaker[Session]) -> None:
    """SELECTED (not yet ACCEPTED/IN_PROGRESS) is a real, distinct refusal from "no selection at
    all" -- the assignment exists and is correct, but the lifecycle gate is what refuses."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(
        repos, sessions, lab_id=lab.id, advance_to_in_progress=False
    )
    assert order.status == "SELECTED"

    with pytest.raises(InvalidResultSubmissionState):
        repos.results.record_numeric(
            order_id=order.id,
            lab_order_item_id=order.items[0].id,
            lab_organization_id=lab.id,
            value=Decimal("1"),
        )


def test_result_for_item_belonging_to_a_different_order_fails_closed(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order_a, _p = _create_and_select(repos, sessions, lab_id=lab.id, key="order-a")
    order_b, _p2 = _create_and_select(repos, sessions, lab_id=lab.id, key="order-b")

    with pytest.raises(OrderItemNotFound):
        repos.results.record_numeric(
            order_id=order_a.id,
            lab_order_item_id=order_b.items[0].id,
            lab_organization_id=lab.id,
            value=Decimal("1"),
        )


def test_duplicate_result_for_the_same_item_refused(sessions: sessionmaker[Session]) -> None:
    """Uses a two-item order so the order is still IN_PROGRESS (not COMPLETED) after the first
    result -- proving DuplicateResult itself, distinct from the order-level state gate a
    one-item order's second attempt would hit instead (see the lifecycle-ordering test below)."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select_two_items(repos, sessions, lab_id=lab.id, key="dup-1")
    item_id = order.items[0].id
    repos.results.record_numeric(
        order_id=order.id, lab_order_item_id=item_id, lab_organization_id=lab.id, value=Decimal("1")
    )

    with pytest.raises(DuplicateResult):
        repos.results.record_numeric(
            order_id=order.id,
            lab_order_item_id=item_id,
            lab_organization_id=lab.id,
            value=Decimal("2"),
        )


def test_duplicate_result_does_not_change_the_first_value(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select_two_items(repos, sessions, lab_id=lab.id, key="dup-2")
    item_id = order.items[0].id
    repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        value=Decimal("1.0000"),
    )

    with pytest.raises(DuplicateResult):
        repos.results.record_numeric(
            order_id=order.id,
            lab_order_item_id=item_id,
            lab_organization_id=lab.id,
            value=Decimal("999"),
        )

    stored = repos.results.get_for_item(item_id)
    assert stored.numeric_value == Decimal("1.0000")


def test_second_submission_on_a_completed_one_item_order_hits_the_state_gate_first(
    sessions: sessionmaker[Session],
) -> None:
    """The ticket's required check sequence puts the IN_PROGRESS lifecycle check (step 4) before
    the item-ownership/duplicate checks (steps 5-6) -- so once a one-item order's sole result
    completes it, a resubmission attempt raises InvalidResultSubmissionState, not DuplicateResult.
    Both are controlled conflicts; this test pins down which one, deliberately, since the two are
    easy to confuse."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="single-dup")
    item_id = order.items[0].id
    outcome = repos.results.record_numeric(
        order_id=order.id, lab_order_item_id=item_id, lab_organization_id=lab.id, value=Decimal("1")
    )
    assert outcome.ready_for_release is True
    assert outcome.order_status == "IN_PROGRESS"
    # DH-EX-012/ADR-044: only an explicit release reaches COMPLETED now.
    _release(repos, order_id=order.id, lab_id=lab.id, key="single-dup")

    with pytest.raises(InvalidResultSubmissionState):
        repos.results.record_numeric(
            order_id=order.id,
            lab_order_item_id=item_id,
            lab_organization_id=lab.id,
            value=Decimal("2"),
        )


def test_partial_results_keep_a_multi_item_order_in_progress(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select_two_items(
        repos, sessions, lab_id=lab.id, key="multi-partial"
    )

    first = repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )
    assert first.ready_for_release is False
    assert first.order_status == "IN_PROGRESS"

    second = repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[1].id,
        lab_organization_id=lab.id,
        value=Decimal("2"),
    )
    # DH-EX-012/ADR-044: even the last missing result leaves the order IN_PROGRESS now --
    # `ready_for_release` reports readiness, but only an explicit release completes the order.
    assert second.ready_for_release is True
    assert second.order_status == "IN_PROGRESS"


def test_result_completion_does_not_automatically_deliver(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id)
    repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )

    delivery = repos.deliveries.get_for_order(order.id)
    assert delivery.status == ResultDeliveryStatus.PENDING.value
    assert delivery.delivered_at is None


def test_delivery_state_independent_of_result_and_destination_explicit(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id)

    delivered = repos.deliveries.mark_delivered(order.id)

    assert delivered.status == ResultDeliveryStatus.DELIVERED.value
    assert delivered.delivered_at is not None
    assert delivered.provider_organization_id == order.provider_organization_id


def test_doctor_review_remains_pending_until_explicit_action(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id)

    review = repos.reviews.get_for_order(order.id)
    assert review.status == DoctorReviewStatus.PENDING.value
    assert review.reviewed_at is None


def test_doctor_review_before_completion_fails_closed(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, practitioner = _create_and_select(repos, sessions, lab_id=lab.id)
    assert order.status == "IN_PROGRESS"  # single item, no result recorded yet -- not COMPLETED

    with pytest.raises(ReviewNotAllowedYet):
        repos.reviews.mark_no_follow_up_required(
            order.id, reviewed_by_practitioner_reference_id=practitioner.id
        )


def test_doctor_review_no_follow_up_required_explicit(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="review-nofu")
    repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )
    # DH-EX-012/ADR-044: review requires COMPLETED, which now requires an explicit release.
    _release(repos, order_id=order.id, lab_id=lab.id, key="review-nofu")

    reviewed = repos.reviews.mark_no_follow_up_required(
        order.id, reviewed_by_practitioner_reference_id=practitioner.id
    )

    assert reviewed.status == DoctorReviewStatus.NO_FOLLOW_UP_REQUIRED.value
    assert reviewed.reviewed_at is not None
    assert reviewed.reviewed_by_practitioner_reference_id == practitioner.id


def test_doctor_review_follow_up_required_explicit(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="review-fu")
    repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )
    # DH-EX-012/ADR-044: review requires COMPLETED, which now requires an explicit release.
    _release(repos, order_id=order.id, lab_id=lab.id, key="review-fu")

    reviewed = repos.reviews.mark_follow_up_required(
        order.id, reviewed_by_practitioner_reference_id=practitioner.id
    )

    assert reviewed.status == DoctorReviewStatus.FOLLOW_UP_REQUIRED.value


def test_release_before_in_progress_raises_not_ready(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(
        repos, sessions, lab_id=lab.id, key="release-too-early", advance_to_in_progress=False
    )
    assert order.status == "SELECTED"

    with pytest.raises(ResultsNotReadyForRelease):
        _release(repos, order_id=order.id, lab_id=lab.id, key="release-too-early")


def test_release_refused_when_a_result_is_still_missing(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select_two_items(
        repos, sessions, lab_id=lab.id, key="release-incomplete"
    )
    repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )

    with pytest.raises(ResultsNotReadyForRelease):
        _release(repos, order_id=order.id, lab_id=lab.id, key="release-incomplete")

    refreshed = repos.orders.get(order.id)
    assert refreshed.status == "IN_PROGRESS"


def test_release_from_the_wrong_lab_fails_closed(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab_selected = make_lab(repos.organizations, name="Release Lab Selected")
    lab_impostor = make_lab(repos.organizations, name="Release Lab Impostor")
    order, _practitioner = _create_and_select(
        repos, sessions, lab_id=lab_selected.id, key="release-wrong-lab"
    )
    repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab_selected.id,
        value=Decimal("1"),
    )

    with pytest.raises(LabOrderNotAssigned):
        _release(repos, order_id=order.id, lab_id=lab_impostor.id, key="release-wrong-lab")


def test_release_idempotent_replay_preserves_first_committer_provenance(
    sessions: sessionmaker[Session],
) -> None:
    """A lost-response retry by the same releaser, and a later retry by a genuinely different
    authorized releaser, must both return the exact same provenance -- the fact recorded is who
    actually committed the release first, never rewritten by whoever asks most recently."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="release-replay")
    repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )

    first_record, first_created = _release(
        repos, order_id=order.id, lab_id=lab.id, key="release-replay-releaser-a"
    )
    assert first_created is True

    same_releaser_replay, replay_created = _release(
        repos, order_id=order.id, lab_id=lab.id, key="release-replay-releaser-a"
    )
    assert replay_created is False
    assert same_releaser_replay.id == first_record.id
    assert same_releaser_replay.released_at == first_record.released_at.replace(
        tzinfo=same_releaser_replay.released_at.tzinfo
    )
    assert same_releaser_replay.released_by_identity_id == first_record.released_by_identity_id

    different_releaser_record, different_created = _release(
        repos, order_id=order.id, lab_id=lab.id, key="release-replay-releaser-b"
    )
    assert different_created is False
    assert different_releaser_record.id == first_record.id
    assert different_releaser_record.released_at == first_record.released_at.replace(
        tzinfo=different_releaser_record.released_at.tzinfo
    )
    assert different_releaser_record.released_by_identity_id == first_record.released_by_identity_id


def test_release_on_legacy_completed_order_raises_distinguishable_conflict(
    sessions: sessionmaker[Session],
) -> None:
    """A pre-DH-EX-012 order that reached COMPLETED via the old automatic-completion behavior has
    no `LabResultRelease` row -- manufactured here via a raw status write, since no live code path
    can produce this shape any more. `LegacyCompletedOrderHasNoRelease` must be raised, distinct
    from `ResultsNotReadyForRelease`, and no release row may be fabricated."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(
        repos, sessions, lab_id=lab.id, key="legacy-completed"
    )
    repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )
    with sessions() as session:
        from daher_adapter.exchange.models import LabOrder as _LabOrder

        raw_order = session.get(_LabOrder, order.id)
        raw_order.status = "COMPLETED"
        session.commit()

    with pytest.raises(LegacyCompletedOrderHasNoRelease):
        _release(repos, order_id=order.id, lab_id=lab.id, key="legacy-completed")

    assert repos.releases.get_for_order(order.id) is None


def test_dual_role_identity_release_records_that_same_identity(
    sessions: sessionmaker[Session],
) -> None:
    """ADR-044: the same human identity may both enter the result and release it -- no code path
    compares `entered_by` to `released_by`, and the release record simply reflects whichever
    identity actually called `release`."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="dual-role-repo")
    repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )

    dual_identity, _ = repos.identities.get_or_create(
        issuer="https://test.example", subject="dual-role-repo-human"
    )
    record, created = repos.releases.release(
        order_id=order.id, lab_organization_id=lab.id, released_by_identity_id=dual_identity.id
    )
    assert created is True
    assert record.released_by_identity_id == dual_identity.id


def test_second_terminal_review_is_a_controlled_conflict_not_a_silent_overwrite(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="review-twice")
    repos.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )
    # DH-EX-012/ADR-044: review requires COMPLETED, which now requires an explicit release.
    _release(repos, order_id=order.id, lab_id=lab.id, key="review-twice")
    repos.reviews.mark_no_follow_up_required(
        order.id, reviewed_by_practitioner_reference_id=practitioner.id
    )

    with pytest.raises(ReviewAlreadyFinalized):
        repos.reviews.mark_follow_up_required(
            order.id, reviewed_by_practitioner_reference_id=practitioner.id
        )

    unchanged = repos.reviews.get_for_order(order.id)
    assert unchanged.status == DoctorReviewStatus.NO_FOLLOW_UP_REQUIRED.value


# -- DH-EX-012 real-thread concurrency proofs -------------------------------------------------
#
# Same pattern as `test_exchange_lab_workflow.py`'s own `_build_repo_harness`: a dedicated
# file-based SQLite engine (not the shared in-memory `sessions` fixture, which cannot support
# genuine cross-thread contention), `BEGIN IMMEDIATE` + a busy-timeout so SQLite's own locking
# serializes concurrent writers exactly as PostgreSQL's `FOR UPDATE` would -- these tests prove
# the *serialization discipline* (single lock order, idempotent release, re-checked
# completeness), not PostgreSQL-specific behavior. Real-PostgreSQL qualification for the same
# scenarios lives in `tests/integration/test_dhex012_postgres_qualification.py`.


def _build_release_repo_harness(tmp_path, *, db_name: str) -> tuple[Repos, sessionmaker[Session]]:
    from sqlalchemy import create_engine, event

    from daher_adapter.exchange.models import Base

    database = tmp_path / f"{db_name}.sqlite"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _disable(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin_immediate(connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    Base.metadata.create_all(engine)
    local_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    return _build(local_sessions), local_sessions


def test_10way_concurrent_release_converges_to_one_record(tmp_path) -> None:
    import threading

    h, local_sessions = _build_release_repo_harness(tmp_path, db_name="release-10way")
    lab = make_lab(h.organizations, name="release-10way-lab")
    order, _practitioner = _create_and_select(h, local_sessions, lab_id=lab.id, key="r10")
    h.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )

    thread_count = 10
    outcomes: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(thread_count)

    def attempt(i: int) -> None:
        try:
            releaser, _ = h.identities.get_or_create(
                issuer="https://test.example", subject=f"r10-releaser-{i}"
            )
            barrier.wait(timeout=10)
            _record, created = h.releases.release(
                order_id=order.id, lab_organization_id=lab.id, released_by_identity_id=releaser.id
            )
            with lock:
                outcomes.append(created)
        except Exception as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(outcomes) == thread_count
    assert outcomes.count(True) == 1

    final = h.orders.get(order.id)
    assert final.status == "COMPLETED"


def _run_final_result_vs_release_once(tmp_path, i: int) -> str:
    """Races the last missing result's submission against a release attempt on a two-item order
    whose first item already has a result. `LabOrder`'s shared `FOR UPDATE` lock (held by both
    `_submit_in_session` and `_release_in_session`) means exactly one of the two genuinely runs
    first -- both outcomes are valid, but each must be internally coherent: a release that runs
    before the final result lands must be refused (`ResultsNotReadyForRelease`), never complete a
    half-resulted order; a release that runs after must succeed. Either way, the submission itself
    must always succeed (nothing about a losing release may block or corrupt the result insert),
    and the final state is never split-brain (a `LabResultRelease` row with a missing result, or a
    `COMPLETED` order with no release row). Returns which ordering actually occurred
    (``"release_first"``/``"release_second"``) so the caller can assert both are genuinely
    exercised across repetitions, not merely tolerated -- see the alternating `.start()` order
    below for why a fixed order would silently defeat that."""
    import threading

    h, local_sessions = _build_release_repo_harness(tmp_path, db_name=f"final-vs-release-{i}")
    lab = make_lab(h.organizations, name=f"final-vs-release-lab-{i}")
    order, _practitioner = _create_and_select_two_items(
        h, local_sessions, lab_id=lab.id, key=f"fvr-{i}"
    )
    h.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )
    releaser, _ = h.identities.get_or_create(
        issuer="https://test.example", subject=f"fvr-releaser-{i}"
    )

    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def do_submit() -> None:
        try:
            barrier.wait(timeout=10)
            outcomes["submit"] = h.results.record_numeric(
                order_id=order.id,
                lab_order_item_id=order.items[1].id,
                lab_organization_id=lab.id,
                value=Decimal("2"),
            )
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    def do_release() -> None:
        try:
            barrier.wait(timeout=10)
            outcomes["release"] = h.releases.release(
                order_id=order.id, lab_organization_id=lab.id, released_by_identity_id=releaser.id
            )
        except ResultsNotReadyForRelease as refused:
            outcomes["release"] = refused
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    # Alternating which thread's `.start()` call comes first matters: with a fixed start order,
    # the first-started thread wins the SQLite BEGIN IMMEDIATE lock acquisition almost every time
    # (thread-launch overhead dwarfs the barrier's synchronization window) -- observed directly
    # while writing this test, a fixed order produced "release_first" 20/20 times, which would
    # have silently made this test exercise only one of its two claimed orderings.
    t1 = threading.Thread(target=do_submit)
    t2 = threading.Thread(target=do_release)
    if i % 2 == 0:
        t1.start()
        t2.start()
    else:
        t2.start()
        t1.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, errors
    assert "submit" in outcomes and "release" in outcomes  # submission always succeeds

    final = h.orders.get(order.id)
    release_outcome = outcomes["release"]
    if isinstance(release_outcome, ResultsNotReadyForRelease):
        # Release ran first, while the second item was still missing -- refused, order untouched.
        assert final.status == "IN_PROGRESS"
        assert h.releases.get_for_order(order.id) is None
        return "release_first"
    else:
        # Release ran second (or won the lock after the submission committed) -- succeeded.
        _record, created = release_outcome
        assert created is True
        assert final.status == "COMPLETED"
        assert h.releases.get_for_order(order.id) is not None
        return "release_second"


def test_final_result_vs_release_race_both_valid_orderings(tmp_path) -> None:
    """Runs enough repetitions to actually observe both valid orderings, not merely tolerate
    either one -- a run that happened to take the same branch every time would not be evidence
    that the other branch is handled correctly at all."""
    orderings = [_run_final_result_vs_release_once(tmp_path, i) for i in range(20)]
    assert set(orderings) == {"release_first", "release_second"}, (
        f"expected both orderings to occur across 20 repetitions, observed only: "
        f"{set(orderings)} -- distribution: {orderings}"
    )


def test_two_different_releasers_race_no_overwrite(tmp_path) -> None:
    """Two distinct, equally-authorized `LAB_RESULT_RELEASER` identities call `release`
    concurrently on the same ready order -- exactly one may actually create the row; the other
    must observe that same row back, never a second row and never its own identity overwriting
    the winner's."""
    import threading

    h, local_sessions = _build_release_repo_harness(tmp_path, db_name="two-releasers-race")
    lab = make_lab(h.organizations, name="two-releasers-lab")
    order, _practitioner = _create_and_select(h, local_sessions, lab_id=lab.id, key="tworel")
    h.results.record_numeric(
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )
    releaser_a, _ = h.identities.get_or_create(issuer="https://test.example", subject="tworel-a")
    releaser_b, _ = h.identities.get_or_create(issuer="https://test.example", subject="tworel-b")

    results: list[tuple] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def attempt(identity_id) -> None:
        try:
            barrier.wait(timeout=10)
            record, created = h.releases.release(
                order_id=order.id, lab_organization_id=lab.id, released_by_identity_id=identity_id
            )
            with lock:
                results.append((record, created))
        except Exception as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    t1 = threading.Thread(target=attempt, args=(releaser_a.id,))
    t2 = threading.Thread(target=attempt, args=(releaser_b.id,))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, errors
    assert len(results) == 2
    created_flags = [created for _record, created in results]
    assert created_flags.count(True) == 1
    winning_identity = next(
        record.released_by_identity_id for record, created in results if created
    )
    for record, _created in results:
        assert record.released_by_identity_id == winning_identity
        assert record.id == results[0][0].id
