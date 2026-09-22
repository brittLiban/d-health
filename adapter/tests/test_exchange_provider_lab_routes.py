"""DH-CLINIC-006: ``ProviderLabRouteRepository`` -- route CRUD (R-series) and the order-specific
whole-order eligibility read (E-series). HTTP-level tests (route management API, eligible-labs API,
authorization, anti-enumeration) live in ``test_exchange_provider_lab_routes_api.py``.
"""

from __future__ import annotations

import uuid

import pytest
from exchange_support import make_lab, make_offering, make_provider
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.models import Organization
from daher_adapter.exchange.organizations import OrganizationNotFound, OrganizationRepository
from daher_adapter.exchange.provider_lab_routes import (
    InvalidProviderLabRouteOrganizationType,
    ProviderLabRouteRepository,
)


def _build(sessions: sessionmaker[Session]):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    provider_lab_routes = ProviderLabRouteRepository(sessions, organizations)
    return organizations, catalog, directory, provider_lab_routes


def _accepting(directory: LabDirectoryProfileRepository, lab_id, *, listed: bool = False) -> None:
    directory.upsert(
        lab_organization_id=lab_id,
        public_address="Route Test Lab Address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=listed,
        accepting_orders=True,
    )


# -- R-series: route CRUD --------------------------------------------------------------------


def test_r1_create_route(sessions: sessionmaker[Session]) -> None:
    organizations, _catalog, _directory, provider_lab_routes = _build(sessions)
    provider = make_provider(organizations, name="R1Provider")
    lab = make_lab(organizations, name="R1Lab")

    record, created, changed = provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )

    assert created is True
    assert changed is True
    assert record.active is True
    assert record.provider_organization_id == provider.id
    assert record.lab_organization_id == lab.id
    assert record.lab_organization_name == "R1Lab"


def test_r2_replay_with_same_active_value_is_unchanged(sessions: sessionmaker[Session]) -> None:
    organizations, _catalog, _directory, provider_lab_routes = _build(sessions)
    provider = make_provider(organizations, name="R2Provider")
    lab = make_lab(organizations, name="R2Lab")

    first, first_created, first_changed = provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )
    second, second_created, second_changed = provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )

    assert first_created is True
    assert second_created is False
    assert second_changed is False
    assert first.id == second.id


def test_r3_deactivate_route(sessions: sessionmaker[Session]) -> None:
    organizations, _catalog, _directory, provider_lab_routes = _build(sessions)
    provider = make_provider(organizations, name="R3Provider")
    lab = make_lab(organizations, name="R3Lab")
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )

    record, created, changed = provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=False
    )

    assert created is False
    assert changed is True
    assert record.active is False


def test_r4_reactivate_route(sessions: sessionmaker[Session]) -> None:
    organizations, _catalog, _directory, provider_lab_routes = _build(sessions)
    provider = make_provider(organizations, name="R4Provider")
    lab = make_lab(organizations, name="R4Lab")
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=False
    )

    record, created, changed = provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )

    assert created is False
    assert changed is True
    assert record.active is True


def test_r5_unique_provider_lab_pair_never_duplicates(sessions: sessionmaker[Session]) -> None:
    organizations, _catalog, _directory, provider_lab_routes = _build(sessions)
    provider = make_provider(organizations, name="R5Provider")
    lab = make_lab(organizations, name="R5Lab")

    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=False
    )
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )

    routes = provider_lab_routes.list_for_provider(provider.id, active_only=False)
    assert len(routes) == 1


def test_r6a_lab_as_provider_side_is_rejected(sessions: sessionmaker[Session]) -> None:
    organizations, _catalog, _directory, provider_lab_routes = _build(sessions)
    lab_as_provider = make_lab(organizations, name="R6ALabAsProvider")
    lab = make_lab(organizations, name="R6ALab")

    with pytest.raises(InvalidProviderLabRouteOrganizationType):
        provider_lab_routes.upsert(
            provider_organization_id=lab_as_provider.id, lab_organization_id=lab.id, active=True
        )


def test_r6b_provider_as_lab_side_is_rejected(sessions: sessionmaker[Session]) -> None:
    organizations, _catalog, _directory, provider_lab_routes = _build(sessions)
    provider = make_provider(organizations, name="R6BProvider")
    provider_as_lab = make_provider(organizations, name="R6BProviderAsLab")

    with pytest.raises(InvalidProviderLabRouteOrganizationType):
        provider_lab_routes.upsert(
            provider_organization_id=provider.id,
            lab_organization_id=provider_as_lab.id,
            active=True,
        )


