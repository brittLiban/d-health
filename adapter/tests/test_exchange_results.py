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
from sqlalchemy import Engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import LabOrderNotAssigned, LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    DoctorReview,
    DoctorReviewStatus,
    LabObservation,
    LabOrder,
    LabPrice,
    LabResultType,
    ResultDeliveryStatus,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    DoctorReviewRepository,
    DuplicateResult,
    EntryAlreadyCompleted,
    EntryNotReadyForCompletion,
    InvalidResultSubmissionState,
    LabResultReleaseRepository,
    LabResultRepository,
    LegacyCompletedOrderHasNoRelease,
    ObservationNotFound,
    OrderItemNotFound,
    ResultDeliveryRepository,
    ResultsNotReadyForRelease,
    ReviewAlreadyFinalized,
    ReviewNotAllowedYet,
    StaleObservationVersion,
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


def _labtech_identity_id(repos: Repos, subject: str = "labtech-generic"):
    """DH-EX-012C: `record_numeric`/`record_text`/`record_observation`/`complete_entry` all now
    require a real actor identity (native completion provenance must never be fabricated). Tests
    that do not care which specific identity recorded a result share this one generic identity;
    tests that do (dual-role, provenance-specific) bootstrap their own."""
    identity, _ = repos.identities.get_or_create(issuer="https://test.example", subject=subject)
    return identity.id


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


def _create_and_select_n_items(
    repos: Repos, sessions: sessionmaker[Session], *, lab_id, key: str, count: int
):
    """Same shape as `_create_and_select_two_items`, generalized to an arbitrary item count --
    needed by the DH-EX-014A query-count control, which must exercise an order wide enough that
    an accidental per-item query would actually show up in the count."""
    repos.directory.upsert(
        lab_organization_id=lab_id,
        public_address="Test Lab Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    entries_and_offerings = [
        make_priced_offering(
            repos.catalog,
            lab_organization_id=lab_id,
            amount=Decimal("100"),
            code=f"DH-{key}-{i}",
        )
        for i in range(count)
    ]
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
            OrderItemInput(catalog_entry_id=entry.id) for entry, _offering in entries_and_offerings
        ],
    ).order
    expected_price_ids = frozenset(
        _current_price_id(sessions, offering.id) for _entry, offering in entries_and_offerings
    )
    repos.selections.select(
        lab_order_id=order.id, lab_organization_id=lab_id, expected_price_ids=expected_price_ids
    )
    repos.workflow.accept(order_id=order.id, lab_organization_id=lab_id)
    repos.workflow.record_collection(order_id=order.id, lab_organization_id=lab_id)
    repos.workflow.start_processing(order_id=order.id, lab_organization_id=lab_id)
    return repos.orders.get(order.id), practitioner


def test_result_binds_to_the_correct_order_item_and_lab(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id)
    item_id = order.items[0].id

    outcome = repos.results.record_numeric(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        value=Decimal("13.5"),
        unit="g/dL",
    )

    # DH-EX-012/ADR-044: recording even a one-item order's sole result no longer completes it --
    # `ready_for_release` reports the same "every item's entry is complete" fact, but only an
    # explicit `LabResultReleaseRepository.release` call may act on it.
    assert outcome.ready_for_release is True
    assert outcome.order_status == "IN_PROGRESS"
    stored = repos.results.get_for_item(item_id)
    assert stored is not None
    assert stored.lab_order_item_id == item_id
    assert stored.lab_organization_id == lab.id
    assert stored.entry_completed_at is not None
    assert len(stored.observations) == 1
    assert stored.observations[0].id == outcome.observation.id


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
            recorded_by_identity_id=_labtech_identity_id(repos),
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
            recorded_by_identity_id=_labtech_identity_id(repos),
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
            recorded_by_identity_id=_labtech_identity_id(repos),
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
            recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )

    with pytest.raises(DuplicateResult):
        repos.results.record_numeric(
            recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        value=Decimal("1.0000"),
    )

    with pytest.raises(DuplicateResult):
        repos.results.record_numeric(
            recorded_by_identity_id=_labtech_identity_id(repos),
            order_id=order.id,
            lab_order_item_id=item_id,
            lab_organization_id=lab.id,
            value=Decimal("999"),
        )

    stored = repos.results.get_for_item(item_id)
    assert len(stored.observations) == 1
    assert stored.observations[0].numeric_value == Decimal("1.0000")


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
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )
    assert outcome.ready_for_release is True
    assert outcome.order_status == "IN_PROGRESS"
    # DH-EX-012/ADR-044: only an explicit release reaches COMPLETED now.
    _release(repos, order_id=order.id, lab_id=lab.id, key="single-dup")

    with pytest.raises(InvalidResultSubmissionState):
        repos.results.record_numeric(
            recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )
    assert first.ready_for_release is False
    assert first.order_status == "IN_PROGRESS"

    second = repos.results.record_numeric(
        recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1"),
    )

    delivery = repos.deliveries.get_for_order(order.id)
    assert delivery.status == ResultDeliveryStatus.PENDING.value
    assert delivery.delivered_at is None


