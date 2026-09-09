from unittest.mock import patch

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
from daher_adapter.exchange.models import LabOrderStatus
from daher_adapter.exchange.orders import (
    EmptyOrder,
    IdempotencyKeyConflict,
    InvalidOrderTransition,
    LabOrderRepository,
    OrderItemInput,
    OrderNotFound,
)
from daher_adapter.exchange.organizations import InvalidOrganizationType, OrganizationRepository


def _build(sessions: sessionmaker[Session]):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    return organizations, catalog, patients, practitioners, orders


def test_complete_synthetic_laborder_graph_persists_with_explicit_ownership(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry_one, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-CBC-001")
    entry_two, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-GLU-001")
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)

    result = orders.create(
        idempotency_key="req-1",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[
            OrderItemInput(catalog_entry_id=entry_one.id),
            OrderItemInput(catalog_entry_id=entry_two.id),
        ],
    )

    assert result.created is True
    order = result.order
    assert order.provider_organization_id == provider.id
    assert order.patient_reference_id == patient.id
    assert order.practitioner_reference_id == practitioner.id
    assert order.status == LabOrderStatus.AWAITING_LAB_SELECTION.value
    assert len(order.items) == 2
    assert {item.catalog_code_snapshot for item in order.items} == {"DH-CBC-001", "DH-GLU-001"}


def test_empty_order_refused(sessions: sessionmaker[Session]) -> None:
    organizations, _catalog, patients, practitioners, orders = _build(sessions)
    provider = make_provider(organizations)
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)

    with pytest.raises(EmptyOrder):
        orders.create(
            idempotency_key="req-empty",
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            items=[],
        )


def test_order_sender_must_be_an_active_provider_organization(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders = _build(sessions)
    lab = make_lab(organizations)
    entry, _ = make_priced_offering(catalog, lab_organization_id=lab.id)
    patient = make_patient_reference(patients, provider_org_id=lab.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=lab.id)

    with pytest.raises(InvalidOrganizationType):
        orders.create(
            idempotency_key="req-wrong-sender",
            provider_organization_id=lab.id,  # a LAB, not a PROVIDER
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            items=[OrderItemInput(catalog_entry_id=entry.id)],
        )


def test_same_idempotency_key_returns_the_existing_order(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry, _ = make_priced_offering(catalog, lab_organization_id=lab.id)
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)
    items = [OrderItemInput(catalog_entry_id=entry.id)]

    first = orders.create(
        idempotency_key="req-dup",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=items,
    )
    second = orders.create(
        idempotency_key="req-dup",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=items,
    )

    assert first.created is True
    assert second.created is False
    assert first.order.id == second.order.id


def test_new_idempotency_key_with_identical_payload_creates_a_new_order(
    sessions: sessionmaker[Session],
) -> None:
    """DH-011H's lesson applied at this boundary: a genuinely new intentional order is never
    suppressed just because its clinical content matches a prior order."""
    organizations, catalog, patients, practitioners, orders = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry, _ = make_priced_offering(catalog, lab_organization_id=lab.id)
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)
    items = [OrderItemInput(catalog_entry_id=entry.id)]

    first = orders.create(
        idempotency_key="req-A",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=items,
    )
    second = orders.create(
        idempotency_key="req-B",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=items,
    )

    assert first.created is True
    assert second.created is True
    assert first.order.id != second.order.id


