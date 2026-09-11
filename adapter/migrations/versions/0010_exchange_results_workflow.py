"""DH-EX-007: lab result recording, order completion, and doctor review.

Widens ``exchange_security_audit_events``'s closed ``event_type`` CHECK constraint for
``lab_result_recorded``/``lab_order_completed``/``doctor_review_recorded`` -- the exact same
purely-additive pattern ``0006``/``0007``/``0008``/``0009`` already established. No new table, no
new column: ``LabOrder.status`` already includes ``COMPLETED`` (unchanged CHECK, unchanged values,
already reachable per ``exchange/orders.py``'s ``_ALLOWED_TRANSITIONS``), ``LabOrder.updated_at``
already advances on every status write, and ``LabResult.completed_at``/``DoctorReview.reviewed_at``
already exist and are unused until this ticket populates them. ``resource_type`` needs no widening
-- ``'lab_order'`` already exists from ``0007`` and this ticket's three new events all reference a
``LabOrder`` id, never a sub-item id (DH-EX-007's own scope decision: audit stays order-centric).
Does NOT touch ``LabResult``, ``LabOrderItem``, ``ResultDelivery``, or ``LabSelection``.

Revision ID: 0010_exchange_results_workflow
Revises: 0009_exchange_lab_workflow
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_exchange_results_workflow"
down_revision: str | Sequence[str] | None = "0009_exchange_lab_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRE_0010_EVENT_TYPES = (
    "'organization_created', 'membership_created', 'role_assigned', "
    "'catalog_entry_created', 'lab_directory_profile_created', "
    "'lab_directory_profile_updated', 'lab_offering_created', "
    "'lab_offering_status_changed', 'lab_price_changed', "
    "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
    "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started'"
)
_0010_NEW_EVENT_TYPES = "'lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded'"


def upgrade() -> None:
    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0010_EVENT_TYPES}, {_0010_NEW_EVENT_TYPES})",
    )


class DowngradeBlockedByResultsWorkflowHistory(RuntimeError):
    """0010 -> 0009 was refused because real DH-EX-007 results-workflow audit history exists that
    the narrower 0009 CHECK constraint cannot represent (DH-EX-003R through DH-EX-006's downgrade
    contract, applied here to DH-EX-007's own widening). Re-applying the narrower CHECK against
    existing rows using the new event types would fail at the DDL level regardless -- this check
    fails fast, before attempting any DDL, with an explanation instead of a raw constraint-
    violation error."""


def downgrade() -> None:
    bind = op.get_bind()
    results_audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events WHERE event_type IN "
            "('lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded')"
        )
    ).scalar_one()
    if results_audit_count:
        raise DowngradeBlockedByResultsWorkflowHistory(
            f"refusing to downgrade past 0010_exchange_results_workflow: "
            f"{results_audit_count} results-workflow exchange_security_audit_events row(s) exist "
            f"using an event_type the narrower 0009 CHECK constraint cannot represent. Reversing "
            f"this migration would require deleting or rewriting that audit history -- and this "
            f"migration does none of that. No schema change has been made -- the database is "
            f"unchanged and remains at 0010_exchange_results_workflow. 0010 is conditionally "
            f"reversible: only before any lab has recorded a result, any order has completed, or "
            f"any doctor has reviewed an order."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0010_EVENT_TYPES})",
    )
