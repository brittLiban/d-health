"""DH-011J: crash-safe cancellation reconciliation for added-test events.

Deliberately NOT `RecoveryCoordinator.reconcile` (see PROVENANCE.md's DH-011J section for the
full account). That coordinator's `TargetPresence.FOUND`/`ABSENT` model has no vocabulary for
"found but not yet cancelled" versus "found and cancelled" -- and cancellation always targets
something that already exists (created by an earlier, already-completed add-test event), never
something to create if absent. This is a parallel, small coordinator built from the same real
components (`LedgerRepository`, the `RecoveryFence` protocol), not a change to `recovery.py`'s
existing enums or `RecoveryCoordinator.reconcile` -- which stays untouched, still covering the
create path exactly as DH-011P/P2/P3 left it, CI-green.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from daher_adapter.ledger import LedgerRepository, MessageIdentity
from daher_adapter.openelis import CancellationProbeResult, CancellationTargetState
from daher_adapter.recovery import (
    AmbiguousTargetState,
    DeadLetterRecoveryBlocked,
    RecoveryFence,
    RecoveryStateError,
    SourceCorrelation,
)


class CancellationOutcome(StrEnum):
    CONFIRMED_CANCELLED = "confirmed_cancelled"
    CONFIRMED_NOT_CANCELLED = "confirmed_not_cancelled"


@dataclass(frozen=True, slots=True)
class CancellationResult:
    outcome: CancellationOutcome
    target_correlation: SourceCorrelation
    record_id: str


CancellationProbe = Callable[[SourceCorrelation], Awaitable[CancellationProbeResult]]
CancellationWriter = Callable[[SourceCorrelation], Awaitable[CancellationProbeResult]]


class DaherCancellationCoordinator:
    """Mirrors `RecoveryCoordinator`'s crash-safety shape (durable claim -> probe -> write only if
    needed -> mark_completed) for the different state machine cancellation actually has.

    `identity` identifies the *cancellation event itself* -- its own durable ledger claim, its
    own idempotent-replay identity -- never the original add-test event's identity, which already
    has its own, separate, already-`completed` ledger row from when the test was created. `target`
    is the *original* added-test correlation (the `daher_analysis_correlation` value the target
    was created under, read from wherever the cancellation source event carries it) -- the two
    are related but distinct, and collapsing them would make this coordinator unable to tell "a
    replay of the same cancellation" from "a different cancellation that happens to resolve to
    the same Analysis."
    """

    def __init__(self, ledger: LedgerRepository) -> None:
        self._ledger = ledger

    async def reconcile_cancellation(
        self,
        identity: MessageIdentity,
        target: SourceCorrelation,
        *,
        fence: RecoveryFence,
        probe_target: CancellationProbe,
        write_target: CancellationWriter,
    ) -> CancellationResult:
        if not fence.held:
            raise RecoveryStateError("exclusive startup recovery fence is not held")

        state = self._ledger.get_state(identity)
        if state is None:
            raise RecoveryStateError("cancellation event has no durable claim to recover")
        if state == "dead_letter":
            raise DeadLetterRecoveryBlocked(
                "dead-lettered cancellation event requires operator review"
            )
        if state == "completed":
            raise RecoveryStateError(
                "completed cancellation event is a normal duplicate, not a recovery candidate"
            )

        probe = await probe_target(target)
        if probe.state in (CancellationTargetState.AMBIGUOUS, CancellationTargetState.UNREACHABLE):
            raise AmbiguousTargetState("cancellation target lookup could not prove state")

        if probe.state is CancellationTargetState.EXISTS_CANCELLED:
            # Already cancelled -- by this exact event on a prior attempt, or by any other route
            # (§16/§7's "cancel an already-cancelled item"). Idempotent: no write attempted.
            result_probe = probe
        else:
            # write_target (OpenElisClient.cancel_service_request) never trusts its own POST's
            # status and reconciles internally -- it raises AmbiguousTargetState itself on
            # AMBIGUOUS/UNREACHABLE, so nothing further to check here on that axis.
            result_probe = await write_target(target)

        outcome = (
            CancellationOutcome.CONFIRMED_CANCELLED
            if result_probe.state is CancellationTargetState.EXISTS_CANCELLED
            else CancellationOutcome.CONFIRMED_NOT_CANCELLED
        )
        record_id = result_probe.record_id
        if record_id is None:  # defensive; both EXISTS_* states always carry one
            raise AmbiguousTargetState("cancellation reconciliation resolved with no target id")

        self._ledger.mark_completed(
            identity, target_system="ServiceRequest", target_record_id=record_id
        )
        return CancellationResult(outcome=outcome, target_correlation=target, record_id=record_id)
