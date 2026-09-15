"""DH-EX result entry, release, immutable correction/amendment history, delivery, and review.

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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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
    LabResultAmendment,
    LabResultAmendmentStatus,
    LabResultRelease,
    LabResultReleaseManifestEntry,
    LabResultReleaseVersion,
    LabResultType,
    LabSelection,
    ResultDelivery,
    ResultDeliveryStatus,
)
from daher_adapter.models import utc_now

__all__ = [
    "AmendmentAlreadyInProgress",
    "AmendmentBaseVersionSuperseded",
    "AmendmentInspectionItemRecord",
    "AmendmentInspectionRecord",
    "AmendmentNotFound",
    "AmendmentNotReadyForRelease",
    "AmendmentNotPreparing",
    "AmendmentReleaseIntegrityError",
    "DeliveryNotFound",
    "DoctorReviewRecord",
    "DoctorReviewRepository",
    "DuplicateResult",
    "EntryAlreadyCompleted",
    "EntryNotReadyForCompletion",
    "ExplicitReviewVersionRequired",
    "InvalidResultSubmissionState",
    "LabOrderNotAssigned",
    "LabObservationHistoryRecord",
    "LabObservationRecord",
    "LabResultAmendmentRecord",
    "LabResultAmendmentRepository",
    "LabResultEntryCompletionRecord",
    "LabResultInspectionItemRecord",
    "LabResultInspectionRecord",
    "LabResultRecord",
    "LabResultReleaseRecord",
    "LabResultReleaseRepository",
    "LabResultReleaseVersionRecord",
    "LabResultRepository",
    "ReleaseManifestItemRecord",
    "ReleaseVersionManifestRecord",
    "LegacyCompletedOrderHasNoRelease",
    "MalformedZeroObservationEntry",
    "ObservationNotEligibleForAmendment",
    "ObservationNotFound",
    "OrderItemNotFound",
    "OrderNotCompletedForAmendment",
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
    """A DoctorReview mutation was attempted on a review that has already left ``PENDING``.

    Every released version has its own immutable obligation under ADR-048. A second terminal
    mutation of that same version is therefore a controlled conflict, never an overwrite and
    never a request to reset the review for a later version.
    """


class ExplicitReviewVersionRequired(RuntimeError):
    """An order-only review request targeted an order with an amendment release.

    The legacy route remains safe for grandfathered/unversioned orders and Version 1-only orders,
    but once Version 2 exists a caller must name the exact release version whose content it
    reviewed. This fails closed rather than silently applying a stale screen's disposition to the
    newest version.
    """


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


# -- DH-EX-014B/ADR-048: post-release amendment ---------------------------------------------------


class OrderNotCompletedForAmendment(RuntimeError):
    """DH-EX-014B: an amendment operation was attempted against an order that is not (yet)
    ``COMPLETED`` -- amendment is a versioned result-lifecycle layer on top of a terminal completed
    order; it is never a way to act on an in-progress one. Distinct from
    ``LegacyCompletedOrderHasNoRelease`` (the order *is* ``COMPLETED`` but has no release version at
    all, i.e. grandfathered) -- these are two different reasons an order cannot be amended, exactly
    mirroring ``ResultsNotReadyForRelease``/``LegacyCompletedOrderHasNoRelease``'s own established
    distinction one level up."""


class AmendmentAlreadyInProgress(RuntimeError):
    """DH-EX-014B: a ``PREPARING`` amendment already exists for this order -- at most one at a
    time, enforced by ``uq_exchange_amendments_one_preparing_per_order`` at the database level, not
    merely here. A controlled conflict, never a silent second open episode."""


class AmendmentNotFound(RuntimeError):
    """DH-EX-014B: no ``LabResultAmendment`` matches the given id *for this exact order* --
    identical response whether the amendment id is entirely bogus or belongs to a genuinely
    different order, mirroring ``ObservationNotFound``'s own non-enumerating convention."""


class AmendmentNotPreparing(RuntimeError):
    """DH-EX-014B: a mutation (correction or release) was attempted against an amendment that has
    already reached ``RELEASED`` -- terminal, no discard/reopen path exists anywhere in this
    codebase."""


class ObservationNotEligibleForAmendment(RuntimeError):
    """DH-EX-014B: the target observation is currently effective but is neither named in this
    amendment's own ``base_release_version_id`` manifest nor itself a revision already created by
    *this same* ``PREPARING`` amendment -- see
    ``LabResultAmendmentRepository.correct_observation``'s own docstring for the full eligibility
    predicate this guards. Refuses starting a correction from an older historical release
    observation, an observation belonging to a different amendment episode, or (structurally
    prevented by ``StaleObservationVersion`` first in practice, but conceptually part of the same
    guard) an already-superseded one."""


class AmendmentBaseVersionSuperseded(RuntimeError):
    """DH-EX-014B: defense-in-depth only -- structurally unreachable given
    ``uq_exchange_amendments_one_preparing_per_order`` (at most one open amendment per order) plus
    the ``LabOrder FOR UPDATE`` lock (no other path can create a new release version while this
    amendment is ``PREPARING``). Raised if the order's latest release version at release time is
    somehow not the exact version this amendment recorded as its ``base_release_version_id`` at
    creation -- release refuses rather than silently releasing against a baseline the amendment was
    never actually prepared against."""


class AmendmentNotReadyForRelease(RuntimeError):
    """DH-EX-014B: amendment release was attempted with zero observation revisions belonging to
    this exact amendment episode -- an amendment can never release with no changes. Checked against
    the episode itself (``LabObservation.amendment_id == this amendment's id``), never against
    "has anything changed since the original release," which would be true for reasons unrelated to
    this episode once a second amendment exists."""


