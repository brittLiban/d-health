"""DH-EX-001: selecting one destination lab for an order.

MVP scope is one lab per order (DH-PIVOT-001's approved architecture). Selecting a lab freezes a
per-item price snapshot onto ``LabOrderItem`` at the moment of selection and never re-reads
``LabPrice`` afterward, so a lab raising its price later cannot silently change what an
already-selecting patient saw and agreed to.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import (
    LabOffering,
    LabOrder,
    LabOrderItem,
    LabOrderStatus,
    LabPrice,
    LabSelection,
    OrganizationType,
)
from daher_adapter.exchange.orders import InvalidOrderTransition, OrderNotFound
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.models import utc_now


class LabCannotFulfillOrder(RuntimeError):
    """The chosen lab does not have an active offering + current price for every ordered test."""


@dataclass(frozen=True, slots=True)
class LabSelectionRecord:
    id: uuid.UUID
    lab_order_id: uuid.UUID
    lab_organization_id: uuid.UUID
    selected_at: datetime


class LabSelectionRepository:
    def __init__(
        self, session_factory: sessionmaker[Session], organizations: OrganizationRepository
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations

    def select(
        self, *, lab_order_id: uuid.UUID, lab_organization_id: uuid.UUID
    ) -> LabSelectionRecord:
        self._organizations.require_type(lab_organization_id, OrganizationType.LAB)

        with self._sessions.begin() as session:
            order = session.scalar(
                select(LabOrder).where(LabOrder.id == lab_order_id).with_for_update()
            )
            if order is None:
                raise OrderNotFound(str(lab_order_id))
            if order.status != LabOrderStatus.AWAITING_LAB_SELECTION.value:
                raise InvalidOrderTransition(
                    f"cannot select a lab for LabOrder {lab_order_id} in status "
                    f"{order.status!r} -- an unselected lab may never become the implicit target"
                )

            # Every ordered item must have an active offering + current price at this lab, or the
            # selection fails closed entirely -- no partial selection, no fabricated price.
            priced_items: list[tuple[LabOrderItem, LabPrice]] = []
            for item in order.items:
                row = session.execute(
                    select(LabOffering, LabPrice)
                    .join(
                        LabPrice,
                        (LabPrice.lab_offering_id == LabOffering.id)
                        & (LabPrice.superseded_at.is_(None)),
                    )
                    .where(
                        LabOffering.lab_organization_id == lab_organization_id,
                        LabOffering.catalog_entry_id == item.catalog_entry_id,
                        LabOffering.active.is_(True),
                    )
                ).first()
                if row is None:
                    raise LabCannotFulfillOrder(
                        f"lab {lab_organization_id} has no active priced offering for catalog "
                        f"entry {item.catalog_entry_id} -- refusing partial/fabricated selection"
                    )
                _offering, price = row
                priced_items.append((item, price))

            now = utc_now()
            for item, price in priced_items:
                item.selected_price_amount = price.amount
                item.selected_price_currency = price.currency

            lab_selection = LabSelection(
                lab_order_id=lab_order_id, lab_organization_id=lab_organization_id, selected_at=now
            )
            session.add(lab_selection)
            order.status = LabOrderStatus.SELECTED.value
            session.flush()
            return LabSelectionRecord(
                id=lab_selection.id,
                lab_order_id=lab_order_id,
                lab_organization_id=lab_organization_id,
                selected_at=now,
            )

    def get_for_order(self, lab_order_id: uuid.UUID) -> LabSelectionRecord | None:
        with self._sessions() as session:
            lab_selection = session.scalar(
                select(LabSelection).where(LabSelection.lab_order_id == lab_order_id)
            )
            if lab_selection is None:
                return None
            return LabSelectionRecord(
                id=lab_selection.id,
                lab_order_id=lab_selection.lab_order_id,
                lab_organization_id=lab_selection.lab_organization_id,
                selected_at=lab_selection.selected_at,
            )
