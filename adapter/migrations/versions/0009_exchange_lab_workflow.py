"""DH-EX-006: selected-lab assigned-order workflow (accept / reject / start processing).

Widens ``exchange_security_audit_events``'s closed ``event_type`` CHECK constraint for
``lab_order_accepted``/``lab_order_rejected``/``lab_order_processing_started`` -- the exact same
purely-additive pattern ``0006``/``0007``/``0008`` already established. No new table, no new
column: this ticket's own transitions (``SELECTED``->``ACCEPTED``, ``SELECTED``->``REJECTED``,
``ACCEPTED``->``IN_PROGRESS``) already fit inside ``LabOrder.status`` (unchanged CHECK, unchanged
values) and the existing ``LabOrder.updated_at`` (``onupdate=utc_now``, already reliably
maintained on every status write). ``resource_type`` needs no widening -- ``'lab_order'`` already
exists from ``0007``. Does NOT touch ``LabSelection``, ``LabOrderItem``, or any patient-access
table.

Revision ID: 0009_exchange_lab_workflow
Revises: 0008_exchange_patient_access
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_exchange_lab_workflow"
down_revision: str | Sequence[str] | None = "0008_exchange_patient_access"
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
        "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
        "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started'"
        ")",
    )


class DowngradeBlockedByLabWorkflowHistory(RuntimeError):
    """0009 -> 0008 was refused because real DH-EX-006 lab-workflow audit history exists that the
    narrower 0008 CHECK constraint cannot represent (DH-EX-003R/DH-EX-004R/DH-EX-005R's downgrade
    contract, applied here to DH-EX-006's own widening). Re-applying the narrower CHECK against
    existing rows using the new event types would fail at the DDL level regardless -- this check
    fails fast, before attempting any DDL, with an explanation instead of a raw constraint-
    violation error."""


def downgrade() -> None:
    bind = op.get_bind()
    workflow_audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events WHERE event_type IN "
            "('lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started')"
        )
    ).scalar_one()
    if workflow_audit_count:
        raise DowngradeBlockedByLabWorkflowHistory(
            f"refusing to downgrade past 0009_exchange_lab_workflow: "
            f"{workflow_audit_count} lab-workflow exchange_security_audit_events row(s) exist "
            f"using an event_type the narrower 0008 CHECK constraint cannot represent. Reversing "
            f"this migration would require deleting or rewriting that audit history -- and this "
            f"migration does none of that. No schema change has been made -- the database is "
            f"unchanged and remains at 0009_exchange_lab_workflow. 0009 is conditionally "
            f"reversible: only before any lab has accepted, rejected, or started processing an "
            f"assigned order."
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
