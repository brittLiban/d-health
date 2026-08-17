"""Create the durable poll watermark table (DH-011E, ADR-015).

`watermark_raw` and `bootstrapped_raw` are String, not timestamp columns, deliberately: the
adapter stores the exact instant string the FHIR server emitted and hands it back unchanged
(WATERMARK-DESIGN.md §7). A timestamp column would invite the database or driver to normalise
the value, which is precisely what the design forbids.

Revision ID: 0002_watermarks
Revises: 0001_safety_ledger
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_watermarks"
down_revision: str | Sequence[str] | None = "0001_safety_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watermarks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("watermark_raw", sa.String(length=64), nullable=False),
        sa.Column("bootstrapped_raw", sa.String(length=64), nullable=False),
        sa.Column("bootstrapped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique in the database, not by convention: two rows for one stream would let two poll
    # cycles disagree about what had already been processed.
    op.create_index(
        "uq_watermarks_stream", "watermarks", ["direction", "resource_type"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_watermarks_stream", table_name="watermarks")
    op.drop_table("watermarks")
