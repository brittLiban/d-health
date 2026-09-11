"""DH-EX-001 models, DH-EX-007 workflow: lab results, order completion, result delivery, and
doctor review.

Three deliberately independent state machines (DH-EX-001's own instruction, unchanged by
DH-EX-007): a ``LabResult`` existing does not move ``ResultDelivery`` out of ``PENDING``, and
``ResultDelivery`` reaching ``DELIVERED`` does not move ``DoctorReview`` out of ``PENDING``. Each
requires its own explicit repository call. DH-EX-007 adds exactly one new automatic coupling,
scoped narrowly: recording the *last* missing ``LabResult`` for an order also transitions that
``LabOrder`` to ``COMPLETED``, in the same transaction -- this is the one place "result existing"
and "order state" are linked, and it is a `LabOrder` transition, not a `ResultDelivery` one.
``ResultDelivery`` stays untouched by this module's DH-EX-007 additions: no code path here ever
calls ``mark_delivered``/``mark_failed`` as a side effect of anything -- the exact semantic of
``DELIVERED`` remains an explicit future product decision (recorded in PROJECT_STATE.md's DH-EX-007
entry), not inferred here.

**Lock order matches DH-EX-005/DH-EX-006's own established discipline:** ``LabOrder`` is locked
``FOR UPDATE`` first, before the ``LabSelection`` assignment check, before the lifecycle check,
before the item lookup -- consistency of lock order across the whole package is what actually
serializes concurrent result submissions (including two different, final, order-completing results
submitted at the same instant) down to one coherent outcome, exactly like ``lab_workflow.py``'s own
docstring explains for accept/reject/start-processing. Completeness is re-derived from the database
with an explicit aggregate query after the insert, never read off an already-loaded
``order.items[*].result`` relationship -- SQLAlchemy identity-map/relationship state is not treated
as a strong enough completion invariant for this clinical workflow.

**Assignment/lifecycle failures are non-enumerating, matching every other tenant-scoped lookup in
this package:** a nonexistent order, an order assigned to a different lab, and an order with no
``LabSelection`` at all (i.e. still before ``SELECTED``) all raise the identical
``LabOrderNotAssigned`` (imported from ``lab_workflow.py`` rather than redefined here, so API
routes need only one ``except`` clause across both DH-EX-006 and DH-EX-007 lab-side mutations) --
never disclosing which of those three is actually true to an unassigned caller.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.lab_workflow import LabOrderNotAssigned
from daher_adapter.exchange.models import (
    DoctorReview,
    DoctorReviewStatus,
    LabOrder,
    LabOrderItem,
    LabOrderStatus,
    LabResult,
    LabResultType,
    LabSelection,
    ResultDelivery,
    ResultDeliveryStatus,
)
from daher_adapter.models import utc_now

__all__ = [
    "DeliveryNotFound",
    "DoctorReviewRecord",
    "DoctorReviewRepository",
    "DuplicateResult",
    "InvalidResultSubmissionState",
    "LabOrderNotAssigned",
    "LabResultRecord",
    "LabResultRepository",
    "OrderItemNotFound",
    "ResultDeliveryRecord",
    "ResultDeliveryRepository",
    "ResultSubmissionOutcome",
    "ReviewAlreadyFinalized",
    "ReviewNotAllowedYet",
    "ReviewNotFound",
]


class OrderItemNotFound(RuntimeError):
    """No ``LabOrderItem`` matches the given id *for this exact order* -- identical response
    whether the item id is entirely bogus or belongs to a genuinely different order, so a caller
    can never use this endpoint to probe which order a real item id belongs to."""


class InvalidResultSubmissionState(RuntimeError):
    """The order's current status does not permit result submission -- only ``IN_PROGRESS``
    does. Mirrors ``lab_workflow.InvalidLabWorkflowTransition``'s role for accept/reject/
    start-processing: a real, distinguishable conflict, never a silent no-op."""


class DuplicateResult(RuntimeError):
    """A LabOrderItem already has a result -- V1 models no amendment/correction workflow."""


class DeliveryNotFound(RuntimeError):
    pass


class ReviewNotFound(RuntimeError):
    pass


class ReviewNotAllowedYet(RuntimeError):
    """A DoctorReview mutation was attempted before its LabOrder reached ``COMPLETED``."""


class ReviewAlreadyFinalized(RuntimeError):
    """A DoctorReview mutation was attempted on a review that has already left ``PENDING`` -- V1
    has no review-revision/amendment model, so a second terminal mutation is a controlled
    conflict, never a silent overwrite of the first doctor's disposition."""


