"""DH-EX-003: the lab directory profile -- one-to-one per LAB organization, safety defaults,
upsert idempotency, and directory-browse visibility rules.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from exchange_support import make_lab, make_provider
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.models import LabDirectoryProfile
from daher_adapter.exchange.organizations import InvalidOrganizationType, OrganizationRepository


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


@pytest.fixture
def directory(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> LabDirectoryProfileRepository:
    return LabDirectoryProfileRepository(sessions, organizations)


def test_provider_organization_cannot_get_a_profile(
    directory: LabDirectoryProfileRepository, organizations: OrganizationRepository
) -> None:
    provider = make_provider(organizations)
    with pytest.raises(InvalidOrganizationType):
        directory.upsert(
            lab_organization_id=provider.id,
            public_address="123 Main St",
            latitude=None,
            longitude=None,
            public_phone=None,
            listed=True,
            accepting_orders=True,
        )


def test_first_upsert_creates_a_profile_with_stated_defaults(
    directory: LabDirectoryProfileRepository, organizations: OrganizationRepository
) -> None:
    lab = make_lab(organizations)
    record, created, changed = directory.upsert(
        lab_organization_id=lab.id,
        public_address="123 Main St, Djibouti City",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=False,
    )
    assert created is True
    assert changed is True
    assert record.listed is False
    assert record.accepting_orders is False


def test_replay_with_identical_values_is_unchanged(
    directory: LabDirectoryProfileRepository, organizations: OrganizationRepository
) -> None:
    lab = make_lab(organizations)
    kwargs = dict(
        lab_organization_id=lab.id,
        public_address="123 Main St",
        latitude=Decimal("11.588700"),
        longitude=Decimal("43.145600"),
        public_phone="+253-21-000000",
        listed=True,
        accepting_orders=True,
    )
    first, first_created, first_changed = directory.upsert(**kwargs)
    second, second_created, second_changed = directory.upsert(**kwargs)

    assert first_created is True
    assert second_created is False
    assert second_changed is False
    assert first.id == second.id


def test_upsert_with_new_values_is_updated(
    directory: LabDirectoryProfileRepository, organizations: OrganizationRepository
) -> None:
    lab = make_lab(organizations)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="Old address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=False,
    )
    updated, created, changed = directory.upsert(
        lab_organization_id=lab.id,
        public_address="New address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    assert created is False
    assert changed is True
    assert updated.public_address == "New address"
    assert updated.listed is True
    assert updated.accepting_orders is True


def test_only_one_profile_per_lab(
    directory: LabDirectoryProfileRepository, organizations: OrganizationRepository
) -> None:
    lab = make_lab(organizations)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="A",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=False,
    )
    record, created, _ = directory.upsert(
        lab_organization_id=lab.id,
        public_address="B",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=False,
    )
    assert created is False
    fetched = directory.get_for_lab(lab.id)
    assert fetched is not None
    assert fetched.id == record.id


def test_list_listed_excludes_unlisted_and_inactive_org(
    directory: LabDirectoryProfileRepository, organizations: OrganizationRepository
) -> None:
    listed_lab = make_lab(organizations, name="Listed Lab")
    unlisted_lab = make_lab(organizations, name="Unlisted Lab")
    directory.upsert(
        lab_organization_id=listed_lab.id,
        public_address="Listed",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    directory.upsert(
        lab_organization_id=unlisted_lab.id,
        public_address="Unlisted",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=False,
    )

    listing = directory.list_listed()
    listed_ids = {lab.lab_organization_id for lab in listing}
    assert listed_lab.id in listed_ids
    assert unlisted_lab.id not in listed_ids


def test_get_listing_for_lab_hides_unlisted_unless_include_unlisted(
    directory: LabDirectoryProfileRepository, organizations: OrganizationRepository
) -> None:
    lab = make_lab(organizations)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="Hidden",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=False,
    )
    assert directory.get_listing_for_lab(lab.id, include_unlisted=False) is None
    visible = directory.get_listing_for_lab(lab.id, include_unlisted=True)
    assert visible is not None
    assert visible.lab_organization_id == lab.id


# -- DB-level CHECK constraints (defense in depth beyond the API's Pydantic validation) -----------


def test_db_rejects_latitude_without_longitude(
    organizations: OrganizationRepository, sessions: sessionmaker[Session]
) -> None:
    lab = make_lab(organizations)
    with sessions() as session, pytest.raises(IntegrityError):
        session.add(
            LabDirectoryProfile(
                lab_organization_id=lab.id,
                public_address="X",
                latitude=Decimal("11.0"),
                longitude=None,
                listed=False,
                accepting_orders=False,
            )
        )
        session.commit()


def test_db_rejects_out_of_range_latitude(
    organizations: OrganizationRepository, sessions: sessionmaker[Session]
) -> None:
    lab = make_lab(organizations)
    with sessions() as session, pytest.raises(IntegrityError):
        session.add(
            LabDirectoryProfile(
                lab_organization_id=lab.id,
                public_address="X",
                latitude=Decimal("91.0"),
                longitude=Decimal("0.0"),
                listed=False,
                accepting_orders=False,
            )
        )
        session.commit()


def test_db_rejects_out_of_range_longitude(
    organizations: OrganizationRepository, sessions: sessionmaker[Session]
) -> None:
    lab = make_lab(organizations)
    with sessions() as session, pytest.raises(IntegrityError):
        session.add(
            LabDirectoryProfile(
                lab_organization_id=lab.id,
                public_address="X",
                latitude=Decimal("0.0"),
                longitude=Decimal("181.0"),
                listed=False,
                accepting_orders=False,
            )
        )
        session.commit()
