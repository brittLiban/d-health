"""DH-EX-001 / DH-EX-005 / DH-EX-010: selecting one destination lab for an order.

**DH-EX-010 addition.** ``select()`` below is DH-EX-005's *patient* price-consent workflow --
unchanged by this addition except for one new guard (see its own docstring). ``assign_by_provider``
is a second, wholly separate workflow: ADR-043's pre-authorized launch option (a), a provider
routing an order to a known lab with no price marketplace/consent step at all. Both share the same
canonical ``LabSelection`` row -- one order, one destination lab, one row -- distinguished only by
``selected_via_patient_access_grant_id`` (non-``NULL`` = patient-driven, ``NULL`` = provider-driven
or legacy). This is a deliberate two-named-methods design, not one method with a boolean like
``require_price=``: the two are different security/product policies, and keeping them as separate,
independently auditable, independently testable methods is this package's established discipline
for exactly this situation (see e.g. `PatientReferenceRepository.get_or_create` vs.
`get_or_create_with_outcome` for the same "add a new policy, don't overload the old one" pattern).

MVP scope is one lab per order (DH-PIVOT-001's approved architecture). Selecting a lab freezes a
per-item price snapshot onto ``LabOrderItem`` at the moment of selection and never re-reads
``LabPrice`` afterward, so a lab raising its price later cannot silently change what an
already-selecting patient saw and agreed to.

**DH-EX-005 hardening.** The original DH-EX-001 implementation only checked "active offering +
current price at this lab" -- predating DH-EX-003's directory/catalog-orderability rules, it never
verified the lab was actually a *marketplace-eligible* whole-order option (ACTIVE organization,
`listed`, `accepting_orders`, every catalog entry still ACTIVE+SYNTHETIC, every price still DJF).
``select()`` now re-runs that full contract at commit time, scoped to the one chosen lab, rather
than trusting whatever the patient saw when they last read the marketplace -- and additionally
requires the caller to state the *exact* current price ids they are consenting to
(``expected_price_ids``), so a lab that changed a price between "patient viewed options" and
"patient clicked select" produces a clean conflict rather than silently freezing a value the
patient never actually saw and agreed to.

No row locking is needed on the price/offering/directory rows themselves for this to be race-safe
against a concurrent admin mutation: this method only ever *reads* that state and copies values
onto ``LabOrderItem`` (a plain column copy, not a mutation of the source rows), so PostgreSQL's
MVCC snapshot semantics already guarantee every read here observes some genuinely-committed state,
never a torn one -- the same reasoning `exchange/catalog.py`'s `find_whole_order_eligible_labs`
docstring gives for why its own re-reads are safe without locking. The one row that *is* locked
(`LabOrder`, via ``with_for_update()``) is what actually matters: it serializes every concurrent
selection attempt -- same lab or different -- against the *same* order down to one winner, exactly
like `exchange/orders.py`'s ``transition`` does for status changes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import (
    LabDirectoryProfile,
    LabOffering,
    LabOrder,
    LabOrderStatus,
    LabPrice,
    LabSelection,
    Organization,
    OrganizationStatus,
    OrganizationType,
    PatientAccessGrant,
    TestCatalogEntry,
)
from daher_adapter.exchange.orders import InvalidOrderTransition, OrderNotFound
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.models import utc_now


def _aware(value: datetime) -> datetime:
    """See `patient_access.py`'s identical helper: PostgreSQL's `timestamptz` is always aware;
    SQLite (test-only) hands back naive datetimes on a fresh read."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class LabCannotFulfillOrder(RuntimeError):
    """The chosen lab does not currently satisfy the full whole-order marketplace-eligibility
    contract (missing/inactive offering, inactive or non-synthetic catalog entry, non-DJF price,
    unlisted, not accepting orders, or an inactive lab organization) -- selection fails closed
    rather than freezing a partial or fabricated price."""


class StalePriceSelection(RuntimeError):
    """The current price ids for the selected lab's ordered items no longer match exactly what
    the caller explicitly consented to (``expected_price_ids``) -- a price changed between the
    caller last reading the marketplace and this selection attempt. Refused rather than silently
    freezing a different amount the caller never actually saw."""