@dataclass(frozen=True, slots=True)
class LabResultRecord:
    id: uuid.UUID
    lab_order_item_id: uuid.UUID
    lab_organization_id: uuid.UUID
    result_type: str
    numeric_value: Decimal | None
    numeric_unit: str | None
    reference_range_low: Decimal | None
    reference_range_high: Decimal | None
    text_value: str | None
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class ResultSubmissionOutcome:
    result: LabResultRecord
    order_completed: bool
    """``True`` only for the exact submission that satisfied the last missing item -- never true
    twice for the same order, since a second submission attempt on an already-``COMPLETED`` order
    fails closed at the lifecycle check before reaching the insert at all."""
    order_status: str


@dataclass(frozen=True, slots=True)
class ResultDeliveryRecord:
    id: uuid.UUID
    lab_order_id: uuid.UUID
    provider_organization_id: uuid.UUID
    status: str
    delivered_at: datetime | None


@dataclass(frozen=True, slots=True)
class DoctorReviewRecord:
    id: uuid.UUID
    lab_order_id: uuid.UUID
    status: str
    reviewed_at: datetime | None
    reviewed_by_practitioner_reference_id: uuid.UUID | None


class LabResultRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def record_numeric(
        self,
        *,
        order_id: uuid.UUID,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        value: Decimal,
        unit: str | None = None,
        reference_range_low: Decimal | None = None,
        reference_range_high: Decimal | None = None,
        session: Session | None = None,
    ) -> ResultSubmissionOutcome:
        return self._submit(
            order_id=order_id,
            lab_order_item_id=lab_order_item_id,
            lab_organization_id=lab_organization_id,
            result_type=LabResultType.NUMERIC,
            numeric_value=value,
            numeric_unit=unit,
            reference_range_low=reference_range_low,
            reference_range_high=reference_range_high,
            text_value=None,
            session=session,
        )

    def record_text(
        self,
        *,
        order_id: uuid.UUID,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        value: str,
        session: Session | None = None,
    ) -> ResultSubmissionOutcome:
        if not value.strip():
            raise ValueError("value is required for a text result")
        return self._submit(
            order_id=order_id,
            lab_order_item_id=lab_order_item_id,
            lab_organization_id=lab_organization_id,
            result_type=LabResultType.TEXT,
            numeric_value=None,
            numeric_unit=None,
            reference_range_low=None,
            reference_range_high=None,
            text_value=value,
            session=session,
        )

    def _submit(
        self,
        *,
        order_id: uuid.UUID,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        result_type: LabResultType,
        numeric_value: Decimal | None,
        numeric_unit: str | None,
        reference_range_low: Decimal | None,
        reference_range_high: Decimal | None,
        text_value: str | None,
        session: Session | None,
    ) -> ResultSubmissionOutcome:
        if session is not None:
            return self._submit_in_session(
                session,
                order_id=order_id,
                lab_order_item_id=lab_order_item_id,
                lab_organization_id=lab_organization_id,
                result_type=result_type,
                numeric_value=numeric_value,
                numeric_unit=numeric_unit,
                reference_range_low=reference_range_low,
                reference_range_high=reference_range_high,
                text_value=text_value,
            )
        with self._sessions.begin() as owned_session:
            return self._submit_in_session(
                owned_session,
                order_id=order_id,
                lab_order_item_id=lab_order_item_id,
                lab_organization_id=lab_organization_id,
                result_type=result_type,
                numeric_value=numeric_value,
                numeric_unit=numeric_unit,
                reference_range_low=reference_range_low,
                reference_range_high=reference_range_high,
                text_value=text_value,
            )

    def _submit_in_session(
        self,
        session: Session,
        *,
        order_id: uuid.UUID,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        result_type: LabResultType,
        numeric_value: Decimal | None,
        numeric_unit: str | None,
        reference_range_low: Decimal | None,
        reference_range_high: Decimal | None,
        text_value: str | None,
    ) -> ResultSubmissionOutcome:
        order = session.scalar(select(LabOrder).where(LabOrder.id == order_id).with_for_update())
        if order is None:
            raise LabOrderNotAssigned(str(order_id))

        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        if selection is None or selection.lab_organization_id != lab_organization_id:
            raise LabOrderNotAssigned(str(order_id))

        if LabOrderStatus(order.status) != LabOrderStatus.IN_PROGRESS:
            raise InvalidResultSubmissionState(
                f"LabOrder {order_id} is {order.status!r}, not 'IN_PROGRESS' -- a result may "
                f"only be recorded while the selected lab is actively processing the order"
            )

        item = session.get(LabOrderItem, lab_order_item_id)
        if item is None or item.lab_order_id != order_id:
            raise OrderItemNotFound(str(lab_order_item_id))

        existing = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item.id))
        if existing is not None:
            raise DuplicateResult(
                f"LabOrderItem {lab_order_item_id} already has a result -- no amendment "
                f"workflow exists in V1"
            )

        result = LabResult(
            lab_order_item_id=item.id,
            lab_organization_id=lab_organization_id,
            result_type=result_type.value,
            numeric_value=numeric_value,
            numeric_unit=numeric_unit,
            reference_range_low=reference_range_low,
            reference_range_high=reference_range_high,
            text_value=text_value,
        )
        try:
            with session.begin_nested():
                session.add(result)
                session.flush()
        except IntegrityError:
            # Defensive last line only: the LabOrder FOR UPDATE lock acquired above already
            # serializes every concurrent submission for this order down to one winner, so the
            # pre-insert check above should always be the one that fires in practice. Translated
            # to the same controlled conflict a pre-insert duplicate raises -- never a raw DB
            # error reaching a caller.
            raise DuplicateResult(
                f"LabOrderItem {lab_order_item_id} already has a result (constraint-detected)"
            ) from None

        # DB-grounded completeness check, re-queried explicitly rather than read off an
        # already-loaded `order.items[*].result` relationship -- still inside the same LabOrder
        # FOR UPDATE lock acquired above, so this is a consistent read of this transaction's own
        # write plus whatever was already committed; nothing else can be concurrently mutating
        # this order's items while the lock is held.
        missing_count = session.scalar(
            select(func.count())
            .select_from(LabOrderItem)
            .outerjoin(LabResult, LabResult.lab_order_item_id == LabOrderItem.id)
            .where(LabOrderItem.lab_order_id == order_id, LabResult.id.is_(None))
        )
        order_completed = False
        if missing_count == 0:
            order.status = LabOrderStatus.COMPLETED.value
            order_completed = True
        session.flush()

        return ResultSubmissionOutcome(
            result=_to_result_record(result),
            order_completed=order_completed,
            order_status=order.status,
        )

    def get_for_item(self, lab_order_item_id: uuid.UUID) -> LabResultRecord | None:
        with self._sessions() as session:
            result = session.scalar(
                select(LabResult).where(LabResult.lab_order_item_id == lab_order_item_id)
            )
            return None if result is None else _to_result_record(result)

    def list_for_order(self, order_id: uuid.UUID) -> list[LabResultRecord]:
        """Read-only, tenant-unsafe by itself -- the caller (the provider result-read API route)
        is responsible for proving the requesting provider owns this order and that it has
        reached ``COMPLETED`` *before* calling this, exactly mirroring how
        ``orders.get_for_organization`` is the tenant check that already gates every other
        provider-scoped order read in this package."""
        with self._sessions() as session:
            results = session.scalars(
                select(LabResult)
                .join(LabOrderItem, LabResult.lab_order_item_id == LabOrderItem.id)
                .where(LabOrderItem.lab_order_id == order_id)
            ).all()
            return [_to_result_record(result) for result in results]


