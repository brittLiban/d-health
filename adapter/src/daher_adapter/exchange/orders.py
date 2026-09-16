"""DH-EX-001: LabOrder creation and its status machine.

Idempotency mirrors ``daher_adapter.ledger.LedgerRepository.claim`` -- insert-first-wins on a
unique column, catch the resulting ``IntegrityError``, and return what is already there instead of
raising -- but keyed on a client-supplied *request* identity (``idempotency_key``), not a replayed
source-event tuple: a ``LabOrder`` is created by a business command, not observed from an upstream
system. Two calls with the same key return the same order (`created=False` on the second); two
calls with different keys always create two orders, even with byte-identical clinical content --
this repository never inspects patient/test/practitioner/time to guess duplicate intent, per
DH-EX-001's explicit instruction.

**DH-EX-001R hardening, two findings:**

1. Uniqueness is ``(provider_organization_id, idempotency_key)``, not a bare global
   ``idempotency_key`` -- see ``LabOrder``'s own docstring in ``models.py`` for why a global key
   is a cross-tenant data-leak risk, not just a UX one.
2. A resubmitted key is only ever treated as a *replay* if the canonical immutable identifiers of
   the request -- patient, practitioner, and the exact set of requested catalog entries -- match
   the original order exactly. A resubmission with the same key but different content raises
   ``IdempotencyKeyConflict`` rather than silently handing back the original (mismatched) order.
   The idempotency key represents one intentional submission, not "whatever happened to be stored
   under this string" -- comparison uses only canonical immutable identifiers (UUIDs), never
   mutable display fields.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.models import (
    DoctorReview,
    DoctorReviewStatus,
    LabOrder,
    LabOrderItem,
    LabOrderStatus,
    LabResultRelease,
    LabSampleCollection,
    OrganizationType,
    ResultDelivery,
    ResultDeliveryStatus,
)
from daher_adapter.exchange.organizations import OrganizationRepository

# DH-EX-001's approved minimal status machine. A status absent from this mapping (COMPLETED,
# REJECTED, CANCELLED) is terminal -- no key means no outbound transition is ever permitted, not
# even back to an earlier state. This is checked in code (`transition`), never inferred from the
# DB CHECK constraint alone, because the CHECK only proves the value is one of the seven strings,
# not that reaching it from the current status is legal.
_ALLOWED_TRANSITIONS: dict[LabOrderStatus, frozenset[LabOrderStatus]] = {
    LabOrderStatus.AWAITING_LAB_SELECTION: frozenset(
        {LabOrderStatus.SELECTED, LabOrderStatus.REJECTED, LabOrderStatus.CANCELLED}
    ),
    LabOrderStatus.SELECTED: frozenset(
        {LabOrderStatus.ACCEPTED, LabOrderStatus.REJECTED, LabOrderStatus.CANCELLED}
    ),
    LabOrderStatus.ACCEPTED: frozenset({LabOrderStatus.IN_PROGRESS, LabOrderStatus.CANCELLED}),
    LabOrderStatus.IN_PROGRESS: frozenset({LabOrderStatus.COMPLETED, LabOrderStatus.CANCELLED}),
    LabOrderStatus.COMPLETED: frozenset(),
    LabOrderStatus.REJECTED: frozenset(),
    LabOrderStatus.CANCELLED: frozenset(),
}


class InvalidOrderTransition(RuntimeError):
    """A LabOrder was asked to move to a status its current status does not permit."""


class OrderNotFound(RuntimeError):
    """No LabOrder matches the given id or (provider_organization_id, idempotency_key) pair."""


class IdempotencyKeyConflict(RuntimeError):
    """The same (provider_organization_id, idempotency_key) was resubmitted with different
    canonical request content (patient, practitioner, or requested tests) -- refused rather than
    silently returning the original, mismatched order."""


class EmptyOrder(ValueError):
    """A LabOrder was requested with zero items."""


class DuplicateOrderItems(ValueError):
    """The same catalog_entry_id was requested more than once for one order -- refused rather
    than silently deduplicated (DH-EX-004R: the HTTP schema already rejects this, but the
    repository is the actual enforcement point, not just one caller -- matching this package's
    established discipline for every other duplicate/conflict check)."""


@dataclass(frozen=True, slots=True)
class OrderItemInput:
    catalog_entry_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LabOrderItemRecord:
    id: uuid.UUID
    catalog_entry_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str
    selected_price_amount: Decimal | None
    selected_price_currency: str | None


@dataclass(frozen=True, slots=True)
class LabOrderRecord:
    id: uuid.UUID
    idempotency_key: str
    provider_organization_id: uuid.UUID
    patient_reference_id: uuid.UUID
    practitioner_reference_id: uuid.UUID
    status: str
    created_at: datetime
    items: tuple[LabOrderItemRecord, ...]


@dataclass(frozen=True, slots=True)
class CreateOrderResult:
    order: LabOrderRecord
    created: bool
    """``False`` means an existing order for this idempotency key was returned instead of a new
    one being created -- the caller's retry/duplicate-click case, not an error."""


class LabOrderRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        organizations: OrganizationRepository,
        catalog: CatalogRepository,
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations
        self._catalog = catalog

    def create(
        self,
        *,
        idempotency_key: str,
        provider_organization_id: uuid.UUID,
        patient_reference_id: uuid.UUID,
        practitioner_reference_id: uuid.UUID,
        items: list[OrderItemInput],
        session: Session | None = None,
    ) -> CreateOrderResult:
        """DH-EX-004: ``session`` lets a caller (the HTTP order-creation command) fold this into
        one outer transaction alongside a practitioner-reference get-or-create and the
        authoritative ``lab_order_created`` audit row -- when given, the insert attempt runs
        inside a SAVEPOINT (``begin_nested``) so a duplicate idempotency-key race only unwinds to
        that savepoint, never poisoning the caller's outer transaction, matching the exact pattern
        already established for offerings/prices/memberships/practitioner references. When
        omitted, this method still owns and commits its own transaction exactly as before,
        preserving every existing caller (e.g. this package's own tests)."""
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if not items:
            raise EmptyOrder("a LabOrder must have at least one item")
        requested_ids = [item.catalog_entry_id for item in items]
        if len(set(requested_ids)) != len(requested_ids):
            raise DuplicateOrderItems(
                "an order must not request the same catalog_entry_id more than once"
            )

        # Fails closed if the sender is not an ACTIVE provider organization.
        self._organizations.require_type(provider_organization_id, OrganizationType.PROVIDER)

        # Snapshot each item's catalog display/code *before* the insert attempt -- read-only,
        # outside any write transaction, so a duplicate submission never pays for a second write
        # even though it still re-reads the catalog (cheap, and keeps this method simple).
        snapshots = [
            (item.catalog_entry_id, self._catalog.get_entry(item.catalog_entry_id))
            for item in items
        ]

        if session is not None:
            return self._create_in_session(
                session,
                idempotency_key=idempotency_key,
                provider_organization_id=provider_organization_id,
                patient_reference_id=patient_reference_id,
                practitioner_reference_id=practitioner_reference_id,
                items=items,
                snapshots=snapshots,
            )
        with self._sessions() as owned_session:
            result = self._create_in_session(
                owned_session,
                idempotency_key=idempotency_key,
                provider_organization_id=provider_organization_id,
                patient_reference_id=patient_reference_id,
                practitioner_reference_id=practitioner_reference_id,
                items=items,
                snapshots=snapshots,
            )
            owned_session.commit()
            return result

    def _create_in_session(
        self,
        session: Session,
        *,
        idempotency_key: str,
        provider_organization_id: uuid.UUID,
        patient_reference_id: uuid.UUID,
        practitioner_reference_id: uuid.UUID,
        items: list[OrderItemInput],
        snapshots: list[tuple[uuid.UUID, object]],
    ) -> CreateOrderResult:
        order = LabOrder(
            idempotency_key=idempotency_key,
            provider_organization_id=provider_organization_id,
            patient_reference_id=patient_reference_id,
            practitioner_reference_id=practitioner_reference_id,
            status=LabOrderStatus.AWAITING_LAB_SELECTION.value,
        )
        order.items = [
            LabOrderItem(
                catalog_entry_id=catalog_entry_id,
                catalog_code_snapshot=entry.code,
                catalog_display_name_snapshot=entry.display_name,
            )
            for catalog_entry_id, entry in snapshots
        ]

        try:
            with session.begin_nested():
                session.add(order)
                # DoctorReview is created PENDING in the same transaction as the order itself --
                # "review remains pending until explicit provider action" is true from the first
                # instant the order exists, never a state inferred by a missing row.
                session.flush()  # assigns order.id without committing
                session.add(
                    DoctorReview(lab_order_id=order.id, status=DoctorReviewStatus.PENDING.value)
                )
                # Created PENDING alongside the order itself, same reasoning as DoctorReview:
                # "provider destination explicit" is true from the first instant the order
                # exists, and delivery status must never be inferred from LabResult completion.
                session.add(
                    ResultDelivery(
                        lab_order_id=order.id,
                        provider_organization_id=provider_organization_id,
                        status=ResultDeliveryStatus.PENDING.value,
                    )
                )
                session.flush()
            return CreateOrderResult(order=_to_order_record(order), created=True)
        except IntegrityError:
            existing = session.scalar(
                select(LabOrder).where(
                    LabOrder.provider_organization_id == provider_organization_id,
                    LabOrder.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise
            existing_record = _to_order_record(existing)
            existing_item_ids = frozenset(item.catalog_entry_id for item in existing_record.items)
            requested_item_ids = frozenset(item.catalog_entry_id for item in items)
            if (
                existing_record.patient_reference_id != patient_reference_id
                or existing_record.practitioner_reference_id != practitioner_reference_id
                or existing_item_ids != requested_item_ids
            ):
                raise IdempotencyKeyConflict(
                    f"idempotency_key {idempotency_key!r} for provider organization "
                    f"{provider_organization_id} was already used for a request with a "
                    f"different patient, practitioner, or requested test set -- refusing to "
                    f"return mismatched data"
                ) from None
            return CreateOrderResult(order=existing_record, created=False)

    def get(self, order_id: uuid.UUID) -> LabOrderRecord:
        with self._sessions() as session:
            order = session.get(LabOrder, order_id)
            if order is None:
                raise OrderNotFound(str(order_id))
            return _to_order_record(order)

    def get_for_organization(
        self, order_id: uuid.UUID, provider_organization_id: uuid.UUID
    ) -> LabOrderRecord | None:
        """DH-EX-004: tenant-safe lookup for the HTTP order-detail endpoint -- `get` above is
        keyed globally by order UUID alone and is unsafe to back an HTTP route directly (nothing
        stops a valid UUID belonging to a *different* provider from being probed). Returns
        ``None`` both when the order does not exist at all and when it exists but belongs to a
        different provider, so a caller maps either case to the same 404, never disclosing that
        an order id belongs to someone else."""
        with self._sessions() as session:
            order = session.get(LabOrder, order_id)
            if order is None or order.provider_organization_id != provider_organization_id:
                return None
            return _to_order_record(order)

    def list_for_organization(
        self, provider_organization_id: uuid.UUID, *, limit: int = 50
    ) -> list[LabOrderRecord]:
        """DH-EX-004: bounded, deterministically-ordered listing for the HTTP order-list endpoint
        -- newest first, order UUID as a stable tie-break so two orders created in the same
        instant still sort deterministically across repeated calls."""
        with self._sessions() as session:
            orders = session.scalars(
                select(LabOrder)
                .options(selectinload(LabOrder.items))
                .where(LabOrder.provider_organization_id == provider_organization_id)
                .order_by(LabOrder.created_at.desc(), LabOrder.id.desc())
                .limit(limit)
            ).all()
            return [_to_order_record(order) for order in orders]

    def list_for_patient(
        self,
        provider_organization_id: uuid.UUID,
        patient_reference_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> list[LabOrderRecord]:
        """DH-CLINIC-003: bounded, deterministically-ordered listing of orders for a specific
        patient reference under a provider organization. Eagerly loads items to prevent N+1 queries.
        Newest first, order UUID as a stable tie-break."""
        with self._sessions() as session:
            orders = session.scalars(
                select(LabOrder)
                .options(selectinload(LabOrder.items))
                .where(
                    LabOrder.provider_organization_id == provider_organization_id,
                    LabOrder.patient_reference_id == patient_reference_id,
                )
                .order_by(LabOrder.created_at.desc(), LabOrder.id.desc())
                .limit(limit)
            ).all()
            return [_to_order_record(order) for order in orders]

    def get_by_idempotency_key(
        self, provider_organization_id: uuid.UUID, idempotency_key: str
    ) -> LabOrderRecord:
        with self._sessions() as session:
            order = session.scalar(
                select(LabOrder).where(
                    LabOrder.provider_organization_id == provider_organization_id,
                    LabOrder.idempotency_key == idempotency_key,
                )
            )
            if order is None:
                raise OrderNotFound(idempotency_key)
            return _to_order_record(order)

    def transition(self, order_id: uuid.UUID, to_status: LabOrderStatus) -> LabOrderRecord:
        """Row-locks the order and refuses any transition not explicitly present in
        ``_ALLOWED_TRANSITIONS`` -- this is the sole place order status ever changes after
        creation. Never silently invents a rollback transition; a COMPLETED/REJECTED/CANCELLED
        order has no outgoing edges at all.

        DH-EX-011: a generic ``ACCEPTED -> IN_PROGRESS`` transition through this method requires
        the same recorded sample/specimen collection milestone
        ``exchange/lab_workflow.py``'s own dedicated ``start_processing`` action requires --
        deliberately duplicated here (a single trivial existence query), not shared via a
        cross-module helper, so that this generic state-machine primitive cannot become an
        unguarded bypass of an invariant its own domain-specific sibling enforces. This method has
        no production HTTP caller today (`lab_workflow.start_processing` is the sole supported way
        to reach ``IN_PROGRESS``), but nothing prevents a future caller from using it directly, and
        this repository's own established discipline is to enforce a security/workflow invariant
        inside the mutation itself, never rely solely on "no caller happens to bypass it today"
        (the same reasoning DH-EX-010's `assign_by_provider` tenant check and DH-EX-010A's
        capability check both already apply).

        DH-EX-012/ADR-044: a generic ``IN_PROGRESS -> COMPLETED`` transition through this method
        now requires an existing ``LabResultRelease`` row too, for the identical reason -- the
        dedicated release action (``exchange/results.py``'s ``LabResultReleaseRepository.release``)
        is the sole supported production way to reach ``COMPLETED``, but this generic primitive
        must not become an unguarded bypass of that same invariant."""
        with self._sessions.begin() as session:
            order = session.scalar(
                select(LabOrder).where(LabOrder.id == order_id).with_for_update()
            )
            if order is None:
                raise OrderNotFound(str(order_id))
            current = LabOrderStatus(order.status)
            allowed = _ALLOWED_TRANSITIONS[current]
            if to_status not in allowed:
                raise InvalidOrderTransition(
                    f"cannot move LabOrder {order_id} from {current.value!r} to {to_status.value!r}"
                )
            if to_status == LabOrderStatus.IN_PROGRESS:
                has_collection = (
                    session.scalar(
                        select(LabSampleCollection.id).where(
                            LabSampleCollection.lab_order_id == order_id
                        )
                    )
                    is not None
                )
                if not has_collection:
                    raise InvalidOrderTransition(
                        f"cannot move LabOrder {order_id} from 'ACCEPTED' to 'IN_PROGRESS' -- no "
                        f"sample/specimen collection milestone has been recorded for this order "
                        f"(DH-EX-011)"
                    )
            if to_status == LabOrderStatus.COMPLETED:
                has_release = (
                    session.scalar(
                        select(LabResultRelease.id).where(LabResultRelease.lab_order_id == order_id)
                    )
                    is not None
                )
                if not has_release:
                    raise InvalidOrderTransition(
                        f"cannot move LabOrder {order_id} from 'IN_PROGRESS' to 'COMPLETED' -- "
                        f"no result release record exists for this order (DH-EX-012)"
                    )
            order.status = to_status.value
            session.flush()
            return _to_order_record(order)


def _to_order_record(order: LabOrder) -> LabOrderRecord:
    return LabOrderRecord(
        id=order.id,
        idempotency_key=order.idempotency_key,
        provider_organization_id=order.provider_organization_id,
        patient_reference_id=order.patient_reference_id,
        practitioner_reference_id=order.practitioner_reference_id,
        status=order.status,
        created_at=order.created_at,
        items=tuple(
            LabOrderItemRecord(
                id=item.id,
                catalog_entry_id=item.catalog_entry_id,
                catalog_code_snapshot=item.catalog_code_snapshot,
                catalog_display_name_snapshot=item.catalog_display_name_snapshot,
                selected_price_amount=item.selected_price_amount,
                selected_price_currency=item.selected_price_currency,
            )
            for item in order.items
        ),
    )