class DifferentLabAlreadySelected(RuntimeError):
    """This order already has a LabSelection for a different lab -- there is no reselection
    workflow in this MVP; changing an already-made selection requires an explicit future policy."""


class LabAlreadyAssignedByProvider(RuntimeError):
    """DH-EX-010: a patient's ``select()`` call named the *same* lab an order was already routed
    to by a non-patient-driven provider assignment (``LabSelection.
    selected_via_patient_access_grant_id IS NULL``). Refused rather than silently returning that
    existing selection as though the patient had just consented to its (nonexistent) price --
    doing so would retroactively fabricate patient price consent for a routing decision the
    patient never made, and DH-EX-010's provider-assignment path never wrote a price to consent
    to in the first place."""


class PatientAccessGrantInvalidated(RuntimeError):
    """DH-EX-005R: the patient access grant that authorized this call is no longer valid *as of
    this transaction* -- revoked, expired, or (defensively) no longer bound to this order -- even
    though it may have passed the HTTP dependency's own check moments earlier. A caller (the
    FastAPI patient-auth dependency) validates a bearer token in its own short-lived, already-
    closed read transaction *before* this method's own mutation transaction begins; without a
    fresh re-check here, a grant revoked in that gap could still author a real mutation. Maps to
    the same generic 401 every other invalid-grant response uses -- this is an authentication
    failure discovered late, not a domain conflict."""


@dataclass(frozen=True, slots=True)
class LabSelectionRecord:
    id: uuid.UUID
    lab_order_id: uuid.UUID
    lab_organization_id: uuid.UUID
    selected_at: datetime
    selected_via_patient_access_grant_id: uuid.UUID | None


