"""DH-EX-001 models, DH-EX-007 workflow, DH-EX-012/ADR-044 release, DH-EX-012C/ADR-047
multi-observation result domain, DH-EX-013/ADR-045 pre-release observation correction: lab results,
explicit order-level result release, order completion, result delivery, and doctor review.

**DH-EX-013/ADR-045 (pre-release correction only).** A `LAB_TECH` at the selected lab may correct
an *effective* (not yet superseded) observation while the order is still `IN_PROGRESS` -- before or
after the item's own entry is marked complete, but never after the order is released
(`COMPLETED`). `LabResultRepository.correct_observation` never mutates an existing observation's
value: it inserts one new immutable `LabObservation` row (`supersedes_observation_id` pointing at
the corrected row) and sets `superseded_at` on the row it replaces, in the same transaction. Every
ordinary read (`get_for_item`, `list_for_order`, `get_for_lab`, and the completeness query) now
returns only *effective* observations (`superseded_at IS NULL`) -- the full chain, including every
superseded historical version, is available only through `get_correction_history`, a dedicated
read never used by the provider- or release-facing paths. See `LabObservation`'s own `models.py`
docstring for the full chain-integrity/database-enforcement reasoning, and `correct_observation`'s
own docstring below for why this does not "reopen" a completed entry.

Three deliberately independent state machines (DH-EX-001's own instruction): a ``LabResult``
existing does not move ``ResultDelivery`` out of ``PENDING``, and ``ResultDelivery`` reaching
``DELIVERED`` does not move ``DoctorReview`` out of ``PENDING``. Each requires its own explicit
repository call. ``ResultDelivery`` stays untouched by this module's DH-EX-007/DH-EX-012/DH-EX-012C
additions: no code path here ever calls ``mark_delivered``/``mark_failed`` as a side effect of
anything -- the exact semantic of ``DELIVERED`` remains an explicit future product decision
(recorded in PROJECT_STATE.md's DH-EX-007 entry), not inferred here, and is not conflated with
"released by the lab" (ADR-044's own explicit instruction: released-by-lab and delivered-to-provider
are distinct concepts).

**DH-EX-012/ADR-044 removes DH-EX-007's original automatic coupling.** Recording a result -- even
the last one -- only ever reports readiness (``ready_for_release``) without mutating status. Only
``LabResultReleaseRepository.release`` -- gated on a distinct ``LAB_RESULT_RELEASER`` role, never
``LAB_TECH`` alone -- may move the order to ``COMPLETED``.

**DH-EX-012C/ADR-047 replaces DH-EX-007's "one `LabResult` row per item" completeness fact with
"one `LabResult` *container* per item, explicitly marked `entry_completed_at`, with at least one
child `LabObservation`."** ``LabResult`` no longer holds a value directly -- see its own
``models.py`` docstring. Three distinct actions now exist where DH-EX-007 had one:

1. ``record_observation`` -- append one ``LabObservation`` under an item's container (creating the
   container on the first call). May be called any number of times while the container's entry is
   not yet completed. Never itself completes anything.
2. ``complete_entry`` -- the **only** place ``LabResult.entry_completed_at`` is ever set. Requires
   at least one existing observation; idempotent on replay (returns the original completion fact
   unchanged, never overwriting its actor/time). Freezes the container: once completed, no further
   observation may be added under it (checked by (1) above).
3. ``record_numeric``/``record_text`` -- the **backward-compatible convenience path** for a simple,
   single-observation service: atomically creates the container, records exactly one observation,
   and completes the entry, in one call, one transaction. Refuses outright if the item already has
   a container (i.e. already has observation/completion content of any kind) -- this path is a
   one-shot convenience, never a way to add a second observation or to re-complete.

**Completeness is never inferred by counting.** `_count_missing_results` (the single choke point
shared by result submission's `ready_for_release` and release's own re-check, exactly as before this
ticket) now asks "does this item have a container whose `entry_completed_at` is set, backed by at
least one real observation row" -- the last clause is deliberate defense-in-depth, re-derived from
the database every time, never trusted from the container's own flag alone (see
`_count_missing_or_malformed_results`'s docstring for why).

**Lock order matches DH-EX-005/DH-EX-006's own established discipline:** ``LabOrder`` is locked
``FOR UPDATE`` first, before the ``LabSelection`` assignment check, before the lifecycle check,
before the item lookup -- unchanged by this ticket, and it is this same lock, not any new
row-level lock on `LabResult`/`LabObservation`, that serializes concurrent observation inserts,
concurrent completions, an observation racing a completion, and a completion racing a release, down
to one coherent outcome for a given order. `record_observation`, `complete_entry`, `record_numeric`/
`record_text`, and `LabResultReleaseRepository.release` all acquire the identical `LabOrder FOR
UPDATE` lock first, in that order, before touching anything else -- two concurrent callers for the
same order fully serialize at that point; whichever commits first is authoritative, and the second
sees its result via a fresh read after the first lock is released, never a stale one.

**Assignment/lifecycle failures are non-enumerating, matching every other tenant-scoped lookup in
this package:** a nonexistent order, an order assigned to a different lab, and an order with no
``LabSelection`` at all (i.e. still before ``SELECTED``) all raise the identical
``LabOrderNotAssigned`` (imported from ``lab_workflow.py`` rather than redefined here, so API
routes need only one ``except`` clause across every DH-EX-006/007/012/012C lab-side mutation) --
never disclosing which of those three is actually true to an unassigned caller.

**Legacy grandfather rule (ADR-044), unaffected by DH-EX-012C.** A pre-DH-EX-012 order already
``COMPLETED`` (because DH-EX-007's original automatic-completion behavior reached it) has no
``LabResultRelease`` row. ``LabResultReleaseRepository.release`` never fabricates one retroactively
-- it raises ``LegacyCompletedOrderHasNoRelease`` instead, distinguishable from
``ResultsNotReadyForRelease``. Provider visibility and clinician review for such an order are
unaffected by this ticket -- both already read only ``LabOrder.status``, which such an order already
satisfies; migration ``0016_exchange_observations`` gives such an order's migrated container an
honest ``entry_completed_at`` (backfilled from the old ``completed_at``) with a ``NULL`` actor,
never a fabricated one (see that migration's own docstring)."""

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
    LabObservation,
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
    "EntryAlreadyCompleted",
    "EntryNotReadyForCompletion",
    "InvalidResultSubmissionState",
    "LabOrderNotAssigned",
    "LabObservationHistoryRecord",
    "LabObservationRecord",
    "LabResultEntryCompletionRecord",
    "LabResultInspectionItemRecord",
    "LabResultInspectionRecord",
    "LabResultRecord",
    "LabResultReleaseRecord",
    "LabResultReleaseRepository",
    "LabResultRepository",
    "LegacyCompletedOrderHasNoRelease",
    "MalformedZeroObservationEntry",
    "ObservationNotFound",
    "OrderItemNotFound",
    "ResultDeliveryRecord",
    "ResultDeliveryRepository",
    "ResultsNotReadyForRelease",
    "ResultSubmissionOutcome",
    "ReviewAlreadyFinalized",
    "ReviewNotAllowedYet",
    "ReviewNotFound",
    "StaleObservationVersion",
]

