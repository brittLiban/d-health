"""DH-EX-001: the DHealth-owned test catalog, lab offerings, and pricing.

No fuzzy test mapping and no clinical approval happen anywhere here -- ``TestCatalogEntry`` rows
are DHealth-owned identities, seeded as synthetic/demo data for DH-EX-001 (DH-011I's own finding,
zero exact OpenMRS<->OpenELIS LOINC matches, stays a separate connector-track concern).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import (
    LabOffering,
    LabPrice,
    Organization,
    OrganizationStatus,
    OrganizationType,
    TestCatalogEntry,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.models import utc_now


class CatalogEntryNotFound(RuntimeError):
    """No catalog entry matches the given id."""


class OfferingNotFound(RuntimeError):
    """No lab offering matches the given id."""


@dataclass(frozen=True, slots=True)
class CatalogEntryRecord:
    id: uuid.UUID
    code: str
    version: int
    display_name: str
    active: bool
    is_synthetic: bool


@dataclass(frozen=True, slots=True)
class LabOfferingRecord:
    id: uuid.UUID
    lab_organization_id: uuid.UUID
    catalog_entry_id: uuid.UUID
    active: bool


@dataclass(frozen=True, slots=True)
class EligibleLab:
    """One row of "this lab can perform this test at this price" -- the exact shape the
    Provider/patient-facing selection screen needs, per DH-PIVOT-001's golden journey."""

    lab_organization_id: uuid.UUID
    lab_organization_name: str
    lab_offering_id: uuid.UUID
    price_amount: Decimal
    price_currency: str


class CatalogRepository:
    def __init__(
        self, session_factory: sessionmaker[Session], organizations: OrganizationRepository
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations

    def create_entry(
        self, *, code: str, display_name: str, version: int = 1, is_synthetic: bool = True
    ) -> CatalogEntryRecord:
        if not code.strip():
            raise ValueError("code is required")
        if not display_name.strip():
            raise ValueError("display_name is required")
        entry = TestCatalogEntry(
            code=code, version=version, display_name=display_name, is_synthetic=is_synthetic
        )
        with self._sessions() as session:
            session.add(entry)
            session.commit()
            return _to_entry_record(entry)

    def get_entry(self, entry_id: uuid.UUID) -> CatalogEntryRecord:
        with self._sessions() as session:
            entry = session.get(TestCatalogEntry, entry_id)
            if entry is None:
                raise CatalogEntryNotFound(str(entry_id))
            return _to_entry_record(entry)

    def create_offering(
        self,
        *,
        lab_organization_id: uuid.UUID,
        catalog_entry_id: uuid.UUID,
        local_test_code: str | None = None,
    ) -> LabOfferingRecord:
        # Fails closed if the organization is not an ACTIVE lab -- see
        # `OrganizationRepository.require_type`'s own docstring for why this lives here rather
        # than a portable cross-table SQL CHECK constraint.
        self._organizations.require_type(lab_organization_id, OrganizationType.LAB)
        self.get_entry(catalog_entry_id)  # 404s if unknown -- no fabricated offering target
        offering = LabOffering(
            lab_organization_id=lab_organization_id,
            catalog_entry_id=catalog_entry_id,
            local_test_code=local_test_code,
        )
        with self._sessions() as session:
            session.add(offering)
            session.commit()
            return _to_offering_record(offering)

    def set_price(self, *, offering_id: uuid.UUID, amount: Decimal, currency: str = "DJF") -> None:
        """Supersedes the existing current price (if any) rather than mutating it -- history is
        preserved, and any already-made ``LabOrderItem`` selection snapshot is unaffected because
        it never re-reads this table after selection (see ``exchange/selection.py``).

        DH-EX-001R live-proven finding, twice: two concurrent callers racing to set a price for
        the same offering both try to insert a new current row -- the partial unique index
        (``uq_exchange_lab_prices_current_per_offering``) correctly permits only one, but the
        loser previously surfaced a raw ``IntegrityError`` instead of completing. The first fix
        attempt used ``SELECT ... FOR UPDATE`` to find the row to supersede; live PostgreSQL
        testing under real 8-way concurrency showed that approach can still lose the race, because
        a blocked ``SELECT ... FOR UPDATE`` that unblocks onto a row whose ``WHERE`` clause no
        longer matches (a concurrent winner already superseded it) simply returns no row, not the
        winner's new row -- the loser then wrongly believes no current price exists and collides on
        insert anyway. This version instead issues a plain, unconditional ``UPDATE ... WHERE
        superseded_at IS NULL`` first: it never raises, matches zero or one row depending on what is
        actually currently true at execution time, and the *only* remaining failure surface is the
        final INSERT's own unique-constraint race, retried below. Reproduced live against real
        PostgreSQL under 8-way concurrent contention before this fix, and re-verified clean (0
        failures across repeated runs) after it."""
        if amount < 0:
            raise ValueError("amount must be non-negative")
        max_attempts = 25
        for attempt in range(1, max_attempts + 1):
            try:
                with self._sessions.begin() as session:
                    offering = session.get(LabOffering, offering_id)
                    if offering is None:
                        raise OfferingNotFound(str(offering_id))
                    now = utc_now()
                    session.execute(
                        update(LabPrice)
                        .where(
                            LabPrice.lab_offering_id == offering_id,
                            LabPrice.superseded_at.is_(None),
                        )
                        .values(superseded_at=now)
                    )
                    session.add(
                        LabPrice(
                            lab_offering_id=offering_id,
                            amount=amount,
                            currency=currency,
                            effective_at=now,
                        )
                    )
                return
            except IntegrityError:
                if attempt == max_attempts:
                    raise

    def set_offering_active(self, offering_id: uuid.UUID, active: bool) -> None:
        with self._sessions.begin() as session:
            offering = session.get(LabOffering, offering_id)
            if offering is None:
                raise OfferingNotFound(str(offering_id))
            offering.active = active

    def find_eligible_labs(self, catalog_entry_id: uuid.UUID) -> list[EligibleLab]:
        """Active offerings, from active labs, with a current price only -- an inactive offering
        or an inactive lab is silently excluded (never surfaced as a fabricated eligibility
        result), matching DH-EX-001's required negative control."""
        with self._sessions() as session:
            rows = session.execute(
                select(Organization, LabOffering, LabPrice)
                .join(LabOffering, LabOffering.lab_organization_id == Organization.id)
                .join(
                    LabPrice,
                    (LabPrice.lab_offering_id == LabOffering.id)
                    & (LabPrice.superseded_at.is_(None)),
                )
                .where(
                    LabOffering.catalog_entry_id == catalog_entry_id,
                    LabOffering.active.is_(True),
                    Organization.org_type == OrganizationType.LAB.value,
                    Organization.status == OrganizationStatus.ACTIVE.value,
                )
            ).all()
            return [
                EligibleLab(
                    lab_organization_id=organization.id,
                    lab_organization_name=organization.name,
                    lab_offering_id=offering.id,
                    price_amount=price.amount,
                    price_currency=price.currency,
                )
                for organization, offering, price in rows
            ]


def _to_entry_record(entry: TestCatalogEntry) -> CatalogEntryRecord:
    return CatalogEntryRecord(
        id=entry.id,
        code=entry.code,
        version=entry.version,
        display_name=entry.display_name,
        active=entry.active,
        is_synthetic=entry.is_synthetic,
    )


def _to_offering_record(offering: LabOffering) -> LabOfferingRecord:
    return LabOfferingRecord(
        id=offering.id,
        lab_organization_id=offering.lab_organization_id,
        catalog_entry_id=offering.catalog_entry_id,
        active=offering.active,
    )
