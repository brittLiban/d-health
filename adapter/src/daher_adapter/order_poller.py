"""The OpenMRS order poll cycle (DH-011E).

⚠️ **BUILT, NOT ACTIVATED.** Nothing in the running adapter starts this loop. BLOCKER-6 itself is
resolved -- the OpenMRS -> OpenELIS target architecture is selected and proven (Option F) -- but
the production `DeliverOrder` composition and the real facility mapping inputs (DH-011H/DH-011I)
are not yet assembled, so the delivery callable below has no production implementation to compose
against. That is deliberate: the poller is complete and tested against the accepted design, and it
moves no clinical data until that composition and those mapping inputs exist.

**DH-011E2 (2026-08-30): stale-claim recovery writes are per-event, not poller-lifetime-fixed.**
DH-011K discovered that a single `write_target` bound once at construction can never carry a
payload-dependent writer, because `RecoveryCoordinator.reconcile` only ever gives it the opaque
`SourceCorrelation` -- a one-way digest with no way back to the original event. `_handle` now
builds a small closure per event, over that *same* `event` object, and reuses `self._deliver` --
the identical callable a fresh claim uses -- as the actual write. This does not change what a
concrete `DeliverOrder` implementation has to do (it always did need to attach DH-025's
correlation), and it does not change `RecoveryCoordinator`'s own transport-neutral contract at
all; the fix is entirely in how `OrderPoller` composes it. See `_retry_with_validated_correlation`
below for the correlation-consistency guard this closure carries.

**The one rule everything else serves:** the watermark advances only past work that is durably
accounted for. A crash re-polls; the ledger absorbs the duplicates. A gap is unrecoverable and
leaves no trace, so the cycle never risks one — it stops at the last contiguous success and stays
loud.

Ordering of the safety checks in `poll_once` is not arbitrary:

1. Read the server's clock **first**, and use it as the window's upper bound. Taking it later
   would let records written during the fetch fall inside a window whose `total` was already
   read, and the completeness check would fire forever on healthy traffic.
2. Assemble the cohort — dedupe, prove completeness, order locally (the server ignores `_sort`).
3. Walk it in order, claiming each event in the ledger before doing anything with it.
4. Advance only if every event reached a terminal, accounted-for state.

A `dead_letter` or an unresolved in-flight claim **stops the cycle at that event** rather than
skipping it. Stopping is visible; skipping is not.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from daher_adapter.instants import ServerInstant, highest
from daher_adapter.ledger import Direction, LedgerRepository
from daher_adapter.openmrs import SERVICE_REQUEST, OpenMrsOrderSource
from daher_adapter.recovery import (
    AmbiguousTargetState,
    DeadLetterRecoveryBlocked,
    RecoveryCoordinator,
    RecoveryFence,
    SourceCorrelation,
    TargetProbe,
    TargetRecord,
    source_correlation,
)
from daher_adapter.source_events import Cohort, SourceOrderEvent, assemble_cohort

logger = logging.getLogger("daher_adapter.order_poller")

# Deliver one source event onward and return where it landed. **This is the production
# DeliverOrder composition seam BLOCKER-6's resolution (Option F) still needs wired in.**
# The poller neither knows nor cares what implements it; it must return the target record it
# actually created, and it must attach DH-025's correlation to that write.
DeliverOrder = Callable[[SourceOrderEvent], Awaitable[TargetRecord]]


class RecoveryCorrelationMismatch(RuntimeError):
    """A stale-claim recovery writer received a correlation that does not match the event it
    would retry. Structurally this should never happen -- `RecoveryCoordinator.reconcile` always
    derives its correlation from the same `MessageIdentity` `_handle` passes it, so the two are
    mathematically identical by construction -- but the check exists as a permanent guard against
    a future refactor (e.g. a closure hoisted out of its per-event scope) silently pairing one
    event's payload with a different event's correlation. Never silently recomputed and swapped
    in; the write is refused instead."""


async def _retry_with_validated_correlation(
    event: SourceOrderEvent, correlation: SourceCorrelation, deliver: DeliverOrder
) -> TargetRecord:
    """DH-011E2's stale-claim writer body, factored out of `OrderPoller._handle` so it is directly
    unit-testable without going through the full `RecoveryCoordinator.reconcile` machinery.

    Reuses `deliver` -- the exact same callable a fresh claim uses -- rather than a second,
    independent delivery path. `deliver` already has to attach DH-025's correlation to whatever
    it writes (its own contract, unchanged by this function); this only proves the correlation
    `RecoveryCoordinator` is asking to reconcile is genuinely this event's own before handing the
    event to it at all.
    """
    expected = source_correlation(event.identity)
    if correlation != expected:
        raise RecoveryCorrelationMismatch(
            f"recovery correlation {correlation.value!r} for resource {event.resource_id!r} "
            f"does not match its own identity's correlation {expected.value!r} -- refusing to "
            f"deliver a possibly-foreign event's payload"
        )
    return await deliver(event)


class WatermarkStore(Protocol):
    """What the cycle needs from the watermark, and nothing more.

    A structural type rather than the concrete `WatermarkRepository` so this module carries no
    hard dependency on a SQLAlchemy session, and so a test can substitute a store without a
    database. Narrow on purpose: the cycle can advance a position and bootstrap one, and has no
    way to move a watermark backwards.
    """

    def get_or_bootstrap(
        self, *, resource_type: str, direction: Direction, server_now: ServerInstant
    ) -> ServerInstant: ...

    def advance(
        self, *, resource_type: str, direction: Direction, to: ServerInstant
    ) -> ServerInstant: ...


class PollCycleHalted(RuntimeError):
    """The cycle stopped early and the watermark was deliberately not advanced past the cause."""


@dataclass(frozen=True, slots=True)
class PollOutcome:
    """What one cycle did. Counts and identifiers only — never a clinical value."""

    window_start: ServerInstant
    window_end: ServerInstant
    reported_total: int
    delivered: int = 0
    already_completed: int = 0
    reconciled: int = 0
    duplicates_absorbed: int = 0
    advanced_to: ServerInstant | None = None
    halted_reason: str | None = None
    halted_at_resource: str | None = None
    dead_lettered: tuple[str, ...] = field(default_factory=tuple)

    @property
    def advanced(self) -> bool:
        return self.advanced_to is not None


class OrderPoller:
    def __init__(
        self,
        *,
        source: OpenMrsOrderSource,
        ledger: LedgerRepository,
        watermarks: WatermarkStore,
        deliver: DeliverOrder,
        recovery: RecoveryCoordinator,
        fence: RecoveryFence,
        probe_target: TargetProbe,
        resource_type: str = SERVICE_REQUEST,
        direction: Direction = "openmrs_to_openelis",
    ) -> None:
        self._source = source
        self._ledger = ledger
        self._watermarks = watermarks
        self._deliver = deliver
        self._recovery = recovery
        self._fence = fence
        self._probe_target = probe_target
        self._resource_type = resource_type
        self._direction = direction

    async def poll_once(self) -> PollOutcome:
        # Step 1. The window's top comes from the server, before anything is fetched.
        upper = self._source.server_instant(self._resource_type)
        since = self._watermarks.get_or_bootstrap(
            resource_type=self._resource_type,
            direction=self._direction,
            server_now=upper,
        )

        window = self._source.fetch_window(
            since=since, upper=upper, resource_type=self._resource_type
        )
        cohort: Cohort = assemble_cohort(
            window.resources,
            reported_total=window.reported_total,
            resource_type=self._resource_type,
            direction=self._direction,
        )

        outcome = await self._walk(cohort, since=since, upper=upper)
        return outcome

    async def _walk(
        self, cohort: Cohort, *, since: ServerInstant, upper: ServerInstant
    ) -> PollOutcome:
        delivered = already = reconciled = 0
        dead_lettered: list[str] = []
        # Starts at the existing position, so a cycle that processes nothing still advances to
        # the frozen upper bound only if the whole cohort is clean — never past a failure.
        contiguous = since
        halted: tuple[str, str] | None = None

        for event in cohort.events:
            try:
                handled = await self._handle(event)
            except (DeadLetterRecoveryBlocked, AmbiguousTargetState) as stop:
                # Both mean "the outcome of earlier work is unknown". Advancing past an unknown
                # is how an order gets silently lost, so the cycle ends here with the watermark
                # still behind this event.
                halted = (type(stop).__name__, event.resource_id)
                logger.error(
                    "poll_halted_unresolved",
                    extra={
                        "resource_id": event.resource_id,
                        "reason": type(stop).__name__,
                    },
                )
                break
            except Exception as error:
                # A terminal delivery failure. Dead-letter it so it is operator-visible, then
                # stop — §3 rule 3: the watermark never advances past a failed item.
                self._ledger.move_to_dead_letter(
                    event.identity,
                    payload=dict(event.payload),
                    error=error,
                    attempts=1,
                )
                dead_lettered.append(event.resource_id)
                halted = ("delivery_failed", event.resource_id)
                logger.error(
                    "poll_halted_dead_letter",
                    extra={
                        "resource_id": event.resource_id,
                        "error_type": type(error).__name__,
                    },
                )
                break

            if handled == "delivered":
                delivered += 1
            elif handled == "already_completed":
                already += 1
            else:
                reconciled += 1
            contiguous = highest(contiguous, event.last_updated)

        advanced_to: ServerInstant | None = None
        if halted is None:
            # Nothing was left unaccounted for, so the window is closed at its frozen top.
            # Advancing to `upper` rather than to the last event's timestamp is what stops the
            # boundary second from being re-fetched forever when it is already complete.
            advanced_to = self._watermarks.advance(
                resource_type=self._resource_type,
                direction=self._direction,
                to=highest(contiguous, upper),
            )
        elif contiguous > since:
            # Partial progress is still progress, and it is safe: every event up to here reached
            # a terminal accounted-for state. Advancing to the last contiguous success avoids
            # re-delivering them next cycle while leaving the halting event in the window.
            advanced_to = self._watermarks.advance(
                resource_type=self._resource_type,
                direction=self._direction,
                to=contiguous,
            )

        return PollOutcome(
            window_start=since,
            window_end=upper,
            reported_total=cohort.reported_total,
            delivered=delivered,
            already_completed=already,
            reconciled=reconciled,
            duplicates_absorbed=cohort.duplicates_absorbed,
            advanced_to=advanced_to,
            halted_reason=halted[0] if halted else None,
            halted_at_resource=halted[1] if halted else None,
            dead_lettered=tuple(dead_lettered),
        )

    async def _handle(self, event: SourceOrderEvent) -> str:
        """Claim and process exactly one event. Returns how it was accounted for."""
        claim = self._ledger.claim(event.identity)

        if claim.claimed:
            target = await self._deliver(event)
            self._ledger.mark_completed(
                event.identity,
                target_system=target.system,
                target_record_id=target.record_id,
            )
            return "delivered"

        if claim.state == "completed":
            # The expected duplicate: the inclusive lower bound re-presents the boundary second
            # every cycle. Safe to skip, and the watermark may pass it.
            return "already_completed"

        if claim.state == "dead_letter":
            raise DeadLetterRecoveryBlocked(
                f"resource {event.resource_id!r} is dead-lettered and needs operator resolution; "
                f"the watermark will not advance past it"
            )

        # `processing` and not ours: a previous cycle claimed it and did not finish. DH-025 owns
        # this — it may have written to the target already, so a blind retry could duplicate an
        # order. The coordinator completes it if exactly one target write exists, retries only
        # on proven absence, and raises on any ambiguity. A timeout alone never steals a claim.
        #
        # DH-011E2: the writer is built here, per event, over this exact `event` object — never
        # a poller-lifetime-fixed callable — because a real writer needs this event's own payload,
        # not just the opaque correlation `RecoveryCoordinator` computes from its identity.
        async def write_recovered_event(correlation: SourceCorrelation) -> TargetRecord:
            return await _retry_with_validated_correlation(event, correlation, self._deliver)

        await self._recovery.reconcile(
            event.identity,
            fence=self._fence,
            probe_target=self._probe_target,
            write_target=write_recovered_event,
        )
        return "reconciled"