# DH-EX-012C: the self-asserted source-system value for every observation DHealth itself records
# natively (no external LIS/Connect source exists yet) -- deliberately the identical literal
# `exchange/api.py`'s `_CLINICIAN_PRACTITIONER_SOURCE_SYSTEM` already uses, for the identical fact
# ("DHealth itself is asserting this identity, not an external source system"), not a new sentinel
# invented for this ticket alone.
NATIVE_OBSERVATION_SOURCE_SYSTEM = "dhealth-exchange"


class OrderItemNotFound(RuntimeError):
    """No ``LabOrderItem`` matches the given id *for this exact order* -- identical response
    whether the item id is entirely bogus or belongs to a genuinely different order, so a caller
    can never use this endpoint to probe which order a real item id belongs to."""


class InvalidResultSubmissionState(RuntimeError):
    """The order's current status does not permit result submission -- only ``IN_PROGRESS``
    does. Mirrors ``lab_workflow.InvalidLabWorkflowTransition``'s role for accept/reject/
    start-processing: a real, distinguishable conflict, never a silent no-op."""


class DuplicateResult(RuntimeError):
    """DH-EX-012C: the backward-compatible single-observation convenience path
    (``record_numeric``/``record_text``) was called for a ``LabOrderItem`` that already has a
    ``LabResult`` container -- i.e. already has observation and/or completion content of any kind.
    Since a container's existence is, by construction, proof that at least one observation already
    exists under it (see ``LabResult``'s own docstring), this is never a false positive: the
    convenience path is a one-shot action, never a way to add a second observation to an
    already-started item or to re-run completion. Use the granular ``record_observation``/
    ``complete_entry`` pair for anything beyond the simple one-observation case."""


class EntryAlreadyCompleted(RuntimeError):
    """DH-EX-012C: an observation was attempted for an item whose result entry is already marked
    complete. Freeze-after-completion is deliberate and strict until ADR-045 implements a
    correction/revision mechanism -- there is no implicit "reopen" path anywhere in this
    codebase."""


class EntryNotReadyForCompletion(RuntimeError):
    """DH-EX-012C: ``complete_entry`` was attempted for an item with zero recorded observations (no
    container exists yet, or -- structurally unreachable via any code path, but checked anyway --
    a container exists with no observations). Completion always requires at least one observation;
    DHealth never completes an item's entry on the caller's say-so alone."""


class MalformedZeroObservationEntry(RuntimeError):
    """DH-EX-012C: release's own defense-in-depth re-check found a `LabResult` container whose
    `entry_completed_at` is set but which has zero associated `LabObservation` rows -- a state no
    code path in this package can produce (both completion paths require >=1 observation first,
    and observations are never deleted), but is checked for explicitly at the one point
    (`LabResultReleaseRepository.release`) where such a malformed row would otherwise silently let a
    provider see a "complete" item carrying no value at all. Guards against malformed legacy/manual
    database state, a migration bug, or a future bypass -- never expected to fire in normal
    operation, but release refuses to trust the absence of such a row rather than assuming it."""


class ObservationNotFound(RuntimeError):
    """DH-EX-013: no ``LabObservation`` matches the given id *under this exact item's container* --
    identical response whether the observation id is entirely bogus, belongs to a different item's
    container, or belongs to a different lab's order entirely, so a caller can never use this
    endpoint to probe which item or container a real observation id actually belongs to."""


class StaleObservationVersion(RuntimeError):
    """DH-EX-013/ADR-045: a correction was attempted against an observation that is no longer
    *effective* (``superseded_at`` is already set) -- either because it was already corrected by an
    earlier request, or (structurally unreachable via any code path holding the ``LabOrder`` lock
    correctly, but checked anyway as the database-constraint backstop) a genuine concurrent race.
    A correction must always start from the currently effective observation; this is the controlled
    conflict a caller sees instead of silently creating a second, ambiguous "current" value."""


class ReviewNotFound(RuntimeError):
    pass


class ReviewNotAllowedYet(RuntimeError):
    """A DoctorReview mutation was attempted before its LabOrder reached ``COMPLETED``."""


class ReviewAlreadyFinalized(RuntimeError):
    """A DoctorReview mutation was attempted on a review that has already left ``PENDING`` -- V1
    has no review-revision/amendment model, so a second terminal mutation is a controlled
    conflict, never a silent overwrite of the first doctor's disposition."""


