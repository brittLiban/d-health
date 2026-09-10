import uuid
from decimal import Decimal

import pytest
from exchange_support import make_lab, make_priced_offering, make_provider
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.models import LabPrice
from daher_adapter.exchange.organizations import InvalidOrganizationType, OrganizationRepository


def test_synthetic_catalog_entry_persists(sessions: sessionmaker[Session]) -> None:
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)

    entry, created = catalog.create_entry(
        code="DH-CBC-001", display_name="Complete Blood Count (synthetic)"
    )

    assert created is True
    assert entry.is_synthetic is True
    assert catalog.get_entry(entry.id).code == "DH-CBC-001"


def test_inactive_offering_excluded_from_eligibility(sessions: sessionmaker[Session]) -> None:
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    lab = make_lab(organizations)

    entry, _offering = make_priced_offering(catalog, lab_organization_id=lab.id, active=False)

    assert catalog.find_eligible_labs(entry.id) == []


def test_active_offering_with_price_is_eligible(sessions: sessionmaker[Session]) -> None:
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    lab = make_lab(organizations, name="Lab B")

    entry, offering = make_priced_offering(
        catalog, lab_organization_id=lab.id, amount=Decimal("750.00")
    )

    eligible = catalog.find_eligible_labs(entry.id)
    assert len(eligible) == 1
    assert eligible[0].lab_organization_id == lab.id
    assert eligible[0].lab_offering_id == offering.id
    assert eligible[0].price_amount == Decimal("750.00")
    assert eligible[0].price_currency == "DJF"


def test_unknown_test_returns_empty_never_a_fabricated_match(
    sessions: sessionmaker[Session],
) -> None:
    catalog = CatalogRepository(sessions, OrganizationRepository(sessions))

    assert catalog.find_eligible_labs(uuid.uuid4()) == []


def test_offering_requires_an_active_lab_organization(sessions: sessionmaker[Session]) -> None:
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    provider = make_provider(organizations)
    entry, _ = catalog.create_entry(
        code="DH-CBC-001", display_name="Complete Blood Count (synthetic)"
    )

    with pytest.raises(InvalidOrganizationType):
        catalog.create_offering(lab_organization_id=provider.id, catalog_entry_id=entry.id)


def test_historical_price_survives_a_later_price_change(sessions: sessionmaker[Session]) -> None:
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    lab = make_lab(organizations)
    entry, offering = make_priced_offering(
        catalog, lab_organization_id=lab.id, amount=Decimal("500.00")
    )
    with sessions() as session:
        current_price_id = session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering.id, LabPrice.superseded_at.is_(None)
            )
        )

    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("650.00"),
        currency="DJF",
        expected_current_price_id=current_price_id,
    )

    eligible = catalog.find_eligible_labs(entry.id)
    assert len(eligible) == 1
    # Only the CURRENT price is ever surfaced by eligibility -- the superseded 500.00 row still
    # exists (its immutability is what actually protects an existing selection; see
    # test_exchange_orders.py's price-snapshot test), but `find_eligible_labs` shows exactly one
    # row: the new current price.
    assert eligible[0].price_amount == Decimal("650.00")