class ResultDeliveryRepository:
    """Unchanged by DH-EX-007 -- no code anywhere in this package calls ``mark_delivered``/
    ``mark_failed`` as a side effect of a result being recorded, an order completing, or a
    provider reading results. The exact semantic of ``DELIVERED`` is an explicit future product
    decision (see PROJECT_STATE.md's DH-EX-007 entry), not inferred here."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def mark_delivered(self, lab_order_id: uuid.UUID) -> ResultDeliveryRecord:
        return self._mark(lab_order_id, ResultDeliveryStatus.DELIVERED)

    def mark_failed(self, lab_order_id: uuid.UUID) -> ResultDeliveryRecord:
        return self._mark(lab_order_id, ResultDeliveryStatus.FAILED)

    def _mark(self, lab_order_id: uuid.UUID, status: ResultDeliveryStatus) -> ResultDeliveryRecord:
        with self._sessions.begin() as session:
            delivery = session.scalar(
                select(ResultDelivery)
                .where(ResultDelivery.lab_order_id == lab_order_id)
                .with_for_update()
            )
            if delivery is None:
                raise DeliveryNotFound(str(lab_order_id))
            delivery.status = status.value
            delivery.delivered_at = utc_now() if status == ResultDeliveryStatus.DELIVERED else None
            session.flush()
            return _to_delivery_record(delivery)

    def get_for_order(self, lab_order_id: uuid.UUID) -> ResultDeliveryRecord:
        with self._sessions() as session:
            delivery = session.scalar(
                select(ResultDelivery).where(ResultDelivery.lab_order_id == lab_order_id)
            )
            if delivery is None:
                raise DeliveryNotFound(str(lab_order_id))
            return _to_delivery_record(delivery)


class DoctorReviewRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def mark_no_follow_up_required(
        self,
        lab_order_id: uuid.UUID,
        *,
        reviewed_by_practitioner_reference_id: uuid.UUID,
        session: Session | None = None,
    ) -> DoctorReviewRecord:
        return self._mark(
            lab_order_id,
            DoctorReviewStatus.NO_FOLLOW_UP_REQUIRED,
            reviewed_by_practitioner_reference_id,
            session,
        )

    def mark_follow_up_required(
        self,
        lab_order_id: uuid.UUID,
        *,
        reviewed_by_practitioner_reference_id: uuid.UUID,
        session: Session | None = None,
    ) -> DoctorReviewRecord:
        return self._mark(
            lab_order_id,
            DoctorReviewStatus.FOLLOW_UP_REQUIRED,
            reviewed_by_practitioner_reference_id,
            session,
        )

    def _mark(
        self,
        lab_order_id: uuid.UUID,
        status: DoctorReviewStatus,
        reviewed_by_practitioner_reference_id: uuid.UUID,
        session: Session | None,
    ) -> DoctorReviewRecord:
        if session is not None:
            return self._mark_in_session(
                session, lab_order_id, status, reviewed_by_practitioner_reference_id
            )
        with self._sessions.begin() as owned_session:
            return self._mark_in_session(
                owned_session, lab_order_id, status, reviewed_by_practitioner_reference_id
            )

    def _mark_in_session(
        self,
        session: Session,
        lab_order_id: uuid.UUID,
        status: DoctorReviewStatus,
        reviewed_by_practitioner_reference_id: uuid.UUID,
    ) -> DoctorReviewRecord:
        # LabOrder.status == COMPLETED is a terminal fact once true (no outgoing transition
        # exists from COMPLETED in `orders.py`'s `_ALLOWED_TRANSITIONS`) -- a plain read, no
        # FOR UPDATE needed here, matching `lab_workflow.py`'s own reasoning for why its
        # post-lock reads need no lock of their own.
        order = session.get(LabOrder, lab_order_id)
        if order is None:
            raise ReviewNotFound(str(lab_order_id))
        if LabOrderStatus(order.status) != LabOrderStatus.COMPLETED:
            raise ReviewNotAllowedYet(
                f"LabOrder {lab_order_id} is {order.status!r}, not 'COMPLETED' -- a doctor "
                f"review may only be recorded once every requested result is in"
            )

        review = session.scalar(
            select(DoctorReview).where(DoctorReview.lab_order_id == lab_order_id).with_for_update()
        )
        if review is None:
            raise ReviewNotFound(str(lab_order_id))
        if DoctorReviewStatus(review.status) != DoctorReviewStatus.PENDING:
            raise ReviewAlreadyFinalized(
                f"DoctorReview for LabOrder {lab_order_id} is already {review.status!r} -- V1 "
                f"has no review-revision model, refusing to silently overwrite the earlier "
                f"disposition"
            )

        review.status = status.value
        review.reviewed_at = utc_now()
        review.reviewed_by_practitioner_reference_id = reviewed_by_practitioner_reference_id
        session.flush()
        return _to_review_record(review)

    def get_for_order(self, lab_order_id: uuid.UUID) -> DoctorReviewRecord:
        with self._sessions() as session:
            review = session.scalar(
                select(DoctorReview).where(DoctorReview.lab_order_id == lab_order_id)
            )
            if review is None:
                raise ReviewNotFound(str(lab_order_id))
            return _to_review_record(review)


def _to_result_record(result: LabResult) -> LabResultRecord:
    return LabResultRecord(
        id=result.id,
        lab_order_item_id=result.lab_order_item_id,
        lab_organization_id=result.lab_organization_id,
        result_type=result.result_type,
        numeric_value=result.numeric_value,
        numeric_unit=result.numeric_unit,
        reference_range_low=result.reference_range_low,
        reference_range_high=result.reference_range_high,
        text_value=result.text_value,
        completed_at=result.completed_at,
    )


def _to_delivery_record(delivery: ResultDelivery) -> ResultDeliveryRecord:
    return ResultDeliveryRecord(
        id=delivery.id,
        lab_order_id=delivery.lab_order_id,
        provider_organization_id=delivery.provider_organization_id,
        status=delivery.status,
        delivered_at=delivery.delivered_at,
    )


def _to_review_record(review: DoctorReview) -> DoctorReviewRecord:
    return DoctorReviewRecord(
        id=review.id,
        lab_order_id=review.lab_order_id,
        status=review.status,
        reviewed_at=review.reviewed_at,
        reviewed_by_practitioner_reference_id=review.reviewed_by_practitioner_reference_id,
    )