# -- DH-EX-012C: granular multi-observation entry / explicit completion ----------------------


def test_multiple_observations_recorded_under_one_item_before_completion(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="multi-obs")
    item_id = order.items[0].id
    actor = _labtech_identity_id(repos)

    first = repos.results.record_observation(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="WBC",
        display_label_snapshot="White blood cell count",
        numeric_value=Decimal("6.2"),
        numeric_unit="10^9/L",
    )
    second = repos.results.record_observation(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.TEXT,
        source_code="MORPH",
        display_label_snapshot="Morphology",
        text_value="Normal",
    )

    assert first.lab_result_id == second.lab_result_id
    stored = repos.results.get_for_item(item_id)
    assert stored is not None
    assert stored.entry_completed_at is None
    assert {o.id for o in stored.observations} == {first.id, second.id}

    completion, created = repos.results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=actor,
    )
    assert created is True
    assert completion.entry_completed_by_identity_id == actor

    completed = repos.results.get_for_item(item_id)
    assert completed.entry_completed_at is not None
    assert len(completed.observations) == 2


def test_completion_rejected_with_zero_observations(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="zero-obs")

    with pytest.raises(EntryNotReadyForCompletion):
        repos.results.complete_entry(
            order_id=order.id,
            lab_order_item_id=order.items[0].id,
            lab_organization_id=lab.id,
            completed_by_identity_id=_labtech_identity_id(repos),
        )

    refreshed = repos.orders.get(order.id)
    assert refreshed.status == "IN_PROGRESS"


def test_completion_is_idempotent_and_never_overwrites_first_actor(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="complete-idem")
    item_id = order.items[0].id
    repos.results.record_observation(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot="Glucose",
        numeric_value=Decimal("5.1"),
    )
    first_actor = _labtech_identity_id(repos, subject="complete-idem-first")
    second_actor = _labtech_identity_id(repos, subject="complete-idem-second")

    first_record, first_created = repos.results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=first_actor,
    )
    assert first_created is True
    assert first_record.entry_completed_by_identity_id == first_actor

    replay_record, replay_created = repos.results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=second_actor,
    )
    assert replay_created is False
    assert replay_record.entry_completed_by_identity_id == first_actor
    assert replay_record.entry_completed_at == first_record.entry_completed_at.replace(
        tzinfo=replay_record.entry_completed_at.tzinfo
    )


def test_observation_after_completion_is_frozen_and_refused(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="freeze")
    item_id = order.items[0].id
    repos.results.record_observation(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="NA",
        display_label_snapshot="Sodium",
        numeric_value=Decimal("140"),
    )
    repos.results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=_labtech_identity_id(repos),
    )

    with pytest.raises(EntryAlreadyCompleted):
        repos.results.record_observation(
            recorded_by_identity_id=_labtech_identity_id(repos),
            order_id=order.id,
            lab_order_item_id=item_id,
            lab_organization_id=lab.id,
            result_type=LabResultType.NUMERIC,
            source_code="K",
            display_label_snapshot="Potassium",
            numeric_value=Decimal("4.0"),
        )

    stored = repos.results.get_for_item(item_id)
    assert len(stored.observations) == 1


def test_release_refuses_malformed_zero_observation_complete_container(
    sessions: sessionmaker[Session],
) -> None:
    """Defense in depth: a `LabResult` container marked `entry_completed_at` with zero
    observations cannot be produced by any live code path (both completion paths require >=1
    observation first, and observations are never deleted) -- manufactured here via a raw write,
    exactly matching the established pattern for the legacy-completed-order test above. Release
    must still refuse it, never trusting the container's own flag alone."""
    from daher_adapter.exchange.models import LabResult as _LabResult
    from daher_adapter.models import utc_now as _utc_now

    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="malformed-zero")
    item_id = order.items[0].id

    with sessions() as session:
        session.add(
            _LabResult(
                lab_order_item_id=item_id,
                lab_organization_id=lab.id,
                entry_completed_at=_utc_now(),
            )
        )
        session.commit()

    with pytest.raises(ResultsNotReadyForRelease):
        _release(repos, order_id=order.id, lab_id=lab.id, key="malformed-zero")

    refreshed = repos.orders.get(order.id)
    assert refreshed.status == "IN_PROGRESS"
    assert repos.releases.get_for_order(order.id) is None


