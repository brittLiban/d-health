"""DH-CLINIC-003C: backend-owned durable canonical order intent and exact replay.

This migration creates the tables for LabOrderIntent and LabOrderIntentItem.
An intent represents an immutable, backend-durable diagnostic order preparation
created before the final mutation that creates a LabOrder.
This allows the client to recover the exact diagnostic intent across browser crashes
and page reloads without storing diagnostic service IDs or test names in browser
local storage.

New tables:
- `exchange_lab_order_intents`
- `exchange_lab_order_intent_items`

Revision ID: 0020_exchange_order_intents
Revises: 0019_exchange_amendments
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_exchange_order_intents"
down_revision: str | Sequence[str] | None = "0019_exchange_amendments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class DowngradeBlockedByOrderIntentHistory(RuntimeError):
    """0020 -> 0019 was refused because real `exchange_lab_order_intents` rows exist --
    dropping the table would destroy truthful clinical order intent records."""


def upgrade() -> None:
    op.create_table(
        "exchange_lab_order_intents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column(
            "provider_organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "patient_reference_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_patient_references.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "practitioner_reference_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_practitioner_references.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "committed_lab_order_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider_organization_id",
            "idempotency_key",
            name="uq_exchange_lab_order_intents_provider_idempotency_key",
        ),
        sa.UniqueConstraint(
            "committed_lab_order_id",
            name="uq_exchange_lab_order_intents_committed_order",
        ),
    )
    op.create_index(
        "ix_exchange_lab_order_intents_provider_org_id",
        "exchange_lab_order_intents",
        ["provider_organization_id"],
    )
    op.create_index(
        "ix_exchange_lab_order_intents_patient_ref_id",
        "exchange_lab_order_intents",
        ["provider_organization_id", "patient_reference_id"],
    )

    op.create_table(
        "exchange_lab_order_intent_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "intent_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_order_intents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "catalog_entry_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_test_catalog_entries.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("catalog_code_snapshot", sa.String(length=64), nullable=False),
        sa.Column("catalog_display_name_snapshot", sa.String(length=256), nullable=False),
        sa.UniqueConstraint(
            "intent_id",
            "catalog_entry_id",
            name="uq_exchange_lab_order_intent_items_intent_entry",
        ),
    )
    op.create_index(
        "ix_exchange_lab_order_intent_items_intent_id",
        "exchange_lab_order_intent_items",
        ["intent_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    intent_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_lab_order_intents")
    ).scalar_one()
    if intent_count:
        raise DowngradeBlockedByOrderIntentHistory(
            f"refusing to downgrade past 0020_exchange_order_intents: {intent_count} "
            f"exchange_lab_order_intents row(s) exist. No schema change has been made -- "
            f"the database is unchanged and remains at 0020_exchange_order_intents."
        )

    op.drop_index(
        "ix_exchange_lab_order_intent_items_intent_id",
        table_name="exchange_lab_order_intent_items",
    )
    op.drop_table("exchange_lab_order_intent_items")

    op.drop_index(
        "ix_exchange_lab_order_intents_patient_ref_id",
        table_name="exchange_lab_order_intents",
    )
    op.drop_index(
        "ix_exchange_lab_order_intents_provider_org_id",
        table_name="exchange_lab_order_intents",
    )
    op.drop_table("exchange_lab_order_intents")
