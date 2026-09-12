"""DH-EX-009: provider-scoped patient-reference binding API.

Widens ``exchange_security_audit_events``'s closed ``event_type`` and ``resource_type`` CHECK
constraints for the new ``patient_reference_bound`` event -- the exact same purely-additive pattern
``0006``/``0007``/``0008``/``0009``/``0010`` already established. No new table, no new column:
``exchange_patient_references`` (its columns, its organization-scoped uniqueness constraint) already
exists unchanged from DH-EX-001/DH-EX-001R; this ticket only exposes an HTTP route and an audit
event on top of it.

Revision id kept short deliberately (``0011_exchange_patient_ref_bind``, not the longer
``..._patient_reference_binding``): Alembic's own ``alembic_version.version_num`` column defaults to
``VARCHAR(32)``, and the longer name (39 chars) does not fit -- discovered by running this migration
against a real PostgreSQL database, not assumed from reading Alembic's source.

Revision ID: 0011_exchange_patient_ref_bind
Revises: 0010_exchange_results_workflow
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_exchange_patient_ref_bind"
down_revision: str | Sequence[str] | None = "0010_exchange_results_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRE_0011_EVENT_TYPES = (
    "'organization_created', 'membership_created', 'role_assigned', "
    "'catalog_entry_created', 'lab_directory_profile_created', "
    "'lab_directory_profile_updated', 'lab_offering_created', "
    "'lab_offering_status_changed', 'lab_price_changed', "
    "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
    "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started', "
    "'lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded'"
)
_0011_NEW_EVENT_TYPES = "'patient_reference_bound'"

_PRE_0011_RESOURCE_TYPES = (
    "'catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
    "'patient_access_grant'"
)
_0011_NEW_RESOURCE_TYPES = "'patient_reference'"


def upgrade() -> None:
    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0011_EVENT_TYPES}, {_0011_NEW_EVENT_TYPES})",
    )
    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN "
        f"({_PRE_0011_RESOURCE_TYPES}, {_0011_NEW_RESOURCE_TYPES})",
    )


class DowngradeBlockedByPatientReferenceBindingHistory(RuntimeError):
    """0011 -> 0010 was refused because real DH-EX-009 patient-reference-binding audit history
    exists that the narrower 0010 CHECK constraints cannot represent -- the same downgrade contract
    0006 through 0010 already established, applied here to 0011's own widening. Re-applying the
    narrower CHECK against existing rows using the new event/resource type would fail at the DDL
    level regardless -- this check fails fast, before attempting any DDL, with an explanation
    instead of a raw constraint-violation error."""


def downgrade() -> None:
    bind = op.get_bind()
    binding_audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events "
            "WHERE event_type = 'patient_reference_bound'"
        )
    ).scalar_one()
    if binding_audit_count:
        raise DowngradeBlockedByPatientReferenceBindingHistory(
            f"refusing to downgrade past 0011_exchange_patient_ref_bind: "
            f"{binding_audit_count} patient_reference_bound exchange_security_audit_events row(s) "
            f"exist using an event_type/resource_type the narrower 0010 CHECK constraints cannot "
            f"represent. Reversing this migration would require deleting or rewriting that audit "
            f"history -- and this migration does none of that. No schema change has been made -- "
            f"the database is unchanged and remains at 0011_exchange_patient_ref_bind. 0011 is "
            f"conditionally reversible: only before any provider has bound a patient reference "
            f"through this ticket's API."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN ({_PRE_0011_RESOURCE_TYPES})",
    )
    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0011_EVENT_TYPES})",
    )