# -- DH-EX-013/ADR-045: pre-release observation correction ------------------------------------


def test_numeric_observation_corrected_to_new_numeric_value(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="correct-numeric")
    item_id = order.items[0].id
    original = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot="Glucose",
        numeric_value=Decimal("5.1"),
        recorded_by_identity_id=_labtech_identity_id(repos),
    )
    corrector = _labtech_identity_id(repos, subject="correct-numeric-corrector")

    corrected = repos.results.correct_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        observation_id=original.id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot="Glucose",
        numeric_value=Decimal("5.9"),
        corrected_by_identity_id=corrector,
    )

    assert corrected.id != original.id
    assert corrected.numeric_value == Decimal("5.9")
    assert corrected.supersedes_observation_id == original.id
    assert corrected.superseded_at is None
    assert corrected.recorded_by_identity_id == corrector

    # Original row is untouched.
    with sessions() as session:
        from daher_adapter.exchange.models import LabObservation as _LabObservation

        original_row = session.get(_LabObservation, original.id)
        assert original_row.numeric_value == Decimal("5.1")
        assert original_row.superseded_at is not None

    stored = repos.results.get_for_item(item_id)
    assert len(stored.observations) == 1
    assert stored.observations[0].id == corrected.id
    assert stored.observations[0].numeric_value == Decimal("5.9")


def test_text_observation_corrected_to_new_text_value(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="correct-text")
    item_id = order.items[0].id
    original = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.TEXT,
        source_code="MORPH",
        display_label_snapshot="Morphology",
        text_value="Normal",
        recorded_by_identity_id=_labtech_identity_id(repos),
    )

    corrected = repos.results.correct_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        observation_id=original.id,
        result_type=LabResultType.TEXT,
        source_code="MORPH",
        display_label_snapshot="Morphology",
        text_value="Abnormal -- see correction",
        corrected_by_identity_id=_labtech_identity_id(repos, subject="correct-text-corrector"),
    )

    assert corrected.text_value == "Abnormal -- see correction"
    assert corrected.supersedes_observation_id == original.id

    stored = repos.results.get_for_item(item_id)
    assert stored.observations[0].text_value == "Abnormal -- see correction"


def test_correction_can_change_unit_and_reference_bounds_without_mutating_original(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="correct-bounds")
    item_id = order.items[0].id
    original = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="HGB",
        display_label_snapshot="Hemoglobin",
        numeric_value=Decimal("13.5"),
        numeric_unit="g/dL",
        reference_range_low=Decimal("12.0"),
        reference_range_high=Decimal("15.5"),
        recorded_by_identity_id=_labtech_identity_id(repos),
    )

    corrected = repos.results.correct_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        observation_id=original.id,
        result_type=LabResultType.NUMERIC,
        source_code="HGB",
        display_label_snapshot="Hemoglobin",
        numeric_value=Decimal("135"),
        numeric_unit="g/L",
        reference_range_low=Decimal("120"),
        reference_range_high=Decimal("155"),
        corrected_by_identity_id=_labtech_identity_id(repos, subject="correct-bounds-corrector"),
    )

    assert corrected.numeric_unit == "g/L"
    assert corrected.reference_range_low == Decimal("120")
    assert corrected.reference_range_high == Decimal("155")

    with sessions() as session:
        from daher_adapter.exchange.models import LabObservation as _LabObservation

        original_row = session.get(_LabObservation, original.id)
        assert original_row.numeric_unit == "g/dL"
        assert original_row.reference_range_low == Decimal("12.0")
        assert original_row.reference_range_high == Decimal("15.5")