class ResultsNotReadyForRelease(RuntimeError):
    """DH-EX-012/ADR-044, extended by DH-EX-012C: release was attempted while the order is not
    ``IN_PROGRESS``, or is ``IN_PROGRESS`` but at least one ordered item still lacks a
    completed, observation-backed result entry. Distinct from
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


class DeliveryNotFound(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LabObservationRecord:
    """DH-EX-013: ``recorded_by_identity_id`` is ``None`` only for a pre-DH-EX-013 observation
    (never fabricated retroactively). ``supersedes_observation_id`` is ``None`` for an original
    observation; ``superseded_at`` is ``None`` exactly when this is the chain's current *effective*
    value -- the single explicit rule every ordinary read filters on, never inferred from
    ``recorded_at`` ordering."""

    id: uuid.UUID
    lab_result_id: uuid.UUID
    source_code: str
    source_system: str
    display_label_snapshot: str
    result_type: str
    numeric_value: Decimal | None
    numeric_unit: str | None
    reference_range_low: Decimal | None
    reference_range_high: Decimal | None
    text_value: str | None
    recorded_at: datetime
    recorded_by_identity_id: uuid.UUID | None
    supersedes_observation_id: uuid.UUID | None
    superseded_at: datetime | None


@dataclass(frozen=True, slots=True)
class LabResultRecord:
    """DH-EX-012C: one ordered item's result-entry container plus every observation recorded under
    it so far -- ``entry_completed_at``/``entry_completed_by_identity_id`` are ``None`` while entry
    is still in progress, exactly mirroring how a pre-DH-EX-012 order's ``LabResultRelease`` is
    absent rather than fabricated."""

    id: uuid.UUID
    lab_order_item_id: uuid.UUID
    lab_organization_id: uuid.UUID
    entry_completed_at: datetime | None
    entry_completed_by_identity_id: uuid.UUID | None
    observations: tuple[LabObservationRecord, ...]


@dataclass(frozen=True, slots=True)
class LabObservationHistoryRecord:
    """DH-EX-013: every observation that has ever existed under one ordered item's container,
    superseded and effective alike, ordered oldest to newest -- the one place a lab-authorized
    caller may see historical (non-effective) observations at all. Never used by any
    provider-facing or release-facing read."""

    item_id: uuid.UUID
    observations: tuple[LabObservationRecord, ...]


@dataclass(frozen=True, slots=True)
class LabResultEntryCompletionRecord:
    id: uuid.UUID
    lab_order_item_id: uuid.UUID
    lab_organization_id: uuid.UUID
    entry_completed_at: datetime
    entry_completed_by_identity_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class LabResultInspectionItemRecord:
    """DH-EX-012, extended by DH-EX-012C/DH-EX-013: one ordered item's entry state for the lab's
    own pre-release inspection read -- ``entry_completed_at`` is ``None`` and ``observations`` may
    be empty while entry is not yet complete, so a releaser can see exactly what remains missing,
    not only what has already been entered. ``observations`` here is always the *effective* set
    only; ``has_correction_history`` is a cheap, batched boolean signal (never the full chain) that
    at least one observation under this item has been corrected at least once -- a caller who needs
    the actual history calls ``get_correction_history`` separately."""

    item_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str
    entry_completed_at: datetime | None
    observations: tuple[LabObservationRecord, ...]
    has_correction_history: bool


@dataclass(frozen=True, slots=True)
class LabResultInspectionRecord:
    """DH-EX-012: the selected lab's own operational view of an order's entered results, before
    release -- deliberately separate from ``ProviderOrderResultsResponse`` (provider delivery,
    gated on ``COMPLETED``) and from ``LabWorkflowOrderRecord`` (DH-EX-006's order-detail view,
    which carries no result values at all). No clinical interpretation is added: no normal/
    abnormal flag, no reference-range evaluation -- ``all_items_entry_complete``/``released`` are
    workflow facts, not clinical ones."""

    order_id: uuid.UUID
    order_status: str
    all_items_entry_complete: bool
    released: bool
    released_at: datetime | None
    items: tuple[LabResultInspectionItemRecord, ...]


@dataclass(frozen=True, slots=True)
class ResultSubmissionOutcome:
    """DH-EX-012C: the outcome of the backward-compatible single-observation convenience path.
    ``observation``/``entry_completed_at`` report the one observation this call recorded and the
    completion it atomically performed -- both are new facts on this call, never a replay (the
    convenience path refuses outright, via ``DuplicateResult``, if the item already has a
    container)."""

    observation: LabObservationRecord
    entry_completed_at: datetime
    ready_for_release: bool
    """``True`` exactly when every ordered item on this order now has a completed,
    observation-backed result entry -- a DB-grounded fact re-derived by explicit aggregate query,
    never inferred from "this was the Nth submission" or any loaded relationship. It no longer
    drives any status mutation, only informs the caller (unchanged from DH-EX-012/ADR-044)."""
    order_status: str
    """Always ``'IN_PROGRESS'`` after a successful submission -- this method never writes
    ``COMPLETED`` itself (unchanged from DH-EX-012/ADR-044)."""


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

    # -- DH-EX-012C: granular multi-observation entry ------------------------------------------

    def record_observation(
        self,
        *,
        order_id: uuid.UUID,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        result_type: LabResultType,
        source_code: str,
        display_label_snapshot: str,
        recorded_by_identity_id: uuid.UUID,
        numeric_value: Decimal | None = None,
        numeric_unit: str | None = None,
        reference_range_low: Decimal | None = None,
        reference_range_high: Decimal | None = None,
        text_value: str | None = None,
        session: Session | None = None,
    ) -> LabObservationRecord:
        if session is not None:
            return self._record_observation_in_session(
                session,
                order_id=order_id,
                lab_order_item_id=lab_order_item_id,
                lab_organization_id=lab_organization_id,
                result_type=result_type,
                source_code=source_code,
                display_label_snapshot=display_label_snapshot,
                recorded_by_identity_id=recorded_by_identity_id,
                numeric_value=numeric_value,
                numeric_unit=numeric_unit,
                reference_range_low=reference_range_low,
                reference_range_high=reference_range_high,
                text_value=text_value,
            )
        with self._sessions.begin() as owned_session:
            return self._record_observation_in_session(
                owned_session,
                order_id=order_id,
                lab_order_item_id=lab_order_item_id,
                lab_organization_id=lab_organization_id,
                result_type=result_type,
                source_code=source_code,
                display_label_snapshot=display_label_snapshot,
                recorded_by_identity_id=recorded_by_identity_id,
                numeric_value=numeric_value,
                numeric_unit=numeric_unit,
                reference_range_low=reference_range_low,
                reference_range_high=reference_range_high,
                text_value=text_value,
            )

    def _record_observation_in_session(
        self,
        session: Session,
        *,
        order_id: uuid.UUID,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        result_type: LabResultType,
        source_code: str,
        display_label_snapshot: str,
        recorded_by_identity_id: uuid.UUID,
        numeric_value: Decimal | None,
        numeric_unit: str | None,
        reference_range_low: Decimal | None,
        reference_range_high: Decimal | None,
        text_value: str | None,
    ) -> LabObservationRecord:
        item, _order = _lock_order_and_item_for_entry(
            session,
            order_id=order_id,
            lab_order_item_id=lab_order_item_id,
            lab_organization_id=lab_organization_id,
        )

        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item.id))
        if container is not None and container.entry_completed_at is not None:
            raise EntryAlreadyCompleted(
                f"LabOrderItem {lab_order_item_id} already has a completed result entry -- no "
                f"further observation may be added until ADR-045 implements correction/revision"
            )
        if container is None:
            container = LabResult(
                lab_order_item_id=item.id, lab_organization_id=lab_organization_id
            )
            try:
                with session.begin_nested():
                    session.add(container)
                    session.flush()
            except IntegrityError:
                # Defensive last line only, mirroring `_submit_in_session`'s identical pattern --
                # the LabOrder FOR UPDATE lock above already serializes concurrent callers for this
                # order down to one winner for container creation, so this should never actually
                # fire in practice.
                raise EntryAlreadyCompleted(
                    f"LabOrderItem {lab_order_item_id} already has result-entry content "
                    f"(constraint-detected)"
                ) from None

        observation = LabObservation(
            lab_result_id=container.id,
            source_code=source_code,
            source_system=NATIVE_OBSERVATION_SOURCE_SYSTEM,
            display_label_snapshot=display_label_snapshot,
            result_type=result_type.value,
            numeric_value=numeric_value,
            numeric_unit=numeric_unit,
            reference_range_low=reference_range_low,
            reference_range_high=reference_range_high,
            text_value=text_value,
            recorded_by_identity_id=recorded_by_identity_id,
        )
        session.add(observation)
        session.flush()
        return _to_observation_record(observation)

    def complete_entry(
        self,
        *,
        order_id: uuid.UUID,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        completed_by_identity_id: uuid.UUID,
        session: Session | None = None,
    ) -> tuple[LabResultEntryCompletionRecord, bool]:
        """Returns ``(record, created)``. ``created=False`` means an existing completion was
        returned as a safe idempotent replay -- the original completion time and actor are never
        overwritten by a later caller, mirroring ``LabResultReleaseRepository.release``'s identical
        replay discipline."""
        if session is not None:
            return self._complete_entry_in_session(
                session,
                order_id=order_id,
                lab_order_item_id=lab_order_item_id,
                lab_organization_id=lab_organization_id,
                completed_by_identity_id=completed_by_identity_id,
            )
        with self._sessions.begin() as owned_session:
            return self._complete_entry_in_session(
                owned_session,
                order_id=order_id,
                lab_order_item_id=lab_order_item_id,
                lab_organization_id=lab_organization_id,
                completed_by_identity_id=completed_by_identity_id,
            )

    def _complete_entry_in_session(
        self,
        session: Session,
        *,
        order_id: uuid.UUID,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        completed_by_identity_id: uuid.UUID,
    ) -> tuple[LabResultEntryCompletionRecord, bool]:
        item, _order = _lock_order_and_item_for_entry(
            session,
            order_id=order_id,
            lab_order_item_id=lab_order_item_id,
            lab_organization_id=lab_organization_id,
        )

        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item.id))
        if container is not None and container.entry_completed_at is not None:
            return _to_completion_record(container), False

        observation_count = (
            session.scalar(
                select(func.count())
                .select_from(LabObservation)
                .where(LabObservation.lab_result_id == container.id)
            )
            if container is not None
            else 0
        )
        if container is None or observation_count == 0:
            raise EntryNotReadyForCompletion(
                f"LabOrderItem {lab_order_item_id} has no recorded observation yet -- entry "
                f"cannot be marked complete with zero observations"
            )

        container.entry_completed_at = utc_now()
        container.entry_completed_by_identity_id = completed_by_identity_id
        session.flush()
        return _to_completion_record(container), True

    # -- DH-EX-013/ADR-045: pre-release observation correction -----------------------------------

    def correct_observation(
        self,
        *,
        order_id: uuid.UUID,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        observation_id: uuid.UUID,
        result_type: LabResultType,
        source_code: str,
        display_label_snapshot: str,
        corrected_by_identity_id: uuid.UUID,
        numeric_value: Decimal | None = None,
        numeric_unit: str | None = None,
        reference_range_low: Decimal | None = None,
        reference_range_high: Decimal | None = None,
        text_value: str | None = None,
        session: Session | None = None,
    ) -> LabObservationRecord:
        """DH-EX-013/ADR-045 (pre-release correction only): supersedes the currently *effective*
        ``observation_id`` with one new immutable ``LabObservation`` row, never mutating the
        original row's value in any way -- the only field ever written on the superseded row is
        ``superseded_at`` (a supersession marker, not a result-value change; see this method's own
        "no destructive update" discipline below). Reuses the identical ``LabOrder FOR UPDATE``
        lock-and-``IN_PROGRESS``-check preamble every other entry mutation in this module uses
        (``_lock_order_and_item_for_entry``) -- this is exactly what makes "correction after
        release" structurally refuse: once ``LabResultReleaseRepository.release`` has moved the
        order to ``COMPLETED``, this same preamble raises ``InvalidResultSubmissionState`` before
        this method ever touches an observation row, identical to how it already refuses a new
        ``record_observation`` call on a released order.

        **Deliberately does NOT check ``LabResult.entry_completed_at``** -- unlike
        ``record_observation``'s freeze check, a correction of an *existing effective* observation
        is permitted whether or not the item's entry is already complete. This is not "reopening"
        entry: ``entry_completed_at``/``entry_completed_by_identity_id`` are never read, cleared, or
        rewritten here, so the historical fact "entry was completed at this instant" stays exactly
        as true after a correction as before it. What changes is only which observation row is
        currently effective for an item whose entry-completion fact is untouched.

        **Chain integrity, application-layer checks (see ``LabObservation``'s own ``models.py``
        docstring for why a database-level guard is impractical for these two without a trigger):**
        the predecessor observation must belong to *this exact item's container*
        (``ObservationNotFound`` otherwise, non-enumerating across containers/labs/orders) and must
        be currently effective (``StaleObservationVersion`` if ``superseded_at`` is already set --
        correction always starts from the currently effective observation, never an older one).

        **Concurrency:** two concurrent corrections of the same effective observation, for the same
        order, fully serialize at the ``LabOrder FOR UPDATE`` lock acquired by
        ``_lock_order_and_item_for_entry`` -- the second caller, after acquiring the lock, re-reads
        the predecessor fresh and finds it already superseded by the first, raising
        ``StaleObservationVersion`` before ever attempting an insert. The database's own ``UNIQUE``
        constraint on ``supersedes_observation_id`` is the defense-in-depth backstop for this
        identical invariant, translated from ``IntegrityError`` to the same
        ``StaleObservationVersion`` conflict -- structurally unreachable in practice given the
        lock, but never assumed absent."""
        if session is not None:
            return self._correct_observation_in_session(
                session,
                order_id=order_id,
                lab_order_item_id=lab_order_item_id,
                lab_organization_id=lab_organization_id,
                observation_id=observation_id,
                result_type=result_type,
                source_code=source_code,
                display_label_snapshot=display_label_snapshot,
                corrected_by_identity_id=corrected_by_identity_id,
                numeric_value=numeric_value,
                numeric_unit=numeric_unit,
                reference_range_low=reference_range_low,
                reference_range_high=reference_range_high,
                text_value=text_value,
            )
        with self._sessions.begin() as owned_session:
            return self._correct_observation_in_session(
                owned_session,
                order_id=order_id,
                lab_order_item_id=lab_order_item_id,
                lab_organization_id=lab_organization_id,
                observation_id=observation_id,
                result_type=result_type,
                source_code=source_code,
                display_label_snapshot=display_label_snapshot,
                corrected_by_identity_id=corrected_by_identity_id,
                numeric_value=numeric_value,
                numeric_unit=numeric_unit,
                reference_range_low=reference_range_low,
                reference_range_high=reference_range_high,
                text_value=text_value,
            )

    def _correct_observation_in_session(
        self,
        session: Session,
        *,
        order_id: uuid.UUID,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        observation_id: uuid.UUID,
        result_type: LabResultType,
        source_code: str,
        display_label_snapshot: str,
        corrected_by_identity_id: uuid.UUID,
        numeric_value: Decimal | None,
        numeric_unit: str | None,
        reference_range_low: Decimal | None,
        reference_range_high: Decimal | None,
        text_value: str | None,
    ) -> LabObservationRecord:
        item, _order = _lock_order_and_item_for_entry(
            session,
            order_id=order_id,
            lab_order_item_id=lab_order_item_id,
            lab_organization_id=lab_organization_id,
        )

        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item.id))
        if container is None:
            raise ObservationNotFound(str(observation_id))

        predecessor = session.scalar(
            select(LabObservation).where(LabObservation.id == observation_id)
        )
        if predecessor is None or predecessor.lab_result_id != container.id:
            raise ObservationNotFound(str(observation_id))
        if predecessor.superseded_at is not None:
            raise StaleObservationVersion(
                f"LabObservation {observation_id} is no longer effective -- a correction must "
                f"start from the currently effective observation"
            )

        successor = LabObservation(
            lab_result_id=container.id,
            source_code=source_code,
            source_system=NATIVE_OBSERVATION_SOURCE_SYSTEM,
            display_label_snapshot=display_label_snapshot,
            result_type=result_type.value,
            numeric_value=numeric_value,
            numeric_unit=numeric_unit,
            reference_range_low=reference_range_low,
            reference_range_high=reference_range_high,
            text_value=text_value,
            recorded_by_identity_id=corrected_by_identity_id,
            supersedes_observation_id=predecessor.id,
        )
        try:
            with session.begin_nested():
                session.add(successor)
                predecessor.superseded_at = utc_now()
                session.flush()
        except IntegrityError:
            # Defensive last line only, mirroring `_record_observation_in_session`'s identical
            # pattern -- the LabOrder FOR UPDATE lock above already serializes concurrent
            # corrections of this exact observation down to one winner, so this should never
            # actually fire in practice.
            raise StaleObservationVersion(
                f"LabObservation {observation_id} is no longer effective (constraint-detected)"
            ) from None
        return _to_observation_record(successor)

    def get_correction_history(
        self, lab_order_item_id: uuid.UUID
    ) -> LabObservationHistoryRecord | None:
        """DH-EX-013: every observation that has ever existed under this item's container,
        superseded and effective alike, ordered oldest to newest -- the one supported way to
        reconstruct a correction chain. Never used by any provider-facing or release-facing read;
        callers are responsible for their own lab-authorization check before calling this, exactly
        mirroring ``get_for_lab``'s own convention."""
        with self._sessions() as session:
            container = session.scalar(
                select(LabResult).where(LabResult.lab_order_item_id == lab_order_item_id)
            )
            if container is None:
                return None
            observations = session.scalars(
                select(LabObservation)
                .where(LabObservation.lab_result_id == container.id)
                .order_by(LabObservation.recorded_at)
            ).all()
            return LabObservationHistoryRecord(
                item_id=lab_order_item_id,
                observations=tuple(_to_observation_record(o) for o in observations),
            )

    # -- DH-EX-007/DH-EX-012C: backward-compatible single-observation convenience path ----------

    def record_numeric(
        self,
        *,
        order_id: uuid.UUID,
        lab_order_item_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        value: Decimal,
        recorded_by_identity_id: uuid.UUID,
        unit: str | None = None,
        reference_range_low: Decimal | None = None,
        reference_range_high: Decimal | None = None,
        session: Session | None = None,
    ) -> ResultSubmissionOutcome:
        return self._submit(
            order_id=order_id,
            lab_order_item_id=lab_order_item_id,
            lab_organization_id=lab_organization_id,
            recorded_by_identity_id=recorded_by_identity_id,
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
        recorded_by_identity_id: uuid.UUID,
        session: Session | None = None,
    ) -> ResultSubmissionOutcome:
        if not value.strip():
            raise ValueError("value is required for a text result")
        return self._submit(
            order_id=order_id,
            lab_order_item_id=lab_order_item_id,
            lab_organization_id=lab_organization_id,
            recorded_by_identity_id=recorded_by_identity_id,
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
        recorded_by_identity_id: uuid.UUID,
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
                recorded_by_identity_id=recorded_by_identity_id,
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
                recorded_by_identity_id=recorded_by_identity_id,
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
        recorded_by_identity_id: uuid.UUID,
        result_type: LabResultType,
        numeric_value: Decimal | None,
        numeric_unit: str | None,
        reference_range_low: Decimal | None,
        reference_range_high: Decimal | None,
        text_value: str | None,
    ) -> ResultSubmissionOutcome:
        item, order = _lock_order_and_item_for_entry(
            session,
            order_id=order_id,
            lab_order_item_id=lab_order_item_id,
            lab_organization_id=lab_organization_id,
        )

        existing = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item.id))
        if existing is not None:
            raise DuplicateResult(
                f"LabOrderItem {lab_order_item_id} already has result-entry content -- the "
                f"single-observation convenience path refuses to run on an item that already has "
                f"a container; use the granular observation/completion endpoints instead"
            )

        container = LabResult(lab_order_item_id=item.id, lab_organization_id=lab_organization_id)
        try:
            with session.begin_nested():
                session.add(container)
                session.flush()
        except IntegrityError:
            # Defensive last line only: the LabOrder FOR UPDATE lock acquired above already
            # serializes every concurrent submission for this order down to one winner, so the
            # pre-insert check above should always be the one that fires in practice.
            raise DuplicateResult(
                f"LabOrderItem {lab_order_item_id} already has result-entry content "
                f"(constraint-detected)"
            ) from None

        observation = LabObservation(
            lab_result_id=container.id,
            source_code=item.catalog_code_snapshot,
            source_system=NATIVE_OBSERVATION_SOURCE_SYSTEM,
            display_label_snapshot=item.catalog_display_name_snapshot,
            result_type=result_type.value,
            numeric_value=numeric_value,
            numeric_unit=numeric_unit,
            reference_range_low=reference_range_low,
            reference_range_high=reference_range_high,
            text_value=text_value,
            recorded_by_identity_id=recorded_by_identity_id,
        )
        session.add(observation)
        session.flush()

        container.entry_completed_at = utc_now()
        container.entry_completed_by_identity_id = recorded_by_identity_id
        session.flush()

        # DB-grounded completeness check, re-queried explicitly rather than trusted from an
        # already-loaded relationship -- still inside the same LabOrder FOR UPDATE lock acquired
        # above, so this is a consistent read of this transaction's own write plus whatever was
        # already committed.
        missing_count = _count_missing_or_malformed_results(session, order_id)

        return ResultSubmissionOutcome(
            observation=_to_observation_record(observation),
            entry_completed_at=container.entry_completed_at,
            ready_for_release=missing_count == 0,
            order_status=order.status,
        )

    # -- Reads -----------------------------------------------------------------------------------

    def get_for_item(self, lab_order_item_id: uuid.UUID) -> LabResultRecord | None:
        """DH-EX-013: returns *effective* observations only (``superseded_at IS NULL``) -- a
        superseded historical version is never mixed into this ordinary read. Use
        ``get_correction_history`` to see every version."""
        with self._sessions() as session:
            container = session.scalar(
                select(LabResult).where(LabResult.lab_order_item_id == lab_order_item_id)
            )
            if container is None:
                return None
            observations = session.scalars(
                select(LabObservation)
                .where(
                    LabObservation.lab_result_id == container.id,
                    LabObservation.superseded_at.is_(None),
                )
                .order_by(LabObservation.recorded_at)
            ).all()
            return _to_result_record(container, observations)

    def list_for_order(self, order_id: uuid.UUID) -> list[LabResultRecord]:
        """Read-only, tenant-unsafe by itself -- the caller (the provider result-read API route)
        is responsible for proving the requesting provider owns this order and that it has
        reached ``COMPLETED`` *before* calling this, exactly mirroring how
        ``orders.get_for_organization`` is the tenant check that already gates every other
        provider-scoped order read in this package. Two queries total regardless of item/
        observation count -- no N+1. DH-EX-013: the second query is filtered to *effective*
        observations only (``superseded_at IS NULL``); a superseded historical version can never
        reach a provider through this path, before or after release -- correction is refused
        outright once the order is released (see ``correct_observation``'s own docstring), so the
        effective set this query reads is exactly what release itself already saw."""
        with self._sessions() as session:
            containers = session.scalars(
                select(LabResult)
                .join(LabOrderItem, LabResult.lab_order_item_id == LabOrderItem.id)
                .where(LabOrderItem.lab_order_id == order_id)
            ).all()
            container_ids = [c.id for c in containers]
            observations_by_result: dict[uuid.UUID, list[LabObservation]] = {}
            if container_ids:
                for observation in session.scalars(
                    select(LabObservation)
                    .where(
                        LabObservation.lab_result_id.in_(container_ids),
                        LabObservation.superseded_at.is_(None),
                    )
                    .order_by(LabObservation.recorded_at)
                ):
                    observations_by_result.setdefault(observation.lab_result_id, []).append(
                        observation
                    )
            return [
                _to_result_record(container, observations_by_result.get(container.id, []))
                for container in containers
            ]

    def get_for_lab(
        self, order_id: uuid.UUID, lab_organization_id: uuid.UUID
    ) -> LabResultInspectionRecord | None:
        """DH-EX-012, extended by DH-EX-012C: the selected lab's own pre-release read -- "a
        releaser must not be forced to release results they cannot inspect." Read-only,
        tenant-safe: returns ``None`` both when the order does not exist and when it exists but is
        not assigned to this lab, never disclosing which is true. One outer-joined query returns
        every ordered item together with its container (if any); a second, batched query loads
        every observation for every container found, so this stays two queries total regardless of
        how many items or observations exist -- no N+1. DH-EX-013: the second query is filtered to
        *effective* observations only (``superseded_at IS NULL``) -- the normal inspection view
        never mixes a superseded historical value in alongside the current one; a releaser sees
        exactly what they are about to release. ``get_correction_history`` is the dedicated,
        separate read for the full chain."""
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
            container_ids = [container.id for _item, container in rows if container is not None]
            observations_by_result: dict[uuid.UUID, list[LabObservation]] = {}
            corrected_container_ids: set[uuid.UUID] = set()
            if container_ids:
                for observation in session.scalars(
                    select(LabObservation)
                    .where(
                        LabObservation.lab_result_id.in_(container_ids),
                        LabObservation.superseded_at.is_(None),
                    )
                    .order_by(LabObservation.recorded_at)
                ):
                    observations_by_result.setdefault(observation.lab_result_id, []).append(
                        observation
                    )
                # DH-EX-013: a third, still-batched query -- which containers have ever had a
                # correction, regardless of how many. Only the effective observation's own
                # `supersedes_observation_id` needs checking (an effective row that itself
                # supersedes something is proof a correction happened in this container at least
                # once); this stays O(1) queries, not one per item.
                corrected_container_ids = set(
                    session.scalars(
                        select(LabObservation.lab_result_id)
                        .where(
                            LabObservation.lab_result_id.in_(container_ids),
                            LabObservation.supersedes_observation_id.isnot(None),
                        )
                        .distinct()
                    )
                )
            release = session.scalar(
                select(LabResultRelease).where(LabResultRelease.lab_order_id == order_id)
            )
            return LabResultInspectionRecord(
                order_id=order.id,
                order_status=order.status,
                all_items_entry_complete=all(
                    container is not None and container.entry_completed_at is not None
                    for _item, container in rows
                ),
                released=release is not None,
                released_at=release.released_at if release is not None else None,
                items=tuple(
                    _to_inspection_item(
                        item, container, observations_by_result, corrected_container_ids
                    )
                    for item, container in rows
                ),
            )


