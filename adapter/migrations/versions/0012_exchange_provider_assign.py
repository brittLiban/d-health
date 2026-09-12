"""DH-EX-010: provider/facility-assigned lab routing (ADR-043 launch option (a)).

Widens ``exchange_security_audit_events``'s closed ``event_type`` CHECK constraint for the new
``lab_order_assigned`` event -- the exact same purely-additive pattern ``0006`` through ``0011``
already established. ``resource_type`` is unchanged: this event reuses the existing ``lab_order``
value, the same one ``lab_order_accepted``/``lab_order_rejected``/``lab_order_processing_started``
already use, since provider assignment mutates the same ``LabOrder``'s selection, not a new kind of
resource. No new table, no new column: ``LabSelection``/``LabOrderItem`` (both already nullable on
every provider-assignment-relevant column) already exist unchanged from DH-EX-001/DH-EX-005; this
ticket only adds a new repository method, an HTTP route, and this one audit event on top of them.

Revision id kept short deliberately (``0012_exchange_provider_assign``, 29 characters), following
DH-EX-009's own discovery that Alembic's ``alembic_version.version_num`` column is ``VARCHAR(32)``.

Revision ID: 0012_exchange_provider_assign
Revises: 0011_exchange_patient_ref_bind
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_exchange_provider_assign"
down_revision: str | Sequence[str] | None = "0011_exchange_patient_ref_bind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRE_0012_EVENT_TYPES = (
    "'organization_created', 'membership_created', 'role_assigned', "
    "'catalog_entry_created', 'lab_directory_profile_created', "
    "'lab_directory_profile_updated', 'lab_offering_created', "
    "'lab_offering_status_changed', 'lab_price_changed', "
    "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
    "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started', "
    "'lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded', "
    "'patient_reference_bound'"
)
_0012_NEW_EVENT_TYPES = "'lab_order_assigned'"


def upgrade() -> None:
    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0012_EVENT_TYPES}, {_0012_NEW_EVENT_TYPES})",
    )


class DowngradeBlockedByProviderAssignmentHistory(RuntimeError):
    """0012 -> 0011 was refused because real DH-EX-010 provider-lab-assignment audit history
    exists that the narrower 0011 CHECK constraint cannot represent -- the same downgrade contract
    0006 through 0011 already established, applied here to 0012's own widening. Re-applying the
    narrower CHECK against existing rows using the new event type would fail at the DDL level
    regardless -- this check fails fast, before attempting any DDL, with an explanation instead of
    a raw constraint-violation error."""


def downgrade() -> None:
    bind = op.get_bind()
    assignment_audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events "
            "WHERE event_type = 'lab_order_assigned'"
        )
    ).scalar_one()
    if assignment_audit_count:
        raise DowngradeBlockedByProviderAssignmentHistory(
            f"refusing to downgrade past 0012_exchange_provider_assign: "
            f"{assignment_audit_count} lab_order_assigned exchange_security_audit_events row(s) "
            f"exist using an event_type the narrower 0011 CHECK constraint cannot represent. "
            f"Reversing this migration would require deleting or rewriting that audit history -- "
            f"and this migration does none of that. No schema change has been made -- the "
            f"database is unchanged and remains at 0012_exchange_provider_assign. 0012 is "
            f"conditionally reversible: only before any provider has assigned a lab through this "
            f"ticket's API."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0012_EVENT_TYPES})",
    )
