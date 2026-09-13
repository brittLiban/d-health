"""DH-EX-001 models, DH-EX-007 workflow, DH-EX-012/ADR-044 release: lab results, explicit
order-level result release, order completion, result delivery, and doctor review.

Three deliberately independent state machines (DH-EX-001's own instruction): a ``LabResult``
existing does not move ``ResultDelivery`` out of ``PENDING``, and ``ResultDelivery`` reaching
``DELIVERED`` does not move ``DoctorReview`` out of ``PENDING``. Each requires its own explicit
repository call. ``ResultDelivery`` stays untouched by this module's DH-EX-007/DH-EX-012 additions:
no code path here ever calls ``mark_delivered``/``mark_failed`` as a side effect of anything -- the
exact semantic of ``DELIVERED`` remains an explicit future product decision (recorded in
PROJECT_STATE.md's DH-EX-007 entry), not inferred here, and is not conflated with "released by the
lab" (ADR-044's own explicit instruction: released-by-lab and delivered-to-provider are distinct
concepts).

**DH-EX-012/ADR-044 removes DH-EX-007's original automatic coupling.** DH-EX-007 originally
transitioned ``LabOrder`` to ``COMPLETED`` automatically the instant the last missing ``LabResult``
was recorded, in the same transaction as the insert. ADR-044 replaces that with an explicit,
authorized release step: recording a result -- even the last one -- now only ever leaves the order
``IN_PROGRESS``, reporting ``ready_for_release`` (all ordered items now have a result) without
mutating status. Only ``LabResultReleaseRepository.release`` -- gated on a distinct
``LAB_RESULT_RELEASER`` role, never ``LAB_TECH`` alone -- may move the order to ``COMPLETED``, by
inserting one immutable, order-level ``LabResultRelease`` row in the same transaction as the status
write. This is the one place "result existing" and "order state" are linked, and it is a
``LabOrder`` transition, not a ``ResultDelivery`` one.

**Lock order matches DH-EX-005/DH-EX-006's own established discipline:** ``LabOrder`` is locked
``FOR UPDATE`` first, before the ``LabSelection`` assignment check, before the lifecycle check,
before the item lookup -- consistency of lock order across the whole package is what actually
serializes concurrent result submissions, and (DH-EX-012) concurrent release attempts, and a result
submission racing a release, down to one coherent outcome, exactly like ``lab_workflow.py``'s own
docstring explains for accept/reject/start-processing. Completeness is re-derived from the database
with an explicit aggregate query -- shared by both result submission and release via
``_count_missing_results`` -- never read off an already-loaded ``order.items[*].result``
relationship, and never trusted from an earlier response: release re-runs this query itself rather
than trusting a prior ``ready_for_release=true``.

**Assignment/lifecycle failures are non-enumerating, matching every other tenant-scoped lookup in
this package:** a nonexistent order, an order assigned to a different lab, and an order with no
``LabSelection`` at all (i.e. still before ``SELECTED``) all raise the identical
``LabOrderNotAssigned`` (imported from ``lab_workflow.py`` rather than redefined here, so API
routes need only one ``except`` clause across every DH-EX-006/007/012 lab-side mutation) -- never
disclosing which of those three is actually true to an unassigned caller.

**Legacy grandfather rule (ADR-044).** A pre-DH-EX-012 order already ``COMPLETED`` (because
DH-EX-007's original automatic-completion behavior reached it) has no ``LabResultRelease`` row.
``LabResultReleaseRepository.release`` never fabricates one retroactively -- it raises
``LegacyCompletedOrderHasNoRelease`` instead, distinguishable from ``ResultsNotReadyForRelease`` (a
genuinely incomplete or pre-``IN_PROGRESS`` order). Provider visibility and clinician review for
such an order are unaffected by this ticket -- both already read only ``LabOrder.status``, which
such an order already satisfies."""

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
    LabResultRelease,
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
    "LabResultInspectionItemRecord",
    "LabResultInspectionRecord",
    "LabResultRecord",
    "LabResultReleaseRecord",
    "LabResultReleaseRepository",
    "LabResultRepository",
    "LegacyCompletedOrderHasNoRelease",
    "OrderItemNotFound",
    "ResultDeliveryRecord",
    "ResultDeliveryRepository",
    "ResultsNotReadyForRelease",
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


