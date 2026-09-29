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

**DHEALTH-LAB-BLOCKED-001 addition.** ``block``/``resolve_block`` implement
`docs/NATIVE-OPERATING-PRODUCT.md` §16's P0-4: an order this laboratory took and cannot currently
continue gets an honest status instead of sitting in a queue state that claims progress nobody is
making. Unlike ``record_collection``, this *does* move ``LabOrder.status`` -- to ``BLOCKED`` and,
on resolution, back to whatever it genuinely was before -- and it does so together with a
``LabOrderBlock`` episode written in the same transaction. Neither half is ever written alone: a
``BLOCKED`` order with no episode cannot say why it stopped, and an unresolved episode on a
non-``BLOCKED`` order would contradict the status.

Three properties are worth stating here rather than leaving to be inferred:

* **Blocking enforces nothing new.** Every mutation that must not happen while blocked -- accept,
  collection, start-processing, result entry, entry completion, release -- is already gated on a
  specific status (``SELECTED``, ``ACCEPTED``, ``IN_PROGRESS``), so an order in ``BLOCKED``
  fails all of them through the checks that already existed. That is the reason ``BLOCKED`` is a
  status rather than a flag beside one: a flag would have required the same refusal to be
  remembered and re-added in every one of those places, and forgotten in exactly one.
* **The resume state is the server's, recorded at block time.** ``blocked_from_status`` is copied
  off the locked order row, never accepted from a caller, and a resolution restores that value and
  nothing else. See ``LabOrderBlock``'s own docstring for what a client-chosen resume destination
  would let somebody fabricate.
* **Resolution is addressed by episode, not by order.** ``resolve_block`` takes the episode's own
  id, so a retried or long-delayed resolution can only ever resolve the episode its caller
  actually saw -- never a *second*, later block that happened to be active by the time the retry
  arrived.

Lock discipline is this module's existing one, unchanged: ``LabOrder`` ``FOR UPDATE`` first, then
the ``LabSelection`` assignment read. That lock is what serializes two workers blocking the same
order simultaneously; ``LabOrderBlock``'s partial unique index is the second layer behind it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from daher_adapter.exchange.models import (
    BLOCKABLE_ORDER_STATUSES,
    LabOrder,
    LabOrderBlock,
    LabOrderBlockReason,
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


class ConflictingBlockRequest(RuntimeError):
    """DHEALTH-LAB-BLOCKED-001: this order is already blocked, and the incoming request asks for a
    *different* reason or note than the active episode records.

    Refused rather than silently returning the existing episode, and rather than quietly rewriting
    it. Both alternatives are wrong in the same way `IdempotencyKeyConflict` is wrong in
    ``exchange/orders.py``: a retry of the request that already won is a replay, but a genuinely
    different request that happens to arrive second is a real conflict, and answering it with
    somebody else's block would tell the caller their reason was recorded when it was not. Maps to
    HTTP 409."""


class BlockEpisodeNotFound(RuntimeError):
    """No ``LabOrderBlock`` matches the given id *on this order at this laboratory* -- one error
    for all three cases, never disclosing that an episode id exists on an order this caller may
    not see (the same anti-enumeration convention ``LabOrderNotAssigned`` already applies). Maps
    to HTTP 404."""


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
    active_block: LabOrderBlockRecord | None = None
    """DHEALTH-LAB-BLOCKED-001: the unresolved block episode, when there is one. ``None`` and
    ``status != 'BLOCKED'`` always agree -- both halves are written in one transaction -- so a
    caller may read either, and neither can be stale with respect to the other. Resolved episodes
    are deliberately absent here: this is the queue's "what is wrong right now" field, and the
    history behind it is its own bounded read (``list_blocks_for_order``)."""


