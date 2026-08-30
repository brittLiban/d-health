from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.cancellation import (
    CancellationOutcome,
    DaherCancellationCoordinator,
)
from daher_adapter.ledger import LedgerRepository, MessageIdentity
from daher_adapter.openelis import CancellationProbeResult, CancellationTargetState
from daher_adapter.recovery import (
    AmbiguousTargetState,
    DeadLetterRecoveryBlocked,
    RecoveryStateError,
    SourceCorrelation,
)


def cancellation_event_identity(*, version: str = "1") -> MessageIdentity:
    return MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id="cancel-event-uuid",
        resource_version=version,
    )


def target() -> SourceCorrelation:
    return SourceCorrelation(
        system="urn:daher-health:adapter:source-event", value="added-test-corr"
    )


@dataclass
class FakeFence:
    held: bool = True


def run_reconcile(
    coordinator: DaherCancellationCoordinator,
    event: MessageIdentity,
    *,
    fence: FakeFence,
    probe_target,
    write_target,
):
    return asyncio.run(
        coordinator.reconcile_cancellation(
            event,
            target(),
            fence=fence,
            probe_target=probe_target,
            write_target=write_target,
        )
    )


def test_cancellation_requires_exclusive_fence(sessions: sessionmaker[Session]) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(cancellation_event_identity())
    calls: list[str] = []

    async def probe(_: SourceCorrelation) -> CancellationProbeResult:
        calls.append("probe")
        return CancellationProbeResult(CancellationTargetState.EXISTS_NOT_CANCELLED, "sr-1")

    async def write(_: SourceCorrelation) -> CancellationProbeResult:
        calls.append("write")
        return CancellationProbeResult(CancellationTargetState.EXISTS_CANCELLED, "sr-1")

    with pytest.raises(RecoveryStateError, match="fence"):
        run_reconcile(
            DaherCancellationCoordinator(repository),
            cancellation_event_identity(),
            fence=FakeFence(held=False),
            probe_target=probe,
            write_target=write,
        )
    assert calls == []


def test_cancellation_requires_a_durable_claim(sessions: sessionmaker[Session]) -> None:
    repository = LedgerRepository(sessions)

    async def probe(_: SourceCorrelation) -> CancellationProbeResult:
        raise AssertionError("must not probe an unclaimed cancellation event")

    async def write(_: SourceCorrelation) -> CancellationProbeResult:
        raise AssertionError("must not write an unclaimed cancellation event")

    with pytest.raises(RecoveryStateError, match="no durable claim"):
        run_reconcile(
            DaherCancellationCoordinator(repository),
            cancellation_event_identity(),
            fence=FakeFence(),
            probe_target=probe,
            write_target=write,
        )


