"""DH-CLINIC-006/ADR-052: explicit provider-lab routing relationships, distinct from marketplace
listing and from order-level lab assignment.

New table:
- ``exchange_provider_lab_routes`` -- an explicit DHealth routing configuration answering only "may
  this provider route orders to this DHealth lab organization?" Launch-1 configuration is
  platform-admin-only. Unique on ``(provider_organization_id, lab_organization_id)``; no lis_type/
  emr_type/api_url/transport fields -- those belong to a future pilot-topology ticket, never this
  one. Organization-type validity is enforced by the repository layer (``exchange/
  provider_lab_routes.py``), the same convention already used for ``LabOffering`` and
  ``LabDirectoryProfile``, not a cross-table DB constraint.

Changed table:
- ``exchange_security_audit_events``'s ``event_type``/``resource_type`` CHECK constraints widen for
  the new ``provider_lab_route_created``/``provider_lab_route_updated``/``provider_lab_route``
  values, the same purely-additive drop/recreate pattern ``0006`` through ``0022`` already
  established.

Revision ID: 0023_provider_lab_routes
Revises: 0022_exchange_patient_directory
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_provider_lab_routes"
down_revision: str | Sequence[str] | None = "0022_exchange_patient_directory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRE_0023_EVENT_TYPES = (
    "'organization_created', 'membership_created', 'role_assigned', "
    "'catalog_entry_created', 'lab_directory_profile_created', "
    "'lab_directory_profile_updated', 'lab_offering_created', "
    "'lab_offering_status_changed', 'lab_price_changed', "
    "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
    "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started', "
    "'lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded', "
    "'patient_reference_bound', 'lab_order_assigned', 'lab_sample_collection_recorded', "
    "'lab_results_released', 'lab_observation_recorded', 'lab_result_entry_completed', "
    "'lab_observation_corrected', 'lab_result_amendment_started', "
    "'lab_observation_amended', 'lab_result_amendment_released', "
    "'patient_created'"
)
_0023_NEW_EVENT_TYPES = "'provider_lab_route_created', 'provider_lab_route_updated'"

_PRE_0023_RESOURCE_TYPES = (
    "'catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
    "'patient_access_grant', 'patient_reference', 'patient'"
)
_0023_NEW_RESOURCE_TYPES = "'provider_lab_route'"


def upgrade() -> None:
    op.create_table(
        "exchange_provider_lab_routes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "provider_organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "lab_organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider_organization_id",
            "lab_organization_id",
            name="uq_exchange_provider_lab_routes_provider_lab",
        ),
    )
    op.create_index(
        "ix_exchange_provider_lab_routes_lab_organization_id",
        "exchange_provider_lab_routes",
        ["lab_organization_id"],
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0023_EVENT_TYPES}, {_0023_NEW_EVENT_TYPES})",
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
        f"({_PRE_0023_RESOURCE_TYPES}, {_0023_NEW_RESOURCE_TYPES})",
    )


class DowngradeBlockedByProviderLabRoutes(RuntimeError):
    """0023 -> 0022 was refused because at least one ``exchange_provider_lab_routes`` row exists --
    a provider-lab routing relationship the pre-0023 schema has no table to represent. No schema
    change is made; the database remains at 0023_provider_lab_routes."""


def downgrade() -> None:
    bind = op.get_bind()

    route_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_provider_lab_routes")
    ).scalar_one()
    if route_count:
        raise DowngradeBlockedByProviderLabRoutes(
            f"refusing to downgrade past 0023_provider_lab_routes: {route_count} "
            f"exchange_provider_lab_routes row(s) exist, which the narrower 0022 schema cannot "
            f"represent. No schema change has been made -- the database is unchanged and remains "
            f"at 0023_provider_lab_routes."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN ({_PRE_0023_RESOURCE_TYPES})",
    )
    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0023_EVENT_TYPES})",
    )

    op.drop_index(
        "ix_exchange_provider_lab_routes_lab_organization_id",
        table_name="exchange_provider_lab_routes",
    )
    op.drop_table("exchange_provider_lab_routes")