@dataclass(frozen=True, slots=True)
class LabOrderBlockRecord:
    """DHEALTH-LAB-BLOCKED-001: one block episode, exactly as stored.

    This is the **laboratory's own** view and it carries everything, including the free-text notes
    and the actor identities. What a provider or a patient is allowed to see is a strictly smaller
    projection built at the HTTP boundary (``exchange/api.py``), never this record handed onward
    -- keeping the narrowing at one visible place rather than trusting every future caller to
    remember which fields are internal."""

    id: uuid.UUID
    lab_order_id: uuid.UUID
    lab_organization_id: uuid.UUID
    reason: str
    note: str | None
    blocked_from_status: str
    blocked_at: datetime
    blocked_by_identity_id: uuid.UUID
    resolved_at: datetime | None
    resolved_by_identity_id: uuid.UUID | None
    resolution_note: str | None

    @property
    def active(self) -> bool:
        return self.resolved_at is None


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
            return _to_record(order, selection, collection, _get_active_block(session, order_id))

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
                select(LabOrder, LabSelection, LabSampleCollection, LabOrderBlock)
                .join(LabSelection, LabSelection.lab_order_id == LabOrder.id)
                .outerjoin(LabSampleCollection, LabSampleCollection.lab_order_id == LabOrder.id)
                # DHEALTH-LAB-BLOCKED-001: folded into this same query for exactly the reason
                # DH-EX-011 folded collection in -- never a second per-row query. The `resolved_at
                # IS NULL` half of the ON clause is what keeps this one-row-per-order: without it
                # an order with a long block history would multiply its row once per episode.
                # `uq_exchange_lab_order_blocks_one_active_per_order` is what guarantees the
                # filtered join can match at most once.
                .outerjoin(
                    LabOrderBlock,
                    (LabOrderBlock.lab_order_id == LabOrder.id)
                    & (LabOrderBlock.resolved_at.is_(None)),
                )
                .where(LabSelection.lab_organization_id == lab_organization_id)
                .options(selectinload(LabOrder.items))
            )
            if status_filter is not None:
                query = query.where(LabOrder.status == status_filter.value)
            query = query.order_by(LabOrder.updated_at.desc(), LabOrder.id.desc()).limit(limit)
            rows = session.execute(query).all()
            return [
                _to_record(order, selection, collection, block)
                for order, selection, collection, block in rows
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
            return (
                _to_record(
                    order,
                    selection,
                    _get_collection(session, order_id),
                    _get_active_block(session, order_id),
                ),
                False,
            )
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
        return (
            _to_record(
                order,
                selection,
                _get_collection(session, order_id),
                _get_active_block(session, order_id),
            ),
            True,
        )

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

    # -- DHEALTH-LAB-BLOCKED-001: the cannot-proceed workflow -------------------------------

    def block(
        self,
        *,
        order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        reason: LabOrderBlockReason,
        blocked_by_identity_id: uuid.UUID,
        note: str | None = None,
        session: Session | None = None,
    ) -> tuple[LabWorkflowOrderRecord, LabOrderBlockRecord, bool]:
        """Records that this laboratory cannot currently continue this order.

        Returns ``(order, episode, created)``. ``created=False`` is the retry/double-click case:
        the order is already blocked and the active episode records *the same* reason and note, so
        the existing episode is returned unchanged -- no second row, no second audit, no rewritten
        timestamp. The same shape ``accept``/``record_collection`` already use.

        A block request that arrives while a *different* reason or note is already active raises
        ``ConflictingBlockRequest`` (409) instead: see that exception for why returning somebody
        else's episode would be a lie rather than a convenience.

        Only ``reason`` and ``note`` come from the caller. The laboratory, the actor, the instant
        and ``blocked_from_status`` are all derived here from the URL, the authenticated
        principal, the server clock and the order's own locked row."""
        if session is not None:
            return self._block_in_session(
                session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                reason=reason,
                blocked_by_identity_id=blocked_by_identity_id,
                note=note,
            )
        with self._sessions.begin() as owned_session:
            return self._block_in_session(
                owned_session,
                order_id=order_id,
                lab_organization_id=lab_organization_id,
                reason=reason,
                blocked_by_identity_id=blocked_by_identity_id,
                note=note,
            )

    def _block_in_session(
        self,
        session: Session,
        *,
        order_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        reason: LabOrderBlockReason,
        blocked_by_identity_id: uuid.UUID,
        note: str | None,
    ) -> tuple[LabWorkflowOrderRecord, LabOrderBlockRecord, bool]:
        self._organizations.require_type(lab_organization_id, OrganizationType.LAB)

        # Normalized before anything compares it. A browser that sends `""` for an untouched
        # textarea and `null` on the retry is describing the same absent note, and without this
        # the replay check below would call that a conflicting request and refuse the retry with
        # a 409. Whitespace is stripped for the same reason -- " " is not a note.
        note = _normalize_note(note)

        order = session.scalar(select(LabOrder).where(LabOrder.id == order_id).with_for_update())
        if order is None:
            raise LabOrderNotAssigned(str(order_id))

        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        if selection is None or selection.lab_organization_id != lab_organization_id:
            raise LabOrderNotAssigned(str(order_id))

        existing = _get_active_block(session, order_id)
        if existing is not None:
            # Replay-or-conflict is decided before the status check, deliberately: a retry of the
            # request that already won must succeed regardless of what the order's status is now,
            # exactly as `record_collection`'s own idempotent branch fires unconditionally.
            if existing.reason != reason.value or existing.note != note:
                raise ConflictingBlockRequest(
                    f"LabOrder {order_id} already has an active block episode recording a "
                    f"different reason -- resolve it before recording a new one"
                )
            return (
                _to_record(order, selection, _get_collection(session, order_id), existing),
                _to_block_record(existing),
                False,
            )

        if order.status not in BLOCKABLE_ORDER_STATUSES:
            raise InvalidLabWorkflowTransition(
                f"cannot block LabOrder {order_id} in status {order.status!r} -- only work this "
                f"laboratory has taken and not yet released may be blocked "
                f"({', '.join(sorted(BLOCKABLE_ORDER_STATUSES))})"
            )

        block = LabOrderBlock(
            lab_order_id=order_id,
            lab_organization_id=lab_organization_id,
            reason=reason.value,
            note=note,
            # Read off the locked row, never from the request. This is the whole resume design.
            blocked_from_status=order.status,
            blocked_at=utc_now(),
            blocked_by_identity_id=blocked_by_identity_id,
        )
        session.add(block)
        # The status and its episode move together or not at all -- one transaction, so no reader
        # can ever observe a BLOCKED order whose episode does not exist yet, or the reverse.
        order.status = LabOrderStatus.BLOCKED.value
        session.flush()
        return (
            _to_record(order, selection, _get_collection(session, order_id), block),
            _to_block_record(block),
            True,
        )

    def resolve_block(
        self,
        *,
        order_id: uuid.UUID,
        block_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        resolved_by_identity_id: uuid.UUID,
        resolution_note: str | None = None,
        session: Session | None = None,
    ) -> tuple[LabWorkflowOrderRecord, LabOrderBlockRecord, bool]:
        """Resolves one named block episode and restores the workflow state it interrupted.

        Returns ``(order, episode, resolved)``. ``resolved=False`` means this episode was already
        resolved and the call was a safe replay -- the stored resolution is returned untouched,
        and in particular the original resolver and instant are **not** overwritten by the second
        caller's.

        The episode is addressed by id rather than by "whatever is active on this order". A
        delayed retry can then only ever resolve the episode its caller actually saw; addressing
        it by order would let a retry silently resolve a second, later block that had since been
        raised for an entirely different reason.

        The destination is ``blocked_from_status`` and nothing else -- not a parameter, and not
        derivable by the client. ``resolution_note`` is optional free text and is held to
        ``LabOrderBlock.note``'s rules."""
        if session is not None:
            return self._resolve_block_in_session(
                session,
                order_id=order_id,
                block_id=block_id,
                lab_organization_id=lab_organization_id,
                resolved_by_identity_id=resolved_by_identity_id,
                resolution_note=resolution_note,
            )
        with self._sessions.begin() as owned_session:
            return self._resolve_block_in_session(
                owned_session,
                order_id=order_id,
                block_id=block_id,
                lab_organization_id=lab_organization_id,
                resolved_by_identity_id=resolved_by_identity_id,
                resolution_note=resolution_note,
            )

    def _resolve_block_in_session(
        self,
        session: Session,
        *,
        order_id: uuid.UUID,
        block_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        resolved_by_identity_id: uuid.UUID,
        resolution_note: str | None,
    ) -> tuple[LabWorkflowOrderRecord, LabOrderBlockRecord, bool]:
        self._organizations.require_type(lab_organization_id, OrganizationType.LAB)

        order = session.scalar(select(LabOrder).where(LabOrder.id == order_id).with_for_update())
        if order is None:
            raise LabOrderNotAssigned(str(order_id))

        selection = session.scalar(
            select(LabSelection).where(LabSelection.lab_order_id == order_id)
        )
        if selection is None or selection.lab_organization_id != lab_organization_id:
            raise LabOrderNotAssigned(str(order_id))

        block = session.scalar(
            select(LabOrderBlock).where(
                LabOrderBlock.id == block_id,
                LabOrderBlock.lab_order_id == order_id,
                LabOrderBlock.lab_organization_id == lab_organization_id,
            )
        )
        if block is None:
            raise BlockEpisodeNotFound(str(block_id))

        if block.resolved_at is not None:
            # Already resolved: a replay. Nothing is rewritten -- not `resolved_at`, not
            # `resolved_by_identity_id`, and not `resolution_note`, even when this caller supplied
            # a different one. The first resolution is the one that happened.
            return (
                _to_record(
                    order,
                    selection,
                    _get_collection(session, order_id),
                    _get_active_block(session, order_id),
                ),
                _to_block_record(block),
                False,
            )

        if order.status != LabOrderStatus.BLOCKED.value:
            # Structurally unreachable through any supported path -- an unresolved episode and a
            # non-BLOCKED status are written in one transaction and cannot disagree. Refused
            # rather than "repaired": silently forcing the order back to `blocked_from_status`
            # here would overwrite whatever genuinely happened to it with a guess.
            raise InvalidLabWorkflowTransition(
                f"cannot resolve a block on LabOrder {order_id} -- the order is "
                f"{order.status!r}, not 'BLOCKED'"
            )

        block.resolved_at = utc_now()
        block.resolved_by_identity_id = resolved_by_identity_id
        block.resolution_note = _normalize_note(resolution_note)
        # The server's recorded resume state, and the only value this line may ever take. The
        # CHECK on `blocked_from_status` means it is already known to be ACCEPTED or IN_PROGRESS,
        # so this can never restore a released, rejected or cancelled order.
        order.status = block.blocked_from_status
        session.flush()
        return (
            _to_record(order, selection, _get_collection(session, order_id), None),
            _to_block_record(block),
            True,
        )

    def list_blocks_for_order(
        self, order_id: uuid.UUID, lab_organization_id: uuid.UUID
    ) -> list[LabOrderBlockRecord] | None:
        """The order's full block history, newest first. ``None`` -- never an empty list -- when
        the order does not exist or is not assigned to this laboratory, so the caller can map that
        to the same 404 every other tenant-scoped lookup here produces.

        Unbounded by episode count on purpose: this is per-order, an order accumulating enough
        episodes to matter is itself the operational signal, and truncating the history would
        defeat the one question this read exists to answer."""
        with self._sessions() as session:
            order = session.get(LabOrder, order_id)
            if order is None:
                return None
            selection = session.scalar(
                select(LabSelection).where(LabSelection.lab_order_id == order_id)
            )
            if selection is None or selection.lab_organization_id != lab_organization_id:
                return None
            blocks = session.scalars(
                select(LabOrderBlock)
                .where(LabOrderBlock.lab_order_id == order_id)
                .order_by(LabOrderBlock.blocked_at.desc(), LabOrderBlock.id.desc())
            ).all()
            return [_to_block_record(block) for block in blocks]

    def get_active_block_reasons_for_orders(
        self, order_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """DHEALTH-LAB-BLOCKED-001: the **provider-facing** projection, and the only block read in
        this module that is not scoped to the owning laboratory.

        It returns the reason key and nothing else -- no note, no resolution note, no actor, no
        timestamp, no episode id, no history. That narrowing lives here, in the shape of the
        return type, rather than in the caller: a `dict[order_id, reason]` structurally cannot leak
        a laboratory's internal note to the ordering clinic, whereas a list of
        `LabOrderBlockRecord`s left to be filtered correctly by every future caller eventually
        will not be. `docs/NATIVE-OPERATING-PRODUCT.md` §16 makes the ordering clinician the next
        owner of a blocked order, so they must be told *that* it is blocked and *why* in
        operational terms; §26's minimum-necessary rule is why they are told no more than that.

        Not tenant-scoped on purpose, and safe because of what it returns: the caller has already
        proven the orders are its own (provider-side reads run through
        ``_existing_order_for_provider_or_404`` and the provider order listing), and an order id
        the caller could not already see is not in ``order_ids``. Only *active* episodes are
        included -- a resolved block is history the laboratory owns, not a current fact the clinic
        is owed.

        One query for the whole page regardless of size, mirroring
        ``_lab_assignments_for_orders``'s own N+1-safe discipline."""
        if not order_ids:
            return {}
        with self._sessions() as session:
            rows = session.execute(
                select(LabOrderBlock.lab_order_id, LabOrderBlock.reason).where(
                    LabOrderBlock.lab_order_id.in_(order_ids),
                    LabOrderBlock.resolved_at.is_(None),
                )
            ).all()
            return {order_id: reason for order_id, reason in rows}

    def get_collection_for_order(self, order_id: uuid.UUID) -> LabSampleCollectionRecord | None:
        with self._sessions() as session:
            collection = _get_collection(session, order_id)
            return None if collection is None else _to_collection_record(collection)


def _get_collection(session: Session, order_id: uuid.UUID) -> LabSampleCollection | None:
    return session.scalar(
        select(LabSampleCollection).where(LabSampleCollection.lab_order_id == order_id)
    )


def _to_record(
    order: LabOrder,
    selection: LabSelection,
    collection: LabSampleCollection | None,
    active_block: LabOrderBlock | None = None,
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
        active_block=None if active_block is None else _to_block_record(active_block),
    )


def _to_collection_record(collection: LabSampleCollection) -> LabSampleCollectionRecord:
    return LabSampleCollectionRecord(
        id=collection.id,
        lab_order_id=collection.lab_order_id,
        lab_organization_id=collection.lab_organization_id,
        recorded_at=collection.recorded_at,
    )


def _to_block_record(block: LabOrderBlock) -> LabOrderBlockRecord:
    return LabOrderBlockRecord(
        id=block.id,
        lab_order_id=block.lab_order_id,
        lab_organization_id=block.lab_organization_id,
        reason=block.reason,
        note=block.note,
        blocked_from_status=block.blocked_from_status,
        blocked_at=block.blocked_at,
        blocked_by_identity_id=block.blocked_by_identity_id,
        resolved_at=block.resolved_at,
        resolved_by_identity_id=block.resolved_by_identity_id,
        resolution_note=block.resolution_note,
    )


def _normalize_note(note: str | None) -> str | None:
    """``None``, ``""`` and ``"   "`` all mean "no note" and are stored identically.

    This exists so the replay check in ``_block_in_session`` compares meanings rather than
    encodings: a browser that sends an empty string for an untouched textarea and ``null`` on the
    retry would otherwise look like a *different* request and be refused as a conflict."""
    if note is None:
        return None
    stripped = note.strip()
    return stripped or None


def _get_active_block(session: Session, order_id: uuid.UUID) -> LabOrderBlock | None:
    """The one unresolved episode, or ``None``. ``uq_exchange_lab_order_blocks_one_active_per_
    order`` is what lets this return a scalar rather than a list without the caller having to
    decide what "several active blocks" would mean -- there cannot be several."""
    return session.scalar(
        select(LabOrderBlock).where(
            LabOrderBlock.lab_order_id == order_id, LabOrderBlock.resolved_at.is_(None)
        )
    )
