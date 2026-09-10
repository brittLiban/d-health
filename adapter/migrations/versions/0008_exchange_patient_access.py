"""DH-EX-005: opaque, order-scoped patient access grants.

Adds ``exchange_patient_access_grants`` (one row per issued/retired patient access credential --
only a SHA-256 ``token_hash`` is ever persisted, never the raw token; a partial unique index
enforces at most one *live* -- i.e. not yet revoked -- grant per order at the database layer, not
just in application logic), a nullable ``selected_via_patient_access_grant_id`` provenance column
on the existing ``exchange_lab_selections`` table (populated only by patient-API selections; every
pre-DH-EX-005 row stays ``NULL``), and widens ``exchange_security_audit_events``'s closed
``event_type``/``resource_type`` CHECK constraints for ``patient_access_issued``/
``patient_access_revoked`` and ``patient_access_grant`` -- the exact same purely-additive pattern
``0006``/``0007`` already established. Does NOT touch ``LabOrder``, ``LabOrderItem``, or
``LabSelection``'s existing columns.

Revision ID: 0008_exchange_patient_access
Revises: 0007_exchange_order_api
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_exchange_patient_access"
down_revision: str | Sequence[str] | None = "0007_exchange_order_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exchange_patient_access_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_order_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["lab_order_id"],
            ["exchange_lab_orders.id"],
            name="fk_exchange_patient_access_grants_lab_order",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name="ck_exchange_patient_access_grants_expiry_after_issue",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_exchange_patient_access_grants_revoked_after_issue",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_exchange_patient_access_grants_token_hash"),
    )
    op.create_index(
        "uq_exchange_patient_access_grants_one_live_per_order",
        "exchange_patient_access_grants",
        ["lab_order_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )

    op.add_column(
        "exchange_lab_selections",
        sa.Column("selected_via_patient_access_grant_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_exchange_lab_selections_patient_access_grant",
        "exchange_lab_selections",
        "exchange_patient_access_grants",
        ["selected_via_patient_access_grant_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        "event_type IN ("
        "'organization_created', 'membership_created', 'role_assigned', "
        "'catalog_entry_created', 'lab_directory_profile_created', "
        "'lab_directory_profile_updated', 'lab_offering_created', "
        "'lab_offering_status_changed', 'lab_price_changed', "
        "'lab_order_created', 'patient_access_issued', 'patient_access_revoked'"
        ")",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        "resource_type IS NULL OR resource_type IN "
        "('catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
        "'patient_access_grant')",
    )


class DowngradeBlockedByPatientAccessHistory(RuntimeError):
    """0008 -> 0007 was refused because real DH-EX-005 patient-access state exists that this
    migration cannot represent or would have to destroy to reverse (DH-EX-003R/DH-EX-004R's
    downgrade contract, applied here to DH-EX-005's own widening)."""


def downgrade() -> None:
    # Same fail-fast-before-DDL contract 0006/0007 established. Two independent checks, not one:
    # (1) any row in the new grants table at all -- dropping that table always destroys it, even a
    # single issued-but-never-used grant is real state; (2) any patient-access audit event --
    # defensive/redundant with (1) under normal operation (an audit row is only ever written in
    # the same transaction as its grant row, and grants are never physically deleted, only
    # revoked, so one implies the other), but checked independently anyway, matching 0006/0007's
    # own belt-and-suspenders precedent rather than relying on that invariant implicitly.
    bind = op.get_bind()
    grant_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_patient_access_grants")
    ).scalar_one()
    audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events WHERE "
            "event_type IN ('patient_access_issued', 'patient_access_revoked') "
            "OR resource_type = 'patient_access_grant'"
        )
    ).scalar_one()
    if grant_count or audit_count:
        raise DowngradeBlockedByPatientAccessHistory(
            f"refusing to downgrade past 0008_exchange_patient_access: "
            f"{grant_count} exchange_patient_access_grants row(s) and {audit_count} "
            f"patient-access exchange_security_audit_events row(s) exist. Reversing this "
            f"migration would require deleting patient access grants, erasing their audit "
            f"history, or nulling LabSelection provenance that depends on the grants table -- "
            f"and this migration does none of that. No schema change has been made -- the "
            f"database is unchanged and remains at 0008_exchange_patient_access. 0008 is "
            f"conditionally reversible: only before any patient access grant has been issued."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        "resource_type IS NULL OR resource_type IN "
        "('catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order')",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        "event_type IN ("
        "'organization_created', 'membership_created', 'role_assigned', "
        "'catalog_entry_created', 'lab_directory_profile_created', "
        "'lab_directory_profile_updated', 'lab_offering_created', "
        "'lab_offering_status_changed', 'lab_price_changed', "
        "'lab_order_created'"
        ")",
    )

    op.drop_constraint(
        "fk_exchange_lab_selections_patient_access_grant",
        "exchange_lab_selections",
        type_="foreignkey",
    )
    op.drop_column("exchange_lab_selections", "selected_via_patient_access_grant_id")

    op.drop_index(
        "uq_exchange_patient_access_grants_one_live_per_order",
        table_name="exchange_patient_access_grants",
    )
    op.drop_table("exchange_patient_access_grants")
