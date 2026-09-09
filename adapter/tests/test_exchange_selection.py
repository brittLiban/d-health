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
from daher_adapter.exchange.models import LabOrderStatus
from daher_adapter.exchange.orders import InvalidOrderTransition, LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.selection import LabCannotFulfillOrder, LabSelectionRepository


def _build(sessions: sessionmaker[Session]):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)
    return organizations, catalog, patients, practitioners, orders, selections


def _create_order(organizations, catalog, patients, practitioners, orders, *, lab_id, key="req-1"):
    """Creates one priced offering for a fresh catalog entry at `lab_id`, then a one-item order
    for a fresh provider/patient/practitioner referencing it. Returns (order, entry, offering)."""
    entry, offering = make_priced_offering(
        catalog, lab_organization_id=lab_id, amount=Decimal("500.00"), code=f"DH-{key}"
    )
    provider = make_provider(organizations, name=f"Clinic-{key}")
    patient = make_patient_reference(
        patients, provider_org_id=provider.id, source_identity_value=key
    )
    practitioner = make_practitioner_reference(
        practitioners, provider_org_id=provider.id, source_identity_value=f"doc-{key}"
    )
    result = orders.create(
        idempotency_key=key,
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    )
    return result.order, entry, offering


def test_one_chosen_lab_is_recorded_and_order_transitions_to_selected(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders, selections = _build(sessions)
    lab = make_lab(organizations)
    order, _entry, _offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab.id
    )

    selection = selections.select(lab_order_id=order.id, lab_organization_id=lab.id)

    assert selection.lab_organization_id == lab.id
    refreshed = orders.get(order.id)
    assert refreshed.status == LabOrderStatus.SELECTED.value


def test_price_snapshot_frozen_at_selection_survives_a_later_price_change(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders, selections = _build(sessions)
    lab = make_lab(organizations)
    order, _entry, offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab.id
    )

    selections.select(lab_order_id=order.id, lab_organization_id=lab.id)
    selected_item = orders.get(order.id).items[0]
    assert selected_item.selected_price_amount == Decimal("500.00")
    assert selected_item.selected_price_currency == "DJF"

    # Lab raises its price after the patient already selected and was quoted 500.00.
    catalog.set_price(offering_id=offering.id, amount=Decimal("999.00"))

    still_frozen = orders.get(order.id).items[0]
    assert still_frozen.selected_price_amount == Decimal("500.00")


def test_unselected_lab_cannot_become_the_implicit_target(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections = _build(sessions)
    lab_selected = make_lab(organizations, name="Lab Selected")
    lab_other = make_lab(organizations, name="Lab Other")
    order, entry, _offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab_selected.id
    )
    # Lab Other is a real, legitimate alternative -- it can fulfill the same test -- but is never
    # chosen, so it must never end up recorded as the selection.
    other_offering = catalog.create_offering(
        lab_organization_id=lab_other.id, catalog_entry_id=entry.id
    )
    catalog.set_price(offering_id=other_offering.id, amount=Decimal("500.00"))

    selections.select(lab_order_id=order.id, lab_organization_id=lab_selected.id)

    recorded = selections.get_for_order(order.id)
    assert recorded is not None
    assert recorded.lab_organization_id == lab_selected.id
    assert recorded.lab_organization_id != lab_other.id


def test_selecting_a_lab_that_cannot_fulfill_the_order_fails_closed(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders, selections = _build(sessions)
    lab_with_offering = make_lab(organizations, name="Lab With Offering")
    lab_without_offering = make_lab(organizations, name="Lab Without Offering")
    order, _entry, _offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab_with_offering.id
    )

    with pytest.raises(LabCannotFulfillOrder):
        selections.select(lab_order_id=order.id, lab_organization_id=lab_without_offering.id)


def test_selecting_twice_for_the_same_order_is_refused(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections = _build(sessions)
    lab = make_lab(organizations)
    order, _entry, _offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab.id
    )

    selections.select(lab_order_id=order.id, lab_organization_id=lab.id)

    with pytest.raises(InvalidOrderTransition):
        selections.select(lab_order_id=order.id, lab_organization_id=lab.id)


def test_multi_item_order_each_item_gets_its_own_lab_specific_price(
    sessions: sessionmaker[Session],
) -> None:
    """DH-EX-001R Part H: a multi-test order, one selected lab, each offering independently
    priced -- every item must carry the CORRECT price for its OWN catalog entry at that lab, not
    a shared/aggregated/first-item value."""
    organizations, catalog, patients, practitioners, orders, selections = _build(sessions)
    lab = make_lab(organizations)
    entry_cbc = catalog.create_entry(code="DH-MULTI-CBC", display_name="CBC")
    entry_glu = catalog.create_entry(code="DH-MULTI-GLU", display_name="Glucose")
    offering_cbc = catalog.create_offering(
        lab_organization_id=lab.id, catalog_entry_id=entry_cbc.id
    )
    offering_glu = catalog.create_offering(
        lab_organization_id=lab.id, catalog_entry_id=entry_glu.id
    )
    catalog.set_price(offering_id=offering_cbc.id, amount=Decimal("500.00"))
    catalog.set_price(offering_id=offering_glu.id, amount=Decimal("250.00"))

    provider = make_provider(organizations, name="Clinic-multi")
    patient = make_patient_reference(
        patients, provider_org_id=provider.id, source_identity_value="multi"
    )
    practitioner = make_practitioner_reference(
        practitioners, provider_org_id=provider.id, source_identity_value="doc-multi"
    )
    order = orders.create(
        idempotency_key="req-multi",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[
            OrderItemInput(catalog_entry_id=entry_cbc.id),
            OrderItemInput(catalog_entry_id=entry_glu.id),
        ],
    ).order

    selections.select(lab_order_id=order.id, lab_organization_id=lab.id)

    refreshed = orders.get(order.id)
    prices_by_code = {
        item.catalog_code_snapshot: (item.selected_price_amount, item.selected_price_currency)
        for item in refreshed.items
    }
    assert prices_by_code["DH-MULTI-CBC"] == (Decimal("500.00"), "DJF")
    assert prices_by_code["DH-MULTI-GLU"] == (Decimal("250.00"), "DJF")
    # Reconstructing the total the patient was quoted is a plain sum over the frozen snapshots.
    total = sum((amount for amount, _currency in prices_by_code.values()), start=Decimal("0"))
    assert total == Decimal("750.00")
