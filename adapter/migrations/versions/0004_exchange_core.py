"""DH-EX-001: create the DHealth Exchange core domain tables.

DH-PIVOT-001's architecture audit and the resulting Human Decision (Option C, a central Exchange
that may persist minimum transaction-scoped clinical order/result data, never a longitudinal EMR)
are the basis for this schema. See ``adapter/src/daher_adapter/exchange/models.py`` for the ORM
definitions this migration must exactly match, and each table's own docstring there for the
reasoning behind individual columns/constraints.

This migration is purely additive: it creates twelve new tables and touches nothing that already
exists (``processed_messages``, ``watermarks``, ``dead_letters``, ``dead_letter_resolutions`` are
untouched). No existing row anywhere is read, mutated, or deleted by this migration.

Revision ID: 0004_exchange_core
Revises: 0003_dead_letter_resolutions
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_exchange_core"
down_revision: str | Sequence[str] | None = "0003_dead_letter_resolutions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exchange_organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "org_type IN ('PROVIDER', 'LAB')", name="ck_exchange_organizations_org_type"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')", name="ck_exchange_organizations_status"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_exchange_organizations_org_type", "exchange_organizations", ["org_type"])

    op.create_table(
        "exchange_patient_references",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_organization_id", sa.Uuid(), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_identity_system", sa.String(length=256), nullable=False),
        sa.Column("source_identity_value", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("contact_channel_type", sa.String(length=32), nullable=True),
        sa.Column("contact_value", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_patient_references_source_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_organization_id",
            "source_system",
            "source_identity_system",
            "source_identity_value",
            name="uq_exchange_patient_references_source_identity",
        ),
    )
    op.create_index(
        "ix_exchange_patient_references_source_organization_id",
        "exchange_patient_references",
        ["source_organization_id"],
    )

    op.create_table(
        "exchange_practitioner_references",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_organization_id", sa.Uuid(), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_identity_system", sa.String(length=256), nullable=False),
        sa.Column("source_identity_value", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_practitioner_references_source_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_organization_id",
            "source_system",
            "source_identity_system",
            "source_identity_value",
            name="uq_exchange_practitioner_references_source_identity",
        ),
    )
    op.create_index(
        "ix_exchange_practitioner_references_source_organization_id",
        "exchange_practitioner_references",
        ["source_organization_id"],
    )

    op.create_table(
        "exchange_test_catalog_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "code", "version", name="uq_exchange_test_catalog_entries_code_version"
        ),
    )

    op.create_table(
        "exchange_lab_offerings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_organization_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_entry_id", sa.Uuid(), nullable=False),
        sa.Column("local_test_code", sa.String(length=128), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lab_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_lab_offerings_lab_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["catalog_entry_id"],
            ["exchange_test_catalog_entries.id"],
            name="fk_exchange_lab_offerings_catalog_entry",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lab_organization_id", "catalog_entry_id", name="uq_exchange_lab_offerings_lab_catalog"
        ),
    )
    op.create_index(
        "ix_exchange_lab_offerings_catalog_entry_id", "exchange_lab_offerings", ["catalog_entry_id"]
    )

    op.create_table(
        "exchange_lab_prices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_offering_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["lab_offering_id"],
            ["exchange_lab_offerings.id"],
            name="fk_exchange_lab_prices_lab_offering",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_exchange_lab_prices_amount_non_negative"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Partial unique index: at most one CURRENT (superseded_at IS NULL) price per offering.
    # Updating price supersedes the old row rather than mutating it -- see models.py's LabPrice
    # docstring. `sqlite_where` lets this same index definition apply to the SQLite unit-test
    # engine as well as PostgreSQL.
    op.create_index(
        "uq_exchange_lab_prices_current_per_offering",
        "exchange_lab_prices",
        ["lab_offering_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        sqlite_where=sa.text("superseded_at IS NULL"),
    )

    op.create_table(
        "exchange_lab_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("provider_organization_id", sa.Uuid(), nullable=False),
        sa.Column("patient_reference_id", sa.Uuid(), nullable=False),
        sa.Column("practitioner_reference_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_lab_orders_provider_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_reference_id"],
            ["exchange_patient_references.id"],
            name="fk_exchange_lab_orders_patient_reference",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["practitioner_reference_id"],
            ["exchange_practitioner_references.id"],
            name="fk_exchange_lab_orders_practitioner_reference",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('AWAITING_LAB_SELECTION', 'SELECTED', 'ACCEPTED', 'IN_PROGRESS', "
            "'COMPLETED', 'REJECTED', 'CANCELLED')",
            name="ck_exchange_lab_orders_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_organization_id",
            "idempotency_key",
            name="uq_exchange_lab_orders_provider_idempotency_key",
        ),
    )
    op.create_index(
        "ix_exchange_lab_orders_provider_organization_id",
        "exchange_lab_orders",
        ["provider_organization_id"],
    )
    op.create_index("ix_exchange_lab_orders_status", "exchange_lab_orders", ["status"])

    op.create_table(
        "exchange_lab_order_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_order_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_entry_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_code_snapshot", sa.String(length=64), nullable=False),
        sa.Column("catalog_display_name_snapshot", sa.String(length=256), nullable=False),
        sa.Column("selected_price_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("selected_price_currency", sa.String(length=3), nullable=True),
        sa.ForeignKeyConstraint(
            ["lab_order_id"],
            ["exchange_lab_orders.id"],
            name="fk_exchange_lab_order_items_lab_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["catalog_entry_id"],
            ["exchange_test_catalog_entries.id"],
            name="fk_exchange_lab_order_items_catalog_entry",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "(selected_price_amount IS NULL) = (selected_price_currency IS NULL)",
            name="ck_exchange_lab_order_items_price_snapshot_pairing",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_exchange_lab_order_items_lab_order_id", "exchange_lab_order_items", ["lab_order_id"]
    )

    op.create_table(
        "exchange_lab_selections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_order_id", sa.Uuid(), nullable=False),
        sa.Column("lab_organization_id", sa.Uuid(), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lab_order_id"],
            ["exchange_lab_orders.id"],
            name="fk_exchange_lab_selections_lab_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lab_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_lab_selections_lab_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lab_order_id", name="uq_exchange_lab_selections_lab_order_id"),
    )
    op.create_index(
        "ix_exchange_lab_selections_lab_organization_id",
        "exchange_lab_selections",
        ["lab_organization_id"],
    )

    op.create_table(
        "exchange_lab_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_order_item_id", sa.Uuid(), nullable=False),
        sa.Column("lab_organization_id", sa.Uuid(), nullable=False),
        sa.Column("result_type", sa.String(length=16), nullable=False),
        sa.Column("numeric_value", sa.Numeric(18, 4), nullable=True),
        sa.Column("numeric_unit", sa.String(length=32), nullable=True),
        sa.Column("reference_range_low", sa.Numeric(18, 4), nullable=True),
        sa.Column("reference_range_high", sa.Numeric(18, 4), nullable=True),
        sa.Column("text_value", sa.String(length=2048), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lab_order_item_id"],
            ["exchange_lab_order_items.id"],
            name="fk_exchange_lab_results_lab_order_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lab_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_lab_results_lab_organization",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "result_type IN ('NUMERIC', 'TEXT')", name="ck_exchange_lab_results_result_type"
        ),
        sa.CheckConstraint(
            "(result_type = 'NUMERIC' AND numeric_value IS NOT NULL AND text_value IS NULL) OR "
            "(result_type = 'TEXT' AND text_value IS NOT NULL AND numeric_value IS NULL)",
            name="ck_exchange_lab_results_value_matches_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lab_order_item_id", name="uq_exchange_lab_results_lab_order_item_id"),
    )
    op.create_index(
        "ix_exchange_lab_results_lab_organization_id",
        "exchange_lab_results",
        ["lab_organization_id"],
    )

    op.create_table(
        "exchange_result_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_order_id", sa.Uuid(), nullable=False),
        sa.Column("provider_organization_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["lab_order_id"],
            ["exchange_lab_orders.id"],
            name="fk_exchange_result_deliveries_lab_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_result_deliveries_provider_organization",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'DELIVERED', 'FAILED')",
            name="ck_exchange_result_deliveries_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lab_order_id", name="uq_exchange_result_deliveries_lab_order_id"),
    )

    op.create_table(
        "exchange_doctor_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_order_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_practitioner_reference_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["lab_order_id"],
            ["exchange_lab_orders.id"],
            name="fk_exchange_doctor_reviews_lab_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_practitioner_reference_id"],
            ["exchange_practitioner_references.id"],
            name="fk_exchange_doctor_reviews_reviewed_by",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'NO_FOLLOW_UP_REQUIRED', 'FOLLOW_UP_REQUIRED')",
            name="ck_exchange_doctor_reviews_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lab_order_id", name="uq_exchange_doctor_reviews_lab_order_id"),
    )


def downgrade() -> None:
    # Reverse dependency order. Every table here was created by this migration alone, so
    # dropping is unconditional -- there is no pre-existing data this migration could destroy.
    op.drop_table("exchange_doctor_reviews")
    op.drop_table("exchange_result_deliveries")
    op.drop_index("ix_exchange_lab_results_lab_organization_id", table_name="exchange_lab_results")
    op.drop_table("exchange_lab_results")
    op.drop_table("exchange_lab_selections")
    op.drop_index("ix_exchange_lab_order_items_lab_order_id", table_name="exchange_lab_order_items")
    op.drop_table("exchange_lab_order_items")
    op.drop_index("ix_exchange_lab_orders_status", table_name="exchange_lab_orders")
    op.drop_index(
        "ix_exchange_lab_orders_provider_organization_id", table_name="exchange_lab_orders"
    )
    op.drop_table("exchange_lab_orders")
    op.drop_index("uq_exchange_lab_prices_current_per_offering", table_name="exchange_lab_prices")
    op.drop_table("exchange_lab_prices")
    op.drop_index("ix_exchange_lab_offerings_catalog_entry_id", table_name="exchange_lab_offerings")
    op.drop_table("exchange_lab_offerings")
    op.drop_table("exchange_test_catalog_entries")
    op.drop_index(
        "ix_exchange_practitioner_references_source_organization_id",
        table_name="exchange_practitioner_references",
    )
    op.drop_table("exchange_practitioner_references")
    op.drop_index(
        "ix_exchange_patient_references_source_organization_id",
        table_name="exchange_patient_references",
    )
    op.drop_table("exchange_patient_references")
    op.drop_index("ix_exchange_organizations_org_type", table_name="exchange_organizations")
    op.drop_table("exchange_organizations")
