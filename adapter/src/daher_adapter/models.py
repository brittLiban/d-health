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