class AmendmentReleaseIntegrityError(RuntimeError):
    """DH-EX-014B: an effective observation at amendment release time differs from the base release
    version manifest but is not attributable to the amendment being released. Refuses release
    fail-closed if an effective observation was introduced from another amendment, an
    unattributed/direct database mutation, or an unsupported lineage."""


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
    amendment_id: uuid.UUID | None
    """DH-EX-014B: ``None`` for an original entry or a pre-release (DH-EX-013) correction --
    unchanged meaning, no retroactive fabrication for any historical row. Non-``None`` names the
    exact ``LabResultAmendment`` episode that created this revision."""


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
class LabResultReleaseVersionRecord:
    """DH-EX-014A/ADR-048, extended by DH-EX-014B: one released version of an order's result set.
    ``lab_result_release_id`` is non-``None`` for version 1 only; ``amendment_id`` is non-``None``
    for version 2+ only -- the two are mutually exclusive by the model's own ``CHECK`` constraint,
    never both set and never both ``None``."""

    id: uuid.UUID
    lab_order_id: uuid.UUID
    version_number: int
    lab_result_release_id: uuid.UUID | None
    amendment_id: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReleaseManifestItemRecord:
    """DH-EX-014A: one ordered item's exact observation set as it was released under one specific
    version -- ``observations`` is read by manifest-entry id, never filtered by the observation's
    own current ``superseded_at`` state (a manifest entry remains historically correct even after a
    future amendment supersedes the observation it names)."""

    item_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str
    entry_completed_at: datetime
    observations: tuple[LabObservationRecord, ...]


@dataclass(frozen=True, slots=True)
class ReleaseVersionManifestRecord:
    """DH-EX-014A: one released version, plus its exact, immutable manifest -- the reconstruction
    unit ADR-048 requires. ``version.version_number == 1`` and ``version.lab_result_release_id`` is
    always the same order's ``LabResultRelease`` id, for every version this ticket can produce."""

    version: LabResultReleaseVersionRecord
    items: tuple[ReleaseManifestItemRecord, ...]


@dataclass(frozen=True, slots=True)
class ResultDeliveryRecord:
    id: uuid.UUID
    lab_order_id: uuid.UUID
    provider_organization_id: uuid.UUID
    status: str
    delivered_at: datetime | None


@dataclass(frozen=True, slots=True)
class DoctorReviewRecord:
    """DH-EX-014A/ADR-048 (ADR-045 Question 4, Option C): ``release_version_id`` is ``None`` only
    for the one legacy/pre-release row (created at order-creation time, never yet bound to a real
    release) -- see ``DoctorReview``'s own ``models.py`` docstring. Never inferred, never rewritten
    once bound."""

    id: uuid.UUID
    lab_order_id: uuid.UUID
    release_version_id: uuid.UUID | None
    status: str
    reviewed_at: datetime | None
    reviewed_by_practitioner_reference_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class LabResultAmendmentRecord:
    """DH-EX-014B/ADR-048: one amendment episode -- ``PREPARING`` or ``RELEASED``, no other status
    (Decision 4, no discardable draft for MVP). ``released_by_identity_id``/``released_at`` are
    both ``None`` together while ``PREPARING``, both populated together exactly when
    ``RELEASED``."""

    id: uuid.UUID
    lab_order_id: uuid.UUID
    amendment_number: int
    base_release_version_id: uuid.UUID
    status: str
    started_by_identity_id: uuid.UUID
    started_at: datetime
    released_by_identity_id: uuid.UUID | None
    released_at: datetime | None


@dataclass(frozen=True, slots=True)
class AmendmentInspectionItemRecord:
    """DH-EX-014B: one ordered item's *proposed* (currently effective) observation set as seen from
    inside a specific amendment's inspection read -- ``changed_in_this_amendment`` is ``True``
    exactly when at least one of this item's current observations carries this exact amendment's
    id, so a lab user can see which items this episode has actually touched so far without
    re-deriving it client-side."""

    item_id: uuid.UUID
    catalog_code_snapshot: str
    catalog_display_name_snapshot: str
    entry_completed_at: datetime
    observations: tuple[LabObservationRecord, ...]
    changed_in_this_amendment: bool