def test_second_correction_creates_a_three_version_chain(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="correct-chain")
    item_id = order.items[0].id
    v1 = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="K",
        display_label_snapshot="Potassium",
        numeric_value=Decimal("4.0"),
        recorded_by_identity_id=_labtech_identity_id(repos),
    )
    v2 = repos.results.correct_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        observation_id=v1.id,
        result_type=LabResultType.NUMERIC,
        source_code="K",
        display_label_snapshot="Potassium",
        numeric_value=Decimal("4.5"),
        corrected_by_identity_id=_labtech_identity_id(repos, subject="chain-corrector-1"),
    )
    v3 = repos.results.correct_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        observation_id=v2.id,
        result_type=LabResultType.NUMERIC,
        source_code="K",
        display_label_snapshot="Potassium",
        numeric_value=Decimal("4.7"),
        corrected_by_identity_id=_labtech_identity_id(repos, subject="chain-corrector-2"),
    )

    assert v3.supersedes_observation_id == v2.id
    assert v2.supersedes_observation_id == v1.id

    stored = repos.results.get_for_item(item_id)
    assert len(stored.observations) == 1
    assert stored.observations[0].id == v3.id
    assert stored.observations[0].numeric_value == Decimal("4.7")

    history = repos.results.get_correction_history(item_id)
    assert [o.id for o in history.observations] == [v1.id, v2.id, v3.id]
    assert history.observations[0].superseded_at is not None
    assert history.observations[1].superseded_at is not None
    assert history.observations[2].superseded_at is None


def test_correcting_stale_superseded_observation_refused(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="correct-stale")
    item_id = order.items[0].id
    v1 = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="NA",
        display_label_snapshot="Sodium",
        numeric_value=Decimal("140"),
        recorded_by_identity_id=_labtech_identity_id(repos),
    )
    repos.results.correct_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        observation_id=v1.id,
        result_type=LabResultType.NUMERIC,
        source_code="NA",
        display_label_snapshot="Sodium",
        numeric_value=Decimal("141"),
        corrected_by_identity_id=_labtech_identity_id(repos, subject="stale-corrector-1"),
    )

    with pytest.raises(StaleObservationVersion):
        repos.results.correct_observation(
            order_id=order.id,
            lab_order_item_id=item_id,
            lab_organization_id=lab.id,
            observation_id=v1.id,
            result_type=LabResultType.NUMERIC,
            source_code="NA",
            display_label_snapshot="Sodium",
            numeric_value=Decimal("999"),
            corrected_by_identity_id=_labtech_identity_id(repos, subject="stale-corrector-2"),
        )


def test_correction_allowed_after_entry_completion_before_release(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(
        repos, sessions, lab_id=lab.id, key="correct-postcomplete"
    )
    item_id = order.items[0].id
    original = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="CA",
        display_label_snapshot="Calcium",
        numeric_value=Decimal("9.5"),
        recorded_by_identity_id=_labtech_identity_id(repos),
    )
    completion, _created = repos.results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=_labtech_identity_id(repos, subject="postcomplete-completer"),
    )
    original_completed_at = completion.entry_completed_at

    corrected = repos.results.correct_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        observation_id=original.id,
        result_type=LabResultType.NUMERIC,
        source_code="CA",
        display_label_snapshot="Calcium",
        numeric_value=Decimal("9.8"),
        corrected_by_identity_id=_labtech_identity_id(repos, subject="postcomplete-corrector"),
    )

    assert corrected.numeric_value == Decimal("9.8")
    stored = repos.results.get_for_item(item_id)
    # Entry-completion timestamp/actor are never touched by a correction.
    assert stored.entry_completed_at == original_completed_at.replace(
        tzinfo=stored.entry_completed_at.tzinfo
    )
    assert stored.entry_completed_by_identity_id is not None


def test_correction_after_release_refused(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(
        repos, sessions, lab_id=lab.id, key="correct-postrelease"
    )
    item_id = order.items[0].id
    original = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="CL",
        display_label_snapshot="Chloride",
        numeric_value=Decimal("100"),
        recorded_by_identity_id=_labtech_identity_id(repos),
    )
    repos.results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=_labtech_identity_id(repos, subject="postrelease-completer"),
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="correct-postrelease")

    with pytest.raises(InvalidResultSubmissionState):
        repos.results.correct_observation(
            order_id=order.id,
            lab_order_item_id=item_id,
            lab_organization_id=lab.id,
            observation_id=original.id,
            result_type=LabResultType.NUMERIC,
            source_code="CL",
            display_label_snapshot="Chloride",
            numeric_value=Decimal("101"),
            corrected_by_identity_id=_labtech_identity_id(repos, subject="postrelease-corrector"),
        )

    # No new observation was created, and the original remains effective/unchanged.
    with sessions() as session:
        from daher_adapter.exchange.models import LabObservation as _LabObservation

        original_row = session.get(_LabObservation, original.id)
        assert original_row.numeric_value == Decimal("100")
        assert original_row.superseded_at is None


