import uuid
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
from daher_adapter.exchange.models import LabOrderStatus, LabPrice
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.selection import (
    DifferentLabAlreadySelected,
    LabCannotFulfillOrder,
    LabSelectionRepository,
    StalePriceSelection,
)


def _build(sessions: sessionmaker[Session]):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    return organizations, catalog, patients, practitioners, orders, selections, directory


def _listed_and_accepting(directory: LabDirectoryProfileRepository, lab_id) -> None:
    """DH-EX-005: `select()` now revalidates the full DH-EX-003 marketplace-eligibility contract
    at commit time -- every test lab used for a real selection attempt must be listed and
    accepting orders, matching what a real marketplace-eligible lab would look like."""
    directory.upsert(
        lab_organization_id=lab_id,
        public_address="Test Lab Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )


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


def _current_price_id(sessions: sessionmaker[Session], offering_id) -> uuid.UUID | None:
    with sessions() as session:
        return session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering_id, LabPrice.superseded_at.is_(None)
            )
        )


def test_one_chosen_lab_is_recorded_and_order_transitions_to_selected(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    lab = make_lab(organizations)
    _listed_and_accepting(directory, lab.id)
    order, _entry, offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab.id
    )
    price_id = _current_price_id(sessions, offering.id)

    selection, created = selections.select(
        lab_order_id=order.id,
        lab_organization_id=lab.id,
        expected_price_ids=frozenset({price_id}),
    )

    assert created is True
    assert selection.lab_organization_id == lab.id
    assert selection.selected_via_patient_access_grant_id is None
    refreshed = orders.get(order.id)
    assert refreshed.status == LabOrderStatus.SELECTED.value


def test_price_snapshot_frozen_at_selection_survives_a_later_price_change(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    lab = make_lab(organizations)
    _listed_and_accepting(directory, lab.id)
    order, _entry, offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab.id
    )
    price_id = _current_price_id(sessions, offering.id)

    selections.select(
        lab_order_id=order.id,
        lab_organization_id=lab.id,
        expected_price_ids=frozenset({price_id}),
    )
    selected_item = orders.get(order.id).items[0]
    assert selected_item.selected_price_amount == Decimal("500.00")
    assert selected_item.selected_price_currency == "DJF"

    # Lab raises its price after the patient already selected and was quoted 500.00.
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("999.00"),
        currency="DJF",
        expected_current_price_id=price_id,
    )

    still_frozen = orders.get(order.id).items[0]
    assert still_frozen.selected_price_amount == Decimal("500.00")


def test_unselected_lab_cannot_become_the_implicit_target(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    lab_selected = make_lab(organizations, name="Lab Selected")
    lab_other = make_lab(organizations, name="Lab Other")
    _listed_and_accepting(directory, lab_selected.id)
    _listed_and_accepting(directory, lab_other.id)
    order, entry, offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab_selected.id
    )
    # Lab Other is a real, legitimate alternative -- it can fulfill the same test -- but is never
    # chosen, so it must never end up recorded as the selection.
    other_offering, _ = catalog.create_offering(
        lab_organization_id=lab_other.id, catalog_entry_id=entry.id
    )
    catalog.set_price(
        offering_id=other_offering.id,
        amount=Decimal("500.00"),
        currency="DJF",
        expected_current_price_id=None,
    )
    price_id = _current_price_id(sessions, offering.id)

    selections.select(
        lab_order_id=order.id,
        lab_organization_id=lab_selected.id,
        expected_price_ids=frozenset({price_id}),
    )

    recorded = selections.get_for_order(order.id)
    assert recorded is not None
    assert recorded.lab_organization_id == lab_selected.id
    assert recorded.lab_organization_id != lab_other.id


def test_selecting_a_lab_that_cannot_fulfill_the_order_fails_closed(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    lab_with_offering = make_lab(organizations, name="Lab With Offering")
    lab_without_offering = make_lab(organizations, name="Lab Without Offering")
    _listed_and_accepting(directory, lab_with_offering.id)
    _listed_and_accepting(directory, lab_without_offering.id)
    order, _entry, _offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab_with_offering.id
    )

    with pytest.raises(LabCannotFulfillOrder):
        selections.select(
            lab_order_id=order.id,
            lab_organization_id=lab_without_offering.id,
            expected_price_ids=frozenset(),
        )


