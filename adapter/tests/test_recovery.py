from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.ledger import LedgerRepository, MessageIdentity
from daher_adapter.recovery import (
    AmbiguousTargetState,
    DeadLetterRecoveryBlocked,
    PostgresRecoveryFence,
    RecoveryAction,
    RecoveryCoordinator,
    RecoveryFenceUnavailable,
    RecoveryStateError,
    SourceCorrelation,
    TargetPresence,
    TargetProbeResult,
    TargetRecord,
    source_correlation,
)


def identity(*, version: str = "1") -> MessageIdentity:
    return MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id="order-uuid",
        resource_version=version,
    )


@dataclass
class FakeFence:
    held: bool = True


def run_recovery(
    coordinator: RecoveryCoordinator,
    event: MessageIdentity,
    *,
    fence: FakeFence,
    probe_target,
    write_target,
):
    return asyncio.run(
        coordinator.reconcile(
            event,
            fence=fence,
            probe_target=probe_target,
            write_target=write_target,
        )
    )


def test_source_correlation_is_stable_opaque_and_version_specific() -> None:
    first = source_correlation(identity())
    repeat = source_correlation(identity())
    revised = source_correlation(identity(version="2"))

    assert first == repeat
    assert first.system == "urn:daher-health:adapter:source-event"
    assert len(first.value) == 64
    assert "order-uuid" not in first.value
    assert revised.value != first.value