@dataclass(frozen=True, slots=True)
class AmendmentInspectionRecord:
    """DH-EX-014B: the lab-authorized-only pre-release-of-amendment inspection read -- "a releaser
    must not be forced to release an amendment they cannot inspect," the identical framing
    ``LabResultInspectionRecord`` already established for the original release. ``ready_to_release``
    is a DB-grounded fact (at least one observation carries this amendment's id), never inferred
    from item count or client-side state."""

    amendment: LabResultAmendmentRecord
    items: tuple[AmendmentInspectionItemRecord, ...]
    ready_to_release: bool


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
    """DH-EX-012/ADR-044, extended by DH-EX-012C, extended by DH-EX-014A/ADR-048: the sole
    supported production way to move a ``LabOrder`` from ``IN_PROGRESS`` to ``COMPLETED`` -- see
    ``exchange/orders.py``'s ``transition()`` for the identical, deliberately duplicated guard that
    keeps the generic state-machine primitive from becoming an unguarded bypass of this same
    invariant.

    **DH-EX-014A: a successful original release now atomically creates four things in the one
    transaction this class already holds the ``LabOrder FOR UPDATE`` lock for** -- the existing
    immutable ``LabResultRelease`` row (unchanged in shape/meaning); a new
    ``LabResultReleaseVersion`` (``version_number=1``, bound to that same release row); one
    ``LabResultReleaseManifestEntry`` per *effective* observation across every item, computed
    inside this same lock so the manifest can never diverge from the actual released set; and the
    order's existing ``PENDING`` ``DoctorReview`` row bound (``release_version_id`` set from
    ``NULL``) to the new version -- so "Review #1" means exactly "the review obligation for
    Release Version 1" from the instant release occurs. All four commit together or none do -- no
    code path here creates a ``LabResultRelease`` without its version and manifest, or a version
    without a bound review."""

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

        released_at = utc_now()
        release = LabResultRelease(
            lab_order_id=order_id,
            lab_organization_id=lab_organization_id,
            released_by_identity_id=released_by_identity_id,
            released_at=released_at,
        )
        session.add(release)
        session.flush()

        # DH-EX-014A: the exact effective set this release authorizes, computed inside the same
        # LabOrder-locked transaction that already re-checked completeness above -- this is what
        # makes the manifest structurally unable to diverge from what was actually released.
        # DH-EX-014B: extracted to a shared module function, `_effective_observation_rows`, now
        # reused identically by `LabResultAmendmentRepository`'s amendment-release path -- same
        # query, same "no divergence possible" guarantee, not a second implementation to keep in
        # sync.
        effective_rows = _effective_observation_rows(session, order_id)

        version = LabResultReleaseVersion(
            lab_order_id=order_id,
            version_number=1,
            lab_result_release_id=release.id,
            created_at=released_at,
        )
        session.add(version)
        session.flush()

        for item_id, observation_id in effective_rows:
            session.add(
                LabResultReleaseManifestEntry(
                    release_version_id=version.id,
                    lab_order_item_id=item_id,
                    lab_observation_id=observation_id,
                )
            )

        # Bind the order's one existing, still-unbound review row to this new version -- never a
        # new row here (that row was already created PENDING at order-creation time; DH-EX-014A
        # does not change when a review obligation first comes to exist, only what it is bound to).
        # No additional lock needed: the LabOrder FOR UPDATE lock held since this method's first
        # statement already serializes every concurrent caller for this exact order.
        unbound_review = session.scalar(
            select(DoctorReview).where(
                DoctorReview.lab_order_id == order_id, DoctorReview.release_version_id.is_(None)
            )
        )
        if unbound_review is not None:
            unbound_review.release_version_id = version.id

        order.status = LabOrderStatus.COMPLETED.value
        session.flush()
        return _to_release_record(release), True

    def get_for_order(self, order_id: uuid.UUID) -> LabResultReleaseRecord | None:
        with self._sessions() as session:
            release = session.scalar(
                select(LabResultRelease).where(LabResultRelease.lab_order_id == order_id)
            )
            return None if release is None else _to_release_record(release)

    def get_latest_release_version(
        self, order_id: uuid.UUID
    ) -> LabResultReleaseVersionRecord | None:
        """DH-EX-014A: the highest ``version_number`` release version for this order, or ``None``
        for an order with no release version at all -- a grandfathered
        ``COMPLETED``-without-``LabResultRelease`` order, or one not yet released. Ordered
        explicitly by ``version_number``, never by ``created_at``."""
        with self._sessions() as session:
            version = session.scalar(
                select(LabResultReleaseVersion)
                .where(LabResultReleaseVersion.lab_order_id == order_id)
                .order_by(LabResultReleaseVersion.version_number.desc())
                .limit(1)
            )
            return None if version is None else _to_release_version_record(version)

    def get_release_version_history(
        self, order_id: uuid.UUID
    ) -> tuple[LabResultReleaseVersionRecord, ...]:
        """DH-EX-014A: every release version that has ever existed for this order, oldest first --
        in this ticket, at most one (version 1). Never used by any provider-facing route directly;
        a future DH-EX-014B UI reads this to render "Version 1 / Version 2 / ..." history."""
        with self._sessions() as session:
            versions = session.scalars(
                select(LabResultReleaseVersion)
                .where(LabResultReleaseVersion.lab_order_id == order_id)
                .order_by(LabResultReleaseVersion.version_number)
            ).all()
            return tuple(_to_release_version_record(v) for v in versions)

    def get_release_version_history_with_reviews(
        self, order_id: uuid.UUID
    ) -> tuple[tuple[LabResultReleaseVersionRecord, DoctorReviewRecord | None], ...]:
        """DH-EX-014B: every release version and its associated DoctorReview for this order,
        oldest first.

        Single joined query on ``DoctorReview.release_version_id == LabResultReleaseVersion.id``,
        eliminating N+1 query scaling while preserving exact explicit release_version_id binding
        and ordering by version_number (never inferring associations from timestamps).
        """
        with self._sessions() as session:
            rows = session.execute(
                select(LabResultReleaseVersion, DoctorReview)
                .outerjoin(
                    DoctorReview, DoctorReview.release_version_id == LabResultReleaseVersion.id
                )
                .where(LabResultReleaseVersion.lab_order_id == order_id)
                .order_by(LabResultReleaseVersion.version_number)
            ).all()
            return tuple(
                (
                    _to_release_version_record(version),
                    None if review is None else _to_review_record(review),
                )
                for version, review in rows
            )

    def get_release_version(
        self, release_version_id: uuid.UUID
    ) -> LabResultReleaseVersionRecord | None:
        """DH-EX-014A: fetch one release version by its own immutable id. Callers are responsible
        for their own lab/provider-tenant authorization check before calling this, exactly
        mirroring ``LabResultRepository.get_correction_history``'s identical convention."""
        with self._sessions() as session:
            version = session.get(LabResultReleaseVersion, release_version_id)
            return None if version is None else _to_release_version_record(version)

    def get_manifest_for_order(self, order_id: uuid.UUID) -> ReleaseVersionManifestRecord | None:
        """DH-EX-014A: the *latest* released version's exact manifest for this order, or ``None``
        if this order has no release version at all -- callers must fall back to
        ``LabResultRepository.list_for_order``'s live-effective-observation read for that case
        (a grandfathered order, which cannot have a release version by construction). **Historical
        reconstruction, not a live read:** observations are fetched by the manifest's own recorded
        ``lab_observation_id``, never filtered by that observation's current ``superseded_at`` --
        this is the one deliberate difference from every other read in this module."""
        with self._sessions() as session:
            version = session.scalar(
                select(LabResultReleaseVersion)
                .where(LabResultReleaseVersion.lab_order_id == order_id)
                .order_by(LabResultReleaseVersion.version_number.desc())
                .limit(1)
            )
            if version is None:
                return None
            return self._manifest_record_for_version(session, version)

    def get_manifest_for_version(
        self, release_version_id: uuid.UUID
    ) -> ReleaseVersionManifestRecord | None:
        """DH-EX-014A: the exact manifest for one specific release version by id -- the read a
        future "Version 1 / Version 2" history UI uses once more than one version exists. Same
        historical-reconstruction semantic as ``get_manifest_for_order``."""
        with self._sessions() as session:
            version = session.get(LabResultReleaseVersion, release_version_id)
            if version is None:
                return None
            return self._manifest_record_for_version(session, version)

    def _manifest_record_for_version(
        self, session: Session, version: LabResultReleaseVersion
    ) -> ReleaseVersionManifestRecord:
        return _manifest_record_for_version(session, version)


