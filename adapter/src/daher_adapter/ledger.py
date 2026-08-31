from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.models import DeadLetter, DeadLetterResolution, ProcessedMessage, utc_now

Direction = Literal["openmrs_to_openelis", "openelis_to_openmrs"]
LedgerState = Literal["processing", "completed", "dead_letter"]

# DH-011E4: the only V1 operator action (PROJECT_STATE.md's DH-011E3 §9 -- SKIP/DISCARD/
# FORCE-COMPLETE are Human Decision Required and not implemented).
ResolutionAction = Literal["retry_after_correction"]

# "processing" is the sole outcome that flips the ledger; every "refused_*" value records that an
# operator asked and was refused, without touching `ProcessedMessage.state` at all.
ResolutionOutcome = Literal[
    "processing",
    "refused_superseded",
    "refused_ambiguous",
    "refused_unreachable",
    "refused_wrong_state",
]

# DH-011E4B: the only V1 attribution source. `scripts/daher_adapter_admin.py` derives the host
# OS account (name + numeric UID) from `pwd.getpwuid(os.getuid())` on the facility host, before
# `docker compose exec` would otherwise destroy that identity, and passes both in as environment
# variables the CLI reads -- there is no code path left that accepts a free-typed `--actor` flag
# as a peer alternative (see `cli.py`'s own module docstring). This is a trust-boundary claim
# (local facility-host OS-account access), never a cryptographic non-repudiation claim.
ActorSource = Literal["host_os_wrapper"]


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
    state: LedgerState