def test_recovery_requires_exclusive_fence(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    calls: list[str] = []

    async def probe(_: SourceCorrelation) -> TargetProbeResult:
        calls.append("probe")
        return TargetProbeResult(TargetPresence.ABSENT)

    async def write(_: SourceCorrelation) -> TargetRecord:
        calls.append("write")
        return TargetRecord("openelis", "task-1")

    with pytest.raises(RecoveryStateError, match="fence"):
        run_recovery(
            RecoveryCoordinator(repository),
            identity(),
            fence=FakeFence(held=False),
            probe_target=probe,
            write_target=write,
        )

    assert calls == []
    assert repository.get_state(identity()) == "processing"


def test_crash_before_write_retries_only_after_proven_absence(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    writes: list[SourceCorrelation] = []

    async def probe(_: SourceCorrelation) -> TargetProbeResult:
        return TargetProbeResult(TargetPresence.ABSENT)

    async def write(correlation: SourceCorrelation) -> TargetRecord:
        writes.append(correlation)
        return TargetRecord("openelis", "task-created-on-recovery")

    result = run_recovery(
        RecoveryCoordinator(repository),
        identity(),
        fence=FakeFence(),
        probe_target=probe,
        write_target=write,
    )

    assert result.action is RecoveryAction.RETRIED_AFTER_PROVEN_ABSENCE
    assert writes == [source_correlation(identity())]
    assert repository.get_state(identity()) == "completed"
    assert repository.claim(identity()).state == "completed"


def test_crash_after_write_reconciles_without_second_write(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    existing = TargetRecord("openelis", "task-created-before-crash")
    writes: list[SourceCorrelation] = []

    async def probe(correlation: SourceCorrelation) -> TargetProbeResult:
        assert correlation == source_correlation(identity())
        return TargetProbeResult(TargetPresence.FOUND, existing)

    async def write(correlation: SourceCorrelation) -> TargetRecord:
        writes.append(correlation)
        return TargetRecord("openelis", "duplicate")

    result = run_recovery(
        RecoveryCoordinator(repository),
        identity(),
        fence=FakeFence(),
        probe_target=probe,
        write_target=write,
    )

    assert result.action is RecoveryAction.RECONCILED_EXISTING
    assert result.target == existing
    assert writes == []
    assert repository.get_state(identity()) == "completed"


def test_ambiguous_probe_fails_closed_and_preserves_processing(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    writes: list[SourceCorrelation] = []

    async def probe(_: SourceCorrelation) -> TargetProbeResult:
        return TargetProbeResult(TargetPresence.AMBIGUOUS)

    async def write(correlation: SourceCorrelation) -> TargetRecord:
        writes.append(correlation)
        return TargetRecord("openelis", "must-not-exist")

    with pytest.raises(AmbiguousTargetState, match="presence or absence"):
        run_recovery(
            RecoveryCoordinator(repository),
            identity(),
            fence=FakeFence(),
            probe_target=probe,
            write_target=write,
        )

    assert writes == []
    assert repository.get_state(identity()) == "processing"


def test_retry_write_failure_preserves_processing(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())

    async def probe(_: SourceCorrelation) -> TargetProbeResult:
        return TargetProbeResult(TargetPresence.ABSENT)

    async def write(_: SourceCorrelation) -> TargetRecord:
        raise TimeoutError("synthetic target outage")

    with pytest.raises(TimeoutError, match="synthetic"):
        run_recovery(
            RecoveryCoordinator(repository),
            identity(),
            fence=FakeFence(),
            probe_target=probe,
            write_target=write,
        )

    assert repository.get_state(identity()) == "processing"


def test_dead_letter_is_never_recovered_as_a_duplicate(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.move_to_dead_letter(
        identity(),
        payload={"resourceType": "ServiceRequest"},
        error=ValueError("synthetic invalid mapping"),
        attempts=1,
    )
    calls: list[str] = []

    async def probe(_: SourceCorrelation) -> TargetProbeResult:
        calls.append("probe")
        return TargetProbeResult(TargetPresence.ABSENT)

    async def write(_: SourceCorrelation) -> TargetRecord:
        calls.append("write")
        return TargetRecord("openelis", "must-not-exist")

    with pytest.raises(DeadLetterRecoveryBlocked, match="operator"):
        run_recovery(
            RecoveryCoordinator(repository),
            identity(),
            fence=FakeFence(),
            probe_target=probe,
            write_target=write,
        )

    assert calls == []
    assert repository.get_state(identity()) == "dead_letter"


def test_completed_duplicate_is_not_a_recovery_candidate(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)
    repository.claim(identity())
    repository.mark_completed(identity(), target_system="openelis", target_record_id="task-1")
    duplicate = repository.claim(identity())
    calls: list[str] = []

    async def probe(_: SourceCorrelation) -> TargetProbeResult:
        calls.append("probe")
        return TargetProbeResult(TargetPresence.ABSENT)

    async def write(_: SourceCorrelation) -> TargetRecord:
        calls.append("write")
        return TargetRecord("openelis", "must-not-exist")

    assert duplicate.claimed is False
    assert duplicate.state == "completed"
    with pytest.raises(RecoveryStateError, match="normal duplicate"):
        run_recovery(
            RecoveryCoordinator(repository),
            identity(),
            fence=FakeFence(),
            probe_target=probe,
            write_target=write,
        )
    assert calls == []


def test_recovery_rejects_missing_claim(
    sessions: sessionmaker[Session],
) -> None:
    repository = LedgerRepository(sessions)

    async def probe(_: SourceCorrelation) -> TargetProbeResult:
        raise AssertionError("probe must not run")

    async def write(_: SourceCorrelation) -> TargetRecord:
        raise AssertionError("write must not run")

    with pytest.raises(RecoveryStateError, match="no durable claim"):
        run_recovery(
            RecoveryCoordinator(repository),
            identity(),
            fence=FakeFence(),
            probe_target=probe,
            write_target=write,
        )


def test_postgres_fence_refuses_non_postgres_engine(engine: Engine) -> None:
    fence = PostgresRecoveryFence(engine)

    with pytest.raises(RecoveryFenceUnavailable, match="PostgreSQL"):
        with fence:
            raise AssertionError("unreachable")

    assert fence.held is False


def test_target_probe_result_validates_presence_contract() -> None:
    with pytest.raises(ValueError, match="requires"):
        TargetProbeResult(TargetPresence.FOUND)
    with pytest.raises(ValueError, match="only a found"):
        TargetProbeResult(TargetPresence.ABSENT, TargetRecord("openelis", "task-1"))