class LabResultAmendmentRepository:
    """DH-EX-014B/ADR-048 Model 3: post-release amendment preparation and release, layered entirely
    on top of a ``COMPLETED`` order -- never reopens it, never touches
    ``LabResultRelease``/Version 1, never adds a new ``LabOrderStatus``. Lock order matches every
    other mutation in this module exactly: ``LabOrder FOR UPDATE`` first, then the amendment row,
    then (for a correction) the item/container/observation, then (for a release) the new version/
    manifest/review -- two concurrent callers for the same order fully serialize at the first lock,
    identical to ``_lock_order_and_item_for_entry``'s own established discipline.

    **Deliberately does not reuse ``_lock_order_and_item_for_entry``** -- that preamble requires
    ``IN_PROGRESS`` and is exactly the DH-EX-013 pre-release gate this ticket must never loosen
    (required negative control 3). Amendment mutations require the *opposite* order status
    (``COMPLETED``) plus their own amendment-state checks, so they get their own preamble, parallel
    to it, never a modified branch of it."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    # -- start ------------------------------------------------------------------------------------

    def start_amendment(
        self,
        *,
        order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        started_by_identity_id: uuid.UUID,
        session: Session | None = None,
    ) -> LabResultAmendmentRecord:
        if session is not None:
            return self._start_amendment_in_session(
                session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                started_by_identity_id=started_by_identity_id,
            )
        with self._sessions.begin() as owned_session:
            return self._start_amendment_in_session(
                owned_session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                started_by_identity_id=started_by_identity_id,
            )

    def _start_amendment_in_session(
        self,
        session: Session,
        *,
        order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        started_by_identity_id: uuid.UUID,
    ) -> LabResultAmendmentRecord:
        order = session.scalar(select(LabOrder).where(LabOrder.id == order_id).with_for_update())
        if order is None:
            raise LabOrderNotAssigned(str(order_id))

        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        if selection is None or selection.lab_organization_id != lab_organization_id:
            raise LabOrderNotAssigned(str(order_id))

        if LabOrderStatus(order.status) != LabOrderStatus.COMPLETED:
            raise OrderNotCompletedForAmendment(
                f"LabOrder {order_id} is {order.status!r}, not 'COMPLETED' -- an amendment may "
                f"only be prepared against an order whose original results have already been "
                f"released"
            )

        latest_version = session.scalar(
            select(LabResultReleaseVersion)
            .where(LabResultReleaseVersion.lab_order_id == order_id)
            .order_by(LabResultReleaseVersion.version_number.desc())
            .limit(1)
        )
        if latest_version is None:
            raise LegacyCompletedOrderHasNoRelease(
                f"LabOrder {order_id} is 'COMPLETED' but has no release version -- a "
                f"grandfathered order with no explicit LabResultRelease is not amendment-eligible "
                f"(ADR-048 Decision 6); DHealth will not fabricate a historical release/version "
                f"for it"
            )

        existing_preparing = session.scalar(
            select(LabResultAmendment).where(
                LabResultAmendment.lab_order_id == order_id,
                LabResultAmendment.status == LabResultAmendmentStatus.PREPARING.value,
            )
        )
        if existing_preparing is not None:
            raise AmendmentAlreadyInProgress(
                f"LabOrder {order_id} already has a PREPARING amendment "
                f"({existing_preparing.id}, "
                f"amendment_number={existing_preparing.amendment_number}) -- at most one open "
                f"amendment may exist per order at a time"
            )

        next_number = (
            session.scalar(
                select(func.coalesce(func.max(LabResultAmendment.amendment_number), 0)).where(
                    LabResultAmendment.lab_order_id == order_id
                )
            )
            + 1
        )

        amendment = LabResultAmendment(
            lab_order_id=order_id,
            amendment_number=next_number,
            base_release_version_id=latest_version.id,
            status=LabResultAmendmentStatus.PREPARING.value,
            started_by_identity_id=started_by_identity_id,
            started_at=utc_now(),
        )
        try:
            with session.begin_nested():
                session.add(amendment)
                session.flush()
        except IntegrityError:
            # Defensive last line only, mirroring every other container-creation pattern in this
            # module -- the LabOrder FOR UPDATE lock above already serializes concurrent start
            # attempts for this exact order down to one winner, so this should never actually fire
            # in practice; the partial unique index is the real backstop for anything that reaches
            # this table any other way.
            raise AmendmentAlreadyInProgress(
                f"LabOrder {order_id} already has a PREPARING amendment (constraint-detected)"
            ) from None
        return _to_amendment_record(amendment)

    # -- amendment-scoped observation correction --------------------------------------------------

    def correct_observation(
        self,
        *,
        amendment_id: uuid.UUID,
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
        """DH-EX-014B: supersedes a currently-effective observation with one new, immutable
        ``LabObservation`` row carrying ``amendment_id`` -- immediate/permanent revision semantics
        (ADR-048 Decision 4), never a discardable draft. Never mutates the predecessor's value; the
        only field ever written on it is ``superseded_at``, identical to DH-EX-013's own
        pre-release correction.

        **The eligibility predicate, in one check, not four:** the predecessor must be currently
        effective (``superseded_at IS NULL``) *and* either (a) named in this amendment's own
        ``base_release_version_id`` manifest, or (b) itself carry this exact amendment's id. (a)
        covers every observation unchanged since the base release; (b) covers reconsidering an
        already-amended-but-not-yet-released value within the same episode (B1 released -> B2
        amendment revision -> B3 second amendment revision before release, all three permanently
        preserved). This single predicate rejects, without a special case for each: an older
        historical release observation (not in the base manifest, no matching amendment_id), an
        already-superseded observation (caught by the effective check first), an observation
        belonging to a different amendment episode (not in *this* amendment's base manifest -- it
        may be in a later one -- and its ``amendment_id`` is not this amendment's), and any
        cross-order/cross-item observation (rejected earlier by the item/container ownership check
        below, before this predicate is ever evaluated)."""
        if session is not None:
            return self._correct_observation_in_session(
                session,
                amendment_id=amendment_id,
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
                amendment_id=amendment_id,
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
        amendment_id: uuid.UUID,
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
        order = session.scalar(select(LabOrder).where(LabOrder.id == order_id).with_for_update())
        if order is None:
            raise LabOrderNotAssigned(str(order_id))

        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        if selection is None or selection.lab_organization_id != lab_organization_id:
            raise LabOrderNotAssigned(str(order_id))

        if LabOrderStatus(order.status) != LabOrderStatus.COMPLETED:
            raise OrderNotCompletedForAmendment(
                f"LabOrder {order_id} is {order.status!r}, not 'COMPLETED'"
            )

        amendment = session.get(LabResultAmendment, amendment_id)
        if amendment is None or amendment.lab_order_id != order_id:
            raise AmendmentNotFound(str(amendment_id))
        if LabResultAmendmentStatus(amendment.status) != LabResultAmendmentStatus.PREPARING:
            raise AmendmentNotPreparing(
                f"LabResultAmendment {amendment_id} is {amendment.status!r}, not 'PREPARING'"
            )

        item = session.get(LabOrderItem, lab_order_item_id)
        if item is None or item.lab_order_id != order_id:
            raise OrderItemNotFound(str(lab_order_item_id))

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
                f"LabObservation {observation_id} is no longer effective -- an amendment "
                f"correction must start from the currently effective observation"
            )

        in_base_manifest = session.scalar(
            select(LabResultReleaseManifestEntry.id).where(
                LabResultReleaseManifestEntry.release_version_id
                == amendment.base_release_version_id,
                LabResultReleaseManifestEntry.lab_observation_id == predecessor.id,
            )
        )
        eligible = in_base_manifest is not None or predecessor.amendment_id == amendment.id
        if not eligible:
            raise ObservationNotEligibleForAmendment(
                f"LabObservation {observation_id} is neither part of amendment "
                f"{amendment_id}'s base release version manifest nor a revision already created "
                f"by this same amendment -- refusing to start a correction from it"
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
            amendment_id=amendment.id,
        )
        try:
            with session.begin_nested():
                session.add(successor)
                predecessor.superseded_at = utc_now()
                session.flush()
        except IntegrityError:
            # Defensive last line only -- see `LabResultRepository.correct_observation`'s
            # identical pattern for why this is structurally unreachable in practice given the
            # LabOrder FOR UPDATE lock acquired above.
            raise StaleObservationVersion(
                f"LabObservation {observation_id} is no longer effective (constraint-detected)"
            ) from None
        return _to_observation_record(successor)

    # -- inspection -------------------------------------------------------------------------------

    def get_amendment(self, amendment_id: uuid.UUID) -> LabResultAmendmentRecord | None:
        """DH-EX-014B: fetch one amendment by its own immutable id. Callers are responsible for
        their own lab/tenant authorization check before calling this, exactly mirroring
        ``LabResultReleaseRepository.get_release_version``'s identical convention."""
        with self._sessions() as session:
            amendment = session.get(LabResultAmendment, amendment_id)
            return None if amendment is None else _to_amendment_record(amendment)

    def get_amendment_inspection(self, amendment_id: uuid.UUID) -> AmendmentInspectionRecord | None:
        """Return the truthful inspection for one amendment episode.

        A PREPARING episode shows the order's current effective proposal and may be ready to
        release. A RELEASED episode is reconstructed from the immutable release manifest bound to
        that exact amendment, never from later live state; its readiness is always false because
        the release action is already complete. Callers perform the lab/tenant check first.
        """
        with self._sessions() as session:
            amendment = session.get(LabResultAmendment, amendment_id)
            if amendment is None:
                return None

            if LabResultAmendmentStatus(amendment.status) == LabResultAmendmentStatus.RELEASED:
                version = session.scalar(
                    select(LabResultReleaseVersion).where(
                        LabResultReleaseVersion.amendment_id == amendment.id
                    )
                )
                if version is None:
                    raise AmendmentNotReadyForRelease(
                        f"released LabResultAmendment {amendment.id} has no release version"
                    )
                manifest = _manifest_record_for_version(session, version)
                return AmendmentInspectionRecord(
                    amendment=_to_amendment_record(amendment),
                    items=tuple(
                        AmendmentInspectionItemRecord(
                            item_id=item.item_id,
                            catalog_code_snapshot=item.catalog_code_snapshot,
                            catalog_display_name_snapshot=item.catalog_display_name_snapshot,
                            entry_completed_at=item.entry_completed_at,
                            observations=item.observations,
                            changed_in_this_amendment=any(
                                observation.amendment_id == amendment.id
                                for observation in item.observations
                            ),
                        )
                        for item in manifest.items
                    ),
                    ready_to_release=False,
                )

            rows = session.execute(
                select(LabOrderItem, LabResult)
                .outerjoin(LabResult, LabResult.lab_order_item_id == LabOrderItem.id)
                .where(LabOrderItem.lab_order_id == amendment.lab_order_id)
            ).all()
            if any(
                container is None or container.entry_completed_at is None
                for _item, container in rows
            ):
                raise AmendmentNotReadyForRelease(
                    f"LabResultAmendment {amendment.id} cannot be inspected because its completed "
                    f"order has a missing or incomplete result container"
                )
            container_ids = [container.id for _item, container in rows if container is not None]
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

            items = tuple(
                AmendmentInspectionItemRecord(
                    item_id=item.id,
                    catalog_code_snapshot=item.catalog_code_snapshot,
                    catalog_display_name_snapshot=item.catalog_display_name_snapshot,
                    entry_completed_at=container.entry_completed_at,  # type: ignore[union-attr,arg-type]
                    observations=tuple(
                        _to_observation_record(o)
                        for o in observations_by_result.get(container.id, [])
                    )
                    if container is not None
                    else (),
                    changed_in_this_amendment=any(
                        o.amendment_id == amendment.id
                        for o in (observations_by_result.get(container.id, []) if container else [])
                    ),
                )
                for item, container in rows
            )
            return AmendmentInspectionRecord(
                amendment=_to_amendment_record(amendment),
                items=items,
                ready_to_release=any(item.changed_in_this_amendment for item in items),
            )

    # -- release --------------------------------------------------------------------------------

    def release_amendment(
        self,
        *,
        amendment_id: uuid.UUID,
        order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        released_by_identity_id: uuid.UUID,
        session: Session | None = None,
    ) -> tuple[LabResultReleaseVersionRecord, bool]:
        """Returns ``(record, created)``. ``created=False`` means an already-``RELEASED`` amendment
        was returned as a safe idempotent replay -- unconditionally, regardless of who is retrying,
        mirroring ``LabResultReleaseRepository.release``'s identical replay discipline exactly: the
        fact is who actually released it first, never rewritten by a later retry."""
        if session is not None:
            return self._release_amendment_in_session(
                session,
                amendment_id=amendment_id,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                released_by_identity_id=released_by_identity_id,
            )
        with self._sessions.begin() as owned_session:
            return self._release_amendment_in_session(
                owned_session,
                amendment_id=amendment_id,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                released_by_identity_id=released_by_identity_id,
            )

    def _release_amendment_in_session(
        self,
        session: Session,
        *,
        amendment_id: uuid.UUID,
        order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        released_by_identity_id: uuid.UUID,
    ) -> tuple[LabResultReleaseVersionRecord, bool]:
        order = session.scalar(select(LabOrder).where(LabOrder.id == order_id).with_for_update())
        if order is None:
            raise LabOrderNotAssigned(str(order_id))

        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        if selection is None or selection.lab_organization_id != lab_organization_id:
            raise LabOrderNotAssigned(str(order_id))

        amendment = session.scalar(
            select(LabResultAmendment)
            .where(LabResultAmendment.id == amendment_id)
            .with_for_update()
        )
        if amendment is None or amendment.lab_order_id != order_id:
            raise AmendmentNotFound(str(amendment_id))

        if LabResultAmendmentStatus(amendment.status) == LabResultAmendmentStatus.RELEASED:
            existing_version = session.scalar(
                select(LabResultReleaseVersion).where(
                    LabResultReleaseVersion.amendment_id == amendment.id
                )
            )
            if existing_version is None:
                # Structurally unreachable given `uq_exchange_release_versions_amendment_id` and
                # this method's own atomic transaction -- an amendment can only ever reach
                # RELEASED in the same transaction that created its bound version.
                raise RuntimeError(
                    f"LabResultAmendment {amendment_id} is RELEASED but has no bound "
                    f"LabResultReleaseVersion -- data integrity violation"
                )
            return _to_release_version_record(existing_version), False

        if LabOrderStatus(order.status) != LabOrderStatus.COMPLETED:
            raise OrderNotCompletedForAmendment(
                f"LabOrder {order_id} is {order.status!r}, not 'COMPLETED'"
            )

        has_changes = (
            session.scalar(
                select(func.count())
                .select_from(LabObservation)
                .where(LabObservation.amendment_id == amendment.id)
            )
            > 0
        )
        if not has_changes:
            raise AmendmentNotReadyForRelease(
                f"LabResultAmendment {amendment_id} has zero observation revisions -- an "
                f"amendment cannot release with no changes"
            )

        latest_version = session.scalar(
            select(LabResultReleaseVersion)
            .where(LabResultReleaseVersion.lab_order_id == order_id)
            .order_by(LabResultReleaseVersion.version_number.desc())
            .limit(1)
        )
        if latest_version is None or latest_version.id != amendment.base_release_version_id:
            raise AmendmentBaseVersionSuperseded(
                f"LabResultAmendment {amendment_id}'s base_release_version_id no longer matches "
                f"the order's latest release version -- refusing to release against a baseline "
                f"this amendment was never actually prepared against"
            )

        # Re-checked here for the identical defense-in-depth reason the original release() checks
        # it -- structurally unreachable (amendment corrections only ever supersede an existing
        # effective observation, never remove one, and entry_completed_at is never cleared), but
        # never trusted absent rather than proven absent.
        missing_count = _count_missing_or_malformed_results(session, order_id)
        if missing_count:
            raise ResultsNotReadyForRelease(
                f"cannot release amendment for LabOrder {order_id} -- {missing_count} ordered "
                f"item(s) no longer have a completed, observation-backed result entry"
            )

        released_at = utc_now()
        new_version_number = latest_version.version_number + 1
        version = LabResultReleaseVersion(
            lab_order_id=order_id,
            version_number=new_version_number,
            lab_result_release_id=None,
            amendment_id=amendment.id,
            created_at=released_at,
        )
        session.add(version)
        session.flush()

        # DH-EX-014B: the complete, newly-released manifest -- every unchanged observation carried
        # forward (referenced again, never copied) plus every amendment-created revision now
        # replacing what it superseded. Identical shared query to the original release's own
        # manifest construction (`_effective_observation_rows`) -- structurally cannot omit an
        # unaffected item merely because another item changed.
        effective_rows = _effective_observation_rows(session, order_id)

        base_manifest_items = session.scalars(
            select(LabResultReleaseManifestEntry.lab_observation_id).where(
                LabResultReleaseManifestEntry.release_version_id
                == amendment.base_release_version_id
            )
        ).all()
        base_observation_ids = set(base_manifest_items)

        for item_id, observation_id in effective_rows:
            if observation_id not in base_observation_ids:
                obs = session.get(LabObservation, observation_id)
                if obs is None or obs.amendment_id != amendment.id:
                    raise AmendmentReleaseIntegrityError(
                        f"cannot release amendment {amendment_id} for LabOrder {order_id} -- "
                        f"effective observation {observation_id} is not present in base release "
                        f"manifest {amendment.base_release_version_id} and is not attributed to "
                        f"this amendment (amendment_id={getattr(obs, 'amendment_id', None)})"
                    )
            session.add(
                LabResultReleaseManifestEntry(
                    release_version_id=version.id,
                    lab_order_item_id=item_id,
                    lab_observation_id=observation_id,
                )
            )

        amendment.status = LabResultAmendmentStatus.RELEASED.value
        amendment.released_by_identity_id = released_by_identity_id
        amendment.released_at = released_at

        # DH-EX-014B/ADR-048 Decision 2 (ADR-045 Question 4, Option C): always a brand-new PENDING
        # row, never a bind of an existing one -- unlike the original release (which binds the
        # order's one pre-existing unbound row), no unbound row exists at this point, and the
        # prior version's review row is never reset, rewritten, or reused.
        review = DoctorReview(
            lab_order_id=order_id,
            release_version_id=version.id,
            status=DoctorReviewStatus.PENDING.value,
        )
        session.add(review)
        session.flush()

        return _to_release_version_record(version), True


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
    """ADR-048 version-bound clinician review history.

    Reads may resolve the latest review by explicit ``version_number`` ordering. Mutations are
    stricter: the version-targeted methods bind to exactly the release version the caller names;
    legacy order-only methods remain compatible only while the order has no amendment release.
    Every mutation locks ``LabOrder`` first, matching amendment release's lock order, so a version
    cannot appear between the compatibility check and the review write.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    @staticmethod
    def _current_review_id_query(lab_order_id: uuid.UUID):
        return (
            select(DoctorReview.id)
            .outerjoin(
                LabResultReleaseVersion,
                LabResultReleaseVersion.id == DoctorReview.release_version_id,
            )
            .where(DoctorReview.lab_order_id == lab_order_id)
            .order_by(func.coalesce(LabResultReleaseVersion.version_number, 0).desc())
            .limit(1)
        )

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

    def mark_no_follow_up_required_for_release_version(
        self,
        lab_order_id: uuid.UUID,
        release_version_id: uuid.UUID,
        *,
        reviewed_by_practitioner_reference_id: uuid.UUID,
        session: Session | None = None,
    ) -> DoctorReviewRecord:
        return self._mark(
            lab_order_id,
            DoctorReviewStatus.NO_FOLLOW_UP_REQUIRED,
            reviewed_by_practitioner_reference_id,
            session,
            release_version_id=release_version_id,
        )

    def mark_follow_up_required_for_release_version(
        self,
        lab_order_id: uuid.UUID,
        release_version_id: uuid.UUID,
        *,
        reviewed_by_practitioner_reference_id: uuid.UUID,
        session: Session | None = None,
    ) -> DoctorReviewRecord:
        return self._mark(
            lab_order_id,
            DoctorReviewStatus.FOLLOW_UP_REQUIRED,
            reviewed_by_practitioner_reference_id,
            session,
            release_version_id=release_version_id,
        )

    def _mark(
        self,
        lab_order_id: uuid.UUID,
        status: DoctorReviewStatus,
        reviewed_by_practitioner_reference_id: uuid.UUID,
        session: Session | None,
        *,
        release_version_id: uuid.UUID | None = None,
    ) -> DoctorReviewRecord:
        if session is not None:
            return self._mark_in_session(
                session,
                lab_order_id,
                status,
                reviewed_by_practitioner_reference_id,
                release_version_id=release_version_id,
            )
        with self._sessions.begin() as owned_session:
            return self._mark_in_session(
                owned_session,
                lab_order_id,
                status,
                reviewed_by_practitioner_reference_id,
                release_version_id=release_version_id,
            )

    def _mark_in_session(
        self,
        session: Session,
        lab_order_id: uuid.UUID,
        status: DoctorReviewStatus,
        reviewed_by_practitioner_reference_id: uuid.UUID,
        *,
        release_version_id: uuid.UUID | None,
    ) -> DoctorReviewRecord:
        # Amendment release locks the same row first. This prevents a new release version from
        # appearing between an order-only request's compatibility check and its review write.
        order = session.scalar(
            select(LabOrder).where(LabOrder.id == lab_order_id).with_for_update()
        )
        if order is None:
            raise ReviewNotFound(str(lab_order_id))
        if LabOrderStatus(order.status) != LabOrderStatus.COMPLETED:
            raise ReviewNotAllowedYet(
                f"LabOrder {lab_order_id} is {order.status!r}, not 'COMPLETED' -- a doctor "
                f"review may only be recorded once every requested result is in"
            )

        if release_version_id is None:
            latest_version_number = session.scalar(
                select(LabResultReleaseVersion.version_number)
                .where(LabResultReleaseVersion.lab_order_id == lab_order_id)
                .order_by(LabResultReleaseVersion.version_number.desc())
                .limit(1)
            )
            if latest_version_number is not None and latest_version_number > 1:
                raise ExplicitReviewVersionRequired(
                    f"LabOrder {lab_order_id} has amendment releases; review must name the exact "
                    f"release_version_id whose content was reviewed"
                )
            review_id = session.scalar(self._current_review_id_query(lab_order_id))
        else:
            review_id = session.scalar(
                select(DoctorReview.id).where(
                    DoctorReview.lab_order_id == lab_order_id,
                    DoctorReview.release_version_id == release_version_id,
                )
            )
        if review_id is None:
            raise ReviewNotFound(str(release_version_id or lab_order_id))
        review = session.scalar(
            select(DoctorReview).where(DoctorReview.id == review_id).with_for_update()
        )
        if review is None:
            raise ReviewNotFound(str(lab_order_id))
        if DoctorReviewStatus(review.status) != DoctorReviewStatus.PENDING:
            raise ReviewAlreadyFinalized(
                f"DoctorReview for LabOrder {lab_order_id}, release version "
                f"{review.release_version_id}, is already {review.status!r}; refusing to "
                f"overwrite the earlier disposition"
            )

        review.status = status.value
        review.reviewed_at = utc_now()
        review.reviewed_by_practitioner_reference_id = reviewed_by_practitioner_reference_id
        session.flush()
        return _to_review_record(review)

    def get_for_order(self, lab_order_id: uuid.UUID) -> DoctorReviewRecord:
        """DH-EX-014A: resolves to the review bound to the order's latest release version -- see
        this class's own docstring. Order-oriented signature kept unchanged for backward
        compatibility; internally version-aware."""
        with self._sessions() as session:
            review_id = session.scalar(self._current_review_id_query(lab_order_id))
            if review_id is None:
                raise ReviewNotFound(str(lab_order_id))
            review = session.get(DoctorReview, review_id)
            if review is None:
                raise ReviewNotFound(str(lab_order_id))
            return _to_review_record(review)

    def get_for_release_version(self, release_version_id: uuid.UUID) -> DoctorReviewRecord | None:
        """Return the review obligation for one immutable release version by id.

        Callers are responsible for their own tenant authorization check first, exactly mirroring
        every other by-id read in this module.
        """
        with self._sessions() as session:
            review = session.scalar(
                select(DoctorReview).where(DoctorReview.release_version_id == release_version_id)
            )
            return None if review is None else _to_review_record(review)

    def get_for_release_versions(
        self, release_version_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, DoctorReviewRecord]:
        """DH-EX-014B: batch-fetch DoctorReview records for multiple release versions,
        keyed by release_version_id.

        Batch-fetches in a single query via IN clause on release_version_id, preserving
        exact explicit release_version_id binding without N+1 query scaling.
        """
        if not release_version_ids:
            return {}
        with self._sessions() as session:
            rows = session.scalars(
                select(DoctorReview).where(DoctorReview.release_version_id.in_(release_version_ids))
            ).all()
            return {
                r.release_version_id: _to_review_record(r)
                for r in rows
                if r.release_version_id is not None
            }

    def get_history_for_order(self, lab_order_id: uuid.UUID) -> tuple[DoctorReviewRecord, ...]:
        """Every review row that has ever existed for this order, ordered by the
        release version it is bound to (the one unbound legacy row, if present, sorts first) --
        never inferred from ``reviewed_at``. One row exists per released version; a grandfathered
        order may instead have the one honest unbound legacy row."""
        with self._sessions() as session:
            rows = session.scalars(
                select(DoctorReview)
                .outerjoin(
                    LabResultReleaseVersion,
                    LabResultReleaseVersion.id == DoctorReview.release_version_id,
                )
                .where(DoctorReview.lab_order_id == lab_order_id)
                .order_by(func.coalesce(LabResultReleaseVersion.version_number, 0))
            ).all()
            return tuple(_to_review_record(r) for r in rows)


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


def _manifest_record_for_version(
    session: Session, version: LabResultReleaseVersion
) -> ReleaseVersionManifestRecord:
    """Reconstruct one immutable release manifest, including superseded historical observations.

    Shared by provider version reads and released-amendment inspection so neither can accidentally
    fall back to the order's later current-effective state.
    """
    rows = session.execute(
        select(LabOrderItem, LabResult, LabObservation)
        .select_from(LabResultReleaseManifestEntry)
        .join(
            LabOrderItem,
            LabResultReleaseManifestEntry.lab_order_item_id == LabOrderItem.id,
        )
        .join(LabResult, LabResult.lab_order_item_id == LabOrderItem.id)
        .join(
            LabObservation,
            LabResultReleaseManifestEntry.lab_observation_id == LabObservation.id,
        )
        .where(LabResultReleaseManifestEntry.release_version_id == version.id)
        .order_by(LabOrderItem.id, LabObservation.recorded_at)
    ).all()
    observations_by_item: dict[uuid.UUID, list[LabObservation]] = {}
    item_by_id: dict[uuid.UUID, LabOrderItem] = {}
    entry_completed_at_by_item: dict[uuid.UUID, datetime] = {}
    for item, container, observation in rows:
        if container.entry_completed_at is None:
            raise ResultsNotReadyForRelease(
                f"release version {version.id} references an incomplete result container"
            )
        observations_by_item.setdefault(item.id, []).append(observation)
        item_by_id[item.id] = item
        entry_completed_at_by_item[item.id] = container.entry_completed_at
    items = tuple(
        ReleaseManifestItemRecord(
            item_id=item_id,
            catalog_code_snapshot=item_by_id[item_id].catalog_code_snapshot,
            catalog_display_name_snapshot=item_by_id[item_id].catalog_display_name_snapshot,
            entry_completed_at=entry_completed_at_by_item[item_id],
            observations=tuple(_to_observation_record(o) for o in observations),
        )
        for item_id, observations in observations_by_item.items()
    )
    return ReleaseVersionManifestRecord(version=_to_release_version_record(version), items=items)


def _effective_observation_rows(
    session: Session, order_id: uuid.UUID
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """DH-EX-014A/DH-EX-014B: the exact ``(lab_order_item_id, lab_observation_id)`` effective set
    for every item on this order right now -- shared by ``LabResultReleaseRepository``'s original
    release and ``LabResultAmendmentRepository``'s amendment release, so both build a manifest from
    the identical query, never two independently-maintained copies that could silently drift apart.
    Callers are responsible for holding the ``LabOrder`` row's ``FOR UPDATE`` lock first, exactly
    mirroring ``_count_missing_or_malformed_results``'s own convention."""
    return session.execute(
        select(LabOrderItem.id, LabObservation.id)
        .select_from(LabOrderItem)
        .join(LabResult, LabResult.lab_order_item_id == LabOrderItem.id)
        .join(
            LabObservation,
            (LabObservation.lab_result_id == LabResult.id)
            & (LabObservation.superseded_at.is_(None)),
        )
        .where(LabOrderItem.lab_order_id == order_id)
    ).all()


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
        amendment_id=observation.amendment_id,
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
        release_version_id=review.release_version_id,
        status=review.status,
        reviewed_at=review.reviewed_at,
        reviewed_by_practitioner_reference_id=review.reviewed_by_practitioner_reference_id,
    )


def _to_release_version_record(version: LabResultReleaseVersion) -> LabResultReleaseVersionRecord:
    return LabResultReleaseVersionRecord(
        id=version.id,
        lab_order_id=version.lab_order_id,
        version_number=version.version_number,
        lab_result_release_id=version.lab_result_release_id,
        amendment_id=version.amendment_id,
        created_at=_as_utc(version.created_at),
    )


def _to_amendment_record(amendment: LabResultAmendment) -> LabResultAmendmentRecord:
    return LabResultAmendmentRecord(
        id=amendment.id,
        lab_order_id=amendment.lab_order_id,
        amendment_number=amendment.amendment_number,
        base_release_version_id=amendment.base_release_version_id,
        status=amendment.status,
        started_by_identity_id=amendment.started_by_identity_id,
        started_at=_as_utc(amendment.started_at),
        released_by_identity_id=amendment.released_by_identity_id,
        released_at=None if amendment.released_at is None else _as_utc(amendment.released_at),
    )


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive round trips to the stored UTC contract."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
