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
    dead_letter: Mapped[DeadLetter | None] = relationship(back_populates="processed_message")


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
    __tablename__ = "dead_letters"
    __table_args__ = (
        CheckConstraint("attempts > 0", name="ck_dead_letters_attempts_positive"),
        Index("ix_dead_letters_created_at", "created_at"),
        Index("ix_dead_letters_resolved_at", "resolved_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    processed_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processed_messages.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    error_type: Mapped[str] = mapped_column(String(256), nullable=False)
    error_summary: Mapped[str] = mapped_column(String(1024), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(String(1024))
    processed_message: Mapped[ProcessedMessage] = relationship(back_populates="dead_letter")