def test_r6c_nonexistent_organization_is_rejected(sessions: sessionmaker[Session]) -> None:
    organizations, _catalog, _directory, provider_lab_routes = _build(sessions)
    provider = make_provider(organizations, name="R6CProvider")

    with pytest.raises(OrganizationNotFound):
        provider_lab_routes.upsert(
            provider_organization_id=provider.id, lab_organization_id=uuid.uuid4(), active=True
        )


def test_r6d_route_creation_does_not_require_active_organizations(
    sessions: sessionmaker[Session],
) -> None:
    """DH-CLINIC-006: type-correctness is a permanent structural fact this repository enforces;
    ACTIVE status is re-checked fresh at eligibility/assignment time instead -- a route to a
    since-deactivated lab must still be readable/deactivatable, not permanently stuck."""
    organizations, _catalog, _directory, provider_lab_routes = _build(sessions)
    provider = make_provider(organizations, name="R6DProvider")
    lab = make_lab(organizations, name="R6DLab")
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )
    with sessions() as session:
        org_row = session.get(Organization, lab.id)
        org_row.status = "INACTIVE"
        session.commit()

    # Deactivating a route to a now-inactive lab must still succeed.
    record, created, changed = provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=False
    )
    assert created is False
    assert changed is True
    assert record.active is False


def test_r7_route_deactivation_never_touches_a_historical_selection(
    sessions: sessionmaker[Session],
) -> None:
    """DH-CLINIC-006 §11: deactivating a route must not delete/modify/cancel/hide an
    already-assigned order's ``LabSelection`` -- only prevent *new* assignments."""
    from daher_adapter.exchange.identity import (
        PatientReferenceRepository,
        PractitionerReferenceRepository,
    )
    from daher_adapter.exchange.models import LabOrderStatus
    from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
    from daher_adapter.exchange.selection import LabSelectionRepository, ProviderLabRouteNotActive

    organizations, catalog, directory, provider_lab_routes = _build(sessions)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)

    provider = make_provider(organizations, name="R7Provider")
    lab = make_lab(organizations, name="R7Lab")
    entry, _ = catalog.create_entry(code="DH-R7", display_name="R7 Test")
    _accepting(directory, lab.id)
    make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry.id)
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )
    patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="R7-PATIENT",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-practitioner-id",
        source_identity_value="R7-DOC",
    )
    order = orders.create(
        idempotency_key="r7-order",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order

    record, created = selections.assign_by_provider(
        lab_order_id=order.id, lab_organization_id=lab.id, provider_organization_id=provider.id
    )
    assert created is True

    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=False
    )

    # The historical selection is untouched.
    still_there = selections.get_for_order(order.id)
    assert still_there is not None
    assert still_there.id == record.id
    assert still_there.lab_organization_id == lab.id
    refreshed = orders.get(order.id)
    assert refreshed.status == LabOrderStatus.SELECTED.value

    # But a *second*, different order to the same now-deactivated-route lab is refused.
    other_patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="dhealth_provider_portal",
        source_identity_system="urn:daher-health:provider-portal-patient-id",
        source_identity_value="R7-PATIENT-2",
    )
    other_order = orders.create(
        idempotency_key="r7-order-2",
        provider_organization_id=provider.id,
        patient_reference_id=other_patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order
    with pytest.raises(ProviderLabRouteNotActive):
        selections.assign_by_provider(
            lab_order_id=other_order.id,
            lab_organization_id=lab.id,
            provider_organization_id=provider.id,
        )


# -- E-series: order-specific whole-order eligibility read ------------------------------------


def _order_setup(organizations, catalog, directory):
    provider = make_provider(organizations, name=f"EProvider-{uuid.uuid4()}")
    lab = make_lab(organizations, name=f"ELab-{uuid.uuid4()}")
    entry, _ = catalog.create_entry(code=f"DH-E-{uuid.uuid4()}", display_name="E Test")
    return provider, lab, entry


def test_e1_active_everything_is_eligible(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, directory, provider_lab_routes = _build(sessions)
    provider, lab, entry = _order_setup(organizations, catalog, directory)
    _accepting(directory, lab.id)
    make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry.id)
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )

    results = provider_lab_routes.find_whole_order_eligible_labs(provider.id, [entry.id])
    assert [r.lab_organization_id for r in results] == [lab.id]
    assert results[0].lab_organization_name == lab.name


def test_e2_no_route_is_not_eligible(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, directory, provider_lab_routes = _build(sessions)
    provider, lab, entry = _order_setup(organizations, catalog, directory)
    _accepting(directory, lab.id)
    make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry.id)

    results = provider_lab_routes.find_whole_order_eligible_labs(provider.id, [entry.id])
    assert results == []


