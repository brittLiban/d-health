"""DH-011E4: allow repeated dead-letter attempts, and record operator resolution history.

`dead_letters.processed_message_id` was `UNIQUE` -- one dead-letter row per source event. DH-011E3
found this cannot survive a second real failure after an operator-authorized retry (the second
`INSERT` would violate the constraint). This migration drops that uniqueness, adds a `sequence`
column so attempts have a deterministic order (never `created_at` alone -- see `models.py`'s
`DeadLetter.sequence` docstring), and adds the append-only `dead_letter_resolutions` table that
records every operator action, authorized or refused, without ever mutating a `DeadLetter` row.

No existing row is deleted, mutated, or backfilled with an invented `sequence` other than `1`
(every dead-letter row that exists before this migration is, by definition, the first and only
attempt recorded for its message).

Revision ID: 0003_dead_letter_resolutions
Revises: 0002_watermarks
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_dead_letter_resolutions"
down_revision: str | Sequence[str] | None = "0002_watermarks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add `sequence` as nullable, backfill every existing row to 1 (its only possible value
    #    under the old UNIQUE constraint), then make it NOT NULL. Never invented for a row that
    #    did not exist before this migration ran.
    op.add_column("dead_letters", sa.Column("sequence", sa.Integer(), nullable=True))
    op.execute("UPDATE dead_letters SET sequence = 1 WHERE sequence IS NULL")
    op.alter_column("dead_letters", "sequence", nullable=False)
    op.create_check_constraint("ck_dead_letters_sequence_positive", "dead_letters", "sequence > 0")

    # 2. Drop the old one-row-per-message constraint, replace it with a non-unique index (FK
    #    columns are not auto-indexed on PostgreSQL) plus a composite uniqueness on
    #    (processed_message_id, sequence) -- many dead-letters per message now, but never two
    #    sharing the same attempt number.
    op.drop_constraint("uq_dead_letters_processed_message_id", "dead_letters", type_="unique")
    op.create_index(
        "ix_dead_letters_processed_message_id", "dead_letters", ["processed_message_id"]
    )
    op.create_index(
        "uq_dead_letters_processed_message_sequence",
        "dead_letters",
        ["processed_message_id", "sequence"],
        unique=True,
    )

    # 3. The append-only resolution-audit table. Every FK uses RESTRICT, matching
    #    `dead_letters`' own FK to `processed_messages`: this history is never allowed to be
    #    orphaned by deleting the row it is about.
    op.create_table(
        "dead_letter_resolutions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dead_letter_id", sa.Uuid(), nullable=False),
        sa.Column("processed_message_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=1024), nullable=False),
        sa.Column("actor", sa.String(length=256), nullable=False),
        sa.Column("actor_uid", sa.Integer(), nullable=False),
        sa.Column("actor_source", sa.String(length=32), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resulting_state", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["dead_letter_id"],
            ["dead_letters.id"],
            name="fk_dead_letter_resolutions_dead_letter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["processed_message_id"],
            ["processed_messages.id"],
            name="fk_dead_letter_resolutions_processed_message",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dead_letter_resolutions_dead_letter_id", "dead_letter_resolutions", ["dead_letter_id"]
    )
    op.create_index(
        "ix_dead_letter_resolutions_processed_message_id",
        "dead_letter_resolutions",
        ["processed_message_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dead_letter_resolutions_processed_message_id", table_name="dead_letter_resolutions"
    )
    op.drop_index("ix_dead_letter_resolutions_dead_letter_id", table_name="dead_letter_resolutions")
    op.drop_table("dead_letter_resolutions")

    op.drop_index("uq_dead_letters_processed_message_sequence", table_name="dead_letters")
    op.drop_index("ix_dead_letters_processed_message_id", table_name="dead_letters")
    # Downgrading past this point is only safe if no message has more than one dead-letter row --
    # the same real constraint this migration exists to relax. A downgrade on a database that has
    # actually used the new capability will fail loudly here, which is correct: silently deleting
    # the extra attempts to satisfy the old constraint would violate DH-011E4's own history
    # invariant even on the way back down.
    op.create_unique_constraint(
        "uq_dead_letters_processed_message_id", "dead_letters", ["processed_message_id"]
    )
    op.drop_constraint("ck_dead_letters_sequence_positive", "dead_letters", type_="check")
    op.drop_column("dead_letters", "sequence")
