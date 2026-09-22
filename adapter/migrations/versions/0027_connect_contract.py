"""DH-CONNECT-CONTRACT-001: connector installations and durable qualification evidence.

New tables:
- ``connector_installations`` -- one connector implementation, bound to exactly one organization by
  foreign key. The binding is explicit and structural precisely because the alternatives (infer the
  facility from a patient identifier, a hostname, an email address, an external facility name) are
  all accident- or attacker-controlled, and any of them would be a cross-tenant data path.
- ``connector_qualifications`` -- one row per completed qualification attempt, carrying **copies**
  of the contract version, connector version, configuration fingerprint and mapping fingerprint
  that were in place when it ran. Copies, not a join: that is the entire mechanism by which a later
  configuration change makes an old qualification stop applying. Reading those values through the
  foreign key would make every historical qualification silently describe the newest build.
- ``connector_qualification_capabilities`` -- which capability each attempt exercised and whether it
  held. A child table rather than a JSON column because "did this laboratory connector prove it can
  publish an amendment" gates production clinical routing, and a fact that gates routing should be
  something a query can filter and a constraint can protect.

**There is no ``qualified`` column anywhere in this migration, and that is deliberate.** Whether a
facility's connector is qualified is derived from this evidence by
``connect.qualification.applicable_qualification``. Storing it as well would create a second answer
that can drift from the first, and the drift would surface as a facility being routable when its
evidence says otherwise. The installation's ``status`` records only what a human decided --
REGISTERED, SUSPENDED, RETIRED -- which is a different kind of fact and cannot be derived.

**One live installation per facility**, as a partial unique index excluding RETIRED rows. Retired
installations accumulate forever (evidence is never deleted) without ever blocking the registration
of a successor, and the exclusion is what makes "retire the old one, then register the new one" the
supported replacement sequence rather than a constraint violation.

**No backfill, and that is the point.** Existing CONNECT enrollments get **no** connector
installation. Fabricating one would be inventing evidence: nobody has registered a connector for
those facilities and nothing has been qualified, so ``connector_qualified`` stays false and
``connector_operational`` stays false, which is the truth. NATIVE facilities are untouched and have
no connector concept at all. (At the time of writing no seeded facility is CONNECT, so this is a
no-op in practice as well as in principle -- stated explicitly because "it happened to be empty" is
not the same guarantee as "this migration creates none".)

**No secrets.** There is no column here that could hold an API key, a password, a client secret, a
private key or an SFTP credential, and deliberately no generic JSON column that could be used to
smuggle one in.

The downgrade drops all three tables and restores the pre-0027 audit vocabulary. Lossless in the
sense that matters: the tables are new in this migration.

Revision ID: 0027_connect_contract
Revises: 0026_facility_enrollment
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_connect_contract"
down_revision: str | Sequence[str] | None = "0026_facility_enrollment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Audit vocabulary, extended with the same drop-and-recreate discipline 0024 and 0026 used. The
# CHECK constraint is what makes an unrecognised event type a database error rather than a silently
# accepted string, so it is extended deliberately rather than loosened.
_PRE_0027_EVENT_TYPES = (
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
    "'lab_result_template_created', 'lab_result_template_activated', "
    "'facility_enrollment_created', 'facility_participation_mode_changed', "
    "'facility_onboarding_status_changed', 'facility_technical_discovery_updated', "
    "'facility_interface_capability_recorded', 'facility_onboarding_blocked', "
    "'facility_onboarding_unblocked', 'provider_lab_route_activation_refused'"
)

# One event type for both qualification results, not two. The `outcome` column already distinguishes
# a pass ('created') from a failure ('refused'); a second type would be a synonym, and a synonym in
# this vocabulary costs a migration to say the same thing twice.
_0027_NEW_EVENT_TYPES = (
    "'connector_registered', 'connector_suspended', 'connector_restored', "
    "'connector_retired', 'connector_configuration_changed', "
    "'connector_qualification_recorded'"
)

_PRE_0027_RESOURCE_TYPES = (
    "'catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
    "'patient_access_grant', 'patient_reference', 'patient', 'provider_lab_route', "
    "'lab_result_template', 'facility_enrollment', 'facility_interface_capability', "
    "'facility_enrollment_blocker'"
)

_0027_NEW_RESOURCE_TYPES = "'connector_installation'"

_CAPABILITIES = (
    "'ORDER_RECEIPT', 'BUSINESS_ACCEPTANCE', 'RELEASED_RESULT_PUBLICATION', "
    "'AMENDMENT_PUBLICATION', 'COLLECTION_EVENT', 'PROCESSING_EVENT', "
    "'ORDER_SUBMISSION', 'RELEASED_RESULT_RECEIPT', 'AMENDMENT_RECEIPT', "
    "'REVIEW_REPORTING'"
)


def upgrade() -> None:
    op.create_table(
        "connector_installations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("connector_key", sa.String(length=64), nullable=False),
        sa.Column("connector_version", sa.String(length=32), nullable=False),
        sa.Column("contract_version", sa.String(length=16), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_system_version", sa.String(length=64)),
        sa.Column("interface_type", sa.String(length=32), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("mapping_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('REGISTERED', 'SUSPENDED', 'RETIRED')",
            name="ck_connector_installations_status",
        ),
    )
    op.create_index(
        "ix_connector_installations_organization_id",
        "connector_installations",
        ["organization_id"],
    )
    # Partial unique index: at most one non-retired installation per facility. Same shape as
    # `uq_exchange_amendments_one_preparing_per_order`, different status predicate.
    op.create_index(
        "uq_connector_installations_one_live_per_org",
        "connector_installations",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'RETIRED'"),
        sqlite_where=sa.text("status <> 'RETIRED'"),
    )

    op.create_table(
        "connector_qualifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connector_installation_id",
            sa.Uuid(),
            sa.ForeignKey("connector_installations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("contract_version", sa.String(length=16), nullable=False),
        sa.Column("connector_version", sa.String(length=32), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("mapping_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("suite_version", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("evidence_summary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "result IN ('PASSED', 'FAILED')", name="ck_connector_qualifications_result"
        ),
        sa.CheckConstraint(
            "completed_at >= started_at",
            name="ck_connector_qualifications_completed_after_start",
        ),
    )
    op.create_index(
        "ix_connector_qualifications_installation_id",
        "connector_qualifications",
        ["connector_installation_id"],
    )

    op.create_table(
        "connector_qualification_capabilities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "qualification_id",
            sa.Uuid(),
            sa.ForeignKey("connector_qualifications.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.String(length=512)),
        sa.UniqueConstraint(
            "qualification_id",
            "capability",
            name="uq_connector_qualification_capabilities_unique",
        ),
        sa.CheckConstraint(
            f"capability IN ({_CAPABILITIES})",
            name="ck_connector_qualification_capabilities_capability",
        ),
    )
    op.create_index(
        "ix_connector_qualification_capabilities_qualification_id",
        "connector_qualification_capabilities",
        ["qualification_id"],
    )

    for name, expression in (
        (
            "ck_exchange_security_audit_events_event_type",
            f"event_type IN ({_PRE_0027_EVENT_TYPES}, {_0027_NEW_EVENT_TYPES})",
        ),
        (
            "ck_exchange_security_audit_events_resource_type",
            f"resource_type IS NULL OR resource_type IN "
            f"({_PRE_0027_RESOURCE_TYPES}, {_0027_NEW_RESOURCE_TYPES})",
        ),
    ):
        op.drop_constraint(name, "exchange_security_audit_events", type_="check")
        op.create_check_constraint(name, "exchange_security_audit_events", expression)


def downgrade() -> None:
    for name, expression in (
        (
            "ck_exchange_security_audit_events_resource_type",
            f"resource_type IS NULL OR resource_type IN ({_PRE_0027_RESOURCE_TYPES})",
        ),
        (
            "ck_exchange_security_audit_events_event_type",
            f"event_type IN ({_PRE_0027_EVENT_TYPES})",
        ),
    ):
        op.drop_constraint(name, "exchange_security_audit_events", type_="check")
        op.create_check_constraint(name, "exchange_security_audit_events", expression)

    op.drop_index(
        "ix_connector_qualification_capabilities_qualification_id",
        table_name="connector_qualification_capabilities",
    )
    op.drop_table("connector_qualification_capabilities")
    op.drop_index(
        "ix_connector_qualifications_installation_id", table_name="connector_qualifications"
    )
    op.drop_table("connector_qualifications")
    op.drop_index(
        "uq_connector_installations_one_live_per_org", table_name="connector_installations"
    )
    op.drop_index(
        "ix_connector_installations_organization_id", table_name="connector_installations"
    )
    op.drop_table("connector_installations")
