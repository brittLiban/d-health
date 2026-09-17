"""DH-IDENTITY-001/ADR-051: provider-scoped native Patient directory, separated from the exact
source-identity PatientReference binding.

New tables:
- ``exchange_patients`` -- the provider-scoped directory record.
- ``exchange_patient_registration_requests`` -- immutable idempotency/replay record for native
  ``POST .../patients`` registration.

Changed tables:
- ``exchange_patient_references`` gains a NOT NULL ``patient_id``, backfilled 1:1 (never merged --
  DH-IDENTITY-001 §10 "if there are 500 existing PatientReference rows, create 500 Patient rows"),
  and a composite foreign key to ``exchange_patients(id, provider_organization_id)`` that makes a
  cross-provider reference-to-patient link a database-rejected fact, not merely an application
  check (§8).
- ``exchange_security_audit_events``'s ``event_type``/``resource_type`` CHECK constraints widen for
  the new ``patient_created``/``patient`` values, the same purely-additive drop/recreate pattern
  ``0006`` through ``0019`` already established.

**Backfill technique**: a backfilled Patient reuses its originating PatientReference's own ``id``
value as its own primary key. This is not a general invariant (a natively-created Patient and its
PatientReference always get two independently-generated UUIDs) -- it is a one-time backfill
convenience that makes the 1:1 link trivial (``UPDATE exchange_patient_references SET patient_id =
id``) without a CTE, a temp table, or re-reading generated ids back out of an INSERT. Reusing an
already-unique UUID across two different tables' primary keys is not itself a correctness risk --
nothing in either table's schema requires the two ids to differ.

**§11 safety guard**: before touching any row, this migration verifies every existing
``exchange_patient_references.source_organization_id`` resolves to a PROVIDER organization -- the
assumption the whole ``Patient.provider_organization_id`` design rests on. If that assumption is
ever false in some real database this migration has not seen, it fails loudly, before any DDL, with
an explanation -- never silently produces an invalid Patient owner.

Revision ID: 0022_exchange_patient_directory
Revises: 0021_order_intent_ownership
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_exchange_patient_directory"
down_revision: str | Sequence[str] | None = "0021_order_intent_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRE_0022_EVENT_TYPES = (
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
    "'lab_observation_amended', 'lab_result_amendment_released'"
)
_0022_NEW_EVENT_TYPES = "'patient_created'"

_PRE_0022_RESOURCE_TYPES = (
    "'catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
    "'patient_access_grant', 'patient_reference'"
)
_0022_NEW_RESOURCE_TYPES = "'patient'"


class UnexpectedNonProviderPatientReferenceOwner(RuntimeError):
    """§11: an existing ``exchange_patient_references`` row's ``source_organization_id`` resolves
    to a non-PROVIDER (or missing) organization -- the migration refuses to guess an owner for the
    new ``exchange_patients`` row this reference would need. No schema change is made."""


def upgrade() -> None:
    bind = op.get_bind()

    bad_owner_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_patient_references pr "
            "LEFT JOIN exchange_organizations org ON org.id = pr.source_organization_id "
            "WHERE org.id IS NULL OR org.org_type != 'PROVIDER'"
        )
    ).scalar_one()
    if bad_owner_count:
        raise UnexpectedNonProviderPatientReferenceOwner(
            f"refusing to migrate 0021 -> 0022: {bad_owner_count} exchange_patient_references "
            f"row(s) have a source_organization_id that is missing or not a PROVIDER organization "
            f"-- the new exchange_patients.provider_organization_id design assumes every existing "
            f"reference was asserted by a provider. No schema change has been made."
        )

    op.create_table(
        "exchange_patients",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "provider_organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("contact_channel_type", sa.String(length=32), nullable=True),
        sa.Column("contact_value", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "id", "provider_organization_id", name="uq_exchange_patients_id_provider"
        ),
        sa.CheckConstraint(
            "(contact_channel_type IS NULL) = (contact_value IS NULL)",
            name="ck_exchange_patients_contact_pairing",
        ),
    )
    op.create_index(
        "ix_exchange_patients_provider_organization_id",
        "exchange_patients",
        ["provider_organization_id"],
    )

    op.create_table(
        "exchange_patient_registration_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "provider_organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "patient_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_patients.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_identity_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider_organization_id",
            "idempotency_key",
            name="uq_exchange_patient_registration_requests_provider_key",
        ),
    )
    op.create_index(
        "ix_exchange_patient_registration_requests_patient_id",
        "exchange_patient_registration_requests",
        ["patient_id"],
    )

    # Backfill: one existing PatientReference -> exactly one new Patient, 1:1, never deduplicated
    # or grouped by name/contact/source-identity similarity (§10). Reuses the reference's own id
    # as the new Patient's id -- see this file's own module docstring for why that is safe and
    # makes the subsequent link trivial.
    op.execute(
        sa.text(
            "INSERT INTO exchange_patients "
            "(id, provider_organization_id, display_name, date_of_birth, "
            " contact_channel_type, contact_value, created_at) "
            "SELECT id, source_organization_id, display_name, NULL, "
            "       contact_channel_type, contact_value, created_at "
            "FROM exchange_patient_references"
        )
    )

    op.add_column("exchange_patient_references", sa.Column("patient_id", sa.Uuid(), nullable=True))
    op.execute(sa.text("UPDATE exchange_patient_references SET patient_id = id"))
    op.alter_column("exchange_patient_references", "patient_id", nullable=False)

    op.create_foreign_key(
        "fk_exchange_patient_references_patient_tenant",
        "exchange_patient_references",
        "exchange_patients",
        ["patient_id", "source_organization_id"],
        ["id", "provider_organization_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_exchange_patient_references_patient_id",
        "exchange_patient_references",
        ["patient_id"],
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0022_EVENT_TYPES}, {_0022_NEW_EVENT_TYPES})",
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
        f"({_PRE_0022_RESOURCE_TYPES}, {_0022_NEW_RESOURCE_TYPES})",
    )


class DowngradeBlockedByPatientDirectoryHistory(RuntimeError):
    """0022 -> 0021 was refused because real patient-directory data exists that the narrower 0021
    schema cannot represent -- see downgrade()'s own docstring for exactly which two signals are
    checked and why a purely-legacy-bind-created Patient with no DOB is not one of them (it is
    fully redundant with its own PatientReference snapshot, matching this migration's own backfill
    technique)."""


def downgrade() -> None:
    """Refuses if either signal of genuinely new, non-backfill-equivalent data exists:

    1. Any ``exchange_patient_registration_requests`` row -- proof the native
       ``POST .../patients`` endpoint was used at least once.
    2. Any ``exchange_patients.date_of_birth IS NOT NULL`` -- proof of data the pre-0022 schema
       has no column to hold anywhere.

    A Patient created purely through the *legacy* exact-identity bind endpoint after this
    migration (no DOB, no registration-request row) is deliberately **not** separately guarded:
    its content is, by construction, a byte-for-byte duplicate of its own PatientReference's
    existing snapshot fields, which the pre-0022 schema already represents unchanged. Dropping the
    redundant Patient/registration-request tables in that case loses no fact the old schema could
    not already state."""
    bind = op.get_bind()

    registration_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_patient_registration_requests")
    ).scalar_one()
    dob_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_patients WHERE date_of_birth IS NOT NULL")
    ).scalar_one()
    if registration_count or dob_count:
        raise DowngradeBlockedByPatientDirectoryHistory(
            f"refusing to downgrade past 0022_exchange_patient_directory: "
            f"{registration_count} native patient registration row(s) and {dob_count} "
            f"Patient row(s) with a recorded date_of_birth exist, neither of which the narrower "
            f"0021 schema can represent. No schema change has been made -- the database is "
            f"unchanged and remains at 0022_exchange_patient_directory."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN ({_PRE_0022_RESOURCE_TYPES})",
    )
    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0022_EVENT_TYPES})",
    )

    op.drop_index(
        "ix_exchange_patient_references_patient_id", table_name="exchange_patient_references"
    )
    op.drop_constraint(
        "fk_exchange_patient_references_patient_tenant",
        "exchange_patient_references",
        type_="foreignkey",
    )
    op.drop_column("exchange_patient_references", "patient_id")

    op.drop_index(
        "ix_exchange_patient_registration_requests_patient_id",
        table_name="exchange_patient_registration_requests",
    )
    op.drop_table("exchange_patient_registration_requests")

    op.drop_index("ix_exchange_patients_provider_organization_id", table_name="exchange_patients")
    op.drop_table("exchange_patients")
