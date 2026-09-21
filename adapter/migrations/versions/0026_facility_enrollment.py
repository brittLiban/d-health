"""DH-MVP-ONBOARD-001: facility enrollment and technical-integration discovery.

New tables:
- ``exchange_facility_enrollments`` -- one row per ``Organization``, unique on ``organization_id``.
  Carries the participation mode (NATIVE/CONNECT), the onboarding state machine, and the CONNECT
  technical-discovery fields as explicit nullable columns rather than a JSON blob, because several
  of them gate whether production orders may be routed somewhere and a fact that decides that
  should be a constrained column a query can filter.
- ``exchange_facility_interface_capabilities`` -- what interfaces a facility's existing system is
  said to expose, each with its own verification state. A child table because a real discovery
  conversation produces several claims at different levels of confidence ("FHIR documented, HL7 v2
  reported, REST unknown") and flattening them would lose which one was actually checked.
- ``exchange_facility_enrollment_blockers`` -- structured reasons a facility cannot progress.
  Resolution is a timestamp, not a delete: what blocked a facility and for how long is the history
  an operator needs when a partner asks why go-live slipped.

**Backfill, and why.** Every existing organization gets a ``NATIVE`` / ``ORGANIZATION_READY``
enrollment. This is not a convenience: DHealth Connect does not exist in any form, so every
organization currently operating in this system *is* Native by construction -- DHealth is already
its workflow software. Recording that is stating a fact, not assuming one.

The alternative -- treating a missing enrollment as "pass the readiness gate" -- was rejected. It
would leave a permanent fail-open hole: any organization that ever lacked an enrollment row would
bypass route-activation readiness forever, and fail-open by omission is precisely the failure mode
this repository's discipline exists to prevent. With the backfill, every organization has an
enrollment and the gate always has something real to evaluate. See ADR-055.

The downgrade drops all three tables. That is lossless in the sense that matters: the tables are
new in this migration, so downgrading returns the database to a state it genuinely had before.

No existing table is altered. ``exchange_provider_lab_routes`` in particular is untouched --
``ProviderLabRoute`` keeps its narrow meaning ("may this provider route orders to this lab?") and
this ticket adds the separate facts around it rather than overloading it.

Revision ID: 0026_facility_enrollment
Revises: 0025_native_result_binding
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_facility_enrollment"
down_revision: str | Sequence[str] | None = "0025_native_result_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Audit vocabulary. Same drop-and-recreate discipline 0024 used: the CHECK constraint is the thing
# that makes an unrecognised event type a database error rather than a silently-accepted string, so
# it is extended deliberately rather than loosened.
_PRE_0026_EVENT_TYPES = (
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
    "'patient_created', "
    "'provider_lab_route_created', 'provider_lab_route_updated', "
    "'lab_result_template_created', 'lab_result_template_activated'"
)

_0026_NEW_EVENT_TYPES = (
    "'facility_enrollment_created', 'facility_participation_mode_changed', "
    "'facility_onboarding_status_changed', 'facility_technical_discovery_updated', "
    "'facility_interface_capability_recorded', 'facility_onboarding_blocked', "
    "'facility_onboarding_unblocked', 'provider_lab_route_activation_refused'"
)

_PRE_0026_RESOURCE_TYPES = (
    "'catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
    "'patient_access_grant', 'patient_reference', 'patient', 'provider_lab_route', "
    "'lab_result_template'"
)

_0026_NEW_RESOURCE_TYPES = (
    "'facility_enrollment', 'facility_interface_capability', 'facility_enrollment_blocker'"
)

# `refused` joins the outcome vocabulary because this ticket audits a refusal that matters: a
# platform admin tried to activate production routing to a facility that cannot take the work.
# The existing outcomes all describe writes that happened; this one describes a write that was
# deliberately prevented, which is exactly the kind of thing an operator later needs to find.
_PRE_0026_OUTCOMES = "'created', 'returned_existing', 'updated', 'unchanged'"
_0026_OUTCOMES = f"{_PRE_0026_OUTCOMES}, 'refused'"


def upgrade() -> None:
    op.create_table(
        "exchange_facility_enrollments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("participation_mode", sa.String(length=16), nullable=False),
        sa.Column("onboarding_status", sa.String(length=32), nullable=False),
        sa.Column("integration_feasibility", sa.String(length=32)),
        sa.Column("existing_system_name", sa.String(length=256)),
        sa.Column("existing_system_vendor", sa.String(length=256)),
        sa.Column("existing_system_version", sa.String(length=64)),
        sa.Column("hosting_model", sa.String(length=16)),
        sa.Column("technical_contact", sa.String(length=256)),
        sa.Column("vendor_contact", sa.String(length=256)),
        sa.Column("documentation_reference", sa.String(length=512)),
        sa.Column("patient_identifier_namespace", sa.String(length=256)),
        sa.Column("order_identifier_concept", sa.String(length=256)),
        sa.Column("report_identifier_concept", sa.String(length=256)),
        sa.Column("specimen_identifier_concept", sa.String(length=256)),
        sa.Column("exposes_source_event_timestamps", sa.Boolean()),
        sa.Column("exposes_professional_release_event", sa.Boolean()),
        sa.Column("notes", sa.String(length=2000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id", name="uq_exchange_facility_enrollments_organization"
        ),
        sa.CheckConstraint(
            "participation_mode IN ('NATIVE', 'CONNECT')",
            name="ck_exchange_facility_enrollments_participation_mode",
        ),
        sa.CheckConstraint(
            "onboarding_status IN ('DRAFT', 'FACILITY_VERIFIED', 'ORGANIZATION_READY', "
            "'CONFIGURATION_IN_PROGRESS', 'TECHNICAL_DISCOVERY', 'AWAITING_CONNECTOR', "
            "'READY_FOR_QUALIFICATION', 'READY_FOR_ROUTING', 'BLOCKED')",
            name="ck_exchange_facility_enrollments_onboarding_status",
        ),
        sa.CheckConstraint(
            "integration_feasibility IS NULL OR integration_feasibility IN "
            "('DISCOVERY_REQUIRED', 'CONNECTABLE', 'CONNECTOR_DEVELOPMENT_REQUIRED', "
            "'NOT_SAFELY_INTEGRABLE')",
            name="ck_exchange_facility_enrollments_integration_feasibility",
        ),
        sa.CheckConstraint(
            "hosting_model IS NULL OR hosting_model IN "
            "('ON_PREMISE', 'CLOUD', 'HYBRID', 'UNKNOWN')",
            name="ck_exchange_facility_enrollments_hosting_model",
        ),
    )
    op.create_index(
        "ix_exchange_facility_enrollments_status",
        "exchange_facility_enrollments",
        ["onboarding_status"],
    )
    op.create_index(
        "ix_exchange_facility_enrollments_mode",
        "exchange_facility_enrollments",
        ["participation_mode"],
    )

    op.create_table(
        "exchange_facility_interface_capabilities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "facility_enrollment_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_facility_enrollments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("interface_type", sa.String(length=32), nullable=False),
        sa.Column("verification_state", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.String(length=512)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "facility_enrollment_id",
            "interface_type",
            name="uq_exchange_facility_interface_capabilities_enrollment_type",
        ),
        sa.CheckConstraint(
            "interface_type IN ('REST_API', 'FHIR', 'HL7_V2', 'WEBHOOK', 'SFTP', "
            "'FILE_IMPORT_EXPORT', 'DATABASE_READ', 'OTHER', 'UNKNOWN')",
            name="ck_exchange_facility_interface_capabilities_type",
        ),
        sa.CheckConstraint(
            "verification_state IN ('REPORTED', 'DOCUMENTED', 'VERIFIED', 'UNSUPPORTED')",
            name="ck_exchange_facility_interface_capabilities_state",
        ),
    )

    op.create_table(
        "exchange_facility_enrollment_blockers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "facility_enrollment_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_facility_enrollments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "code IN ('VENDOR_DOCUMENTATION_UNAVAILABLE', 'VENDOR_CONTACT_REQUIRED', "
            "'REQUIRED_INTERFACE_UNAVAILABLE', 'MISSING_RESULT_RELEASE_EVENT', "
            "'UNKNOWN_PATIENT_IDENTIFIER_BEHAVIOR', 'CONNECTOR_NOT_IMPLEMENTED', "
            "'FACILITY_CONFIGURATION_INCOMPLETE', 'CLINICAL_TEMPLATE_SIGNOFF_REQUIRED')",
            name="ck_exchange_facility_enrollment_blockers_code",
        ),
    )
    op.create_index(
        "ix_exchange_facility_enrollment_blockers_open",
        "exchange_facility_enrollment_blockers",
        ["facility_enrollment_id"],
        postgresql_where=sa.text("resolved_at IS NULL"),
        sqlite_where=sa.text("resolved_at IS NULL"),
    )

    # Backfill. Every organization that exists today operates on DHealth itself, because no
    # connector exists for anything else -- so NATIVE is the true mode, not a default chosen for
    # convenience. ORGANIZATION_READY says the organization is established; it deliberately does
    # not say READY_FOR_ROUTING, because whether routing may be switched on is evaluated from live
    # facts (accepting orders, active offerings) at activation time, never stored as a verdict.
    op.execute(
        sa.text(
            """
            INSERT INTO exchange_facility_enrollments (
                id, organization_id, participation_mode, onboarding_status,
                created_at, updated_at
            )
            -- The enrollment's own id is the organization's uuid. Deliberate: the relationship is
            -- 1:1 and already unique, and it avoids needing a uuid generator whose spelling
            -- differs between SQLite and PostgreSQL. New enrollments created through the
            -- repository get their own fresh uuid; nothing depends on these two matching.
            SELECT
                org.id, org.id, 'NATIVE', 'ORGANIZATION_READY',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM exchange_organizations AS org
            WHERE NOT EXISTS (
                SELECT 1 FROM exchange_facility_enrollments AS fe
                WHERE fe.organization_id = org.id
            )
            """
        )
    )

    for name, expression in (
        (
            "ck_exchange_security_audit_events_event_type",
            f"event_type IN ({_PRE_0026_EVENT_TYPES}, {_0026_NEW_EVENT_TYPES})",
        ),
        (
            "ck_exchange_security_audit_events_resource_type",
            f"resource_type IS NULL OR resource_type IN "
            f"({_PRE_0026_RESOURCE_TYPES}, {_0026_NEW_RESOURCE_TYPES})",
        ),
        ("ck_exchange_security_audit_events_outcome", f"outcome IN ({_0026_OUTCOMES})"),
    ):
        op.drop_constraint(name, "exchange_security_audit_events", type_="check")
        op.create_check_constraint(name, "exchange_security_audit_events", expression)


def downgrade() -> None:
    for name, expression in (
        ("ck_exchange_security_audit_events_outcome", f"outcome IN ({_PRE_0026_OUTCOMES})"),
        (
            "ck_exchange_security_audit_events_resource_type",
            f"resource_type IS NULL OR resource_type IN ({_PRE_0026_RESOURCE_TYPES})",
        ),
        (
            "ck_exchange_security_audit_events_event_type",
            f"event_type IN ({_PRE_0026_EVENT_TYPES})",
        ),
    ):
        op.drop_constraint(name, "exchange_security_audit_events", type_="check")
        op.create_check_constraint(name, "exchange_security_audit_events", expression)

    op.drop_index(
        "ix_exchange_facility_enrollment_blockers_open",
        table_name="exchange_facility_enrollment_blockers",
    )
    op.drop_table("exchange_facility_enrollment_blockers")
    op.drop_table("exchange_facility_interface_capabilities")
    op.drop_index(
        "ix_exchange_facility_enrollments_mode", table_name="exchange_facility_enrollments"
    )
    op.drop_index(
        "ix_exchange_facility_enrollments_status", table_name="exchange_facility_enrollments"
    )
    op.drop_table("exchange_facility_enrollments")