class ResultsNotReadyForRelease(RuntimeError):
    """DH-EX-012/ADR-044: release was attempted while the order is not ``IN_PROGRESS``, or is
    ``IN_PROGRESS`` but at least one ordered item still lacks a recorded result. Distinct from
    ``LegacyCompletedOrderHasNoRelease`` -- that one is specifically an already-``COMPLETED`` order
    with no release row (a pre-DH-EX-012 grandfathered order); this one is any order that has not
    yet reached the point release is meaningful."""


class LegacyCompletedOrderHasNoRelease(RuntimeError):
    """DH-EX-012/ADR-044: the order is already ``COMPLETED`` but has no matching
    ``LabResultRelease`` row -- it reached ``COMPLETED`` before this ticket's explicit release step
    existed (DH-EX-007's original automatic-completion behavior). ADR-044's grandfather rule: this
    is honestly "no release recorded for this COMPLETED order", never fabricated after the fact.
    Provider visibility and clinician review remain unaffected -- both gate on ``LabOrder.status``
    alone, which this order already satisfies."""


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
class LabResultInspectionItemRecord:
    """DH-EX-012: one ordered item's entry state for the lab's own pre-release inspection read --
    every field beyond ``item_id`` is ``None`` when this item has no recorded result yet, so a
    releaser can see exactly what remains missing, not only what has already been entered."""

    item_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str
    result_type: str | None
    numeric_value: Decimal | None
    numeric_unit: str | None
    reference_range_low: Decimal | None
    reference_range_high: Decimal | None
    text_value: str | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class LabResultInspectionRecord:
    """DH-EX-012: the selected lab's own operational view of an order's entered results, before
    release -- deliberately separate from ``ProviderOrderResultsResponse`` (provider delivery,
    gated on ``COMPLETED``) and from ``LabWorkflowOrderRecord`` (DH-EX-006's order-detail view,
    which carries no result values at all). No clinical interpretation is added: no normal/
    abnormal flag, no reference-range evaluation -- ``all_results_entered``/``released`` are
    workflow facts, not clinical ones."""

    order_id: uuid.UUID
    order_status: str
    all_results_entered: bool
    released: bool
    released_at: datetime | None
    items: tuple[LabResultInspectionItemRecord, ...]


@dataclass(frozen=True, slots=True)
class ResultSubmissionOutcome:
    result: LabResultRecord
    ready_for_release: bool
    """DH-EX-012/ADR-044: ``True`` exactly when every ordered item on this order now has a
    recorded result -- a DB-grounded fact re-derived by explicit aggregate query, never inferred
    from "this was the Nth submission" or any loaded relationship. Renamed from DH-EX-007's
    ``order_completed`` (removed): recording a result -- even the last one -- no longer completes
    the order by itself; only an explicit, authorized release does that (see
    ``LabResultReleaseRepository.release``). This field may read ``True`` on more than one
    submission call for the same order (e.g. a duplicate-attempt 409 never reaches this dataclass,
    but a legitimately later resubmission is impossible in V1's one-immutable-result-per-item
    model, so in practice this is still true only once, at the submission that fills the last
    item) -- it no longer drives any status mutation, only informs the caller."""
    order_status: str
    """DH-EX-012: always ``'IN_PROGRESS'`` after a successful submission -- this method never
    writes ``COMPLETED`` itself any more. Kept as a field (not removed) for the same reason
    ``completed_at`` keeps its name: it is an existing response-shape field
    (``LabResultSubmitResponse.order_status``), and its value is still truthful, just narrower."""


