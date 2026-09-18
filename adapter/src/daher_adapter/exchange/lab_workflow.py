"""DH-EX-006: the selected LAB's own view of, and workflow actions on, an order already assigned
to it by a patient's DH-EX-005 selection.

**Assignment ownership is `LabSelection` alone -- never re-derived from marketplace eligibility.**
A lab's ability to see or act on an order is proven by exactly one fact:
``LabSelection.lab_order_id == order.id AND LabSelection.lab_organization_id == <route lab id>``.
This repository never consults `LabDirectoryProfile.listed`/`accepting_orders`,
`LabOffering.active`, or `LabPrice` to decide whether a lab may work an order it was already
assigned -- those govern
whether a lab is eligible to receive a *new* patient selection (DH-EX-003's marketplace contract),
not whether an already-committed assignment remains valid. A lab that becomes unlisted, stops
accepting new orders, deactivates an offering, or changes its price after a patient already chose
it must still be able to see, accept, reject, and start processing that specific assigned order --
exactly the same "historical commitment outlives current marketplace state" principle DH-EX-005's
`selection.py` already established for frozen prices, applied here to the assignment itself.

**Status machine is `exchange/orders.py`'s existing, unmodified one.** This module implements
only the three transitions DH-EX-006 is scoped to: ``SELECTED``->``ACCEPTED``,
``SELECTED``->``REJECTED``, ``ACCEPTED``->``IN_PROGRESS``. It never redesigns
`_ALLOWED_TRANSITIONS`, never exposes a generic
arbitrary-status endpoint, and never reaches ``COMPLETED`` (DH-EX-007's scope). Each transition
method is idempotent for a lost-response retry: calling `accept` again on an already-``ACCEPTED``
order returns the current state without a second mutation or a second audit row; calling it on any
*other* status raises `InvalidLabWorkflowTransition` (mapped to 409) -- never a silent no-op that
masks a genuine conflict (e.g. retrying `accept` after the order was concurrently rejected).

**Lock order matches DH-EX-005's own established discipline:** ``LabOrder`` is locked
``FOR UPDATE`` first, exactly like `selection.py`'s `select()` and `patient_access.py`'s
`issue()`/`revoke()` -- this is what actually serializes concurrent workflow commands (accept vs.
reject, or 25 concurrent identical commands) against the same order down to one winner. The
`LabSelection` assignment check that follows is a plain read (no separate lock needed): it never
mutates, so PostgreSQL's MVCC guarantees it observes a consistent, already-resolved fact once the
order lock is held -- the same reasoning `selection.py`'s own docstring gives for why its
price/offering/directory reads need no lock of their own.

**DH-EX-011 addition.** ``record_collection`` records the explicit, order-level sample/specimen
collection workflow milestone (see ``LabSampleCollection``'s own docstring in ``models.py`` for the
full "record a fact, invent no clinical content" contract) -- a *separate* concern from the
``ACCEPTED``/``IN_PROGRESS``/``REJECTED`` status machine above, not a fourth status value. It shares
this module's own lock-order and assignment-ownership discipline exactly (``LabOrder`` locked first,
``LabSelection`` the sole ownership proof), but does not mutate ``LabOrder.status`` at all -- the
order remains ``ACCEPTED`` after a milestone is recorded. ``start_processing`` (via
``_transition_in_session``) is extended with a one-line, duplicated-not-shared invariant check
requiring a recorded milestone before a *new* ``ACCEPTED -> IN_PROGRESS`` transition -- duplicated
(not factored into a cross-module helper) because it is a single trivial existence query, and this
package's own established style favors a repeated one-liner over a premature shared abstraction; the
identical duplicate lives in ``exchange/orders.py``'s ``transition()``, so that the *generic*
state-machine mutation path cannot bypass the same invariant this module enforces on its own
dedicated ``start_processing`` action -- see that function's own comment for why both must agree.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from daher_adapter.exchange.models import (
    LabOrder,
    LabOrderStatus,
    LabSampleCollection,
    LabSelection,
    OrganizationType,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.models import utc_now


class LabOrderNotAssigned(RuntimeError):
    """No LabOrder matches the given id, or it exists but is not assigned (via a committed
    LabSelection) to the given lab organization -- both cases map to the identical 404, never
    disclosing that an order id belongs to a different lab (anti-enumeration, matching every other
    tenant-scoped lookup in this package)."""


class InvalidLabWorkflowTransition(RuntimeError):
    """The order's current status does not permit the requested lab-workflow action, and it is
    not a safe idempotent replay of an already-completed one either."""


@dataclass(frozen=True, slots=True)
class LabWorkflowOrderItemRecord:
    id: uuid.UUID
    catalog_entry_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str
    selected_price_amount: Decimal | None
    selected_price_currency: str | None


@dataclass(frozen=True, slots=True)
class LabWorkflowOrderRecord:
    id: uuid.UUID
    status: str
    provider_organization_id: uuid.UUID
    patient_reference_id: uuid.UUID
    lab_organization_id: uuid.UUID
    selected_at: datetime
    updated_at: datetime
    items: tuple[LabWorkflowOrderItemRecord, ...]
    collection_recorded_at: datetime | None
    """DH-EX-011: ``None`` means exactly "no sample/specimen collection milestone has been
    recorded in DHealth for this order" -- for a legacy order that predates this ticket, that is
    honestly "unrecorded," never a false claim that collection did not happen (see
    ``LabSampleCollection``'s own docstring)."""


@dataclass(frozen=True, slots=True)
class LabSampleCollectionRecord:
    id: uuid.UUID
    lab_order_id: uuid.UUID
    lab_organization_id: uuid.UUID
    recorded_at: datetime


class LabWorkflowRepository:
    def __init__(
        self, session_factory: sessionmaker[Session], organizations: OrganizationRepository
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations

    def get_for_lab(
        self, order_id: uuid.UUID, lab_organization_id: uuid.UUID
    ) -> LabWorkflowOrderRecord | None:
        """Read-only, tenant-safe -- no lock needed for a GET. Returns ``None`` both when the
        order does not exist and when it exists but is not assigned to this lab."""
        with self._sessions() as session:
            order = session.get(LabOrder, order_id)
            if order is None:
                return None
            selection = session.scalar(
                select(LabSelection).where(LabSelection.lab_order_id == order_id)
            )
            if selection is None or selection.lab_organization_id != lab_organization_id:
                return None
            collection = session.scalar(
                select(LabSampleCollection).where(LabSampleCollection.lab_order_id == order_id)
            )
            return _to_record(order, selection, collection)

    def list_for_lab(
        self,
        lab_organization_id: uuid.UUID,
        *,
        limit: int = 50,
        status_filter: LabOrderStatus | None = None,
    ) -> list[LabWorkflowOrderRecord]:
        """Bounded, deterministically-ordered work-queue listing: most-recently-updated first
        (``LabOrder.updated_at`` carries ``onupdate=utc_now``, so every status transition already
        reliably advances it -- no extra bookkeeping column needed), order UUID as a stable
        tie-break for two orders updated in the same instant.

        DH-EX-011: collection state is folded into this same query via ``outerjoin`` -- never a
        second per-row query -- so exposing ``collection_recorded_at`` here introduces zero N+1
        regression regardless of how many orders this bounded listing returns.

        DH-LAB-002: ``.options(selectinload(LabOrder.items))`` closes the pre-existing,
        DH-EX-011-disclosed ``LabOrder.items`` lazy-load N+1 (see
        ``test_list_for_lab_collection_join_adds_no_marginal_per_order_query``'s own docstring for
        where this was first found and deliberately left unfixed, out of that ticket's scope).
        ``selectinload`` -- not ``joinedload`` -- because the primary query already returns one row
        per ``LabOrder`` via the ``LabSelection``/``LabSampleCollection`` joins above; a
        ``joinedload`` of the one-to-many ``items`` would multiply those rows per item and require
        deduplication, where ``selectinload`` instead issues exactly one additional bounded
        ``WHERE lab_order_id IN (...)`` query for every order in this page's result set, regardless
        of how many orders or items each order has."""
        with self._sessions() as session:
            query = (
                select(LabOrder, LabSelection, LabSampleCollection)
                .join(LabSelection, LabSelection.lab_order_id == LabOrder.id)
                .outerjoin(LabSampleCollection, LabSampleCollection.lab_order_id == LabOrder.id)
                .where(LabSelection.lab_organization_id == lab_organization_id)
                .options(selectinload(LabOrder.items))
            )
            if status_filter is not None:
                query = query.where(LabOrder.status == status_filter.value)
            query = query.order_by(LabOrder.updated_at.desc(), LabOrder.id.desc()).limit(limit)
            rows = session.execute(query).all()
            return [
                _to_record(order, selection, collection) for order, selection, collection in rows
            ]

    def accept(
        self, *, order_id: uuid.UUID, lab_organization_id: uuid.UUID, session: Session | None = None
    ) -> tuple[LabWorkflowOrderRecord, bool]:
        """Returns ``(record, transitioned)``. ``transitioned=False`` means the order was already
        ``ACCEPTED`` and this call was a safe idempotent replay -- no mutation, no audit."""
        if session is not None:
            return self._transition_in_session(
                session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                from_status=LabOrderStatus.SELECTED,
                to_status=LabOrderStatus.ACCEPTED,
            )
        with self._sessions.begin() as owned_session:
            return self._transition_in_session(
                owned_session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                from_status=LabOrderStatus.SELECTED,
                to_status=LabOrderStatus.ACCEPTED,
            )

    def reject(
        self, *, order_id: uuid.UUID, lab_organization_id: uuid.UUID, session: Session | None = None
    ) -> tuple[LabWorkflowOrderRecord, bool]:
        if session is not None:
            return self._transition_in_session(
                session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                from_status=LabOrderStatus.SELECTED,
                to_status=LabOrderStatus.REJECTED,
            )
        with self._sessions.begin() as owned_session:
            return self._transition_in_session(
                owned_session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                from_status=LabOrderStatus.SELECTED,
                to_status=LabOrderStatus.REJECTED,
            )

    def start_processing(
        self, *, order_id: uuid.UUID, lab_organization_id: uuid.UUID, session: Session | None = None
    ) -> tuple[LabWorkflowOrderRecord, bool]:
        if session is not None:
            return self._transition_in_session(
                session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                from_status=LabOrderStatus.ACCEPTED,
                to_status=LabOrderStatus.IN_PROGRESS,
            )
        with self._sessions.begin() as owned_session:
            return self._transition_in_session(
                owned_session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                from_status=LabOrderStatus.ACCEPTED,
                to_status=LabOrderStatus.IN_PROGRESS,
            )

    def _transition_in_session(
        self,
        session: Session,
        *,
        order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        from_status: LabOrderStatus,
        to_status: LabOrderStatus,
    ) -> tuple[LabWorkflowOrderRecord, bool]:
        # Fails closed if the caller's own organization id is not an ACTIVE LAB -- defensive,
        # mirroring `selection.py`'s own `require_type` call; the HTTP layer's authorization
        # dependency already proves this for a real request, but this repository is not solely
        # reachable through that one caller.
        self._organizations.require_type(lab_organization_id, OrganizationType.LAB)

        order = session.scalar(select(LabOrder).where(LabOrder.id == order_id).with_for_update())
        if order is None:
            raise LabOrderNotAssigned(str(order_id))

        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        if selection is None or selection.lab_organization_id != lab_organization_id:
            raise LabOrderNotAssigned(str(order_id))

        current = LabOrderStatus(order.status)
        if current == to_status:
            # Idempotent replay of an already-completed transition -- no mutation, no audit. This
            # branch fires unconditionally, before the DH-EX-011 collection check below, which is
            # exactly what preserves a legacy pre-DH-EX-011 IN_PROGRESS order's safe retry: an
            # order already IN_PROGRESS with no collection milestone never reaches that check on a
            # repeated start_processing call, so no fake milestone is ever needed to let it pass.
            return _to_record(order, selection, _get_collection(session, order_id)), False
        if current != from_status:
            raise InvalidLabWorkflowTransition(
                f"cannot move LabOrder {order_id} to {to_status.value!r} from its current "
                f"status {current.value!r} -- only {from_status.value!r} may transition to "
                f"{to_status.value!r}"
            )

        # DH-EX-011: a *new* ACCEPTED -> IN_PROGRESS transition requires an explicitly recorded
        # sample/specimen collection milestone -- never inferred from status. Deliberately
        # duplicated (not shared via cross-module import) in `exchange/orders.py`'s own generic
        # `transition()`, so the same invariant holds regardless of which repository method a
        # caller uses to reach IN_PROGRESS -- see this module's own docstring for why.
        if to_status == LabOrderStatus.IN_PROGRESS and _get_collection(session, order_id) is None:
            raise InvalidLabWorkflowTransition(
                f"cannot start processing LabOrder {order_id} -- no sample/specimen collection "
                f"milestone has been recorded for this order yet"
            )

        order.status = to_status.value
        session.flush()
        return _to_record(order, selection, _get_collection(session, order_id)), True

    def record_collection(
        self, *, order_id: uuid.UUID, lab_organization_id: uuid.UUID, session: Session | None = None
    ) -> tuple[LabSampleCollectionRecord, bool]:
        """DH-EX-011: records the explicit, order-level fact that this lab's authorized
        ``LAB_TECH`` advanced the sample/specimen collection workflow milestone -- see
        ``LabSampleCollection``'s own docstring in ``models.py`` for the full contract. Returns
        ``(record, created)``: ``created=False`` means an existing milestone was returned as a
        safe idempotent replay -- unconditionally, even if the order has since progressed past
        ``ACCEPTED`` to ``IN_PROGRESS``/``COMPLETED``/``CANCELLED``, because the milestone
        genuinely already happened and this call never rewrites or duplicates it.

        A *new* milestone may only be created while ``order.status == ACCEPTED``. Any other
        status with no existing milestone (including ``SELECTED`` -- collection attempted before
        acceptance -- or a legacy ``IN_PROGRESS``/``COMPLETED`` order that predates this ticket and
        never had one recorded) raises ``InvalidLabWorkflowTransition`` rather than fabricating
        retroactive physical-event history: DHealth did not observe collection for that order, and
        this call must never silently manufacture that it did."""
        if session is not None:
            return self._record_collection_in_session(
                session, order_id=order_id, lab_organization_id=lab_organization_id
            )
        with self._sessions.begin() as owned_session:
            return self._record_collection_in_session(
                owned_session, order_id=order_id, lab_organization_id=lab_organization_id
            )

    def _record_collection_in_session(
        self, session: Session, *, order_id: uuid.UUID, lab_organization_id: uuid.UUID
    ) -> tuple[LabSampleCollectionRecord, bool]:
        self._organizations.require_type(lab_organization_id, OrganizationType.LAB)

        order = session.scalar(select(LabOrder).where(LabOrder.id == order_id).with_for_update())
        if order is None:
            raise LabOrderNotAssigned(str(order_id))

        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        if selection is None or selection.lab_organization_id != lab_organization_id:
            raise LabOrderNotAssigned(str(order_id))

        existing = _get_collection(session, order_id)
        if existing is not None:
            # Idempotent replay -- unconditional regardless of the order's current status: the
            # milestone genuinely already happened, so this never re-validates status, never
            # rewrites `recorded_at`, and never creates a second row.
            return _to_collection_record(existing), False

        if LabOrderStatus(order.status) != LabOrderStatus.ACCEPTED:
            raise InvalidLabWorkflowTransition(
                f"cannot record a sample/specimen collection milestone for LabOrder {order_id} "
                f"in status {order.status!r} -- a new milestone may only be recorded while "
                f"ACCEPTED, and none was ever recorded for this order"
            )

        collection = LabSampleCollection(
            lab_order_id=order_id, lab_organization_id=lab_organization_id, recorded_at=utc_now()
        )
        session.add(collection)
        session.flush()
        return _to_collection_record(collection), True

    def get_collection_for_order(self, order_id: uuid.UUID) -> LabSampleCollectionRecord | None:
        with self._sessions() as session:
            collection = _get_collection(session, order_id)
            return None if collection is None else _to_collection_record(collection)


def _get_collection(session: Session, order_id: uuid.UUID) -> LabSampleCollection | None:
    return session.scalar(
        select(LabSampleCollection).where(LabSampleCollection.lab_order_id == order_id)
    )


def _to_record(
    order: LabOrder, selection: LabSelection, collection: LabSampleCollection | None
) -> LabWorkflowOrderRecord:
    return LabWorkflowOrderRecord(
        id=order.id,
        status=order.status,
        provider_organization_id=order.provider_organization_id,
        patient_reference_id=order.patient_reference_id,
        lab_organization_id=selection.lab_organization_id,
        selected_at=selection.selected_at,
        updated_at=order.updated_at,
        items=tuple(
            LabWorkflowOrderItemRecord(
                id=item.id,
                catalog_entry_id=item.catalog_entry_id,
                catalog_code_snapshot=item.catalog_code_snapshot,
                catalog_display_name_snapshot=item.catalog_display_name_snapshot,
                selected_price_amount=item.selected_price_amount,
                selected_price_currency=item.selected_price_currency,
            )
            for item in order.items
        ),
        collection_recorded_at=collection.recorded_at if collection is not None else None,
    )


def _to_collection_record(collection: LabSampleCollection) -> LabSampleCollectionRecord:
    return LabSampleCollectionRecord(
        id=collection.id,
        lab_order_id=collection.lab_order_id,
        lab_organization_id=collection.lab_organization_id,
        recorded_at=collection.recorded_at,
    )