@dataclass(frozen=True, slots=True)
class DeadLetterRecord:
    """The current (highest-`sequence`) dead-letter attempt for one source event, including its
    `payload` -- callers that only need operator-facing metadata should use `DeadLetterSummary`
    instead, so PHI is not even read into memory for a routine list (§11/§17)."""

    id: uuid.UUID
    processed_message_id: uuid.UUID
    sequence: int
    payload: dict[str, object]
    error_type: str
    error_summary: str
    attempts: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DeadLetterSummary:
    """Operator-listing view. Deliberately excludes `payload` and `error_summary` -- DH-011E3's
    own finding is that both may carry PHI/clinical content. Never render more than this by
    default (PROVENANCE.md's DH-011E3 §11)."""

    resource_type: str
    resource_id: str
    resource_version: str
    direction: str
    error_type: str
    sequence: int
    created_at: datetime


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
                existing_state = self.get_state(identity)
                if existing_state is None:
                    raise
                return ClaimResult(claimed=False, state=existing_state)

    def get_state(self, identity: MessageIdentity) -> LedgerState | None:
        with self._sessions() as session:
            state = session.scalar(
                select(ProcessedMessage.state).where(
                    ProcessedMessage.source_event_id == identity.source_event_id
                )
            )
        if state is None:
            return None
        if state not in {"processing", "completed", "dead_letter"}:
            raise LedgerStateError(f"unsupported persisted ledger state {state!r}")
        return cast(LedgerState, state)

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
            # DH-011E4: 1-based, computed under the same row lock this transaction already
            # holds on `message` -- two dead-letterings of the same event can never race for the
            # same number, and this is the first attempt (sequence 1) unless an earlier one
            # already exists (an operator-authorized retry that failed again).
            previous_max = session.scalar(
                select(func.max(DeadLetter.sequence)).where(
                    DeadLetter.processed_message_id == message.id
                )
            )
            session.add(
                DeadLetter(
                    processed_message_id=message.id,
                    sequence=(previous_max or 0) + 1,
                    payload=payload,
                    error_type=type(error).__name__[:256],
                    error_summary=_safe_error_summary(error),
                    attempts=attempts,
                )
            )

    def latest_dead_letter(self, identity: MessageIdentity) -> DeadLetterRecord | None:
        """The current attempt, or `None` if this source event has never been dead-lettered."""
        with self._sessions() as session:
            message = session.scalar(
                select(ProcessedMessage).where(
                    ProcessedMessage.source_event_id == identity.source_event_id
                )
            )
            if message is None:
                return None
            row = self._current_dead_letter(session, message.id)
            if row is None:
                return None
            return _to_record(row)

    def list_dead_letters(self) -> list[DeadLetterSummary]:
        """Metadata-only, for operator listing (§11/§17) -- never reads `payload` or
        `error_summary` into a Python value at all, let alone returns it."""
        with self._sessions() as session:
            messages = session.scalars(
                select(ProcessedMessage)
                .where(ProcessedMessage.state == "dead_letter")
                .order_by(ProcessedMessage.claimed_at)
            ).all()
            summaries: list[DeadLetterSummary] = []
            for message in messages:
                row = self._current_dead_letter(session, message.id)
                if row is None:  # pragma: no cover - a dead_letter-state message always has one
                    raise LedgerStateError(
                        f"source event {message.source_event_id!r} is in state 'dead_letter' "
                        f"but has no dead-letter row"
                    )
                summaries.append(
                    DeadLetterSummary(
                        resource_type=message.resource_type,
                        resource_id=message.resource_id,
                        resource_version=message.resource_version,
                        direction=message.direction,
                        error_type=row.error_type,
                        sequence=row.sequence,
                        created_at=row.created_at,
                    )
                )
            return summaries

    def authorize_dead_letter_retry(
        self,
        identity: MessageIdentity,
        *,
        dead_letter_id: uuid.UUID,
        action: ResolutionAction,
        reason: str,
        actor: str,
        actor_uid: int,
        actor_source: ActorSource,
        resulting_state: ResolutionOutcome,
    ) -> None:
        """DH-011E4/DH-011E4B: the only way `dead_letter` may become `processing` again.

        Row-locks `ProcessedMessage` exactly like `mark_completed`/`move_to_dead_letter` already
        do, refuses unless `state == "dead_letter"`, refuses unless `dead_letter_id` is still
        this message's current (highest-`sequence`) attempt -- guarding against a race between
        the caller's own source preflight (run outside any lock, since it is a network call) and
        this transaction -- writes one append-only `DeadLetterResolution` row, and only then, only
        when `resulting_state == "processing"`, flips the ledger. Never contacts the target: the
        actual probe-then-write happens later, in the next `poll_once()`, through the unmodified
        `RecoveryCoordinator`/DH-025 machinery -- this method's entire job is the ledger
        transition and its audit trail, nothing else.

        Called for a **refused** outcome too (superseded/ambiguous/unreachable source, or a wrong
        starting state) so every operator attempt is auditable, not only the successful ones.
        """
        if not reason.strip():
            raise ValueError("reason is required")
        if not actor.strip():
            raise ValueError("actor is required")
        if actor_uid < 0:
            raise ValueError("actor_uid must be a non-negative OS UID")
        with self._sessions.begin() as session:
            message = self._locked_message(session, identity)
            if message.state != "dead_letter":
                raise LedgerStateError(
                    f"cannot authorize a dead-letter retry for source event in state "
                    f"{message.state!r}"
                )
            current = self._current_dead_letter(session, message.id)
            if current is None or current.id != dead_letter_id:
                raise LedgerStateError(
                    "the dead-letter attempt examined by the preflight is no longer this "
                    "message's current attempt -- refusing a possibly-stale authorization"
                )
            now = utc_now()
            session.add(
                DeadLetterResolution(
                    dead_letter_id=current.id,
                    processed_message_id=message.id,
                    action=action,
                    reason=reason,
                    actor=actor,
                    actor_uid=actor_uid,
                    actor_source=actor_source,
                    requested_at=now,
                    resolved_at=now,
                    resulting_state=resulting_state,
                )
            )
            if resulting_state == "processing":
                message.state = "processing"

    @staticmethod
    def _current_dead_letter(
        session: Session, processed_message_id: uuid.UUID
    ) -> DeadLetter | None:
        return session.scalar(
            select(DeadLetter)
            .where(DeadLetter.processed_message_id == processed_message_id)
            .order_by(DeadLetter.sequence.desc())
            .limit(1)
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


def _to_record(row: DeadLetter) -> DeadLetterRecord:
    return DeadLetterRecord(
        id=row.id,
        processed_message_id=row.processed_message_id,
        sequence=row.sequence,
        payload=row.payload,
        error_type=row.error_type,
        error_summary=row.error_summary,
        attempts=row.attempts,
        created_at=row.created_at,
    )


def _safe_error_summary(error: Exception) -> str:
    # Stored only in the stack-owned adapter database. It is never emitted to application logs.
    summary = " ".join(str(error).split())
    return (summary or type(error).__name__)[:1024]
