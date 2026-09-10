"""DH-EX-004: widen the transactional security audit's closed event_type/resource_type
CHECK constraints to permit lab-order creation, following the exact same purely-additive pattern
0006 established for catalog/directory/offering/price mutations. No table is created or dropped --
``LabOrder``, ``LabOrderItem``, ``DoctorReview``, and ``ResultDelivery`` already exist from
0004_exchange_core; this migration touches only ``exchange_security_audit_events``.

Revision ID: 0007_exchange_order_api
Revises: 0006_exchange_lab_directory
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_exchange_order_api"
down_revision: str | Sequence[str] | None = "0006_exchange_lab_directory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


class DowngradeBlockedByAuditHistory(RuntimeError):
    """0007 -> 0006 was refused because `exchange_security_audit_events` holds a `lab_order_created`
    row that 0006's narrower `event_type`/`resource_type` CHECK constraints cannot represent
    (DH-EX-003R's downgrade contract, applied here to DH-EX-004's own widening)."""


def downgrade() -> None:
    # Same fail-fast-before-DDL contract 0006's downgrade established: detect incompatible rows
    # first, refuse explicitly if any exist, and never delete audit history or rewrite its
    # event_type/resource_type to force the narrower schema to accept it.
    bind = op.get_bind()
    incompatible_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events WHERE "
            "event_type = 'lab_order_created' OR resource_type = 'lab_order'"
        )
    ).scalar_one()
    if incompatible_count:
        raise DowngradeBlockedByAuditHistory(
            f"refusing to downgrade past 0007_exchange_order_api: "
            f"{incompatible_count} exchange_security_audit_events row(s) exist with "
            f"event_type='lab_order_created' or resource_type='lab_order', which "
            f"0006_exchange_lab_directory's narrower CHECK constraints cannot represent. "
            f"Reversing this migration would require destroying that audit history, and this "
            f"migration does not do that. No schema change has been made -- the database is "
            f"unchanged and remains at 0007_exchange_order_api. 0007 is conditionally reversible: "
            f"only before any lab-order-creation audit history has been recorded."
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
        "('catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price')",
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
