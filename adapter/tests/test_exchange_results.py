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
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import DoctorReviewStatus, ResultDeliveryStatus
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    DoctorReviewRepository,
    DuplicateResult,
    LabResultRepository,
    NoLabSelected,
    ResultDeliveryRepository,
    WrongLabForResult,
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
    results: LabResultRepository
    deliveries: ResultDeliveryRepository
    reviews: DoctorReviewRepository


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
        results=LabResultRepository(sessions),
        deliveries=ResultDeliveryRepository(sessions),
        reviews=DoctorReviewRepository(sessions),
    )


def _create_and_select(repos: Repos, *, lab_id, key: str = "req-1"):
    """Creates one priced offering, a one-item order against it, and immediately selects
    `lab_id`. Returns (refreshed order, ordering practitioner record)."""
    entry, _offering = make_priced_offering(
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
    repos.selections.select(lab_order_id=order.id, lab_organization_id=lab_id)
    return repos.orders.get(order.id), practitioner


def test_result_binds_to_the_correct_order_item_and_lab(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, lab_id=lab.id)
    item_id = order.items[0].id

    recorded = repos.results.record_numeric(
        lab_order_item_id=item_id, lab_organization_id=lab.id, value=Decimal("13.5"), unit="g/dL"
    )

    assert recorded.lab_order_item_id == item_id
    assert recorded.lab_organization_id == lab.id
    assert repos.results.get_for_item(item_id) is not None


def test_result_from_a_different_lab_than_selected_fails_closed(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab_selected = make_lab(repos.organizations, name="Lab Selected")
    lab_impostor = make_lab(repos.organizations, name="Lab Impostor")
    order, _practitioner = _create_and_select(repos, lab_id=lab_selected.id)
    item_id = order.items[0].id

    with pytest.raises(WrongLabForResult):
        repos.results.record_numeric(
            lab_order_item_id=item_id, lab_organization_id=lab_impostor.id, value=Decimal("13.5")
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

    with pytest.raises(NoLabSelected):
        repos.results.record_numeric(
            lab_order_item_id=order.items[0].id, lab_organization_id=lab.id, value=Decimal("1")
        )


def test_duplicate_result_for_the_same_item_refused(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, lab_id=lab.id)
    item_id = order.items[0].id
    repos.results.record_numeric(
        lab_order_item_id=item_id, lab_organization_id=lab.id, value=Decimal("1")
    )

    with pytest.raises(DuplicateResult):
        repos.results.record_numeric(
            lab_order_item_id=item_id, lab_organization_id=lab.id, value=Decimal("2")
        )


def test_result_completion_does_not_automatically_deliver(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, lab_id=lab.id)
    repos.results.record_numeric(
        lab_order_item_id=order.items[0].id, lab_organization_id=lab.id, value=Decimal("1")
    )

    delivery = repos.deliveries.get_for_order(order.id)
    assert delivery.status == ResultDeliveryStatus.PENDING.value
    assert delivery.delivered_at is None


def test_delivery_state_independent_of_result_and_destination_explicit(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, lab_id=lab.id)

    delivered = repos.deliveries.mark_delivered(order.id)

    assert delivered.status == ResultDeliveryStatus.DELIVERED.value
    assert delivered.delivered_at is not None
    assert delivered.provider_organization_id == order.provider_organization_id


def test_doctor_review_remains_pending_until_explicit_action(
    sessions: sessionmaker[Session],
) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, _practitioner = _create_and_select(repos, lab_id=lab.id)

    review = repos.reviews.get_for_order(order.id)
    assert review.status == DoctorReviewStatus.PENDING.value
    assert review.reviewed_at is None


def test_doctor_review_no_follow_up_required_explicit(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, practitioner = _create_and_select(repos, lab_id=lab.id)

    reviewed = repos.reviews.mark_no_follow_up_required(
        order.id, reviewed_by_practitioner_reference_id=practitioner.id
    )

    assert reviewed.status == DoctorReviewStatus.NO_FOLLOW_UP_REQUIRED.value
    assert reviewed.reviewed_at is not None
    assert reviewed.reviewed_by_practitioner_reference_id == practitioner.id


def test_doctor_review_follow_up_required_explicit(sessions: sessionmaker[Session]) -> None:
    repos = _build(sessions)
    lab = make_lab(repos.organizations)
    order, practitioner = _create_and_select(repos, lab_id=lab.id)

    reviewed = repos.reviews.mark_follow_up_required(
        order.id, reviewed_by_practitioner_reference_id=practitioner.id
    )

    assert reviewed.status == DoctorReviewStatus.FOLLOW_UP_REQUIRED.value
