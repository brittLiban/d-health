"""DH-CONNECT-AGENT-001 §20–§37, §52–§56 — the durable delivery engine.

What this module owns: a clinical message that DHealth has accepted responsibility for is never
lost, never applied twice, and never silently discarded. Everything else here is in service of those
three.

**Why a database queue rather than a broker.** §26 forbids browser storage and flat files as the
authoritative queue and forbids requiring partner PostgreSQL to run the agent -- but the *server*
side already has a transactional database holding the clinical orders these messages are about.
Putting the queue there means enqueueing a delivery and the mutation that caused it commit or roll
back together. A broker would make "the order was created" and "the connector was told" two
independent facts that fail independently, and the first bug would be an order the laboratory never
heard about with nothing anywhere recording that DHealth meant to tell them.

**Claim leases, not locks.** ``claim`` hands a bounded batch to one runtime instance and stamps a
deadline. Nothing has to be released: a claim expires. An agent killed mid-batch blocks its work for
one lease and then it becomes claimable again, with no operator gesture and no cleanup job. The
lease is also what makes two agent instances safe -- they cannot claim the same row, and the loser
simply gets the next batch.

**Retry, then stop.** Retryable failures back off exponentially; terminal ones dead-letter
immediately, because retrying a mapping error produces the identical mapping error and burns a
retry budget that exists for network trouble. Exhausting the budget dead-letters too. §56:
``DEAD_LETTER`` is not a deletion and not a cancellation -- it is an unresolved row an operator has
to look at. DHealth does not invent a cancellation on a partner's behalf.

**At-least-once, made idempotent.** Delivery confirmation can be lost after the agent has already
applied a message, so the agent will re-receive it. ``DurableReceiptStore`` is what makes that
harmless: the second application is recognised by ``(scope, source_event_id)`` and skipped, and a
*different* payload under the same key is refused rather than allowed to overwrite the first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from daher_adapter.connect.contract import (
    RETRYABLE,
    Direction,
    Envelope,
    FailureCategory,
    message_direction,
)
from daher_adapter.connect.runtime_models import (
    ConnectorDelivery,
    ConnectorMessageReceipt,
    DeliveryState,
)
from daher_adapter.models import utc_now


class DeliveryDirectionMismatch(ValueError):
    """A message queued in a direction it cannot travel.

    Raised rather than corrected. §45: an agent may only submit inbound message types and DHealth
    may only queue outbound ones, and a runtime that quietly flipped the direction to whatever made
    the row insertable would defeat both checks at once.
    """


class DeliveryConflict(ValueError):
    """The same ``(scope, source_event_id)`` arrived carrying different content.

    Never an overwrite. A partner re-sending event ``42`` with a different result value is either a
    bug in their system or an amendment they have not modelled as one; in both cases the correct
    DHealth behaviour is to refuse and surface it, because the alternative is silently replacing a
    released clinical value with a newer one nobody reviewed.
    """


class DeliveryClaimLost(RuntimeError):
    """An agent reported on a delivery it no longer holds.

    Its lease expired and another instance took the work, or it is reporting on a row it never
    claimed. Either way the report is refused: accepting it would let a stale agent mark delivered a
    message the current holder is still processing, and the message would then never be delivered by
    anyone.
    """


@dataclass(frozen=True, slots=True)
class DeliveryRetryPolicy:
    """How many times, how long apart, and when to give up (§52).

    Exponential with a ceiling, and the ceiling matters more than the growth: unbounded backoff
    means a message that failed six times is next attempted in four hours, which for a clinical
    order is indistinguishable from never. The cap keeps a recovering integration draining promptly.

    ``max_attempts`` is small on purpose. A queue that retries a hundred times looks resilient and
    is actually a queue where nobody ever looks at a dead letter, because nothing ever reaches one
    -- the work simply ages invisibly. Five attempts over roughly four minutes, then a row an
    operator is told about.
    """

    max_attempts: int = 5
    base_delay: timedelta = timedelta(seconds=15)
    max_delay: timedelta = timedelta(minutes=2)

    def next_delay(self, attempt_count: int) -> timedelta:
        """Backoff after ``attempt_count`` attempts have been made.

        No jitter, deliberately. Jitter exists to desynchronise many clients hammering one service;
        here each installation has one agent and the retry timing is per-row, so jitter would buy
        nothing and make the next attempt time untestable without freezing a random seed.
        """
        exponent = max(attempt_count - 1, 0)
        delay = self.base_delay * (2**exponent)
        return min(delay, self.max_delay)


DEFAULT_RETRY_POLICY = DeliveryRetryPolicy()

#: How long a claim holds. Long enough that an agent processing a full batch is not racing its own
#: lease, short enough that a crashed agent's work is retried while the outage is still the same
#: outage. Not configurable per call: two agents with different lease lengths against one queue is a
#: configuration nobody can reason about.
DEFAULT_CLAIM_LEASE = timedelta(minutes=2)

#: The largest batch any single claim returns (§25). A bound rather than "all pending work": an
#: agent that claims ten thousand rows holds all of them under one lease, and if it dies the entire
#: backlog waits for that lease instead of being picked up piecemeal.
MAX_CLAIM_BATCH = 50


class DurableReceiptStore:
    """``contract.ReceiptStore`` backed by ``connector_message_receipts``.

    The production implementation of the Protocol whose in-memory sibling is explicitly not durable.
    The difference is the whole of replay safety: ``InMemoryReceiptStore`` forgets everything when
    the process ends, so an at-least-once transport plus a restart turns one collection event into
    two, and nothing anywhere records that it happened twice.

    Bound to a session rather than an engine. Recording a receipt has to commit in the same
    transaction as the mutation it describes, or a crash between the two leaves DHealth having
    applied a message it does not remember applying -- the same bug the in-memory store has,
    reintroduced with extra steps.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def seen(self, scope: str, source_event_id: str) -> str | None:
        return self._session.scalar(
            select(ConnectorMessageReceipt.content_fingerprint).where(
                ConnectorMessageReceipt.replay_scope == scope,
                ConnectorMessageReceipt.source_event_id == source_event_id,
            )
        )

    def record(self, scope: str, source_event_id: str, content_fingerprint: str) -> None:
        """Remember this event. Idempotent, and never an update.

        A second ``record`` of the same key with the same fingerprint is a no-op rather than an
        error, because an honest retry reaches here twice. A second one with a *different*
        fingerprint raises: the row is the record of what DHealth actually applied, and rewriting it
        would erase the evidence that the partner contradicted themselves.
        """
        existing = self.seen(scope, source_event_id)
        if existing is not None:
            if existing != content_fingerprint:
                raise DeliveryConflict(
                    f"event {source_event_id!r} in scope {scope!r} was already applied with "
                    "different content; refusing to overwrite the earlier record"
                )
            return
        self._session.add(
            ConnectorMessageReceipt(
                replay_scope=scope,
                source_event_id=source_event_id,
                content_fingerprint=content_fingerprint,
            )
        )
        # Flush so a concurrent writer's unique-constraint violation surfaces here, inside the
        # caller's transaction, rather than at an arbitrary later commit where the message it
        # belongs to has already been applied.
        try:
            self._session.flush()
        except IntegrityError as error:
            raise DeliveryConflict(
                f"event {source_event_id!r} in scope {scope!r} was recorded concurrently"
            ) from error


