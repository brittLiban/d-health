"""DH-CLINIC-003C: complete durable intent-to-order ownership schema.

Revision ID: 0021_order_intent_ownership
Revises: 0020_exchange_order_intents
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_order_intent_ownership"
down_revision: str | Sequence[str] | None = "0020_exchange_order_intents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable solely for pre-003C orders. New creation writes this together with
    # the intent's inverse pointer in one transaction.
    op.add_column("exchange_lab_orders", sa.Column("intent_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_exchange_lab_orders_intent_id",
        "exchange_lab_orders",
        "exchange_lab_order_intents",
        ["intent_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_exchange_lab_orders_intent_id", "exchange_lab_orders", ["intent_id"]
    )


def downgrade() -> None:
    # An ownership link is durable clinical workflow history: never silently drop it.
    bind = op.get_bind()
    owned_order_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_lab_orders WHERE intent_id IS NOT NULL")
    ).scalar_one()
    if owned_order_count:
        raise RuntimeError("refusing to downgrade 0021 while intent-owned orders exist")
    op.drop_constraint("uq_exchange_lab_orders_intent_id", "exchange_lab_orders", type_="unique")
    op.drop_constraint(
        "fk_exchange_lab_orders_intent_id",
        "exchange_lab_orders",
        type_="foreignkey",
    )
    op.drop_column("exchange_lab_orders", "intent_id")
