"""DH-EX-003: lab directory profile, and a purely additive generalization of the DH-EX-002R
transactional security audit table to cover catalog/directory/offering/price mutations.

Adds one new table, ``exchange_lab_directory_profiles`` (one-to-one marketplace
visibility/location profile per LAB organization -- see
``adapter/src/daher_adapter/exchange/models.py``'s ``LabDirectoryProfile`` for the field-level
reasoning), and widens ``exchange_security_audit_events`` in three ways, none of which touch any
existing row or narrow any existing meaning:

1. ``organization_id`` becomes nullable -- a platform-admin catalog-entry creation has no single
   owning organization, unlike every DH-EX-002 event type.
2. Two new nullable columns, ``resource_type``/``resource_id``, identify a catalog/directory/
   offering/price mutation's target generically, without overloading ``membership_id``/``role``
   (which keep their exact DH-EX-002 meaning, used only by org/membership/role events).
3. The ``event_type`` and ``outcome`` CHECK constraints are widened to also permit DH-EX-003's
   event types and outcomes; every DH-EX-002 value remains permitted unchanged.

Does NOT touch ``TestCatalogEntry``, ``LabOffering``, or ``LabPrice`` (already created by
0004_exchange_core) -- this ticket extends the transactional-audit and directory-profile schema
only, per DH-EX-003's explicit "do not recreate those tables" instruction.

Revision ID: 0006_exchange_lab_directory
Revises: 0005_exchange_org_auth
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_exchange_lab_directory"
down_revision: str | Sequence[str] | None = "0005_exchange_org_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exchange_lab_directory_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_organization_id", sa.Uuid(), nullable=False),
        sa.Column("listed", sa.Boolean(), nullable=False),
        sa.Column("accepting_orders", sa.Boolean(), nullable=False),
        sa.Column("public_address", sa.String(length=512), nullable=False),
        sa.Column("latitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("public_phone", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lab_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_lab_directory_profiles_lab_organization",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) "
            "OR (latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_exchange_lab_directory_profiles_lat_lon_pair",
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_exchange_lab_directory_profiles_latitude_range",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_exchange_lab_directory_profiles_longitude_range",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lab_organization_id", name="uq_exchange_lab_directory_profiles_lab_organization"
        ),
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
        "'lab_offering_status_changed', 'lab_price_changed'"
        ")",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_outcome", "exchange_security_audit_events", type_="check"
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_outcome",
        "exchange_security_audit_events",
        "outcome IN ('created', 'returned_existing', 'updated', 'unchanged')",
    )

    op.alter_column(
        "exchange_security_audit_events", "organization_id", existing_type=sa.Uuid(), nullable=True
    )

    op.add_column(
        "exchange_security_audit_events",
        sa.Column("resource_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "exchange_security_audit_events", sa.Column("resource_id", sa.Uuid(), nullable=True)
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        "resource_type IS NULL OR resource_type IN "
        "('catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price')",
    )


class DowngradeBlockedByAuditHistory(RuntimeError):
    """0006 -> 0005 was refused because `exchange_security_audit_events` holds rows only valid
    under 0006's widened shape (DH-EX-003R's downgrade contract -- see `downgrade()` below)."""


def downgrade() -> None:
    # DH-EX-003R: fail fast and explicitly, *before* any DDL, rather than letting a narrowing
    # ALTER/CHECK-constraint statement surface a raw NotNullViolation/CheckViolation partway
    # through -- and, just as importantly, rather than "fixing" that by deleting audit rows or
    # fabricating a placeholder organization_id to force compatibility. A platform-level
    # mutation's NULL organization_id, a DH-EX-003 event_type, and an 'updated'/'unchanged'
    # outcome are all real, legitimately-recorded audit facts; 0006's downgrade must never destroy
    # or misattribute them to make its own schema change succeed. `resource_type`/`resource_id`
    # need no separate check: every row that carries either is, by construction, also a DH-EX-003
    # event_type (see `exchange/api.py` -- only the catalog/directory/offering/price handlers ever
    # pass those fields), so the `event_type` clause below already catches it.
    bind = op.get_bind()
    incompatible_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events WHERE "
            "organization_id IS NULL OR "
            "event_type NOT IN ('organization_created', 'membership_created', 'role_assigned') OR "
            "outcome NOT IN ('created', 'returned_existing')"
        )
    ).scalar_one()
    if incompatible_count:
        raise DowngradeBlockedByAuditHistory(
            f"refusing to downgrade past 0006_exchange_lab_directory: "
            f"{incompatible_count} exchange_security_audit_events row(s) exist that are only "
            f"valid under DH-EX-003's widened shape (a NULL organization_id from a platform-level "
            f"mutation, a DH-EX-003 event_type, or an 'updated'/'unchanged' outcome). Reversing "
            f"this migration would require either destroying that audit history or silently "
            f"rewriting its ownership, and this migration does neither. No schema change has been "
            f"made -- the database is unchanged and remains at 0006_exchange_lab_directory. "
            f"0006 is conditionally reversible: only before any DH-EX-003 mutation of this kind "
            f"has been recorded."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.drop_column("exchange_security_audit_events", "resource_id")
    op.drop_column("exchange_security_audit_events", "resource_type")

    op.alter_column(
        "exchange_security_audit_events", "organization_id", existing_type=sa.Uuid(), nullable=False
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_outcome", "exchange_security_audit_events", type_="check"
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_outcome",
        "exchange_security_audit_events",
        "outcome IN ('created', 'returned_existing')",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        "event_type IN ('organization_created', 'membership_created', 'role_assigned')",
    )

    op.drop_table("exchange_lab_directory_profiles")