class DeliveryRepository:
    """Enqueue, claim, confirm, fail. The only writer of ``connector_deliveries``.

    Every method takes an explicit ``now`` rather than reading the clock itself, because half of
    what this class does is decide what time something becomes eligible -- and a decision about time
    that reads its own clock can only be tested by waiting. ``None`` means "now", so ordinary
    callers are not burdened by it.
    """

    def enqueue(
        self,
        session: Session,
        *,
        installation_id: uuid.UUID,
        envelope: Envelope,
        expected_direction: Direction,
        now: datetime | None = None,
    ) -> tuple[ConnectorDelivery, bool]:
        """Persist one message for delivery, or return the row that already holds it.

        Returns ``(delivery, created)``. The flag is not a convenience: the caller has to be able to
        tell a genuinely new message from a replay, and inferring it by comparing ``created_at`` to
        the caller's own clock would be wrong for exactly the rows that matter -- a row written a
        millisecond ago by a concurrent retry looks new under any tolerance you pick.

        Idempotent on ``(installation, direction, replay_scope, source_event_id)``. That is what
        lets the caller be a plain hook with no memory: an order-assignment path that fires twice, a
        retried API request, and a replayed partner event all converge on one row. Returning the
        existing row rather than raising is deliberate -- the caller asked for this message to be
        delivered, and it is going to be.

        A same-key re-enqueue carrying *different* content raises ``DeliveryConflict``. The queue is
        not a place where the second version of a clinical event quietly wins.

        ``expected_direction`` is **required**, and it is the second of the two boundaries §45
        describes. The first is the runtime API refusing an outbound message type from an agent;
        this is the queue itself refusing to hold a message travelling the wrong way, whoever asked.
        A default would have made the check optional in practice: the caller that most needs it is
        the one written later by someone who did not read this docstring.
        """
        moment = now or utc_now()
        direction = message_direction(envelope.payload.message_type)
        if direction is not expected_direction:
            raise DeliveryDirectionMismatch(
                f"{envelope.payload.message_type.value} travels {direction.value}, "
                f"but was queued as {expected_direction.value}"
            )
        scope = envelope.replay_scope()
        fingerprint = envelope.content_fingerprint()

        existing = session.scalar(
            select(ConnectorDelivery).where(
                ConnectorDelivery.connector_installation_id == installation_id,
                ConnectorDelivery.direction == direction.value,
                ConnectorDelivery.replay_scope == scope,
                ConnectorDelivery.source_event_id == envelope.source_event_id,
            )
        )
        if existing is not None:
            if existing.content_fingerprint != fingerprint:
                raise DeliveryConflict(
                    f"event {envelope.source_event_id!r} in scope {scope!r} is already queued with "
                    "different content"
                )
            return existing, False

        delivery = ConnectorDelivery(
            connector_installation_id=installation_id,
            direction=direction.value,
            message_type=envelope.payload.message_type.value,
            message_id=envelope.message_id,
            replay_scope=scope,
            source_event_id=envelope.source_event_id,
            content_fingerprint=fingerprint,
            lab_order_id=envelope.lab_order_id,
            payload=envelope.model_dump_json(),
            state=DeliveryState.PENDING.value,
            attempt_count=0,
            next_attempt_at=moment,
        )
        session.add(delivery)
        try:
            session.flush()
        except IntegrityError as error:
            raise DeliveryConflict(
                f"event {envelope.source_event_id!r} in scope {scope!r} was queued concurrently"
            ) from error
        return delivery, True

    def claim(
        self,
        session: Session,
        *,
        installation_id: uuid.UUID,
        direction: Direction,
        runtime_instance_id: str,
        limit: int = MAX_CLAIM_BATCH,
        lease: timedelta = DEFAULT_CLAIM_LEASE,
        now: datetime | None = None,
    ) -> list[ConnectorDelivery]:
        """Hand a bounded batch to one runtime instance under a lease.

        ``with_for_update(skip_locked=True)`` is what makes two concurrent agents safe rather than
        merely unlikely: the second skips the rows the first has locked instead of blocking on them
        or reading them. **SQLite ignores both silently**, so the concurrency property this line
        provides is unprovable on the unit-test database and is tested against real PostgreSQL
        instead -- a passing SQLite test here would be evidence of nothing.

        Expired claims are reclaimable, which is the whole point of a lease. The filter deliberately
        does not exclude ``CLAIMED`` rows whose ``claim_expires_at`` has passed: excluding them
        would make a crashed agent's batch unreachable forever, which is the failure mode leases
        exist to remove.
        """
        moment = now or utc_now()
        bounded = max(1, min(limit, MAX_CLAIM_BATCH))

        rows = (
            session.execute(
                select(ConnectorDelivery)
                .where(
                    ConnectorDelivery.connector_installation_id == installation_id,
                    ConnectorDelivery.direction == direction.value,
                    ConnectorDelivery.next_attempt_at <= moment,
                    _claimable(moment),
                )
                # Oldest source event first. §79: the order work was created in is the order it is
                # handed over in, so a laboratory sees an order before the amendment to its result.
                .order_by(ConnectorDelivery.created_at, ConnectorDelivery.id)
                .limit(bounded)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )

        for row in rows:
            row.state = DeliveryState.CLAIMED.value
            row.claimed_by_runtime_instance_id = runtime_instance_id
            row.claim_expires_at = moment + lease
            # Counted at hand-over, not at failure. An agent that claims a message and vanishes has
            # attempted it; counting only reported failures would let a crash-looping agent retry
            # forever without any row ever reaching a dead letter.
            row.attempt_count += 1
        session.flush()
        return list(rows)

    def mark_delivered(
        self,
        session: Session,
        *,
        delivery: ConnectorDelivery,
        runtime_instance_id: str,
        now: datetime | None = None,
    ) -> ConnectorDelivery:
        """Confirm one message. Idempotent, and only by the instance that claimed it.

        Already-``DELIVERED`` is a no-op rather than an error: the confirmation itself is delivered
        at-least-once, so the agent will sometimes confirm twice. Confirming a row claimed by a
        *different* instance raises -- that is either a stale agent whose lease expired and whose
        work another instance has since taken, or a confused one, and in both cases accepting the
        confirmation would mark delivered a message the current holder is still working on.
        """
        moment = now or utc_now()
        if delivery.state == DeliveryState.DELIVERED.value:
            return delivery
        _require_holder(delivery, runtime_instance_id)
        delivery.state = DeliveryState.DELIVERED.value
        delivery.delivered_at = moment
        delivery.claimed_by_runtime_instance_id = None
        delivery.claim_expires_at = None
        delivery.last_failure_category = None
        delivery.last_failure_detail = None
        session.flush()
        return delivery

    def mark_failed(
        self,
        session: Session,
        *,
        delivery: ConnectorDelivery,
        runtime_instance_id: str,
        category: FailureCategory,
        detail: str | None = None,
        policy: DeliveryRetryPolicy = DEFAULT_RETRY_POLICY,
        now: datetime | None = None,
    ) -> ConnectorDelivery:
        """Record a failed attempt, and decide whether there will be another.

        Two ways to reach ``DEAD_LETTER``, and they are different facts:

        * a **terminal** category -- mapping, validation, conflict, unsupported workflow, or
          authentication. Retrying produces the identical failure, so the budget is not spent on it.
          An unmapped test code will still be unmapped in fifteen seconds.
        * a **retryable** category whose attempts are exhausted. The integration may well recover,
          but it has not, and continuing to retry silently is how a message ages for a week.

        ``detail`` is truncated rather than rejected: this path runs while something is already
        wrong, and refusing to record a failure because its description was long would lose the only
        evidence of it. Callers are expected to pass one short sentence -- §57 forbids result
        values, patient names, credentials and raw messages here.
        """
        moment = now or utc_now()
        _require_holder(delivery, runtime_instance_id)

        delivery.last_failure_category = category.value
        delivery.last_failure_detail = None if detail is None else detail[:512]
        delivery.claimed_by_runtime_instance_id = None
        delivery.claim_expires_at = None

        terminal = category not in RETRYABLE
        exhausted = delivery.attempt_count >= policy.max_attempts
        if terminal or exhausted:
            delivery.state = DeliveryState.DEAD_LETTER.value
            delivery.dead_lettered_at = moment
        else:
            delivery.state = DeliveryState.PENDING.value
            delivery.next_attempt_at = moment + policy.next_delay(delivery.attempt_count)
        session.flush()
        return delivery

    def queue_depth(
        self, session: Session, *, installation_id: uuid.UUID, direction: Direction | None = None
    ) -> int:
        """Undelivered, non-dead work for this installation."""
        statement = select(func.count(ConnectorDelivery.id)).where(
            ConnectorDelivery.connector_installation_id == installation_id,
            ConnectorDelivery.state.in_((DeliveryState.PENDING.value, DeliveryState.CLAIMED.value)),
        )
        if direction is not None:
            statement = statement.where(ConnectorDelivery.direction == direction.value)
        return int(session.scalar(statement) or 0)


def _claimable(moment: datetime):
    """``PENDING``, or ``CLAIMED`` under a lease that has run out.

    Separated out because it is the one predicate whose wrongness would be silent. Omitting the
    expired-lease half yields a queue that works perfectly until an agent dies, and then strands
    exactly the messages that were in flight when it did.
    """
    return (ConnectorDelivery.state == DeliveryState.PENDING.value) | (
        (ConnectorDelivery.state == DeliveryState.CLAIMED.value)
        & (ConnectorDelivery.claim_expires_at <= moment)
    )


def _require_holder(delivery: ConnectorDelivery, runtime_instance_id: str) -> None:
    if delivery.claimed_by_runtime_instance_id != runtime_instance_id:
        raise DeliveryClaimLost(
            f"delivery {delivery.id} is not held by runtime instance {runtime_instance_id!r}"
        )
