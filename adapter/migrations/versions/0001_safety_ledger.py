"""Create the processed-message ledger and dead-letter table.

Revision ID: 0001_safety_ledger
Revises:
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_safety_ledger"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.String(length=768), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=256), nullable=False),
        sa.Column("resource_version", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("target_system", sa.String(length=32), nullable=True),
        sa.Column("target_record_id", sa.String(length=256), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('processing', 'completed', 'dead_letter')",
            name="ck_processed_messages_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_event_id", name="uq_processed_messages_source_event_id"),
    )
    op.create_index("ix_processed_messages_state", "processed_messages", ["state"])
    op.create_index("ix_processed_messages_claimed_at", "processed_messages", ["claimed_at"])

    op.create_table(
        "dead_letters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("processed_message_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error_type", sa.String(length=256), nullable=False),
        sa.Column("error_summary", sa.String(length=1024), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.String(length=1024), nullable=True),
        sa.CheckConstraint("attempts > 0", name="ck_dead_letters_attempts_positive"),
        sa.ForeignKeyConstraint(
            ["processed_message_id"],
            ["processed_messages.id"],
            name="fk_dead_letters_processed_message",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("processed_message_id", name="uq_dead_letters_processed_message_id"),
    )
    op.create_index("ix_dead_letters_created_at", "dead_letters", ["created_at"])
    op.create_index("ix_dead_letters_resolved_at", "dead_letters", ["resolved_at"])


def downgrade() -> None:
    op.drop_index("ix_dead_letters_resolved_at", table_name="dead_letters")
    op.drop_index("ix_dead_letters_created_at", table_name="dead_letters")
    op.drop_table("dead_letters")
    op.drop_index("ix_processed_messages_claimed_at", table_name="processed_messages")
    op.drop_index("ix_processed_messages_state", table_name="processed_messages")
    op.drop_table("processed_messages")
