from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Protocol, Self

from sqlalchemy import Connection, Engine, text

from daher_adapter.ledger import LedgerRepository, MessageIdentity

SOURCE_CORRELATION_SYSTEM = "urn:daher-health:adapter:source-event"

# Stable project-scoped signed bigint used only for PostgreSQL's session advisory lock.
_RECOVERY_LOCK_ID = 4_918_923_047_219_041_860


class RecoveryStateError(RuntimeError):
    """Recovery was attempted without its required state or exclusive fence."""


class RecoveryFenceUnavailable(RuntimeError):
    """Another process owns recovery, or the database cannot provide the required fence."""


class AmbiguousTargetState(RuntimeError):
    """The target cannot prove whether the source event was already written."""


class DeadLetterRecoveryBlocked(RuntimeError):
    """A dead-lettered event requires an explicit operator resolution path."""


@dataclass(frozen=True, slots=True)
class SourceCorrelation:
    system: str
    value: str


@dataclass(frozen=True, slots=True)
class TargetRecord:
    system: str
    record_id: str

    def __post_init__(self) -> None:
        if not self.system or len(self.system) > 32:
            raise ValueError("target system must contain 1 to 32 characters")
        if not self.record_id or len(self.record_id) > 256:
            raise ValueError("target record ID must contain 1 to 256 characters")


class TargetPresence(StrEnum):
    FOUND = "found"
    ABSENT = "absent"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class TargetProbeResult:
    presence: TargetPresence
    record: TargetRecord | None = None

    def __post_init__(self) -> None:
        if self.presence is TargetPresence.FOUND and self.record is None:
            raise ValueError("a found target probe requires the target record")
        if self.presence is not TargetPresence.FOUND and self.record is not None:
            raise ValueError("only a found target probe may include a target record")


class RecoveryAction(StrEnum):
    RECONCILED_EXISTING = "reconciled_existing"
    RETRIED_AFTER_PROVEN_ABSENCE = "retried_after_proven_absence"


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    action: RecoveryAction
    correlation: SourceCorrelation
    target: TargetRecord


class RecoveryFence(Protocol):
    @property
    def held(self) -> bool: ...


class ManagedRecoveryFence(RecoveryFence, Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


TargetProbe = Callable[[SourceCorrelation], Awaitable[TargetProbeResult]]
TargetWriter = Callable[[SourceCorrelation], Awaitable[TargetRecord]]


def source_correlation(identity: MessageIdentity) -> SourceCorrelation:
    digest = hashlib.sha256(identity.source_event_id.encode("utf-8")).hexdigest()
    return SourceCorrelation(system=SOURCE_CORRELATION_SYSTEM, value=digest)


class PostgresRecoveryFence:
    """Session advisory lock held for the adapter lifetime, including recovery and transport."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def __enter__(self) -> Self:
        if self._connection is not None:
            raise RecoveryStateError("recovery fence is already entered")
        if self._engine.dialect.name != "postgresql":
            raise RecoveryFenceUnavailable("recovery fence requires PostgreSQL")

        connection = self._engine.connect()
        acquired = bool(
            connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": _RECOVERY_LOCK_ID},
            )
        )
        if not acquired:
            connection.close()
            raise RecoveryFenceUnavailable("another adapter process owns startup recovery")

        self._connection = connection
        self._held = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        connection = self._connection
        self._held = False
        self._connection = None
        if connection is None:
            return
        try:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": _RECOVERY_LOCK_ID},
            )
        finally:
            connection.close()


class RecoveryCoordinator:
    """Resolve durable in-flight claims while the process-lifetime fence excludes other workers.

    The concrete target writer must attach the exact ``SourceCorrelation`` it receives. The probe
    may return FOUND only for exactly one matching target, ABSENT only after a successful lookup
    proves zero matches, and AMBIGUOUS for an unreachable target or multiple matches.
    """

    def __init__(self, ledger: LedgerRepository) -> None:
        self._ledger = ledger

    async def reconcile(
        self,
        identity: MessageIdentity,
        *,
        fence: RecoveryFence,
        probe_target: TargetProbe,
        write_target: TargetWriter,
    ) -> RecoveryResult:
        if not fence.held:
            raise RecoveryStateError("exclusive startup recovery fence is not held")

        correlation = source_correlation(identity)
        state = self._ledger.get_state(identity)
        if state is None:
            raise RecoveryStateError("source event has no durable claim to recover")
        if state == "dead_letter":
            raise DeadLetterRecoveryBlocked("dead-lettered source event requires operator review")
        if state == "completed":
            raise RecoveryStateError(
                "completed source event is a normal duplicate, not a recovery candidate"
            )

        probe = await probe_target(correlation)
        if probe.presence is TargetPresence.AMBIGUOUS:
            raise AmbiguousTargetState("target lookup could not prove presence or absence")

        if probe.presence is TargetPresence.FOUND:
            target = probe.record
            if target is None:  # Defensive; TargetProbeResult validates this at construction.
                raise RecoveryStateError("found target probe omitted its record")
            action = RecoveryAction.RECONCILED_EXISTING
        else:
            target = await write_target(correlation)
            action = RecoveryAction.RETRIED_AFTER_PROVEN_ABSENCE

        self._ledger.mark_completed(
            identity,
            target_system=target.system,
            target_record_id=target.record_id,
        )
        return RecoveryResult(action=action, correlation=correlation, target=target)