def test_cross_lab_correction_refused(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab_selected = make_lab(repos.organizations, name="Correct Lab Selected")
    lab_impostor = make_lab(repos.organizations, name="Correct Lab Impostor")
    order, _practitioner = _create_and_select(
        repos, sessions, lab_id=lab_selected.id, key="correct-crosslab"
    )
    item_id = order.items[0].id
    original = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_selected.id,
        result_type=LabResultType.NUMERIC,
        source_code="MG",
        display_label_snapshot="Magnesium",
        numeric_value=Decimal("2.0"),
        recorded_by_identity_id=_labtech_identity_id(repos),
    )

    with pytest.raises(LabOrderNotAssigned):
        repos.results.correct_observation(
            order_id=order.id,
            lab_order_item_id=item_id,
            lab_organization_id=lab_impostor.id,
            observation_id=original.id,
            result_type=LabResultType.NUMERIC,
            source_code="MG",
            display_label_snapshot="Magnesium",
            numeric_value=Decimal("2.1"),
            corrected_by_identity_id=_labtech_identity_id(repos, subject="crosslab-corrector"),
        )


def test_correcting_observation_from_a_different_item_refused(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select_two_items(
        repos, sessions, lab_id=lab.id, key="correct-wrongitem"
    )
    item_a, item_b = order.items[0].id, order.items[1].id
    observation_on_a = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_a,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="A",
        display_label_snapshot="Item A observation",
        numeric_value=Decimal("1"),
        recorded_by_identity_id=_labtech_identity_id(repos),
    )

    with pytest.raises(ObservationNotFound):
        repos.results.correct_observation(
            order_id=order.id,
            lab_order_item_id=item_b,
            lab_organization_id=lab.id,
            observation_id=observation_on_a.id,
            result_type=LabResultType.NUMERIC,
            source_code="A",
            display_label_snapshot="Item A observation",
            numeric_value=Decimal("2"),
            corrected_by_identity_id=_labtech_identity_id(repos, subject="wrongitem-corrector"),
        )


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
        recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(repos),
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
        recorded_by_identity_id=_labtech_identity_id(h),
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
        recorded_by_identity_id=_labtech_identity_id(h),
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
                recorded_by_identity_id=_labtech_identity_id(h),
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
        recorded_by_identity_id=_labtech_identity_id(h),
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


# -- DH-EX-014A/ADR-048: immutable release-version manifest + versioned DoctorReview foundation --


def test_release_creates_version_one_and_manifest_for_single_observation(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="v1-single")
    repos.results.record_numeric(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("5.1"),
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="v1-single")

    version = repos.releases.get_latest_release_version(order.id)
    assert version is not None
    assert version.version_number == 1
    release = repos.releases.get_for_order(order.id)
    assert version.lab_result_release_id == release.id

    manifest = repos.releases.get_manifest_for_order(order.id)
    assert manifest is not None
    assert manifest.version.id == version.id
    assert len(manifest.items) == 1
    assert len(manifest.items[0].observations) == 1
    assert manifest.items[0].observations[0].numeric_value == Decimal("5.1")


def test_release_manifest_includes_every_observation_in_a_panel_item(
    sessions: sessionmaker[Session],
) -> None:
    """One ordered item, several returned observations (ADR-046/ADR-047) -- the manifest must
    name every one of them, never impose a one-observation-per-item limit."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="v1-panel")
    item_id = order.items[0].id
    tech = _labtech_identity_id(repos)
    repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="WBC",
        display_label_snapshot="White cell count",
        numeric_value=Decimal("6.0"),
        recorded_by_identity_id=tech,
    )
    repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="RBC",
        display_label_snapshot="Red cell count",
        numeric_value=Decimal("4.5"),
        recorded_by_identity_id=tech,
    )
    repos.results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=tech,
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="v1-panel")

    manifest = repos.releases.get_manifest_for_order(order.id)
    assert manifest is not None
    assert len(manifest.items) == 1
    assert {o.source_code for o in manifest.items[0].observations} == {"WBC", "RBC"}


def test_release_manifest_spans_multiple_items(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select_two_items(
        repos, sessions, lab_id=lab.id, key="v1-multiitem"
    )
    tech = _labtech_identity_id(repos)
    for item in order.items:
        repos.results.record_numeric(
            recorded_by_identity_id=tech,
            order_id=order.id,
            lab_order_item_id=item.id,
            lab_organization_id=lab.id,
            value=Decimal("1.0"),
        )
    _release(repos, order_id=order.id, lab_id=lab.id, key="v1-multiitem")

    manifest = repos.releases.get_manifest_for_order(order.id)
    assert manifest is not None
    assert {item_record.item_id for item_record in manifest.items} == {
        item.id for item in order.items
    }


def test_release_manifest_uses_effective_corrected_observation_not_original(
    sessions: sessionmaker[Session],
) -> None:
    """A pre-release correction (DH-EX-013) must be reflected in the manifest -- the *corrected*
    observation id, never the original, superseded one."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="v1-corrected")
    item_id = order.items[0].id
    tech = _labtech_identity_id(repos)
    original = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot="Glucose",
        numeric_value=Decimal("5.1"),
        recorded_by_identity_id=tech,
    )
    repos.results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=tech,
    )
    corrected = repos.results.correct_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        observation_id=original.id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot="Glucose",
        numeric_value=Decimal("5.9"),
        corrected_by_identity_id=tech,
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="v1-corrected")

    manifest = repos.releases.get_manifest_for_order(order.id)
    assert manifest is not None
    observation_ids = {o.id for o in manifest.items[0].observations}
    assert observation_ids == {corrected.id}
    assert original.id not in observation_ids