@dataclass(frozen=True, slots=True)
class LabResultReleaseRecord:
    id: uuid.UUID
    lab_order_id: uuid.UUID
    lab_organization_id: uuid.UUID
    released_by_identity_id: uuid.UUID
    released_at: datetime


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
        #
        # DH-EX-012/ADR-044: this no longer mutates `order.status` -- recording even the last
        # missing result leaves the order IN_PROGRESS. `ready_for_release` reports the same
        # DB-grounded fact DH-EX-007's `order_completed` used to report, but only an explicit,
        # authorized `LabResultReleaseRepository.release` call may act on it.
        missing_count = _count_missing_results(session, order_id)
        session.flush()

        return ResultSubmissionOutcome(
            result=_to_result_record(result),
            ready_for_release=missing_count == 0,
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

    def get_for_lab(
        self, order_id: uuid.UUID, lab_organization_id: uuid.UUID
    ) -> LabResultInspectionRecord | None:
        """DH-EX-012: the selected lab's own pre-release read -- "a releaser must not be forced to
        release results they cannot inspect." Read-only, tenant-safe: returns ``None`` both when
        the order does not exist and when it exists but is not assigned to this lab (identical to
        ``lab_workflow.get_for_lab``'s own convention), never disclosing which is true. A single
        outer-joined query returns every ordered item together with any recorded result -- no
        second per-item query, matching DH-EX-011's own N+1-avoidance discipline."""
        with self._sessions() as session:
            order = session.get(LabOrder, order_id)
            if order is None:
                return None
            selection = session.scalar(
                select(LabSelection).where(LabSelection.lab_order_id == order_id)
            )
            if selection is None or selection.lab_organization_id != lab_organization_id:
                return None
            rows = session.execute(
                select(LabOrderItem, LabResult)
                .outerjoin(LabResult, LabResult.lab_order_item_id == LabOrderItem.id)
                .where(LabOrderItem.lab_order_id == order_id)
            ).all()
            release = session.scalar(
                select(LabResultRelease).where(LabResultRelease.lab_order_id == order_id)
            )
            return LabResultInspectionRecord(
                order_id=order.id,
                order_status=order.status,
                all_results_entered=all(result is not None for _item, result in rows),
                released=release is not None,
                released_at=release.released_at if release is not None else None,
                items=tuple(_to_inspection_item(item, result) for item, result in rows),
            )


class LabResultReleaseRepository:
    """DH-EX-012/ADR-044: the sole supported production way to move a ``LabOrder`` from
    ``IN_PROGRESS`` to ``COMPLETED`` -- see ``exchange/orders.py``'s ``transition()`` for the
    identical, deliberately duplicated guard that keeps the generic state-machine primitive from
    becoming an unguarded bypass of this same invariant."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def release(
        self,
        *,
        order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        released_by_identity_id: uuid.UUID,
        session: Session | None = None,
    ) -> tuple[LabResultReleaseRecord, bool]:
        """Returns ``(record, created)``. ``created=False`` means an existing release was returned
        as a safe idempotent replay -- unconditionally, regardless of who is retrying: a lost-
        response retry by the same releaser and a later retry by a *different* authorized releaser
        both return the identical provenance (same id, same ``released_at``, same
        ``released_by_identity_id``) -- the fact is who actually committed the release first,
        never rewritten by whoever asks most recently."""
        if session is not None:
            return self._release_in_session(
                session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                released_by_identity_id=released_by_identity_id,
            )
        with self._sessions.begin() as owned_session:
            return self._release_in_session(
                owned_session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                released_by_identity_id=released_by_identity_id,
            )

    def _release_in_session(
        self,
        session: Session,
        *,
        order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        released_by_identity_id: uuid.UUID,
    ) -> tuple[LabResultReleaseRecord, bool]:
        order = session.scalar(select(LabOrder).where(LabOrder.id == order_id).with_for_update())
        if order is None:
            raise LabOrderNotAssigned(str(order_id))

        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        if selection is None or selection.lab_organization_id != lab_organization_id:
            raise LabOrderNotAssigned(str(order_id))

        existing = session.scalar(
            select(LabResultRelease).where(LabResultRelease.lab_order_id == order_id)
        )
        if existing is not None:
            return _to_release_record(existing), False

        current = LabOrderStatus(order.status)
        if current == LabOrderStatus.COMPLETED:
            raise LegacyCompletedOrderHasNoRelease(
                f"LabOrder {order_id} is already 'COMPLETED' but no LabResultRelease record "
                f"exists for it -- DHealth will not fabricate a release record for an order it "
                f"never observed an explicit release for"
            )
        if current != LabOrderStatus.IN_PROGRESS:
            raise ResultsNotReadyForRelease(
                f"cannot release results for LabOrder {order_id} -- order is {order.status!r}, "
                f"not 'IN_PROGRESS'"
            )

        # Re-checked here, never trusted from an earlier `ready_for_release=true` response --
        # the same aggregate query result submission itself uses, so "ready" means the identical
        # DB-grounded fact in both places.
        missing_count = _count_missing_results(session, order_id)
        if missing_count:
            raise ResultsNotReadyForRelease(
                f"cannot release results for LabOrder {order_id} -- {missing_count} ordered "
                f"item(s) still lack a recorded result"
            )

        release = LabResultRelease(
            lab_order_id=order_id,
            lab_organization_id=lab_organization_id,
            released_by_identity_id=released_by_identity_id,
            released_at=utc_now(),
        )
        session.add(release)
        session.flush()
        order.status = LabOrderStatus.COMPLETED.value
        session.flush()
        return _to_release_record(release), True

    def get_for_order(self, order_id: uuid.UUID) -> LabResultReleaseRecord | None:
        with self._sessions() as session:
            release = session.scalar(
                select(LabResultRelease).where(LabResultRelease.lab_order_id == order_id)
            )
            return None if release is None else _to_release_record(release)


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


def _count_missing_results(session: Session, order_id: uuid.UUID) -> int:
    """The single DB-grounded completeness query shared by result submission
    (``ready_for_release``) and release (``LabResultReleaseRepository``'s own re-check) -- both
    call this rather than each maintaining their own copy, since it is the exact same question
    asked in the same module. Callers are responsible for holding the ``LabOrder`` row's
    ``FOR UPDATE`` lock first; this function does not acquire one itself."""
    return session.scalar(
        select(func.count())
        .select_from(LabOrderItem)
        .outerjoin(LabResult, LabResult.lab_order_item_id == LabOrderItem.id)
        .where(LabOrderItem.lab_order_id == order_id, LabResult.id.is_(None))
    )


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


def _to_inspection_item(
    item: LabOrderItem, result: LabResult | None
) -> LabResultInspectionItemRecord:
    return LabResultInspectionItemRecord(
        item_id=item.id,
        catalog_code_snapshot=item.catalog_code_snapshot,
        catalog_display_name_snapshot=item.catalog_display_name_snapshot,
        result_type=None if result is None else result.result_type,
        numeric_value=None if result is None else result.numeric_value,
        numeric_unit=None if result is None else result.numeric_unit,
        reference_range_low=None if result is None else result.reference_range_low,
        reference_range_high=None if result is None else result.reference_range_high,
        text_value=None if result is None else result.text_value,
        completed_at=None if result is None else result.completed_at,
    )


def _to_release_record(release: LabResultRelease) -> LabResultReleaseRecord:
    return LabResultReleaseRecord(
        id=release.id,
        lab_order_id=release.lab_order_id,
        lab_organization_id=release.lab_organization_id,
        released_by_identity_id=release.released_by_identity_id,
        released_at=release.released_at,
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