def test_dead_lettered_cancellation_requires_operator_review(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(cancellation_event_identity())
    repository.move_to_dead_letter(
        cancellation_event_identity(), payload={}, error=RuntimeError("boom"), attempts=1
    )

    async def probe(_: SourceCorrelation) -> CancellationProbeResult:
        raise AssertionError("must not probe a dead-lettered event")

    async def write(_: SourceCorrelation) -> CancellationProbeResult:
        raise AssertionError("must not write a dead-lettered event")

    with pytest.raises(DeadLetterRecoveryBlocked):
        run_reconcile(
            DaherCancellationCoordinator(repository),
            cancellation_event_identity(),
            fence=FakeFence(),
            probe_target=probe,
            write_target=write,
        )


def test_completed_cancellation_is_a_normal_duplicate_not_a_recovery_candidate(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(cancellation_event_identity())
    repository.mark_completed(
        cancellation_event_identity(), target_system="ServiceRequest", target_record_id="sr-1"
    )

    async def probe(_: SourceCorrelation) -> CancellationProbeResult:
        raise AssertionError("must not re-probe a completed cancellation event")

    async def write(_: SourceCorrelation) -> CancellationProbeResult:
        raise AssertionError("must not re-write a completed cancellation event")

    with pytest.raises(RecoveryStateError, match="normal duplicate"):
        run_reconcile(
            DaherCancellationCoordinator(repository),
            cancellation_event_identity(),
            fence=FakeFence(),
            probe_target=probe,
            write_target=write,
        )


@pytest.mark.parametrize(
    "state", [CancellationTargetState.AMBIGUOUS, CancellationTargetState.UNREACHABLE]
)
def test_ambiguous_or_unreachable_probe_raises_and_never_writes(
    sessions: sessionmaker[Session], state: CancellationTargetState
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(cancellation_event_identity())
    calls: list[str] = []

    async def probe(_: SourceCorrelation) -> CancellationProbeResult:
        calls.append("probe")
        return CancellationProbeResult(state)

    async def write(_: SourceCorrelation) -> CancellationProbeResult:
        calls.append("write")
        return CancellationProbeResult(CancellationTargetState.EXISTS_CANCELLED, "sr-1")

    with pytest.raises(AmbiguousTargetState):
        run_reconcile(
            DaherCancellationCoordinator(repository),
            cancellation_event_identity(),
            fence=FakeFence(),
            probe_target=probe,
            write_target=write,
        )
    assert calls == ["probe"]
    assert repository.get_state(cancellation_event_identity()) == "processing"


def test_already_cancelled_target_is_idempotent_no_write(sessions: sessionmaker[Session]) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(cancellation_event_identity())
    calls: list[str] = []

    async def probe(_: SourceCorrelation) -> CancellationProbeResult:
        calls.append("probe")
        return CancellationProbeResult(CancellationTargetState.EXISTS_CANCELLED, "sr-1")

    async def write(_: SourceCorrelation) -> CancellationProbeResult:
        calls.append("write")
        raise AssertionError("must not write when the probe already proves cancelled")

    result = run_reconcile(
        DaherCancellationCoordinator(repository),
        cancellation_event_identity(),
        fence=FakeFence(),
        probe_target=probe,
        write_target=write,
    )

    assert result.outcome is CancellationOutcome.CONFIRMED_CANCELLED
    assert result.record_id == "sr-1"
    assert calls == ["probe"]
    assert repository.get_state(cancellation_event_identity()) == "completed"


def test_not_yet_cancelled_target_is_written_once_and_confirmed(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(cancellation_event_identity())
    calls: list[str] = []

    async def probe(_: SourceCorrelation) -> CancellationProbeResult:
        calls.append("probe")
        return CancellationProbeResult(CancellationTargetState.EXISTS_NOT_CANCELLED, "sr-1")

    async def write(_: SourceCorrelation) -> CancellationProbeResult:
        calls.append("write")
        return CancellationProbeResult(CancellationTargetState.EXISTS_CANCELLED, "sr-1")

    result = run_reconcile(
        DaherCancellationCoordinator(repository),
        cancellation_event_identity(),
        fence=FakeFence(),
        probe_target=probe,
        write_target=write,
    )

    assert result.outcome is CancellationOutcome.CONFIRMED_CANCELLED
    assert calls == ["probe", "write"]
    assert repository.get_state(cancellation_event_identity()) == "completed"


def test_refused_cancellation_is_a_confirmed_terminal_state_not_a_failure(
    sessions: sessionmaker[Session],
) -> None:
    """ADR-018: a target the native side refuses to cancel (already in progress) is a valid,
    expected outcome -- not something to retry or dead-letter. The ledger completes with
    CONFIRMED_NOT_CANCELLED so a replay of the same cancellation event reconciles to this
    same answer rather than re-attempting forever."""
    repository = LedgerRepository(sessions)
    repository.claim(cancellation_event_identity())

    async def probe(_: SourceCorrelation) -> CancellationProbeResult:
        return CancellationProbeResult(CancellationTargetState.EXISTS_NOT_CANCELLED, "sr-1")

    async def write(_: SourceCorrelation) -> CancellationProbeResult:
        # The native ADR-018 status gate refused (e.g. Technical Acceptance/Finalized) --
        # cancel_service_request's own reconciliation confirms the target is still not cancelled.
        return CancellationProbeResult(CancellationTargetState.EXISTS_NOT_CANCELLED, "sr-1")

    result = run_reconcile(
        DaherCancellationCoordinator(repository),
        cancellation_event_identity(),
        fence=FakeFence(),
        probe_target=probe,
        write_target=write,
    )

    assert result.outcome is CancellationOutcome.CONFIRMED_NOT_CANCELLED
    assert repository.get_state(cancellation_event_identity()) == "completed"