def test_e3_inactive_route_is_not_eligible(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, directory, provider_lab_routes = _build(sessions)
    provider, lab, entry = _order_setup(organizations, catalog, directory)
    _accepting(directory, lab.id)
    make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry.id)
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=False
    )

    results = provider_lab_routes.find_whole_order_eligible_labs(provider.id, [entry.id])
    assert results == []


def test_e4_inactive_lab_organization_is_not_eligible(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, directory, provider_lab_routes = _build(sessions)
    provider, lab, entry = _order_setup(organizations, catalog, directory)
    _accepting(directory, lab.id)
    make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry.id)
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )
    with sessions() as session:
        org_row = session.get(Organization, lab.id)
        org_row.status = "INACTIVE"
        session.commit()

    results = provider_lab_routes.find_whole_order_eligible_labs(provider.id, [entry.id])
    assert results == []


def test_e5_not_accepting_orders_is_not_eligible(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, directory, provider_lab_routes = _build(sessions)
    provider, lab, entry = _order_setup(organizations, catalog, directory)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=False,
    )
    make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry.id)
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )

    results = provider_lab_routes.find_whole_order_eligible_labs(provider.id, [entry.id])
    assert results == []


def test_e6_inactive_offering_is_not_eligible(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, directory, provider_lab_routes = _build(sessions)
    provider, lab, entry = _order_setup(organizations, catalog, directory)
    _accepting(directory, lab.id)
    offering, _ = make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_offering_active(offering.id, False)
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )

    results = provider_lab_routes.find_whole_order_eligible_labs(provider.id, [entry.id])
    assert results == []


def test_e7_unlisted_lab_is_still_eligible(sessions: sessionmaker[Session]) -> None:
    organizations, catalog, directory, provider_lab_routes = _build(sessions)
    provider, lab, entry = _order_setup(organizations, catalog, directory)
    _accepting(directory, lab.id, listed=False)
    make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry.id)
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )

    results = provider_lab_routes.find_whole_order_eligible_labs(provider.id, [entry.id])
    assert [r.lab_organization_id for r in results] == [lab.id]


def test_e8_zero_price_rows_is_still_eligible(sessions: sessionmaker[Session]) -> None:
    """DH-CLINIC-006 §16: the mandatory negative control -- no LabPrice join anywhere, no price
    prerequisite of any kind."""
    organizations, catalog, directory, provider_lab_routes = _build(sessions)
    provider, lab, entry = _order_setup(organizations, catalog, directory)
    _accepting(directory, lab.id)
    make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry.id)
    # Deliberately never call catalog.set_price.
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )

    results = provider_lab_routes.find_whole_order_eligible_labs(provider.id, [entry.id])
    assert [r.lab_organization_id for r in results] == [lab.id]


def test_e9_a_different_providers_route_does_not_leak_eligibility(
    sessions: sessionmaker[Session],
) -> None:
    organizations, catalog, directory, provider_lab_routes = _build(sessions)
    provider, lab, entry = _order_setup(organizations, catalog, directory)
    other_provider = make_provider(organizations, name="E9OtherProvider")
    _accepting(directory, lab.id)
    make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry.id)
    provider_lab_routes.upsert(
        provider_organization_id=other_provider.id, lab_organization_id=lab.id, active=True
    )

    results = provider_lab_routes.find_whole_order_eligible_labs(provider.id, [entry.id])
    assert results == []


def test_e11_partial_capability_lab_is_excluded_entirely(sessions: sessionmaker[Session]) -> None:
    """DH-CLINIC-006 §14: a lab covering only some ordered tests is NOT eligible at all."""
    organizations, catalog, directory, provider_lab_routes = _build(sessions)
    provider = make_provider(organizations, name="E11Provider")
    lab = make_lab(organizations, name="E11Lab")
    entry_a, _ = catalog.create_entry(code="DH-E11-A", display_name="A")
    entry_b, _ = catalog.create_entry(code="DH-E11-B", display_name="B")
    _accepting(directory, lab.id)
    make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=entry_a.id)
    # No offering for entry_b.
    provider_lab_routes.upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )

    results = provider_lab_routes.find_whole_order_eligible_labs(
        provider.id, [entry_a.id, entry_b.id]
    )
    assert results == []


def test_e12_zero_required_entries_returns_empty(sessions: sessionmaker[Session]) -> None:
    organizations, _catalog, _directory, provider_lab_routes = _build(sessions)
    provider = make_provider(organizations, name="E12Provider")

    results = provider_lab_routes.find_whole_order_eligible_labs(provider.id, [])
    assert results == []
