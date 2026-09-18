"""DH-LAB-003/ADR-053: controlled, immutable, LabOffering-scoped Native Lab result-structure
templates.

New tables:
- ``exchange_lab_result_templates`` -- one immutable version of a result structure, scoped to a
  ``LabOffering`` (never directly to ``TestCatalogEntry`` -- two labs offering the identical
  catalog test may legitimately report a different structure). ``UNIQUE(lab_offering_id,
  version)``; at most one ``active=true`` row per offering, enforced by a partial unique index
  (the database backstop -- ``LabResultTemplateRepository.activate_template``'s own
  ``FOR UPDATE``-locked compare-and-swap is the actual serializing mechanism).
- ``exchange_lab_result_template_observations`` -- one controlled observation definition per
  template version. Column shapes deliberately mirror ``LabObservation``'s own
  (``source_code``/``display_label``/``result_type``/``numeric_unit``) so a template value can
  never be too long to copy into the observation a future LAB-004 will derive from it.

Changed table:
- ``exchange_security_audit_events``'s ``event_type``/``resource_type`` CHECK constraints widen
  for ``lab_result_template_created``/``lab_result_template_activated``/``lab_result_template``,
  the same purely-additive drop/recreate pattern ``0006`` through ``0023`` already established.

Revision ID: 0024_result_templates
Revises: 0023_provider_lab_routes
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_result_templates"
down_revision: str | Sequence[str] | None = "0023_provider_lab_routes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRE_0024_EVENT_TYPES = (
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
    "'patient_created', 'provider_lab_route_created', 'provider_lab_route_updated'"
)
_0024_NEW_EVENT_TYPES = "'lab_result_template_created', 'lab_result_template_activated'"

_PRE_0024_RESOURCE_TYPES = (
    "'catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
    "'patient_access_grant', 'patient_reference', 'patient', 'provider_lab_route'"
)
_0024_NEW_RESOURCE_TYPES = "'lab_result_template'"


def upgrade() -> None:
    op.create_table(
        "exchange_lab_result_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "lab_offering_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_offerings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_exchange_lab_result_templates_version"),
        sa.UniqueConstraint(
            "lab_offering_id", "version", name="uq_exchange_lab_result_templates_offering_version"
        ),
    )
    op.create_index(
        "uq_exchange_lab_result_templates_one_active_per_offering",
        "exchange_lab_result_templates",
        ["lab_offering_id"],
        unique=True,
        postgresql_where=sa.text("active = true"),
        sqlite_where=sa.text("active = 1"),
    )

    op.create_table(
        "exchange_lab_result_template_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "result_template_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_result_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_code", sa.String(length=64), nullable=False),
        sa.Column("display_label", sa.String(length=256), nullable=False),
        sa.Column("result_type", sa.String(length=16), nullable=False),
        sa.Column("numeric_unit", sa.String(length=32), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "result_type IN ('NUMERIC', 'TEXT')",
            name="ck_exchange_lab_result_template_observations_result_type",
        ),
        sa.CheckConstraint(
            "result_type = 'NUMERIC' OR numeric_unit IS NULL",
            name="ck_exchange_lab_result_template_observations_text_no_unit",
        ),
        sa.CheckConstraint(
            "sort_order >= 0", name="ck_exchange_lab_result_template_observations_sort"
        ),
        sa.UniqueConstraint(
            "result_template_id",
            "source_code",
            name="uq_exchange_lab_result_template_observations_code",
        ),
        sa.UniqueConstraint(
            "result_template_id",
            "sort_order",
            name="uq_exchange_lab_result_template_observations_sort_order",
        ),
    )
    op.create_index(
        "ix_exchange_lab_result_template_observations_template_id",
        "exchange_lab_result_template_observations",
        ["result_template_id"],
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0024_EVENT_TYPES}, {_0024_NEW_EVENT_TYPES})",
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
        f"({_PRE_0024_RESOURCE_TYPES}, {_0024_NEW_RESOURCE_TYPES})",
    )


class DowngradeBlockedByResultTemplates(RuntimeError):
    """0024 -> 0023 was refused because at least one ``exchange_lab_result_templates`` row exists
    -- controlled Native Lab result configuration the pre-0024 schema has no table to represent.
    No schema change is made; the database remains at 0024_result_templates."""


def downgrade() -> None:
    bind = op.get_bind()

    template_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_lab_result_templates")
    ).scalar_one()
    if template_count:
        raise DowngradeBlockedByResultTemplates(
            f"refusing to downgrade past 0024_result_templates: {template_count} "
            f"exchange_lab_result_templates row(s) exist, which the narrower 0023 schema cannot "
            f"represent. No schema change has been made -- the database is unchanged and remains "
            f"at 0024_result_templates."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN ({_PRE_0024_RESOURCE_TYPES})",
    )
    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0024_EVENT_TYPES})",
    )

    op.drop_index(
        "ix_exchange_lab_result_template_observations_template_id",
        table_name="exchange_lab_result_template_observations",
    )
    op.drop_table("exchange_lab_result_template_observations")

    op.drop_index(
        "uq_exchange_lab_result_templates_one_active_per_offering",
        table_name="exchange_lab_result_templates",
    )
    op.drop_table("exchange_lab_result_templates")