def test_selecting_twice_for_a_different_lab_is_refused(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    lab_a = make_lab(organizations, name="Lab A")
    lab_b = make_lab(organizations, name="Lab B")
    _listed_and_accepting(directory, lab_a.id)
    _listed_and_accepting(directory, lab_b.id)
    order, entry, offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab_a.id
    )
    offering_b, _ = catalog.create_offering(lab_organization_id=lab_b.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering_b.id,
        amount=Decimal("500.00"),
        currency="DJF",
        expected_current_price_id=None,
    )
    price_id_a = _current_price_id(sessions, offering.id)
    price_id_b = _current_price_id(sessions, offering_b.id)

    selections.select(
        lab_order_id=order.id,
        lab_organization_id=lab_a.id,
        expected_price_ids=frozenset({price_id_a}),
    )

    with pytest.raises(DifferentLabAlreadySelected):
        selections.select(
            lab_order_id=order.id,
            lab_organization_id=lab_b.id,
            expected_price_ids=frozenset({price_id_b}),
        )


def test_selecting_same_lab_twice_is_a_safe_replay(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    lab = make_lab(organizations)
    _listed_and_accepting(directory, lab.id)
    order, _entry, offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab.id
    )
    price_id = _current_price_id(sessions, offering.id)

    first, first_created = selections.select(
        lab_order_id=order.id, lab_organization_id=lab.id, expected_price_ids=frozenset({price_id})
    )
    second, second_created = selections.select(
        lab_order_id=order.id, lab_organization_id=lab.id, expected_price_ids=frozenset({price_id})
    )
    assert first_created is True
    assert second_created is False
    assert first.id == second.id


def test_stale_price_id_is_refused(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    lab = make_lab(organizations)
    _listed_and_accepting(directory, lab.id)
    order, _entry, offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab.id
    )

    with pytest.raises(StalePriceSelection):
        selections.select(
            lab_order_id=order.id,
            lab_organization_id=lab.id,
            expected_price_ids=frozenset({uuid.uuid4()}),
        )
    # Refused selection must leave the order untouched.
    refreshed = orders.get(order.id)
    assert refreshed.status == LabOrderStatus.AWAITING_LAB_SELECTION.value
    assert refreshed.items[0].selected_price_amount is None


def test_unlisted_lab_cannot_be_selected(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    lab = make_lab(organizations)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    order, _entry, offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab.id
    )
    price_id = _current_price_id(sessions, offering.id)

    with pytest.raises(LabCannotFulfillOrder):
        selections.select(
            lab_order_id=order.id,
            lab_organization_id=lab.id,
            expected_price_ids=frozenset({price_id}),
        )


def test_not_accepting_orders_lab_cannot_be_selected(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    lab = make_lab(organizations)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=False,
    )
    order, _entry, offering = _create_order(
        organizations, catalog, patients, practitioners, orders, lab_id=lab.id
    )
    price_id = _current_price_id(sessions, offering.id)

    with pytest.raises(LabCannotFulfillOrder):
        selections.select(
            lab_order_id=order.id,
            lab_organization_id=lab.id,
            expected_price_ids=frozenset({price_id}),
        )


def test_multi_item_order_each_item_gets_its_own_lab_specific_price(
    sessions: sessionmaker[Session],
) -> None:
    """DH-EX-001R Part H: a multi-test order, one selected lab, each offering independently
    priced -- every item must carry the CORRECT price for its OWN catalog entry at that lab, not
    a shared/aggregated/first-item value."""
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    lab = make_lab(organizations)
    _listed_and_accepting(directory, lab.id)
    entry_cbc, _ = catalog.create_entry(code="DH-MULTI-CBC", display_name="CBC")
    entry_glu, _ = catalog.create_entry(code="DH-MULTI-GLU", display_name="Glucose")
    offering_cbc, _ = catalog.create_offering(
        lab_organization_id=lab.id, catalog_entry_id=entry_cbc.id
    )
    offering_glu, _ = catalog.create_offering(
        lab_organization_id=lab.id, catalog_entry_id=entry_glu.id
    )
    catalog.set_price(
        offering_id=offering_cbc.id,
        amount=Decimal("500.00"),
        currency="DJF",
        expected_current_price_id=None,
    )
    catalog.set_price(
        offering_id=offering_glu.id,
        amount=Decimal("250.00"),
        currency="DJF",
        expected_current_price_id=None,
    )

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

    price_id_cbc = _current_price_id(sessions, offering_cbc.id)
    price_id_glu = _current_price_id(sessions, offering_glu.id)
    selections.select(
        lab_order_id=order.id,
        lab_organization_id=lab.id,
        expected_price_ids=frozenset({price_id_cbc, price_id_glu}),
    )

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
