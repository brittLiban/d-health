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
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import LabOrder, LabOrderStatus, LabSelection, OrganizationType
from daher_adapter.exchange.organizations import OrganizationRepository


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
            return _to_record(order, selection)

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
        tie-break for two orders updated in the same instant."""
        with self._sessions() as session:
            query = (
                select(LabOrder, LabSelection)
                .join(LabSelection, LabSelection.lab_order_id == LabOrder.id)
                .where(LabSelection.lab_organization_id == lab_organization_id)
            )
            if status_filter is not None:
                query = query.where(LabOrder.status == status_filter.value)
            query = query.order_by(LabOrder.updated_at.desc(), LabOrder.id.desc()).limit(limit)
            rows = session.execute(query).all()
            return [_to_record(order, selection) for order, selection in rows]

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
            # Idempotent replay of an already-completed transition -- no mutation, no audit.
            return _to_record(order, selection), False
        if current != from_status:
            raise InvalidLabWorkflowTransition(
                f"cannot move LabOrder {order_id} to {to_status.value!r} from its current "
                f"status {current.value!r} -- only {from_status.value!r} may transition to "
                f"{to_status.value!r}"
            )

        order.status = to_status.value
        session.flush()
        return _to_record(order, selection), True


def _to_record(order: LabOrder, selection: LabSelection) -> LabWorkflowOrderRecord:
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
                catalog_entry_id=item.catalog_entry_id,
                catalog_code_snapshot=item.catalog_code_snapshot,
                catalog_display_name_snapshot=item.catalog_display_name_snapshot,
                selected_price_amount=item.selected_price_amount,
                selected_price_currency=item.selected_price_currency,
            )
            for item in order.items
        ),
    )