class LabSelectionRepository:
    def __init__(
        self, session_factory: sessionmaker[Session], organizations: OrganizationRepository
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations

    def select(
        self,
        *,
        lab_order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        expected_price_ids: frozenset[uuid.UUID],
        patient_access_grant_id: uuid.UUID | None = None,
    ) -> tuple[LabSelectionRecord, bool]:
        """Returns ``(record, created)``. ``created=False`` means an existing selection for this
        *same* lab was returned instead of re-validating and re-freezing anything -- the historical
        frozen snapshot always wins over whatever the lab's prices are now (DH-EX-005's explicit
        "same-lab replay after a price change" contract). A different lab already selected raises
        `DifferentLabAlreadySelected`, never silently replacing the prior choice.

        ``expected_price_ids`` has no default -- every caller must state exactly which current
        price ids they are consenting to, matching `catalog.set_price`'s own
        `expected_current_price_id` "no silent default" convention for the same reason: this is
        consent data, not an optional optimization."""
        self._organizations.require_type(lab_organization_id, OrganizationType.LAB)

        with self._sessions.begin() as session:
            order = session.scalar(
                select(LabOrder).where(LabOrder.id == lab_order_id).with_for_update()
            )
            if order is None:
                raise OrderNotFound(str(lab_order_id))

            # DH-EX-005R Part U/V/F: re-validate the grant *inside this transaction*, never trust
            # the caller's already-closed pre-transaction auth check. `revoke()` locks this same
            # `LabOrder` row first (see `patient_access.py`), so acquiring the lock above already
            # serializes against a concurrent revoke -- by the time we read the grant below, its
            # `revoked_at` reflects the true, fully-resolved state for this instant, never a
            # value that could still change out from under us mid-transaction. `patient_access_
            # grant_id` is only `None` for non-patient/internal callers (pre-DH-EX-005 test
            # fixtures) -- this check is a no-op for those, exactly as before.
            if patient_access_grant_id is not None:
                grant = session.scalar(
                    select(PatientAccessGrant).where(
                        PatientAccessGrant.id == patient_access_grant_id
                    )
                )
                if (
                    grant is None
                    or grant.lab_order_id != lab_order_id
                    or grant.revoked_at is not None
                    or _aware(grant.expires_at) <= utc_now()
                ):
                    raise PatientAccessGrantInvalidated(
                        f"patient access grant {patient_access_grant_id} is no longer valid for "
                        f"LabOrder {lab_order_id}"
                    )

            existing_selection = session.scalar(
                select(LabSelection).where(LabSelection.lab_order_id == lab_order_id)
            )
            if existing_selection is not None:
                if existing_selection.lab_organization_id != lab_organization_id:
                    raise DifferentLabAlreadySelected(
                        f"LabOrder {lab_order_id} already has a selection for a different lab"
                    )
                # DH-EX-010: a *patient* call (``patient_access_grant_id`` given) naming the same
                # lab a non-patient-driven provider assignment already routed this order to must
                # never be treated as successful patient price consent -- that assignment never
                # wrote a price to consent to, and this method must not fabricate one now.
                # ``patient_access_grant_id is None`` (internal/legacy callers, and every
                # pre-DH-EX-010 test) is unaffected: this check only ever fires for the new
                # patient-vs-provider interaction this ticket introduces.
                if (
                    patient_access_grant_id is not None
                    and existing_selection.selected_via_patient_access_grant_id is None
                ):
                    raise LabAlreadyAssignedByProvider(
                        f"LabOrder {lab_order_id} was already routed to lab "
                        f"{lab_organization_id} by the ordering provider -- a patient selection "
                        f"cannot retroactively supply price consent for a routing decision the "
                        f"patient did not make"
                    )
                # Same-lab replay: the historical frozen snapshot wins, unconditionally -- never
                # re-validate eligibility or re-compare price ids against whatever is current now.
                return _to_selection_record(existing_selection), False

            if order.status != LabOrderStatus.AWAITING_LAB_SELECTION.value:
                raise InvalidOrderTransition(
                    f"cannot select a lab for LabOrder {lab_order_id} in status "
                    f"{order.status!r} -- an unselected lab may never become the implicit target"
                )

            required_entry_ids = frozenset(item.catalog_entry_id for item in order.items)
            required_count = len(required_entry_ids)

            organization = session.get(Organization, lab_organization_id)
            if organization is None or organization.status != OrganizationStatus.ACTIVE.value:
                raise LabCannotFulfillOrder(
                    f"lab {lab_organization_id} is not an ACTIVE organization"
                )
            profile = session.scalar(
                select(LabDirectoryProfile).where(
                    LabDirectoryProfile.lab_organization_id == lab_organization_id
                )
            )
            if profile is None or not profile.listed or not profile.accepting_orders:
                raise LabCannotFulfillOrder(
                    f"lab {lab_organization_id} is not currently listed and accepting orders"
                )

            # The same re-verify-every-condition discipline `find_whole_order_eligible_labs`
            # applies, scoped to this one lab: active offering, active+synthetic catalog entry,
            # current DJF price, for every ordered item -- never trust a stale marketplace read.
            price_rows = session.execute(
                select(LabOffering, TestCatalogEntry, LabPrice)
                .join(TestCatalogEntry, TestCatalogEntry.id == LabOffering.catalog_entry_id)
                .join(
                    LabPrice,
                    (LabPrice.lab_offering_id == LabOffering.id)
                    & (LabPrice.superseded_at.is_(None)),
                )
                .where(
                    LabOffering.lab_organization_id == lab_organization_id,
                    LabOffering.catalog_entry_id.in_(required_entry_ids),
                    LabOffering.active.is_(True),
                    TestCatalogEntry.active.is_(True),
                    TestCatalogEntry.is_synthetic.is_(True),
                    LabPrice.currency == "DJF",
                )
            ).all()

            found_entry_ids = frozenset(entry.id for _offering, entry, _price in price_rows)
            if len(price_rows) != required_count or found_entry_ids != required_entry_ids:
                raise LabCannotFulfillOrder(
                    f"lab {lab_organization_id} no longer has an active offering + current DJF "
                    f"price for every requested item -- refusing partial/fabricated selection"
                )

            actual_price_ids = frozenset(price.id for _offering, _entry, price in price_rows)
            if actual_price_ids != expected_price_ids:
                raise StalePriceSelection(
                    "the current prices for this lab no longer match what was shown -- refresh "
                    "lab options and choose again"
                )

            now = utc_now()
            item_by_entry = {item.catalog_entry_id: item for item in order.items}
            for _offering, entry, price in price_rows:
                item = item_by_entry[entry.id]
                item.selected_price_amount = price.amount
                item.selected_price_currency = price.currency

            lab_selection = LabSelection(
                lab_order_id=lab_order_id,
                lab_organization_id=lab_organization_id,
                selected_at=now,
                selected_via_patient_access_grant_id=patient_access_grant_id,
            )
            session.add(lab_selection)
            order.status = LabOrderStatus.SELECTED.value
            session.flush()
            return _to_selection_record(lab_selection), True

    def assign_by_provider(
        self,
        *,
        lab_order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        provider_organization_id: uuid.UUID,
        session: Session | None = None,
    ) -> tuple[LabSelectionRecord, bool]:
        """DH-EX-010: routes an order to a known lab with no price marketplace/consent step at
        all -- ADR-043's pre-authorized launch option (a) ("a provider/facility-assigned lab
        workflow that never routes through patient price-selection"). Deliberately a *separate*
        named method from ``select()``, not a boolean flag added to it (e.g. ``require_price=``):
        patient price-consent and provider clinical routing are different security/product
        policies, and this module's own docstring's discipline is to keep such policies as
        separate, independently auditable methods rather than one method whose behavior depends
        on a combination of booleans.

        Revalidates lab eligibility fresh at assignment time, never trusting whatever was true
        when the order was created: an ACTIVE ``LAB`` organization, an operational directory
        profile with ``accepting_orders=True``, and an ACTIVE+SYNTHETIC catalog entry with an
        ACTIVE ``LabOffering`` for every requested item. ``listed`` is deliberately **not**
        checked -- an unlisted lab remains a valid, private provider-referral destination; only
        `select()`'s own patient-marketplace path requires `listed=True`. This query never joins
        ``LabPrice`` and this method never writes a price snapshot: ``LabOrderItem.
        selected_price_amount``/``selected_price_currency`` stay ``NULL`` for every item, meaning
        "price was not part of this transaction," never "the test costs zero" -- see
        `exchange/api.py`'s `_patient_order_response` for how the patient-facing API represents
        that honestly rather than as an implied `0 DJF`.

        Enforces the calling provider's own tenant boundary *inside* this locked mutation, not
        only via a pre-route read: an order that exists but belongs to a different provider is
        indistinguishable from one that does not exist at all (`OrderNotFound`), matching
        `LabOrderRepository.get_for_organization`'s anti-enumeration convention -- a future caller
        of this repository method directly (bypassing the HTTP route's own tenant pre-check)
        cannot accidentally assign a different provider's order.

        Returns ``(record, created)``. ``created=False`` means this order already has a
        ``LabSelection`` for this *same* lab -- whether that prior selection was itself an
        earlier provider assignment (a genuine idempotent replay: no second mutation, no second
        audit row) or a patient's own legitimate DH-EX-005 selection (preserved exactly as it
        was: this branch never mutates anything, so its price snapshots and
        `PatientAccessGrant` provenance are untouched). A different lab already selected raises
        `DifferentLabAlreadySelected`, the same exception `select()` raises for the identical
        conflict -- there is no reassignment policy in this MVP for either workflow."""
        if session is not None:
            return self._assign_by_provider_in_session(
                session,
                lab_order_id=lab_order_id,
                lab_organization_id=lab_organization_id,
                provider_organization_id=provider_organization_id,
            )
        with self._sessions.begin() as owned_session:
            return self._assign_by_provider_in_session(
                owned_session,
                lab_order_id=lab_order_id,
                lab_organization_id=lab_organization_id,
                provider_organization_id=provider_organization_id,
            )

    def _assign_by_provider_in_session(
        self,
        session: Session,
        *,
        lab_order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        provider_organization_id: uuid.UUID,
    ) -> tuple[LabSelectionRecord, bool]:
        order = session.scalar(
            select(LabOrder).where(LabOrder.id == lab_order_id).with_for_update()
        )
        if order is None or order.provider_organization_id != provider_organization_id:
            # Cross-provider order ids look exactly like nonexistent ones -- anti-enumeration,
            # matching `LabOrderRepository.get_for_organization`'s convention.
            raise OrderNotFound(str(lab_order_id))

        existing_selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == lab_order_id)
        )
        if existing_selection is not None:
            if existing_selection.lab_organization_id != lab_organization_id:
                raise DifferentLabAlreadySelected(
                    f"LabOrder {lab_order_id} already has a selection for a different lab"
                )
            # Same lab already assigned/selected, regardless of who did it first -- preserved
            # exactly as-is: no mutation, no audit, no price population. This is what keeps a
            # patient's own prior DH-EX-005 selection (its frozen prices, its grant provenance)
            # completely untouched by a later provider assignment naming the same lab.
            return _to_selection_record(existing_selection), False

        if order.status != LabOrderStatus.AWAITING_LAB_SELECTION.value:
            raise InvalidOrderTransition(
                f"cannot assign a lab for LabOrder {lab_order_id} in status {order.status!r} -- "
                f"only an order still AWAITING_LAB_SELECTION may be assigned"
            )

        required_entry_ids = frozenset(item.catalog_entry_id for item in order.items)
        required_count = len(required_entry_ids)

        organization = session.get(Organization, lab_organization_id)
        if (
            organization is None
            or organization.status != OrganizationStatus.ACTIVE.value
            or organization.org_type != OrganizationType.LAB.value
        ):
            raise LabCannotFulfillOrder(
                f"lab {lab_organization_id} is not an ACTIVE LAB organization"
            )
        profile = session.scalar(
            select(LabDirectoryProfile).where(
                LabDirectoryProfile.lab_organization_id == lab_organization_id
            )
        )
        # `listed` is deliberately NOT checked here -- see this method's own docstring.
        if profile is None or not profile.accepting_orders:
            raise LabCannotFulfillOrder(
                f"lab {lab_organization_id} is not currently accepting orders"
            )

        # Deliberately no `LabPrice` join anywhere in this query, unlike `select()`'s equivalent
        # eligibility check -- ADR-043 Law 1: price must never be a prerequisite for
        # provider-assigned routing. DH-EX-010's negative control proves the no-price acceptance
        # test fails if a price join is added back here.
        offering_rows = session.execute(
            select(LabOffering, TestCatalogEntry)
            .join(TestCatalogEntry, TestCatalogEntry.id == LabOffering.catalog_entry_id)
            .where(
                LabOffering.lab_organization_id == lab_organization_id,
                LabOffering.catalog_entry_id.in_(required_entry_ids),
                LabOffering.active.is_(True),
                TestCatalogEntry.active.is_(True),
                TestCatalogEntry.is_synthetic.is_(True),
            )
        ).all()
        found_entry_ids = frozenset(entry.id for _offering, entry in offering_rows)
        if len(offering_rows) != required_count or found_entry_ids != required_entry_ids:
            raise LabCannotFulfillOrder(
                f"lab {lab_organization_id} does not have an active offering for every "
                f"requested item -- refusing partial assignment"
            )

        lab_selection = LabSelection(
            lab_order_id=lab_order_id,
            lab_organization_id=lab_organization_id,
            selected_at=utc_now(),
            selected_via_patient_access_grant_id=None,
        )
        session.add(lab_selection)
        order.status = LabOrderStatus.SELECTED.value
        session.flush()
        return _to_selection_record(lab_selection), True

    def get_for_order(self, lab_order_id: uuid.UUID) -> LabSelectionRecord | None:
        with self._sessions() as session:
            lab_selection = session.scalar(
                select(LabSelection).where(LabSelection.lab_order_id == lab_order_id)
            )
            if lab_selection is None:
                return None
            return _to_selection_record(lab_selection)


def _to_selection_record(lab_selection: LabSelection) -> LabSelectionRecord:
    return LabSelectionRecord(
        id=lab_selection.id,
        lab_order_id=lab_selection.lab_order_id,
        lab_organization_id=lab_selection.lab_organization_id,
        selected_at=lab_selection.selected_at,
        selected_via_patient_access_grant_id=lab_selection.selected_via_patient_access_grant_id,
    )