def test_get_manifest_for_order_reconstructs_exact_observations(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="v1-reconstruct")
    item_id = order.items[0].id
    tech = _labtech_identity_id(repos)
    original = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.TEXT,
        source_code="CULT",
        display_label_snapshot="Culture",
        text_value="no growth",
        recorded_by_identity_id=tech,
    )
    repos.results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=tech,
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="v1-reconstruct")

    manifest = repos.releases.get_manifest_for_order(order.id)
    assert manifest is not None
    reconstructed = manifest.items[0].observations[0]
    assert reconstructed.id == original.id
    assert reconstructed.text_value == "no growth"
    assert reconstructed.source_code == "CULT"


def test_get_manifest_for_order_query_count_does_not_scale_with_item_count(
    engine: Engine, sessions: sessionmaker[Session]
) -> None:
    """DH-EX-014A ticket requirement: measure, don't just reason about, `get_manifest_for_order`'s
    query count. `_manifest_record_for_version` issues exactly one query to find the latest
    version and one joined query for every manifest row -- two SQL statements regardless of how
    many items/observations the order has. A one-item/one-observation order and a five-item/
    ten-observation order must therefore both cost exactly the same number of statements; if a
    future change introduces a per-item or per-observation query, this test goes red because the
    five-item count grows past the one-item count, not because of a hardcoded magic number."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    tech = _labtech_identity_id(repos)

    small_order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="qc-small")
    repos.results.record_numeric(
        recorded_by_identity_id=tech,
        order_id=small_order.id,
        lab_order_item_id=small_order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1.0"),
    )
    repos.results.complete_entry(
        order_id=small_order.id,
        lab_order_item_id=small_order.items[0].id,
        lab_organization_id=lab.id,
        completed_by_identity_id=tech,
    )
    _release(repos, order_id=small_order.id, lab_id=lab.id, key="qc-small")

    large_order, _practitioner = _create_and_select_n_items(
        repos, sessions, lab_id=lab.id, key="qc-large", count=5
    )
    for item in large_order.items:
        repos.results.record_observation(
            order_id=large_order.id,
            lab_order_item_id=item.id,
            lab_organization_id=lab.id,
            result_type=LabResultType.NUMERIC,
            source_code="A",
            display_label_snapshot="A",
            numeric_value=Decimal("1.0"),
            recorded_by_identity_id=tech,
        )
        repos.results.record_observation(
            order_id=large_order.id,
            lab_order_item_id=item.id,
            lab_organization_id=lab.id,
            result_type=LabResultType.NUMERIC,
            source_code="B",
            display_label_snapshot="B",
            numeric_value=Decimal("2.0"),
            recorded_by_identity_id=tech,
        )
        repos.results.complete_entry(
            order_id=large_order.id,
            lab_order_item_id=item.id,
            lab_organization_id=lab.id,
            completed_by_identity_id=tech,
        )
    _release(repos, order_id=large_order.id, lab_id=lab.id, key="qc-large")

    def _count_statements(order_id) -> int:
        statements: list[str] = []

        def _record(conn, cursor, statement, *args) -> None:  # noqa: ARG001
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            manifest = repos.releases.get_manifest_for_order(order_id)
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        assert manifest is not None
        return len(statements)

    small_count = _count_statements(small_order.id)
    large_count = _count_statements(large_order.id)

    assert len(large_order.items) == 5
    assert large_count == small_count


def test_grandfathered_order_has_no_release_version(sessions: sessionmaker[Session]) -> None:
    """A pre-DH-EX-012 COMPLETED order with no LabResultRelease must never get a fabricated
    release version or manifest -- `get_manifest_for_order`/`get_latest_release_version` both
    return None, exactly matching `LegacyCompletedOrderHasNoRelease`'s own no-fabrication rule."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="grandfathered")
    tech = _labtech_identity_id(repos)
    repos.results.record_numeric(
        recorded_by_identity_id=tech,
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1.0"),
    )
    with sessions() as session:
        raw_order = session.get(LabOrder, order.id)
        raw_order.status = "COMPLETED"
        session.commit()

    assert repos.releases.get_latest_release_version(order.id) is None
    assert repos.releases.get_manifest_for_order(order.id) is None
    assert repos.releases.get_release_version_history(order.id) == ()

    with pytest.raises(LegacyCompletedOrderHasNoRelease):
        _release(repos, order_id=order.id, lab_id=lab.id, key="grandfathered")


