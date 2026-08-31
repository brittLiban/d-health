from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"
    __table_args__ = (
        CheckConstraint(
            "state IN ('processing', 'completed', 'dead_letter')",
            name="ck_processed_messages_state",
        ),
        Index("ix_processed_messages_state", "state"),
        Index("ix_processed_messages_claimed_at", "claimed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_event_id: Mapped[str] = mapped_column(String(768), unique=True, nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(256), nullable=False)
    resource_version: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="processing")
    target_system: Mapped[str | None] = mapped_column(String(32))
    target_record_id: Mapped[str | None] = mapped_column(String(256))
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # DH-011E4: one-to-many. A dead-lettered event may be retried and dead-letter again; every
    # attempt is kept, ordered by `DeadLetter.sequence` (never by `created_at` alone -- see that
    # column's own docstring for why).
    dead_letters: Mapped[list[DeadLetter]] = relationship(
        back_populates="processed_message", order_by="DeadLetter.sequence"
    )


class Watermark(Base):
    """One durable poll position per (direction, resource_type) — DH-011E.

    `watermark_raw` is the **exact string the FHIR server emitted**, never a parsed or
    reformatted value and never a local clock reading (`WATERMARK-DESIGN.md` §2, §7). It is a
    string column for that reason: storing it as a timestamp would invite the database or the
    driver to normalise it, and the whole contract is that we hand the server back its own bytes.

    `bootstrapped_raw` records the first value ever persisted and is never updated. ADR-015
    forbids historical backfill and requires the bootstrap instant to be persisted once and
    never re-derived — re-deriving "now" at boot would skip every order placed while the
    adapter was down. Keeping the original alongside the moving position makes that auditable,
    and §8's nightly reconciliation counts only from it.
    """

    __tablename__ = "watermarks"
    __table_args__ = (
        # One position per stream, enforced by the database rather than by convention: two rows
        # for one stream would let two poll cycles disagree about what had been processed.
        Index("uq_watermarks_stream", "direction", "resource_type", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    watermark_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    bootstrapped_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    bootstrapped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    # Diagnostics only. Never used as a query bound — see §2.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class DeadLetter(Base):
    """One dead-lettering occurrence. **Never updated or deleted once written** -- DH-011E4's
    own invariant (PROVENANCE.md's DH-011E4 section, §4). A retried event that fails again gets a
    second row, not a rewrite of this one; `processed_message_id` is deliberately not unique any
    more (DH-011E3 found the original `unique=True` could not survive a second real failure).

    `resolved_at`/`resolution_note` predate DH-011E4 (migration `0001_safety_ledger`,
    2026-08-13) and were always dead code -- nothing ever wrote or read them. They are kept,
    unused, for backward compatibility rather than dropped in this pass; the real, append-only
    resolution history lives in `DeadLetterResolution` instead (never mutates this row).
    """

    __tablename__ = "dead_letters"
    __table_args__ = (
        CheckConstraint("attempts > 0", name="ck_dead_letters_attempts_positive"),
        CheckConstraint("sequence > 0", name="ck_dead_letters_sequence_positive"),
        Index("ix_dead_letters_created_at", "created_at"),
        Index("ix_dead_letters_resolved_at", "resolved_at"),
        Index("ix_dead_letters_processed_message_id", "processed_message_id"),
        Index(
            "uq_dead_letters_processed_message_sequence",
            "processed_message_id",
            "sequence",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    processed_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processed_messages.id", ondelete="RESTRICT"), nullable=False
    )
    # 1-based, assigned under the same `ProcessedMessage` row lock `move_to_dead_letter` already
    # takes (`_locked_message`), so two dead-letterings of the same event can never race for the
    # same number. The deterministic ordering key for "the current/latest attempt" -- never
    # `created_at` alone (DH-011K's own dead-letter test was bitten by exactly that class of
    # whole-second tie-break bug; `eebaa8e` fixed it there, this avoids reintroducing it here).
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    error_type: Mapped[str] = mapped_column(String(256), nullable=False)
    error_summary: Mapped[str] = mapped_column(String(1024), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(String(1024))
    processed_message: Mapped[ProcessedMessage] = relationship(back_populates="dead_letters")
    resolutions: Mapped[list[DeadLetterResolution]] = relationship(back_populates="dead_letter")


class DeadLetterResolution(Base):
    """DH-011E4: one row per operator action against a dead letter -- authorized or refused.
    Append-only: never updated, never deleted. The `DeadLetter` row it points at is never
    mutated by this table's existence; this is the audit trail, not a second copy of the error.
    """

    __tablename__ = "dead_letter_resolutions"
    __table_args__ = (
        Index("ix_dead_letter_resolutions_dead_letter_id", "dead_letter_id"),
        Index("ix_dead_letter_resolutions_processed_message_id", "processed_message_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    dead_letter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dead_letters.id", ondelete="RESTRICT"), nullable=False
    )
    # Denormalized on purpose: lets the CLI/tests query resolution history for a source event
    # without a join, and survives even if a future change ever allowed a `DeadLetter` row to be
    # archived independently of its message.
    processed_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processed_messages.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(1024), nullable=False)
    # DH-011E4B: the host OS account name, derived by `scripts/daher_adapter_admin.py` from
    # `pwd.getpwuid(os.getuid())` on the facility host *before* `docker compose exec` would
    # otherwise destroy that identity -- never a free-typed CLI flag (removed; see `cli.py`'s own
    # module docstring). This is the V1 trust boundary: local facility-host OS-account access,
    # not cryptographic non-repudiation. A host root/Docker administrator can still tamper with
    # the system; V1 does not claim tamper-proof attribution against a compromised host admin.
    actor: Mapped[str] = mapped_column(String(256), nullable=False)
    # The same account's numeric UID, recorded alongside the name because usernames can be
    # reused/recycled on a host over time and a UID is the more durable identifier.
    actor_uid: Mapped[int] = mapped_column(Integer, nullable=False)
    # Always "host_os_wrapper" in V1 -- kept as an explicit column (not a hardcoded constant in
    # code) so the audit trail is self-describing if a second, differently-trusted surface is ever
    # added later, and so it is obvious in the data itself that no other source exists today.
    actor_source: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    # "processing" (authorized) or "refused_superseded" / "refused_ambiguous" /
    # "refused_unreachable" / "refused_wrong_state" -- never "completed": this table records only
    # what the operator action itself did (a ledger-state transition or a refusal), never a
    # target-write outcome, which stays DH-025's alone.
    resulting_state: Mapped[str] = mapped_column(String(32), nullable=False)
    dead_letter: Mapped[DeadLetter] = relationship(back_populates="resolutions")