def test_invalid_state_transition_refused(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry, _ = make_priced_offering(catalog, lab_organization_id=lab.id)
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)
    created = orders.create(
        idempotency_key="req-invalid-transition",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order

    # AWAITING_LAB_SELECTION cannot jump straight to COMPLETED.
    with pytest.raises(InvalidOrderTransition):
        orders.transition(created.id, LabOrderStatus.COMPLETED)


def test_completed_order_cannot_transition_again(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry, _ = make_priced_offering(catalog, lab_organization_id=lab.id)
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)
    created = orders.create(
        idempotency_key="req-terminal",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order

    orders.transition(created.id, LabOrderStatus.SELECTED)
    orders.transition(created.id, LabOrderStatus.ACCEPTED)
    orders.transition(created.id, LabOrderStatus.IN_PROGRESS)
    completed = orders.transition(created.id, LabOrderStatus.COMPLETED)
    assert completed.status == LabOrderStatus.COMPLETED.value

    with pytest.raises(InvalidOrderTransition):
        orders.transition(created.id, LabOrderStatus.IN_PROGRESS)


# --- DH-EX-001R Part F: order-aggregate transaction atomicity, by real fault injection ---
#
# `LabOrderRepository.create` has exactly two flush points in its success path: one explicit
# `session.flush()` right after `session.add(order)` (which, via the `order.items` relationship's
# default save-update cascade, inserts LabOrder and every LabOrderItem together in that same
# flush -- there is no separate "after LabOrder insert" vs "after item insert" boundary in this
# implementation, they are the same statement batch), and the implicit flush inside the final
# `session.commit()` that also durably writes the DoctorReview/ResultDelivery rows added in
# between. The three tests below inject a real exception at each of the three points that
# actually exist between those flushes: before DoctorReview is even constructed, before
# ResultDelivery is constructed, and at the commit call itself -- proving the whole aggregate
# rolls back in every case, and a retry with the same idempotency key afterward produces exactly
# one complete aggregate with no orphan rows left by the failed attempt.


def _seeded_create_args(sessions: sessionmaker[Session]):
    organizations, catalog, patients, practitioners, orders = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry, _ = make_priced_offering(catalog, lab_organization_id=lab.id)
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)
    return orders, {
        "provider_organization_id": provider.id,
        "patient_reference_id": patient.id,
        "practitioner_reference_id": practitioner.id,
        "items": [OrderItemInput(catalog_entry_id=entry.id)],
    }


def _assert_no_trace(
    orders: LabOrderRepository, provider_organization_id, idempotency_key: str
) -> None:
    with pytest.raises(OrderNotFound):
        orders.get_by_idempotency_key(provider_organization_id, idempotency_key)


def test_failure_before_doctor_review_rolls_back_the_whole_aggregate(
    sessions: sessionmaker[Session],
) -> None:
    orders, args = _seeded_create_args(sessions)
    key = "req-fault-before-review"

    with (
        patch("daher_adapter.exchange.orders.DoctorReview", side_effect=RuntimeError("injected")),
        pytest.raises(RuntimeError, match="injected"),
    ):
        orders.create(idempotency_key=key, **args)

    _assert_no_trace(orders, args["provider_organization_id"], key)

    retried = orders.create(idempotency_key=key, **args)
    assert retried.created is True
    assert len(retried.order.items) == 1


def test_failure_before_result_delivery_rolls_back_the_whole_aggregate(
    sessions: sessionmaker[Session],
) -> None:
    orders, args = _seeded_create_args(sessions)
    key = "req-fault-before-delivery"

    with (
        patch("daher_adapter.exchange.orders.ResultDelivery", side_effect=RuntimeError("injected")),
        pytest.raises(RuntimeError, match="injected"),
    ):
        orders.create(idempotency_key=key, **args)

    _assert_no_trace(orders, args["provider_organization_id"], key)

    retried = orders.create(idempotency_key=key, **args)
    assert retried.created is True
    assert len(retried.order.items) == 1


def test_failure_at_commit_rolls_back_the_whole_aggregate(sessions: sessionmaker[Session]) -> None:
    orders, args = _seeded_create_args(sessions)
    key = "req-fault-at-commit"

    with (
        patch.object(Session, "commit", side_effect=RuntimeError("injected-before-commit")),
        pytest.raises(RuntimeError, match="injected-before-commit"),
    ):
        orders.create(idempotency_key=key, **args)

    _assert_no_trace(orders, args["provider_organization_id"], key)

    retried = orders.create(idempotency_key=key, **args)
    assert retried.created is True


# --- DH-EX-001R Part C: idempotency conflict semantics -----------------------------------------
#
# The idempotency key represents one intentional submission, not clinical equivalence. A
# resubmitted key is only a legitimate replay if the canonical immutable identifiers (patient,
# practitioner, requested test set) match exactly -- never compared via mutable display fields.


def test_same_key_different_patient_is_a_conflict_not_a_silent_swap(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry, _ = make_priced_offering(catalog, lab_organization_id=lab.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)
    patient_a = make_patient_reference(
        patients, provider_org_id=provider.id, source_identity_value="A"
    )
    patient_b = make_patient_reference(
        patients, provider_org_id=provider.id, source_identity_value="B"
    )
    key = "req-conflict-patient"

    orders.create(
        idempotency_key=key,
        provider_organization_id=provider.id,
        patient_reference_id=patient_a.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    )

    with pytest.raises(IdempotencyKeyConflict):
        orders.create(
            idempotency_key=key,
            provider_organization_id=provider.id,
            patient_reference_id=patient_b.id,
            practitioner_reference_id=practitioner.id,
            items=[OrderItemInput(catalog_entry_id=entry.id)],
        )

    # No second order was created for this key -- the original, for patient A, is untouched.
    solo = orders.get_by_idempotency_key(provider.id, key)
    assert solo.patient_reference_id == patient_a.id


def test_same_key_different_practitioner_is_a_conflict(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry, _ = make_priced_offering(catalog, lab_organization_id=lab.id)
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    doctor_a = make_practitioner_reference(
        practitioners, provider_org_id=provider.id, source_identity_value="Dr-A"
    )
    doctor_b = make_practitioner_reference(
        practitioners, provider_org_id=provider.id, source_identity_value="Dr-B"
    )
    key = "req-conflict-practitioner"

    orders.create(
        idempotency_key=key,
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=doctor_a.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    )

    with pytest.raises(IdempotencyKeyConflict):
        orders.create(
            idempotency_key=key,
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=doctor_b.id,
            items=[OrderItemInput(catalog_entry_id=entry.id)],
        )


def test_same_key_different_test_set_is_a_conflict(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders = _build(sessions)
    provider = make_provider(organizations)
    lab = make_lab(organizations)
    entry_one, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-CBC-001")
    entry_two, _ = make_priced_offering(catalog, lab_organization_id=lab.id, code="DH-GLU-001")
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)
    key = "req-conflict-items"

    orders.create(
        idempotency_key=key,
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry_one.id)],
    )

    with pytest.raises(IdempotencyKeyConflict):
        orders.create(
            idempotency_key=key,
            provider_organization_id=provider.id,
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            items=[OrderItemInput(catalog_entry_id=entry_two.id)],
        )


def test_same_key_different_provider_org_creates_two_independent_orders(
    sessions: sessionmaker[Session],
) -> None:
    """DH-EX-001R Part D: the idempotency key is scoped to (provider_organization_id,
    idempotency_key), not global -- so the same key string used by two different organizations is
    not the same idempotency identity at all. Each organization's submission is independent and
    legitimately creates its own order; this is the intended effect of the org-scoping fix, not a
    conflict."""
    organizations, catalog, patients, practitioners, orders = _build(sessions)
    provider_a = make_provider(organizations, name="Clinic A")
    provider_b = make_provider(organizations, name="Clinic B")
    lab = make_lab(organizations)
    entry, _ = make_priced_offering(catalog, lab_organization_id=lab.id)
    patient_a = make_patient_reference(
        patients, provider_org_id=provider_a.id, source_identity_value="A"
    )
    patient_b = make_patient_reference(
        patients, provider_org_id=provider_b.id, source_identity_value="B"
    )
    practitioner_a = make_practitioner_reference(practitioners, provider_org_id=provider_a.id)
    practitioner_b = make_practitioner_reference(practitioners, provider_org_id=provider_b.id)
    key = "req-shared-string"

    result_a = orders.create(
        idempotency_key=key,
        provider_organization_id=provider_a.id,
        patient_reference_id=patient_a.id,
        practitioner_reference_id=practitioner_a.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    )
    result_b = orders.create(
        idempotency_key=key,
        provider_organization_id=provider_b.id,
        patient_reference_id=patient_b.id,
        practitioner_reference_id=practitioner_b.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    )

    assert result_a.created is True
    assert result_b.created is True
    assert result_a.order.id != result_b.order.id