def test_unreleased_order_has_no_release_version(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="unreleased-v1")
    assert repos.releases.get_latest_release_version(order.id) is None
    assert repos.releases.get_manifest_for_order(order.id) is None


def test_pending_review_binds_to_release_version_on_release(
    sessions: sessionmaker[Session],
) -> None:
    """ADR-045 Question 4/Option C: the order's existing PENDING review (created at order-creation
    time) must be bound to Release Version 1 the instant release occurs -- never a new row, never
    left unbound."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="review-bind")
    review_before = repos.reviews.get_for_order(order.id)
    assert review_before.release_version_id is None
    assert review_before.status == DoctorReviewStatus.PENDING.value

    repos.results.record_numeric(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1.0"),
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="review-bind")

    version = repos.releases.get_latest_release_version(order.id)
    review_after = repos.reviews.get_for_order(order.id)
    assert review_after.id == review_before.id, (
        "release must bind the existing row, never a new one"
    )
    assert review_after.release_version_id == version.id
    assert review_after.status == DoctorReviewStatus.PENDING.value


def test_review_marked_after_release_is_bound_to_version_and_readable_by_version(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="review-mark-v1")
    repos.results.record_numeric(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1.0"),
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="review-mark-v1")
    version = repos.releases.get_latest_release_version(order.id)

    repos.reviews.mark_no_follow_up_required(
        order.id, reviewed_by_practitioner_reference_id=practitioner.id
    )

    review = repos.reviews.get_for_order(order.id)
    assert review.status == DoctorReviewStatus.NO_FOLLOW_UP_REQUIRED.value
    assert review.release_version_id == version.id

    by_version = repos.reviews.get_for_release_version(version.id)
    assert by_version is not None
    assert by_version.id == review.id

    with pytest.raises(ReviewAlreadyFinalized):
        repos.reviews.mark_follow_up_required(
            order.id, reviewed_by_practitioner_reference_id=practitioner.id
        )


def test_review_history_for_order_returns_the_one_bound_row(
    sessions: sessionmaker[Session],
) -> None:
    """DH-EX-014A: in this ticket at most one review row can ever exist per order -- the
    foundation read (`get_history_for_order`) must still work correctly for that single-row case,
    since DH-EX-014B will add a second row without changing this method's own logic."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="review-history")
    repos.results.record_numeric(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1.0"),
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="review-history")

    history = repos.reviews.get_history_for_order(order.id)
    assert len(history) == 1
    version = repos.releases.get_latest_release_version(order.id)
    assert history[0].release_version_id == version.id


def test_get_release_version_history_orders_by_version_number(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="version-history")
    repos.results.record_numeric(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1.0"),
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="version-history")

    history = repos.releases.get_release_version_history(order.id)
    assert [v.version_number for v in history] == [1]
    assert repos.releases.get_release_version(history[0].id) == history[0]


def test_release_replay_does_not_create_a_second_version_or_manifest(
    sessions: sessionmaker[Session],
) -> None:
    """An idempotent release replay (same or different releaser) must never create a second
    `LabResultReleaseVersion` or a duplicated manifest -- exactly one of each, permanently."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(
        repos, sessions, lab_id=lab.id, key="release-replay-v1"
    )
    repos.results.record_numeric(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1.0"),
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="release-replay-v1-a")
    _release(repos, order_id=order.id, lab_id=lab.id, key="release-replay-v1-b")

    history = repos.releases.get_release_version_history(order.id)
    assert len(history) == 1
    manifest = repos.releases.get_manifest_for_order(order.id)
    assert len(manifest.items) == 1
    assert len(manifest.items[0].observations) == 1


def test_manifest_read_does_not_filter_by_current_superseded_at(
    sessions: sessionmaker[Session],
) -> None:
    """DH-EX-014A's own critical snapshot semantic: a manifest entry names an exact observation
    id and must resolve it regardless of that observation's *current* `superseded_at` state.
    No code path in this ticket can ever actually supersede a manifested observation (correction
    is refused after release, DH-EX-014B does not exist yet) -- this test forces that future
    state directly to prove the *read path* itself is already correct, exactly mirroring this
    codebase's own established pattern of testing a currently-unreachable defense directly."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="hist-read")
    item_id = order.items[0].id
    tech = _labtech_identity_id(repos)
    observation = repos.results.record_observation(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot="Glucose",
        numeric_value=Decimal("5.1"),
        recorded_by_identity_id=tech,
    )
    repos.results.complete_entry(
        order_id=order.id,
        lab_order_item_id=item_id,
        lab_organization_id=lab.id,
        completed_by_identity_id=tech,
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="hist-read")

    # Force the future state directly -- no code path can do this today.
    with sessions() as session:
        raw_observation = session.get(LabObservation, observation.id)
        raw_observation.superseded_at = raw_observation.recorded_at
        session.commit()

    manifest = repos.releases.get_manifest_for_order(order.id)
    assert manifest is not None
    assert len(manifest.items[0].observations) == 1
    assert manifest.items[0].observations[0].id == observation.id


