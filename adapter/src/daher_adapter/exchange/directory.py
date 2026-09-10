"""DH-EX-003: the lab directory profile -- a LAB organization's marketplace visibility/location
record, strictly one-to-one, never created for a PROVIDER organization.

`upsert` is a PUT-shaped, full-replace idempotent write: it never returns a partial update, and a
replay with identical values is a genuine no-op (`changed=False`), matching the price/membership/
offering idempotency discipline established elsewhere in this package. `listed`/`accepting_orders`
default to `False` in the API schema layer (`exchange/api.py`), not here -- this repository writes
exactly what it is given.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import (
    LabDirectoryProfile,
    Organization,
    OrganizationStatus,
    OrganizationType,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.models import utc_now


@dataclass(frozen=True, slots=True)
class LabDirectoryProfileRecord:
    id: uuid.UUID
    lab_organization_id: uuid.UUID
    listed: bool
    accepting_orders: bool
    public_address: str
    latitude: Decimal | None
    longitude: Decimal | None
    public_phone: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class LabDirectoryListing:
    """One row of directory-browsing output -- an ``Organization`` joined with its
    ``LabDirectoryProfile``. Never constructed for an unlisted lab by `list_listed`; may be
    constructed for one by `get_listing_for_lab(..., include_unlisted=True)`, the platform-admin
    detail path."""

    lab_organization_id: uuid.UUID
    lab_organization_name: str
    listed: bool
    accepting_orders: bool
    public_address: str
    latitude: Decimal | None
    longitude: Decimal | None
    public_phone: str | None


class LabDirectoryProfileRepository:
    def __init__(
        self, session_factory: sessionmaker[Session], organizations: OrganizationRepository
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations

    def get_for_lab(self, lab_organization_id: uuid.UUID) -> LabDirectoryProfileRecord | None:
        with self._sessions() as session:
            profile = session.scalar(
                select(LabDirectoryProfile).where(
                    LabDirectoryProfile.lab_organization_id == lab_organization_id
                )
            )
            return None if profile is None else _to_record(profile)

    def upsert(
        self,
        *,
        lab_organization_id: uuid.UUID,
        public_address: str,
        latitude: Decimal | None,
        longitude: Decimal | None,
        public_phone: str | None,
        listed: bool,
        accepting_orders: bool,
        session: Session | None = None,
    ) -> tuple[LabDirectoryProfileRecord, bool, bool]:
        """Returns ``(record, created, changed)``. Fails closed (via
        `OrganizationRepository.require_type`) if `lab_organization_id` does not reference an
        ACTIVE LAB organization -- a PROVIDER organization, or an inactive one, never gets a
        profile written here."""
        self._organizations.require_type(lab_organization_id, OrganizationType.LAB)
        if session is not None:
            return self._upsert_in_session(
                session,
                lab_organization_id=lab_organization_id,
                public_address=public_address,
                latitude=latitude,
                longitude=longitude,
                public_phone=public_phone,
                listed=listed,
                accepting_orders=accepting_orders,
            )
        with self._sessions() as owned_session:
            record, created, changed = self._upsert_in_session(
                owned_session,
                lab_organization_id=lab_organization_id,
                public_address=public_address,
                latitude=latitude,
                longitude=longitude,
                public_phone=public_phone,
                listed=listed,
                accepting_orders=accepting_orders,
            )
            owned_session.commit()
            return record, created, changed

    def _upsert_in_session(
        self,
        session: Session,
        *,
        lab_organization_id: uuid.UUID,
        public_address: str,
        latitude: Decimal | None,
        longitude: Decimal | None,
        public_phone: str | None,
        listed: bool,
        accepting_orders: bool,
    ) -> tuple[LabDirectoryProfileRecord, bool, bool]:
        existing = session.scalar(
            select(LabDirectoryProfile)
            .where(LabDirectoryProfile.lab_organization_id == lab_organization_id)
            .with_for_update()
        )
        now = utc_now()
        if existing is None:
            profile = LabDirectoryProfile(
                lab_organization_id=lab_organization_id,
                public_address=public_address,
                latitude=latitude,
                longitude=longitude,
                public_phone=public_phone,
                listed=listed,
                accepting_orders=accepting_orders,
                created_at=now,
                updated_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(profile)
                    session.flush()
                return _to_record(profile), True, True
            except IntegrityError:
                # Lost a create race to a concurrent caller -- fall through and treat this as an
                # update against the row that now exists, never a second row.
                existing = session.scalar(
                    select(LabDirectoryProfile)
                    .where(LabDirectoryProfile.lab_organization_id == lab_organization_id)
                    .with_for_update()
                )
                if existing is None:
                    raise

        changed = (
            existing.public_address != public_address
            or existing.latitude != latitude
            or existing.longitude != longitude
            or existing.public_phone != public_phone
            or existing.listed != listed
            or existing.accepting_orders != accepting_orders
        )
        if changed:
            existing.public_address = public_address
            existing.latitude = latitude
            existing.longitude = longitude
            existing.public_phone = public_phone
            existing.listed = listed
            existing.accepting_orders = accepting_orders
            existing.updated_at = now
            session.flush()
        return _to_record(existing), False, changed

    def list_listed(self) -> list[LabDirectoryListing]:
        """Directory browsing: ACTIVE labs with `listed=True` only. Never returns an unlisted or
        inactive-organization lab -- see `get_listing_for_lab` for the platform-admin detail path
        that can."""
        with self._sessions() as session:
            rows = session.execute(
                select(Organization, LabDirectoryProfile)
                .join(
                    LabDirectoryProfile, LabDirectoryProfile.lab_organization_id == Organization.id
                )
                .where(
                    Organization.status == OrganizationStatus.ACTIVE.value,
                    LabDirectoryProfile.listed.is_(True),
                )
            ).all()
            return [_to_listing(organization, profile) for organization, profile in rows]

    def get_listing_for_lab(
        self, lab_organization_id: uuid.UUID, *, include_unlisted: bool
    ) -> LabDirectoryListing | None:
        with self._sessions() as session:
            row = session.execute(
                select(Organization, LabDirectoryProfile)
                .join(
                    LabDirectoryProfile, LabDirectoryProfile.lab_organization_id == Organization.id
                )
                .where(
                    Organization.id == lab_organization_id,
                    Organization.status == OrganizationStatus.ACTIVE.value,
                )
            ).first()
            if row is None:
                return None
            organization, profile = row
            if not include_unlisted and not profile.listed:
                return None
            return _to_listing(organization, profile)


def _to_record(profile: LabDirectoryProfile) -> LabDirectoryProfileRecord:
    return LabDirectoryProfileRecord(
        id=profile.id,
        lab_organization_id=profile.lab_organization_id,
        listed=profile.listed,
        accepting_orders=profile.accepting_orders,
        public_address=profile.public_address,
        latitude=profile.latitude,
        longitude=profile.longitude,
        public_phone=profile.public_phone,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _to_listing(organization: Organization, profile: LabDirectoryProfile) -> LabDirectoryListing:
    return LabDirectoryListing(
        lab_organization_id=organization.id,
        lab_organization_name=organization.name,
        listed=profile.listed,
        accepting_orders=profile.accepting_orders,
        public_address=profile.public_address,
        latitude=profile.latitude,
        longitude=profile.longitude,
        public_phone=profile.public_phone,
    )
