"""DH-EX-010: ``LabSelectionRepository.assign_by_provider`` -- provider/facility-assigned lab
routing with no price marketplace/consent step, ADR-043's pre-authorized launch option (a).

Repository-level tests. HTTP-level tests (authorization, the marketplace/directory separation
scenario, the patient-vs-provider provenance protection, and the ``0 DJF`` regression) live in
``test_exchange_provider_assignment_api.py``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from exchange_support import (
    make_lab,
    make_patient_reference,
    make_practitioner_reference,
    make_provider,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.models import (
    LabOrderStatus,
    LabPrice,
    Organization,
    PatientAccessGrant,
)
from daher_adapter.exchange.orders import InvalidOrderTransition as OrderInvalidTransition
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput, OrderNotFound
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.selection import (
    DifferentLabAlreadySelected,
    LabCannotFulfillOrder,
    LabSelectionRepository,
)
from daher_adapter.models import utc_now


def _build(sessions: sessionmaker[Session]):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    return organizations, catalog, patients, practitioners, orders, selections, directory


def _accepting_unlisted(directory: LabDirectoryProfileRepository, lab_id) -> None:
    """The provider-assignment-eligible shape: ``accepting_orders=True`` but deliberately
    ``listed=False`` -- an unlisted lab must remain assignable by a provider (a private referral
    relationship), even though it can never be a patient marketplace option."""
    directory.upsert(
        lab_organization_id=lab_id,
        public_address="Referral Lab Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )


def _create_order(organizations, catalog, patients, practitioners, orders, *, key="req-1"):
    """A one-item order with no lab offering/price seeded anywhere -- returns (order, entry)."""
    entry, _ = catalog.create_entry(code=f"DH-{key}", display_name=f"Test {key}")
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
    return provider, result.order, entry


def test_provider_assigns_lab_with_zero_price_rows_succeeds(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab = make_lab(organizations, name="ReferralLab")
    _accepting_unlisted(directory, lab.id)
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    # Deliberately never call catalog.set_price -- zero LabPrice rows exist anywhere for this lab.

    record, created = selections.assign_by_provider(
        lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
    )

    assert created is True
    assert record.lab_organization_id == lab.id
    assert record.selected_via_patient_access_grant_id is None
    refreshed = orders.get(order.id)
    assert refreshed.status == LabOrderStatus.SELECTED.value
    assert refreshed.items[0].selected_price_amount is None
    assert refreshed.items[0].selected_price_currency is None


def test_unlisted_but_accepting_lab_is_assignable_by_provider(
    sessions: sessionmaker[Session],
) -> None:
    """`listed` is not `accepting_orders` -- an unlisted lab is a valid private referral target."""
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab = make_lab(organizations, name="UnlistedLab")
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    record, created = selections.assign_by_provider(
        lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
    )
    assert created is True
    assert record.lab_organization_id == lab.id


def test_not_accepting_orders_lab_cannot_be_assigned(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab = make_lab(organizations, name="NotAcceptingLab")
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=False,
    )
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    with pytest.raises(LabCannotFulfillOrder):
        selections.assign_by_provider(
            lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
        )


def test_lab_with_no_directory_profile_cannot_be_assigned(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab = make_lab(organizations, name="NoProfileLab")
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    # No directory.upsert call at all -- no LabDirectoryProfile row exists.

    with pytest.raises(LabCannotFulfillOrder):
        selections.assign_by_provider(
            lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
        )


def test_inactive_lab_organization_cannot_be_assigned(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab = make_lab(organizations, name="InactiveLab")
    _accepting_unlisted(directory, lab.id)
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    with sessions() as session:
        org_row = session.get(Organization, lab.id)
        org_row.status = "INACTIVE"
        session.commit()

    with pytest.raises(LabCannotFulfillOrder):
        selections.assign_by_provider(
            lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
        )


def test_provider_organization_as_target_is_refused(sessions: sessionmaker[Session]) -> None:
    """A PROVIDER organization id (not a LAB) can never be the assignment target."""
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, _entry = _create_order(organizations, catalog, patients, practitioners, orders)
    other_provider = make_provider(organizations, name="NotALab")

    with pytest.raises(LabCannotFulfillOrder):
        selections.assign_by_provider(
            lab_order_id=order.id,
            lab_organization_id=other_provider.id,
            provider_organization_id=provider.id,
        )


def test_nonexistent_lab_cannot_be_assigned(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, _entry = _create_order(organizations, catalog, patients, practitioners, orders)

    with pytest.raises(LabCannotFulfillOrder):
        selections.assign_by_provider(
            lab_order_id=order.id,
            lab_organization_id=uuid.uuid4(),
            provider_organization_id=provider.id,
        )


def test_lab_missing_offering_for_one_item_cannot_be_assigned(
    sessions: sessionmaker[Session],
) -> None:
    """A multi-item order: the lab offers one requested test but not the other -- refused
    entirely, never a partial assignment."""
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    entry_a, _ = catalog.create_entry(code="DH-PART-A", display_name="Test A")
    entry_b, _ = catalog.create_entry(code="DH-PART-B", display_name="Test B")
    provider = make_provider(organizations, name="Clinic-part")
    patient = make_patient_reference(
        patients, provider_org_id=provider.id, source_identity_value="p"
    )
    practitioner = make_practitioner_reference(
        practitioners, provider_org_id=provider.id, source_identity_value="d"
    )
    order = orders.create(
        idempotency_key="req-part",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[
            OrderItemInput(catalog_entry_id=entry_a.id),
            OrderItemInput(catalog_entry_id=entry_b.id),
        ],
    ).order
    lab = make_lab(organizations, name="PartialLab")
    _accepting_unlisted(directory, lab.id)
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry_a.id)
    # No offering created for entry_b.

    with pytest.raises(LabCannotFulfillOrder):
        selections.assign_by_provider(
            lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
        )
    refreshed = orders.get(order.id)
    assert refreshed.status == LabOrderStatus.AWAITING_LAB_SELECTION.value


def test_inactive_offering_cannot_be_assigned(sessions: sessionmaker[Session]) -> None:
    """Acceptance criterion 10: catalog/orderability constraints remain enforced -- an offering
    the lab itself deactivated must refuse assignment exactly like a missing one."""
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab = make_lab(organizations, name="InactiveOfferingLab")
    _accepting_unlisted(directory, lab.id)
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_offering_active(offering.id, False)

    with pytest.raises(LabCannotFulfillOrder):
        selections.assign_by_provider(
            lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
        )


def test_inactive_catalog_entry_cannot_be_assigned(sessions: sessionmaker[Session]) -> None:
    """Acceptance criterion 10: a catalog entry deactivated after the offering was created must
    still block assignment -- the offering's own `active` flag alone is not the whole gate."""
    from daher_adapter.exchange.models import TestCatalogEntry

    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab = make_lab(organizations, name="InactiveEntryLab")
    _accepting_unlisted(directory, lab.id)
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    with sessions() as session:
        entry_row = session.get(TestCatalogEntry, entry.id)
        entry_row.active = False
        session.commit()

    with pytest.raises(LabCannotFulfillOrder):
        selections.assign_by_provider(
            lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
        )