def test_manifest_entries_do_not_mix_up_items_within_the_same_order(
    sessions: sessionmaker[Session],
) -> None:
    """Integrity: each manifest entry's observation must belong to exactly the item it is
    recorded under -- never another item's container in the same order."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select_two_items(
        repos, sessions, lab_id=lab.id, key="manifest-item-integrity"
    )
    tech = _labtech_identity_id(repos)
    expected_by_item: dict = {}
    for i, item in enumerate(order.items):
        obs = repos.results.record_numeric(
            recorded_by_identity_id=tech,
            order_id=order.id,
            lab_order_item_id=item.id,
            lab_organization_id=lab.id,
            value=Decimal(f"{i}.0"),
        )
        expected_by_item[item.id] = obs.observation.id
    _release(repos, order_id=order.id, lab_id=lab.id, key="manifest-item-integrity")

    manifest = repos.releases.get_manifest_for_order(order.id)
    assert manifest is not None
    for item_record in manifest.items:
        observation_ids = {o.id for o in item_record.observations}
        assert observation_ids == {expected_by_item[item_record.item_id]}


def test_manifest_never_includes_a_different_orders_item(
    sessions: sessionmaker[Session],
) -> None:
    """Integrity: releasing order A must never manifest an item/observation belonging to a
    different order B, even one selected to the same lab."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    tech = _labtech_identity_id(repos)

    order_a, _practitioner_a = _create_and_select(
        repos, sessions, lab_id=lab.id, key="manifest-order-integrity-a"
    )
    repos.results.record_numeric(
        recorded_by_identity_id=tech,
        order_id=order_a.id,
        lab_order_item_id=order_a.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1.0"),
    )

    order_b, _practitioner_b = _create_and_select(
        repos, sessions, lab_id=lab.id, key="manifest-order-integrity-b"
    )
    repos.results.record_numeric(
        recorded_by_identity_id=tech,
        order_id=order_b.id,
        lab_order_item_id=order_b.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("2.0"),
    )

    _release(repos, order_id=order_a.id, lab_id=lab.id, key="manifest-order-integrity-a")

    manifest_a = repos.releases.get_manifest_for_order(order_a.id)
    assert manifest_a is not None
    manifest_a_item_ids = {item_record.item_id for item_record in manifest_a.items}
    assert manifest_a_item_ids == {order_a.items[0].id}
    assert order_b.items[0].id not in manifest_a_item_ids
    assert repos.releases.get_manifest_for_order(order_b.id) is None


def test_duplicate_review_for_same_release_version_is_rejected_by_the_database(
    sessions: sessionmaker[Session],
) -> None:
    """Database-enforced, not merely application-checked: `uq_exchange_doctor_reviews_release_
    version_id` must refuse a second `DoctorReview` row ever binding to the same release version,
    even via a direct insert bypassing every application-level check."""
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, practitioner = _create_and_select(repos, sessions, lab_id=lab.id, key="dup-review-v1")
    repos.results.record_numeric(
        recorded_by_identity_id=_labtech_identity_id(repos),
        order_id=order.id,
        lab_order_item_id=order.items[0].id,
        lab_organization_id=lab.id,
        value=Decimal("1.0"),
    )
    _release(repos, order_id=order.id, lab_id=lab.id, key="dup-review-v1")
    version = repos.releases.get_latest_release_version(order.id)

    with sessions() as session:
        session.add(
            DoctorReview(
                lab_order_id=order.id,
                release_version_id=version.id,
                status=DoctorReviewStatus.PENDING.value,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
