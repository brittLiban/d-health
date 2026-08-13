from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.models import DeadLetter, ProcessedMessage, utc_now

Direction = Literal["openmrs_to_openelis", "openelis_to_openmrs"]


class LedgerStateError(RuntimeError):
    """A ledger entry was asked to make an invalid state transition."""


@dataclass(frozen=True, slots=True)
class MessageIdentity:
    source_system: str
    direction: Direction
    resource_type: str
    resource_id: str
    resource_version: str

    def __post_init__(self) -> None:
        if self.direction not in {"openmrs_to_openelis", "openelis_to_openmrs"}:
            raise ValueError("direction is not supported")
        fields = {
            "source_system": (self.source_system, 32),
            "resource_type": (self.resource_type, 64),
            "resource_id": (self.resource_id, 256),
            "resource_version": (self.resource_version, 128),
        }
        for name, (value, maximum) in fields.items():
            if not value or len(value) > maximum:
                raise ValueError(f"{name} must contain 1 to {maximum} characters")

    @property
    def source_event_id(self) -> str:
        # A compact JSON tuple is unambiguous even if an upstream identifier contains a delimiter.
        return json.dumps(
            [
                self.source_system,
                self.direction,
                self.resource_type,
                self.resource_id,
                self.resource_version,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class ClaimResult:
    claimed: bool
    state: str


class LedgerRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def claim(self, identity: MessageIdentity) -> ClaimResult:
        message = ProcessedMessage(
            source_event_id=identity.source_event_id,
            source_system=identity.source_system,
            direction=identity.direction,
            resource_type=identity.resource_type,
            resource_id=identity.resource_id,
            resource_version=identity.resource_version,
            state="processing",
        )
        with self._sessions() as session:
            try:
                session.add(message)
                session.commit()
                return ClaimResult(claimed=True, state="processing")
            except IntegrityError:
                session.rollback()
                existing_state = session.scalar(
                    select(ProcessedMessage.state).where(
                        ProcessedMessage.source_event_id == identity.source_event_id
                    )
                )
                if existing_state is None:
                    raise
                return ClaimResult(claimed=False, state=existing_state)

    def mark_completed(
        self, identity: MessageIdentity, *, target_system: str, target_record_id: str
    ) -> None:
        with self._sessions.begin() as session:
            message = self._locked_message(session, identity)
            if message.state != "processing":
                raise LedgerStateError(f"cannot complete source event in state {message.state!r}")
            message.state = "completed"
            message.target_system = target_system
            message.target_record_id = target_record_id
            message.completed_at = utc_now()

    def move_to_dead_letter(
        self,
        identity: MessageIdentity,
        *,
        payload: dict[str, object],
        error: Exception,
        attempts: int,
    ) -> None:
        if attempts <= 0:
            raise ValueError("attempts must be greater than zero")
        with self._sessions.begin() as session:
            message = self._locked_message(session, identity)
            if message.state != "processing":
                raise LedgerStateError(
                    f"cannot dead-letter source event in state {message.state!r}"
                )
            message.state = "dead_letter"
            session.add(
                DeadLetter(
                    processed_message_id=message.id,
                    payload=payload,
                    error_type=type(error).__name__[:256],
                    error_summary=_safe_error_summary(error),
                    attempts=attempts,
                )
            )

    @staticmethod
    def _locked_message(session: Session, identity: MessageIdentity) -> ProcessedMessage:
        message = session.scalar(
            select(ProcessedMessage)
            .where(ProcessedMessage.source_event_id == identity.source_event_id)
            .with_for_update()
        )
        if message is None:
            raise LedgerStateError("source event has not been claimed")
        return message


def _safe_error_summary(error: Exception) -> str:
    # Stored only in the stack-owned adapter database. It is never emitted to application logs.
    summary = " ".join(str(error).split())
    return (summary or type(error).__name__)[:1024]
