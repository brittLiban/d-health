"""DH-EX-001: lab results, result delivery, and doctor review.

Three deliberately independent state machines (DH-EX-001's own instruction): a ``LabResult``
existing does not move ``ResultDelivery`` out of ``PENDING``, and ``ResultDelivery`` reaching
``DELIVERED`` does not move ``DoctorReview`` out of ``PENDING``. Each requires its own explicit
repository call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import (
    DoctorReview,
    DoctorReviewStatus,
    LabOrderItem,
    LabResult,
    LabResultType,
    LabSelection,
    ResultDelivery,
    ResultDeliveryStatus,
)
from daher_adapter.models import utc_now


class OrderItemNotFound(RuntimeError):
    """No LabOrderItem matches the given id."""


class NoLabSelected(RuntimeError):
    """A result was submitted for an order that has no LabSelection yet."""


class WrongLabForResult(RuntimeError):
    """A result was submitted by a lab other than the one selected for this order."""


class DuplicateResult(RuntimeError):
    """A LabOrderItem already has a result -- V1 models no amendment/correction workflow."""


class DeliveryNotFound(RuntimeError):
    pass


class ReviewNotFound(RuntimeError):
    pass


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
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        value: Decimal,
        unit: str | None = None,
        reference_range_low: Decimal | None = None,
        reference_range_high: Decimal | None = None,
    ) -> LabResultRecord:
        return self._record(
            lab_order_item_id=lab_order_item_id,
            lab_organization_id=lab_organization_id,
            result_type=LabResultType.NUMERIC,
            numeric_value=value,
            numeric_unit=unit,
            reference_range_low=reference_range_low,
            reference_range_high=reference_range_high,
            text_value=None,
        )

    def record_text(
        self, *, lab_order_item_id: uuid.UUID, lab_organization_id: uuid.UUID, value: str
    ) -> LabResultRecord:
        if not value.strip():
            raise ValueError("value is required for a text result")
        return self._record(
            lab_order_item_id=lab_order_item_id,
            lab_organization_id=lab_organization_id,
            result_type=LabResultType.TEXT,
            numeric_value=None,
            numeric_unit=None,
            reference_range_low=None,
            reference_range_high=None,
            text_value=value,
        )

    def _record(
        self,
        *,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        result_type: LabResultType,
        numeric_value: Decimal | None,
        numeric_unit: str | None,
        reference_range_low: Decimal | None,
        reference_range_high: Decimal | None,
        text_value: str | None,
    ) -> LabResultRecord:
        with self._sessions() as session:
            item = session.get(LabOrderItem, lab_order_item_id)
            if item is None:
                raise OrderItemNotFound(str(lab_order_item_id))
            if item.result is not None:
                raise DuplicateResult(
                    f"LabOrderItem {lab_order_item_id} already has a result -- no amendment "
                    f"workflow exists in V1"
                )
            selection = session.scalar(
                select(LabSelection).where(LabSelection.lab_order_id == item.lab_order_id)
            )
            if selection is None:
                raise NoLabSelected(
                    f"LabOrder {item.lab_order_id} has no LabSelection -- a result cannot be "
                    f"recorded before a lab has been selected"
                )
            if selection.lab_organization_id != lab_organization_id:
                raise WrongLabForResult(
                    f"lab {lab_organization_id} is not the lab selected "
                    f"({selection.lab_organization_id}) for LabOrder {item.lab_order_id} -- "
                    f"refusing to attach a result from the wrong lab"
                )
            result = LabResult(
                lab_order_item_id=lab_order_item_id,
                lab_organization_id=lab_organization_id,
                result_type=result_type.value,
                numeric_value=numeric_value,
                numeric_unit=numeric_unit,
                reference_range_low=reference_range_low,
                reference_range_high=reference_range_high,
                text_value=text_value,
            )
            session.add(result)
            session.commit()
            return _to_result_record(result)

    def get_for_item(self, lab_order_item_id: uuid.UUID) -> LabResultRecord | None:
        with self._sessions() as session:
            result = session.scalar(
                select(LabResult).where(LabResult.lab_order_item_id == lab_order_item_id)
            )
            return None if result is None else _to_result_record(result)


class ResultDeliveryRepository:
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
        self, lab_order_id: uuid.UUID, *, reviewed_by_practitioner_reference_id: uuid.UUID
    ) -> DoctorReviewRecord:
        return self._mark(
            lab_order_id,
            DoctorReviewStatus.NO_FOLLOW_UP_REQUIRED,
            reviewed_by_practitioner_reference_id,
        )

    def mark_follow_up_required(
        self, lab_order_id: uuid.UUID, *, reviewed_by_practitioner_reference_id: uuid.UUID
    ) -> DoctorReviewRecord:
        return self._mark(
            lab_order_id,
            DoctorReviewStatus.FOLLOW_UP_REQUIRED,
            reviewed_by_practitioner_reference_id,
        )

    def _mark(
        self,
        lab_order_id: uuid.UUID,
        status: DoctorReviewStatus,
        reviewed_by_practitioner_reference_id: uuid.UUID,
    ) -> DoctorReviewRecord:
        with self._sessions.begin() as session:
            review = session.scalar(
                select(DoctorReview)
                .where(DoctorReview.lab_order_id == lab_order_id)
                .with_for_update()
            )
            if review is None:
                raise ReviewNotFound(str(lab_order_id))
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