def test_non_synthetic_catalog_entry_cannot_be_assigned(sessions: sessionmaker[Session]) -> None:
    """Acceptance criterion 10 / the pilot-phase clinical-content gate: a non-synthetic catalog
    entry must never become orderable through provider assignment either -- the same
    `is_synthetic` check `select()`'s own marketplace path already enforces."""
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider = make_provider(organizations, name="Clinic-nonsynth")
    entry, _ = catalog.create_entry(
        code="DH-NONSYNTH", display_name="Real Test", is_synthetic=False
    )
    patient = make_patient_reference(
        patients, provider_org_id=provider.id, source_identity_value="nonsynth"
    )
    practitioner = make_practitioner_reference(
        practitioners, provider_org_id=provider.id, source_identity_value="doc-nonsynth"
    )
    order = orders.create(
        idempotency_key="req-nonsynth",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order
    lab = make_lab(organizations, name="NonSynthLab")
    _accepting_unlisted(directory, lab.id)
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    with pytest.raises(LabCannotFulfillOrder):
        selections.assign_by_provider(
            lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
        )


def test_assign_same_lab_twice_is_idempotent_replay(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab = make_lab(organizations, name="ReplayLab")
    _accepting_unlisted(directory, lab.id)
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    first, first_created = selections.assign_by_provider(
        lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
    )
    second, second_created = selections.assign_by_provider(
        lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
    )
    assert first_created is True
    assert second_created is False
    assert first.id == second.id


def test_assign_different_lab_after_existing_assignment_is_refused(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab_a = make_lab(organizations, name="LabA")
    lab_b = make_lab(organizations, name="LabB")
    _accepting_unlisted(directory, lab_a.id)
    _accepting_unlisted(directory, lab_b.id)
    catalog.create_offering(lab_organization_id=lab_a.id, catalog_entry_id=entry.id)
    catalog.create_offering(lab_organization_id=lab_b.id, catalog_entry_id=entry.id)

    selections.assign_by_provider(
        lab_order_id=order.id, lab_organization_id=lab_a.id, provider_organization_id=provider.id
    )
    with pytest.raises(DifferentLabAlreadySelected):
        selections.assign_by_provider(
            lab_order_id=order.id,
            lab_organization_id=lab_b.id,
            provider_organization_id=provider.id,
        )


def test_cannot_assign_order_owned_by_different_provider(sessions: sessionmaker[Session]) -> None:
    """The tenant boundary is enforced *inside* the locked mutation, not only by a pre-route
    read -- a caller invoking this repository method directly with the wrong
    ``provider_organization_id`` must never assign someone else's order."""
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    _real_provider, order, entry = _create_order(
        organizations, catalog, patients, practitioners, orders
    )
    impostor_provider = make_provider(organizations, name="Impostor")
    lab = make_lab(organizations, name="TenantLab")
    _accepting_unlisted(directory, lab.id)
    catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    with pytest.raises(OrderNotFound):
        selections.assign_by_provider(
            lab_order_id=order.id,
            lab_organization_id=lab.id,
            provider_organization_id=impostor_provider.id,
        )
    refreshed = orders.get(order.id)
    assert refreshed.status == LabOrderStatus.AWAITING_LAB_SELECTION.value


def test_cannot_assign_lab_for_order_not_awaiting_selection(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab_a = make_lab(organizations, name="FirstLab")
    lab_b = make_lab(organizations, name="SecondLab")
    _accepting_unlisted(directory, lab_a.id)
    _accepting_unlisted(directory, lab_b.id)
    catalog.create_offering(lab_organization_id=lab_a.id, catalog_entry_id=entry.id)
    catalog.create_offering(lab_organization_id=lab_b.id, catalog_entry_id=entry.id)
    orders.transition(order.id, LabOrderStatus.SELECTED)
    orders.transition(order.id, LabOrderStatus.REJECTED)

    with pytest.raises(OrderInvalidTransition):
        selections.assign_by_provider(
            lab_order_id=order.id,
            lab_organization_id=lab_b.id,
            provider_organization_id=provider.id,
        )


def test_provider_assignment_after_patient_selection_preserves_patient_selection(
    sessions: sessionmaker[Session],
) -> None:
    """The most important provenance test: a provider naming the *same* lab a patient already
    legitimately selected must never rewrite, re-audit, or otherwise disturb that selection's
    frozen price snapshot or its `PatientAccessGrant` provenance."""
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab = make_lab(organizations, name="PatientChosenLab")
    _listed_and_priced_offering = catalog.create_offering(
        lab_organization_id=lab.id, catalog_entry_id=entry.id
    )[0]
    catalog.set_price(
        offering_id=_listed_and_priced_offering.id,
        amount=Decimal("500.00"),
        currency="DJF",
        expected_current_price_id=None,
    )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    price_id = None
    with sessions() as session:
        price_id = session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == _listed_and_priced_offering.id,
                LabPrice.superseded_at.is_(None),
            )
        )
    fake_grant_id = uuid.uuid4()
    # A real PatientAccessGrant row is required by the FK `select()` reads when
    # `patient_access_grant_id` is not None -- in production the HTTP layer's `patient_access`
    # repository creates and validates this; this test creates the row directly since it is
    # exercising `selections.select`/`assign_by_provider` at the repository layer only.
    with sessions() as session:
        session.add(
            PatientAccessGrant(
                id=fake_grant_id,
                lab_order_id=order.id,
                token_hash="x" * 64,
                expires_at=utc_now() + timedelta(hours=1),
            )
        )
        session.commit()
    patient_selection, patient_created = selections.select(
        lab_order_id=order.id,
        lab_organization_id=lab.id,
        expected_price_ids=frozenset({price_id}),
        patient_access_grant_id=fake_grant_id,
    )
    assert patient_created is True
    assert patient_selection.selected_via_patient_access_grant_id == fake_grant_id

    # Provider now "assigns" the same lab the patient already chose.
    provider_selection, provider_created = selections.assign_by_provider(
        lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
    )

    assert provider_created is False
    assert provider_selection.id == patient_selection.id
    assert provider_selection.selected_via_patient_access_grant_id == fake_grant_id, (
        "the patient's own grant provenance must survive a later provider call naming the same lab"
    )
    refreshed = orders.get(order.id)
    assert refreshed.items[0].selected_price_amount == Decimal("500.00"), (
        "the patient's frozen price snapshot must never be erased or rewritten by a later "
        "provider assignment"
    )


def test_provider_assignment_succeeds_after_all_price_rows_removed(
    sessions: sessionmaker[Session],
) -> None:
    """Price-infrastructure independence: a lab that legitimately HAD prices, which are then
    removed entirely, must remain provider-assignable -- this is not merely "never priced", it
    proves removal of pricing does not disturb provider routing at all."""
    organizations, catalog, patients, practitioners, orders, selections, directory = _build(
        sessions
    )
    provider, order, entry = _create_order(organizations, catalog, patients, practitioners, orders)
    lab = make_lab(organizations, name="PriceRemovedLab")
    _accepting_unlisted(directory, lab.id)
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("500.00"),
        currency="DJF",
        expected_current_price_id=None,
    )
    with sessions() as session:
        session.execute(delete(LabPrice).where(LabPrice.lab_offering_id == offering.id))
        session.commit()
    with sessions() as session:
        remaining = session.scalar(select(LabPrice).where(LabPrice.lab_offering_id == offering.id))
        assert remaining is None, "test setup failed to remove all price rows"

    record, created = selections.assign_by_provider(
        lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
    )
    assert created is True
    assert record.lab_organization_id == lab.id
