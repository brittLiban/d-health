"""DH-EX-001 / DH-EX-003: the DHealth-owned test catalog, lab offerings, and pricing.

No fuzzy test mapping and no clinical approval happen anywhere here -- ``TestCatalogEntry`` rows
are DHealth-owned identities, seeded as synthetic/demo data (DH-011I's own finding, zero exact
OpenMRS<->OpenELIS LOINC matches, stays a separate connector-track concern). DH-EX-003 added
idempotent create semantics, a caller-owned-session path for transactional audit (mirroring
``exchange/membership.py``'s established pattern), optimistic-concurrency price updates, and the
whole-order eligibility query -- all layered onto DH-EX-001's original tables without recreating
them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import (
    LabDirectoryProfile,
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


class CatalogEntryConflict(RuntimeError):
    """Same ``(code, version)`` already exists with different immutable content -- never silently
    overwritten, never fuzzy-merged."""


class OfferingNotFound(RuntimeError):
    """No lab offering matches the given id."""


class OfferingConflict(RuntimeError):
    """Same ``(lab_organization_id, catalog_entry_id)`` already exists with a conflicting
    ``local_test_code``."""


class OfferingInactiveConflict(OfferingConflict):
    """An existing but INACTIVE offering was hit by a create replay -- reactivation is a distinct,
    explicit mutation (`set_offering_active`), never a side effect of `create_offering`."""


class PriceConflict(RuntimeError):
    """Optimistic-concurrency violation: ``expected_current_price_id`` does not match the actual
    current price, and the actual current price does not already equal the requested
    amount/currency (which would instead be a safe idempotent replay -- see `set_price`)."""


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
    local_test_code: str | None
    active: bool


@dataclass(frozen=True, slots=True)
class LabPriceRecord:
    id: uuid.UUID
    lab_offering_id: uuid.UUID
    amount: Decimal
    currency: str
    effective_at: datetime
    superseded_at: datetime | None


@dataclass(frozen=True, slots=True)
class EligibleLab:
    """One row of "this lab can perform this test at this price" -- the exact shape the
    Provider/patient-facing single-test selection screen needs, per DH-PIVOT-001's golden journey.
    Superseded for whole-order use by `WholeOrderEligibleLab` (DH-EX-003) but kept as-is: DH-EX-001
    callers of single-test eligibility are unaffected."""

    lab_organization_id: uuid.UUID
    lab_organization_name: str
    lab_offering_id: uuid.UUID
    price_amount: Decimal
    price_currency: str


@dataclass(frozen=True, slots=True)
class WholeOrderPriceLine:
    catalog_entry_id: uuid.UUID
    catalog_code: str
    display_name: str
    offering_id: uuid.UUID
    current_price_id: uuid.UUID
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class WholeOrderEligibleLab:
    lab_organization_id: uuid.UUID
    lab_organization_name: str
    public_address: str
    latitude: Decimal | None
    longitude: Decimal | None
    public_phone: str | None
    prices: list[WholeOrderPriceLine]
    total_amount: Decimal
    currency: str


class CatalogRepository:
    def __init__(
        self, session_factory: sessionmaker[Session], organizations: OrganizationRepository
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations

    # -- catalog entries --------------------------------------------------------------------

    def create_entry(
        self,
        *,
        code: str,
        display_name: str,
        version: int = 1,
        is_synthetic: bool = True,
        session: Session | None = None,
    ) -> tuple[CatalogEntryRecord, bool]:
        """Insert-first-wins on ``(code, version)``. Returns ``(record, created)`` -- a replay
        with identical ``display_name``/``is_synthetic`` is a no-op; a replay with different
        content raises `CatalogEntryConflict` rather than silently overwriting or fuzzy-merging."""
        if not code.strip():
            raise ValueError("code is required")
        if not display_name.strip():
            raise ValueError("display_name is required")
        if session is not None:
            return self._create_entry_in_session(
                session,
                code=code,
                display_name=display_name,
                version=version,
                is_synthetic=is_synthetic,
            )
        with self._sessions() as owned_session:
            record, created = self._create_entry_in_session(
                owned_session,
                code=code,
                display_name=display_name,
                version=version,
                is_synthetic=is_synthetic,
            )
            owned_session.commit()
            return record, created

    def _create_entry_in_session(
        self, session: Session, *, code: str, display_name: str, version: int, is_synthetic: bool
    ) -> tuple[CatalogEntryRecord, bool]:
        entry = TestCatalogEntry(
            code=code, version=version, display_name=display_name, is_synthetic=is_synthetic
        )
        try:
            with session.begin_nested():
                session.add(entry)
                session.flush()
            return _to_entry_record(entry), True
        except IntegrityError:
            existing = session.scalar(
                select(TestCatalogEntry).where(
                    TestCatalogEntry.code == code, TestCatalogEntry.version == version
                )
            )
            if existing is None:
                raise
            if existing.display_name != display_name or existing.is_synthetic != is_synthetic:
                raise CatalogEntryConflict(
                    f"catalog entry {code!r} v{version} already exists with different content"
                ) from None
            return _to_entry_record(existing), False

    def get_entry(self, entry_id: uuid.UUID) -> CatalogEntryRecord:
        with self._sessions() as session:
            entry = session.get(TestCatalogEntry, entry_id)
            if entry is None:
                raise CatalogEntryNotFound(str(entry_id))
            return _to_entry_record(entry)

    def get_entries_by_ids(
        self, catalog_entry_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, CatalogEntryRecord]:
        """DH-LAB-003 §39: batched, one query regardless of how many ids are requested -- the
        result-entry-plan read's own `is_synthetic` derivation needs this without ever issuing one
        query per ordered item."""
        if not catalog_entry_ids:
            return {}
        with self._sessions() as session:
            entries = session.scalars(
                select(TestCatalogEntry).where(TestCatalogEntry.id.in_(catalog_entry_ids))
            ).all()
            return {e.id: _to_entry_record(e) for e in entries}

    def list_entries(self, *, active_synthetic_only: bool = False) -> list[CatalogEntryRecord]:
        """DH-EX-003R: ``active_synthetic_only=True`` is the pilot-facing view -- an ordinary
        network member must never see an inactive or non-synthetic catalog entry as though it
        were selectable/approved inventory merely because a row exists in the DB (see
        `exchange/api.py`'s `list_catalog_tests`, which passes this only for a non-platform-admin
        caller). ``False`` (the default) is the platform-admin inspection view -- unfiltered,
        matching this ticket's "platform admin may inspect broader catalog if useful" policy."""
        with self._sessions() as session:
            query = select(TestCatalogEntry)
            if active_synthetic_only:
                query = query.where(
                    TestCatalogEntry.active.is_(True), TestCatalogEntry.is_synthetic.is_(True)
                )
            entries = session.scalars(query).all()
            return [_to_entry_record(entry) for entry in entries]

    # -- lab offerings ------------------------------------------------------------------------

    def create_offering(
        self,
        *,
        lab_organization_id: uuid.UUID,
        catalog_entry_id: uuid.UUID,
        local_test_code: str | None = None,
        session: Session | None = None,
    ) -> tuple[LabOfferingRecord, bool]:
        """Fails closed if the organization is not an ACTIVE lab. Insert-first-wins on
        ``(lab_organization_id, catalog_entry_id)``: a replay with the same ``local_test_code`` is
        a no-op; a replay with a different one raises `OfferingConflict`; a replay that would hit
        an existing but INACTIVE offering raises `OfferingInactiveConflict` rather than silently
        reactivating it (reactivation is `set_offering_active`'s job, never a side effect here)."""
        self._organizations.require_type(lab_organization_id, OrganizationType.LAB)
        self.get_entry(catalog_entry_id)  # 404s if unknown -- no fabricated offering target
        if session is not None:
            return self._create_offering_in_session(
                session,
                lab_organization_id=lab_organization_id,
                catalog_entry_id=catalog_entry_id,
                local_test_code=local_test_code,
            )
        with self._sessions() as owned_session:
            record, created = self._create_offering_in_session(
                owned_session,
                lab_organization_id=lab_organization_id,
                catalog_entry_id=catalog_entry_id,
                local_test_code=local_test_code,
            )
            owned_session.commit()
            return record, created

    def _create_offering_in_session(
        self,
        session: Session,
        *,
        lab_organization_id: uuid.UUID,
        catalog_entry_id: uuid.UUID,
        local_test_code: str | None,
    ) -> tuple[LabOfferingRecord, bool]:
        offering = LabOffering(
            lab_organization_id=lab_organization_id,
            catalog_entry_id=catalog_entry_id,
            local_test_code=local_test_code,
        )
        try:
            with session.begin_nested():
                session.add(offering)
                session.flush()
            return _to_offering_record(offering), True
        except IntegrityError:
            existing = session.scalar(
                select(LabOffering).where(
                    LabOffering.lab_organization_id == lab_organization_id,
                    LabOffering.catalog_entry_id == catalog_entry_id,
                )
            )
            if existing is None:
                raise
            if not existing.active:
                raise OfferingInactiveConflict(str(existing.id)) from None
            if existing.local_test_code != local_test_code:
                raise OfferingConflict(
                    f"offering {existing.id} already exists with a different local_test_code"
                ) from None
            return _to_offering_record(existing), False

    def get_offering(self, offering_id: uuid.UUID) -> LabOfferingRecord:
        with self._sessions() as session:
            offering = session.get(LabOffering, offering_id)
            if offering is None:
                raise OfferingNotFound(str(offering_id))
            return _to_offering_record(offering)

    def get_offerings_for_lab_and_entries(
        self, lab_organization_id: uuid.UUID, catalog_entry_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, LabOfferingRecord]:
        """DH-LAB-003 §39: batched lookup for the order-scoped result-entry-plan read -- one query
        regardless of how many catalog entries are requested, keyed by ``catalog_entry_id`` (this
        lab's own `UNIQUE(lab_organization_id, catalog_entry_id)` makes that key collision-free).
        Deliberately does not filter on ``LabOffering.active`` -- an offering that became inactive
        after an order was already assigned here must still resolve (§33's own historical-
        commitment principle); the *template*-activeness check is a separate, later step."""
        if not catalog_entry_ids:
            return {}
        with self._sessions() as session:
            offerings = session.scalars(
                select(LabOffering).where(
                    LabOffering.lab_organization_id == lab_organization_id,
                    LabOffering.catalog_entry_id.in_(catalog_entry_ids),
                )
            ).all()
            return {o.catalog_entry_id: _to_offering_record(o) for o in offerings}

    def list_offerings_for_lab(self, lab_organization_id: uuid.UUID) -> list[LabOfferingRecord]:
        with self._sessions() as session:
            offerings = session.scalars(
                select(LabOffering).where(LabOffering.lab_organization_id == lab_organization_id)
            ).all()
            return [_to_offering_record(offering) for offering in offerings]

    def set_offering_active(
        self, offering_id: uuid.UUID, active: bool, *, session: Session | None = None
    ) -> tuple[LabOfferingRecord, str]:
        """Returns ``(record, outcome)`` where outcome is ``"updated"`` or ``"unchanged"`` -- an
        inactive offering disappears from marketplace eligibility immediately; the row itself is
        never deleted.

        **[Fixed 2026-10-01, DHEALTH-LAB-OFFERINGS-001: the row is now locked ``FOR UPDATE``.]**
        ``outcome`` is not cosmetic -- ``exchange/api.py`` writes it straight into the
        ``lab_offering_status_changed`` audit row's ``outcome`` column, so it is the thing an
        administrator reads back to answer *"when was this service turned off, and by whom?"*.
        Deciding it from an unlocked read made that answer wrong under concurrency: see
        ``_set_offering_active_in_session``."""
        if session is not None:
            return self._set_offering_active_in_session(session, offering_id, active)
        with self._sessions.begin() as owned_session:
            return self._set_offering_active_in_session(owned_session, offering_id, active)

    def _set_offering_active_in_session(
        self, session: Session, offering_id: uuid.UUID, active: bool
    ) -> tuple[LabOfferingRecord, str]:
        # `with_for_update()`, not a plain `session.get`. This is a read-compare-write, and the
        # comparison is what decides whether an audit row is written at all.
        #
        # Unlocked, two administrators enabling the same offering at the same instant both read
        # `active=False`; the second one's UPDATE blocks on the first's row lock, and once it is
        # released it proceeds anyway, because its *decision* was already made against a stale
        # read. Both then report `"updated"` and `exchange/api.py` writes **two**
        # `lab_offering_status_changed` audit rows for **one** state change. The final value is
        # right and the accountability record is wrong -- it says the service was switched twice
        # when it was switched once, which is exactly the question this table exists to answer.
        #
        # The lock makes the comparison happen against committed truth, so the loser reads
        # `active=True` and correctly reports `"unchanged"` without writing a second audit row.
        # Same discipline `exchange/lab_workflow.py` applies to every mutation it owns.
        offering = session.scalar(
            select(LabOffering).where(LabOffering.id == offering_id).with_for_update()
        )
        if offering is None:
            raise OfferingNotFound(str(offering_id))
        if offering.active == active:
            return _to_offering_record(offering), "unchanged"
        offering.active = active
        session.flush()
        return _to_offering_record(offering), "updated"

    # -- pricing --------------------------------------------------------------------------------

    def set_price(
        self,
        *,
        offering_id: uuid.UUID,
        amount: Decimal,
        currency: str,
        expected_current_price_id: uuid.UUID | None,
        session: Session | None = None,
    ) -> tuple[LabPriceRecord, str]:
        """Optimistic-concurrency price update. Returns ``(record, outcome)`` where outcome is
        ``"created"`` (first price for this offering), ``"updated"`` (superseded a different
        current price), or ``"unchanged"`` (the actual current price already equals the requested
        amount/currency -- a safe replay, regardless of what ``expected_current_price_id`` said).

        ``expected_current_price_id=None`` means "I expect no current price exists yet"; a UUID
        means "I expect that row to still be current". A mismatch that is not also an
        already-equal-value replay raises `PriceConflict` (maps to 409) -- a stale concurrent
        writer never silently overwrites a price it did not know about.

        Supersedes via a plain, unconditional ``UPDATE ... WHERE superseded_at IS NULL ...
        RETURNING`` rather than ``SELECT ... FOR UPDATE`` -- DH-EX-001R live-proved under real
        8-way PostgreSQL concurrency that a blocked ``SELECT ... FOR UPDATE`` can unblock onto a
        row whose ``WHERE`` clause no longer matches (a concurrent winner already superseded it)
        and wrongly report no current price.

        DH-EX-003R correction, also found only against real PostgreSQL (SQLite's whole-database
        locking hid it entirely): the discover-what's-current step and the supersede-it step must
        be the **same statement**. A separate prior ``SELECT`` is stale by the time a later
        ``UPDATE`` actually executes under PostgreSQL's default READ COMMITTED isolation, where
        each statement gets its own fresh snapshot rather than the whole transaction sharing one --
        two concurrent callers could both read "no current price", both pass the CAS check, and
        the second one's later ``UPDATE`` would then silently supersede the first one's
        *already-committed* new row, misreporting `"created"` a second time. Reading and
        superseding in one ``UPDATE ... RETURNING`` closes that window: whatever it finds is
        current *at that exact instant*, never a stale prior read. If what it finds already equals
        the requested value, the accidental supersede is reverted within the same transaction
        (invisible to everyone else) and the call returns a clean `"unchanged"` replay."""
        if amount < 0:
            raise ValueError("amount must be non-negative")
        if session is not None:
            return self._set_price_in_session(
                session,
                offering_id=offering_id,
                amount=amount,
                currency=currency,
                expected_current_price_id=expected_current_price_id,
            )
        with self._sessions.begin() as owned_session:
            return self._set_price_in_session(
                owned_session,
                offering_id=offering_id,
                amount=amount,
                currency=currency,
                expected_current_price_id=expected_current_price_id,
            )

    def _set_price_in_session(
        self,
        session: Session,
        *,
        offering_id: uuid.UUID,
        amount: Decimal,
        currency: str,
        expected_current_price_id: uuid.UUID | None,
        max_attempts: int = 25,
    ) -> tuple[LabPriceRecord, str]:
        offering = session.get(LabOffering, offering_id)
        if offering is None:
            raise OfferingNotFound(str(offering_id))

        for attempt in range(1, max_attempts + 1):
            now = utc_now()
            # Discover-and-supersede in one statement -- see the docstring above for why a
            # separate prior SELECT is unsafe under PostgreSQL's READ COMMITTED isolation.
            superseded = session.execute(
                update(LabPrice)
                .where(LabPrice.lab_offering_id == offering_id, LabPrice.superseded_at.is_(None))
                .values(superseded_at=now)
                .returning(LabPrice.id, LabPrice.amount, LabPrice.currency)
            ).first()

            if (
                superseded is not None
                and superseded.amount == amount
                and superseded.currency == currency
            ):
                # Superseded a row that already held the desired value -- revert that (still
                # within this same transaction, so no one else ever observes the toggle) and
                # report a clean replay. This is the documented exception: an already-correct
                # value is always a safe no-op, regardless of what `expected_current_price_id`
                # said.
                session.execute(
                    update(LabPrice).where(LabPrice.id == superseded.id).values(superseded_at=None)
                )
                session.flush()
                reverted = session.get(LabPrice, superseded.id)
                if reverted is None:
                    raise RuntimeError(
                        f"price row {superseded.id} vanished within its own transaction"
                    )
                return _to_price_record(reverted), "unchanged"

            if expected_current_price_id is None:
                if superseded is not None:
                    raise PriceConflict(
                        f"a current price already exists for offering {offering_id}"
                    )
            elif superseded is None or superseded.id != expected_current_price_id:
                raise PriceConflict(
                    f"expected current price {expected_current_price_id} does not match the "
                    f"actual current price for offering {offering_id}"
                )

            new_price = LabPrice(
                lab_offering_id=offering_id, amount=amount, currency=currency, effective_at=now
            )
            try:
                with session.begin_nested():
                    session.add(new_price)
                    session.flush()
                outcome = "created" if superseded is None else "updated"
                return _to_price_record(new_price), outcome
            except IntegrityError:
                if attempt == max_attempts:
                    raise
                continue

        raise RuntimeError("unreachable: set_price loop always returns or raises")

    # -- eligibility ------------------------------------------------------------------------------

    def find_eligible_labs(self, catalog_entry_id: uuid.UUID) -> list[EligibleLab]:
        """Single-test eligibility (DH-EX-001, unchanged). Active offerings, from active labs,
        with a current price only -- an inactive offering or an inactive lab is silently excluded
        (never surfaced as a fabricated eligibility result)."""
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

    def find_whole_order_eligible_labs(
        self, catalog_entry_ids: list[uuid.UUID]
    ) -> list[WholeOrderEligibleLab]:
        """DH-EX-003: labs that can fulfil **every** requested catalog entry, never a partial
        match. A lab missing even one requested test, one active offering, or one current DJF
        price is excluded entirely -- there is no N-of-M result.

        Three passes, each narrowing the candidate set, so a partial-lab bug has to survive all
        three independently rather than one loosely-merged loop: (1) a `GROUP BY`/`HAVING COUNT`
        query finds labs with an ACTIVE offering + CURRENT DJF price for the full requested set;
        (2) those candidates are filtered by ACTIVE organization + `listed` + `accepting_orders`;
        (3) actual price lines are fetched for the surviving labs, re-checking every condition
        pass (1) already checked (active offering, active+synthetic catalog entry, DJF currency --
        DH-EX-003R found pass (3) had silently dropped the currency re-check, see below) and,
        defensively, any lab whose fetched line count does not exactly equal the requested count
        is dropped rather than emitted partially -- belt-and-suspenders against the first query
        ever being wrong.

        DH-EX-003R correction: three separate `SELECT` statements sharing one `Session`/one open
        transaction are *not* automatically one coherent snapshot under PostgreSQL's default READ
        COMMITTED isolation, which hands each individual statement its own fresh snapshot rather
        than sharing one across the whole transaction (the exact same class of gap `set_price`'s
        docstring already documents for a different method). Review here found the self-healing
        design -- every pass re-verifies its own conditions against fresh state, and the final
        exact-line-count check drops rather than partially emits a lab that stopped qualifying
        mid-query -- already prevents any partial-lab or fabricated-eligibility result from a race
        (a lab that stops qualifying between passes is excluded outright, never emitted short).
        What it did *not* prevent on its own: pass (3) omitting pass (1)'s currency/active/
        synthetic re-checks (fixed above), and the response's single `as_of` timestamp not
        actually corresponding to one true database snapshot. Both are closed together here by
        running this whole read under `REPEATABLE READ` on PostgreSQL (a plain per-Session
        isolation-level override, set before the first statement executes, so all three passes
        genuinely observe one consistent snapshot) -- SQLite has no equivalent named isolation
        level and does not need one for a single-connection test run, so the override is applied
        only when the bound dialect is `postgresql`."""
        required_count = len(catalog_entry_ids)
        if required_count == 0:
            return []
        with self._sessions() as session:
            if session.get_bind().dialect.name == "postgresql":
                # Must happen before the first statement on this Session -- once a transaction has
                # implicitly begun, most drivers refuse to change isolation level mid-transaction.
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            candidate_rows = session.execute(
                select(
                    LabOffering.lab_organization_id,
                    func.count(func.distinct(LabOffering.catalog_entry_id)).label("matched"),
                )
                .join(TestCatalogEntry, TestCatalogEntry.id == LabOffering.catalog_entry_id)
                .join(
                    LabPrice,
                    (LabPrice.lab_offering_id == LabOffering.id)
                    & (LabPrice.superseded_at.is_(None)),
                )
                .where(
                    LabOffering.catalog_entry_id.in_(catalog_entry_ids),
                    LabOffering.active.is_(True),
                    TestCatalogEntry.active.is_(True),
                    TestCatalogEntry.is_synthetic.is_(True),
                    LabPrice.currency == "DJF",
                )
                .group_by(LabOffering.lab_organization_id)
                .having(func.count(func.distinct(LabOffering.catalog_entry_id)) == required_count)
            ).all()
            candidate_lab_ids = [row.lab_organization_id for row in candidate_rows]
            if not candidate_lab_ids:
                return []

            directory_rows = session.execute(
                select(Organization, LabDirectoryProfile)
                .join(
                    LabDirectoryProfile, LabDirectoryProfile.lab_organization_id == Organization.id
                )
                .where(
                    Organization.id.in_(candidate_lab_ids),
                    Organization.status == OrganizationStatus.ACTIVE.value,
                    LabDirectoryProfile.listed.is_(True),
                    LabDirectoryProfile.accepting_orders.is_(True),
                )
            ).all()
            if not directory_rows:
                return []
            directory_by_lab = {
                organization.id: (organization, profile) for organization, profile in directory_rows
            }
            final_lab_ids = list(directory_by_lab.keys())

            # Re-verify every condition pass (1) already checked, not just `LabOffering.active` --
            # DH-EX-003R finding: this fetch originally trusted pass (1)'s `LabPrice.currency ==
            # "DJF"` filter without repeating it here. Nothing in this schema forbids a price row
            # from holding a non-DJF currency (the column is a plain code, not a hard enum, by
            # design -- see `LabPrice`'s docstring), so a lab whose current price for one requested
            # test was ever set to a non-DJF currency by anything other than this HTTP API's
            # DJF-only-`Literal` request schema (a future endpoint, a direct repository call, a
            # data migration) would otherwise have that price line silently included here and
            # summed into a `total_amount` this response still labels `"DJF"` -- a real mixed-
            # currency total, not a hypothetical one. Repeating every pass-(1) condition here also
            # narrows (though, under concurrent writes, does not eliminate) the window in which a
            # test/catalog entry deactivated between pass (1) and this fetch could still surface a
            # stale line.
            price_rows = session.execute(
                select(LabOffering, TestCatalogEntry, LabPrice)
                .join(TestCatalogEntry, TestCatalogEntry.id == LabOffering.catalog_entry_id)
                .join(
                    LabPrice,
                    (LabPrice.lab_offering_id == LabOffering.id)
                    & (LabPrice.superseded_at.is_(None)),
                )
                .where(
                    LabOffering.lab_organization_id.in_(final_lab_ids),
                    LabOffering.catalog_entry_id.in_(catalog_entry_ids),
                    LabOffering.active.is_(True),
                    TestCatalogEntry.active.is_(True),
                    TestCatalogEntry.is_synthetic.is_(True),
                    LabPrice.currency == "DJF",
                )
            ).all()

            lines_by_lab: dict[uuid.UUID, list[WholeOrderPriceLine]] = {}
            for offering, entry, price in price_rows:
                lines_by_lab.setdefault(offering.lab_organization_id, []).append(
                    WholeOrderPriceLine(
                        catalog_entry_id=entry.id,
                        catalog_code=entry.code,
                        display_name=entry.display_name,
                        offering_id=offering.id,
                        current_price_id=price.id,
                        amount=price.amount,
                        currency=price.currency,
                    )
                )

            requested_entry_ids = set(catalog_entry_ids)
            results: list[WholeOrderEligibleLab] = []
            for lab_id in final_lab_ids:
                lines = lines_by_lab.get(lab_id, [])
                # Defensive: never emit a partial-lab result. A count check alone would accept a
                # pathological "requested A, B but returned lines A, A" duplicate -- structurally
                # impossible today given `LabOffering`'s `UNIQUE(lab_organization_id,
                # catalog_entry_id)` constraint, but DH-EX-003R made the invariant explicit rather
                # than relying on that constraint implicitly: compare the exact requested *set*,
                # not just its size.
                if (
                    len(lines) != required_count
                    or {line.catalog_entry_id for line in lines} != requested_entry_ids
                ):
                    continue
                organization, profile = directory_by_lab[lab_id]
                total = sum((line.amount for line in lines), start=Decimal("0"))
                results.append(
                    WholeOrderEligibleLab(
                        lab_organization_id=organization.id,
                        lab_organization_name=organization.name,
                        public_address=profile.public_address,
                        latitude=profile.latitude,
                        longitude=profile.longitude,
                        public_phone=profile.public_phone,
                        prices=sorted(lines, key=lambda line: line.catalog_code),
                        total_amount=total,
                        currency="DJF",
                    )
                )
            results.sort(
                key=lambda lab: (
                    lab.total_amount,
                    lab.lab_organization_name,
                    str(lab.lab_organization_id),
                )
            )
            return results


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
        local_test_code=offering.local_test_code,
        active=offering.active,
    )


def _to_price_record(price: LabPrice) -> LabPriceRecord:
    return LabPriceRecord(
        id=price.id,
        lab_offering_id=price.lab_offering_id,
        amount=price.amount,
        currency=price.currency,
        effective_at=price.effective_at,
        superseded_at=price.superseded_at,
    )