class LabResultReleaseRepository:
    """DH-EX-012/ADR-044, extended by DH-EX-012C: the sole supported production way to move a
    ``LabOrder`` from ``IN_PROGRESS`` to ``COMPLETED`` -- see ``exchange/orders.py``'s
    ``transition()`` for the identical, deliberately duplicated guard that keeps the generic
    state-machine primitive from becoming an unguarded bypass of this same invariant."""

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
        # DB-grounded fact in both places. DH-EX-012C: this single query is also the zero-
        # observation defense in depth (see its own docstring) -- release refuses a malformed
        # "complete but empty" container exactly as it refuses a genuinely incomplete one.
        missing_count = _count_missing_or_malformed_results(session, order_id)
        if missing_count:
            raise ResultsNotReadyForRelease(
                f"cannot release results for LabOrder {order_id} -- {missing_count} ordered "
                f"item(s) still lack a completed, observation-backed result entry"
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
    """Unchanged by DH-EX-007/DH-EX-012C -- no code anywhere in this package calls
    ``mark_delivered``/``mark_failed`` as a side effect of a result being recorded, an order
    completing, or a provider reading results. The exact semantic of ``DELIVERED`` is an explicit
    future product decision (see PROJECT_STATE.md's DH-EX-007 entry), not inferred here."""

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


def _lock_order_and_item_for_entry(
    session: Session,
    *,
    order_id: uuid.UUID,
    lab_order_item_id: uuid.UUID,
    lab_organization_id: uuid.UUID,
) -> tuple[LabOrderItem, LabOrder]:
    """Shared lock-and-validate preamble for every result-entry mutation
    (`record_observation`/`complete_entry`/`_submit_in_session`/`correct_observation`) --
    ``LabOrder`` locked ``FOR UPDATE`` first, exactly matching this module's own established
    lock-order discipline, so two concurrent calls for the same order fully serialize here before
    either touches `LabResult`/`LabObservation`. DH-EX-013: this is also exactly what makes
    correction-after-release structurally refuse -- once release moves the order to `COMPLETED`,
    this same `IN_PROGRESS` check raises before `correct_observation` ever reads an observation
    row, identical to how it already refuses a new `record_observation` call."""
    order = session.scalar(select(LabOrder).where(LabOrder.id == order_id).with_for_update())
    if order is None:
        raise LabOrderNotAssigned(str(order_id))

    selection = session.scalar(select(LabSelection).where(LabSelection.lab_order_id == order_id))
    if selection is None or selection.lab_organization_id != lab_organization_id:
        raise LabOrderNotAssigned(str(order_id))

    if LabOrderStatus(order.status) != LabOrderStatus.IN_PROGRESS:
        raise InvalidResultSubmissionState(
            f"LabOrder {order_id} is {order.status!r}, not 'IN_PROGRESS' -- a result entry may "
            f"only be recorded, corrected, or completed while the selected lab is actively "
            f"processing the order"
        )

    item = session.get(LabOrderItem, lab_order_item_id)
    if item is None or item.lab_order_id != order_id:
        raise OrderItemNotFound(str(lab_order_item_id))

    return item, order


def _count_missing_or_malformed_results(session: Session, order_id: uuid.UUID) -> int:
    """The single DB-grounded completeness query shared by result submission
    (``ready_for_release``) and release (``LabResultReleaseRepository``'s own re-check) -- both
    call this rather than each maintaining their own copy. DH-EX-012C: an ordered item now counts
    as "missing" unless it has a container whose ``entry_completed_at`` is set *and* which has at
    least one real ``LabObservation`` row -- the second clause is deliberate defense-in-depth
    (``MalformedZeroObservationEntry``'s own docstring), re-derived from the database every call,
    never trusted from the container's own flag alone. Callers are responsible for holding the
    ``LabOrder`` row's ``FOR UPDATE`` lock first; this function does not acquire one itself."""
    has_observation = (
        select(LabObservation.id)
        .where(
            LabObservation.lab_result_id == LabResult.id,
            LabObservation.superseded_at.is_(None),
        )
        .correlate(LabResult)
        .exists()
    )
    return session.scalar(
        select(func.count())
        .select_from(LabOrderItem)
        .outerjoin(LabResult, LabResult.lab_order_item_id == LabOrderItem.id)
        .where(
            LabOrderItem.lab_order_id == order_id,
            (LabResult.id.is_(None))
            | (LabResult.entry_completed_at.is_(None))
            | (~has_observation),
        )
    )


def _to_observation_record(observation: LabObservation) -> LabObservationRecord:
    return LabObservationRecord(
        id=observation.id,
        lab_result_id=observation.lab_result_id,
        source_code=observation.source_code,
        source_system=observation.source_system,
        display_label_snapshot=observation.display_label_snapshot,
        result_type=observation.result_type,
        numeric_value=observation.numeric_value,
        numeric_unit=observation.numeric_unit,
        reference_range_low=observation.reference_range_low,
        reference_range_high=observation.reference_range_high,
        text_value=observation.text_value,
        recorded_at=observation.recorded_at,
        recorded_by_identity_id=observation.recorded_by_identity_id,
        supersedes_observation_id=observation.supersedes_observation_id,
        superseded_at=observation.superseded_at,
    )


def _to_result_record(container: LabResult, observations: list[LabObservation]) -> LabResultRecord:
    return LabResultRecord(
        id=container.id,
        lab_order_item_id=container.lab_order_item_id,
        lab_organization_id=container.lab_organization_id,
        entry_completed_at=container.entry_completed_at,
        entry_completed_by_identity_id=container.entry_completed_by_identity_id,
        observations=tuple(_to_observation_record(o) for o in observations),
    )


def _to_completion_record(container: LabResult) -> LabResultEntryCompletionRecord:
    # Only ever called with a container whose entry_completed_at is already set -- both call
    # sites in this module check that themselves first (or just set it) before calling this.
    if container.entry_completed_at is None:
        raise RuntimeError("_to_completion_record called on a container with no completion time")
    return LabResultEntryCompletionRecord(
        id=container.id,
        lab_order_item_id=container.lab_order_item_id,
        lab_organization_id=container.lab_organization_id,
        entry_completed_at=container.entry_completed_at,
        entry_completed_by_identity_id=container.entry_completed_by_identity_id,
    )


def _to_inspection_item(
    item: LabOrderItem,
    container: LabResult | None,
    observations_by_result: dict[uuid.UUID, list[LabObservation]],
    corrected_container_ids: set[uuid.UUID],
) -> LabResultInspectionItemRecord:
    observations = (
        ()
        if container is None
        else tuple(_to_observation_record(o) for o in observations_by_result.get(container.id, []))
    )
    return LabResultInspectionItemRecord(
        item_id=item.id,
        catalog_code_snapshot=item.catalog_code_snapshot,
        catalog_display_name_snapshot=item.catalog_display_name_snapshot,
        entry_completed_at=None if container is None else container.entry_completed_at,
        observations=observations,
        has_correction_history=container is not None and container.id in corrected_container_ids,
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
