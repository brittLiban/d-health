"""DHEALTH-CLINIC-VISIT-001: the native clinic visit, its append-only note, and visit linkage.

Two new tables and two new nullable columns. Nothing existing is narrowed, retyped or backfilled.

``exchange_visits`` is the first table in this schema that records *care happening* rather than a
transaction being exchanged. Three of its constraints do real work and are not decoration:

- ``fk_exchange_visits_patient_same_provider`` is a **composite** foreign key against
  ``exchange_patients``' ``uq_exchange_patients_id_provider``, not a plain one against
  ``exchange_patients.id``. It makes the database itself reject a visit whose ``patient_id`` belongs
  to a different provider organization than the visit's own ``provider_organization_id``. The same
  mechanism and the same reasoning as ``exchange_patient_references``' composite link
  (DH-IDENTITY-001 §8's "the database itself must reject a cross-provider link") -- an
  application-layer check alone would have to be repeated correctly by every future call site.
- the three timestamp CHECK constraints make the state machine's consequences non-forgeable:
  ``COMPLETED`` without a ``completed_at``, or a ``completed_at`` on a visit that is not
  ``COMPLETED``, cannot exist as a row. ``started_at`` is paired with the two started states, so a
  ``CANCELLED`` visit (reachable only from ``CHECKED_IN`` -- see below) can never claim to have
  begun.
- ``ck_exchange_visits_taken_visit_has_clinician`` pairs the nullable clinician with the states that
  require one. ``CHECKED_IN`` truthfully has no clinician yet (the waiting list *is* that state);
  ``IN_PROGRESS`` and ``COMPLETED`` cannot exist without one.

**Four states, not five, and that is a deliberate recorded deviation** from the ticket's own §6
wording. ``docs/NATIVE-OPERATING-PRODUCT.md`` §12 defines five plus ``CANCELLED``, but that same
specification's first-ticket brief (§36) says ``PLANNED`` and ``NO_SHOW`` are omitted until a
calendar exists, and this ticket's §32 independently forbids no-show handling. No appointment table
exists, so nothing in this schema could put a visit into either state; §12 itself names "a state
reachable by nothing" as decoration. They arrive with the appointments ticket, together with the
table that makes them reachable. The CHECK constraint therefore lists exactly the four that a real
caller can produce -- this package's established discipline (see
``ck_exchange_security_audit_events_event_type``: widen only when a real new caller exists).
``CANCELLED`` is reachable from ``CHECKED_IN`` only; §12 is explicit that a visit already in
progress is completed-with-a-note, never cancelled, because contact with the patient happened and a
status must not be able to erase that.

``exchange_visit_notes`` is append-only by construction, and
``uq_exchange_visit_notes_visit_revision`` is what makes that a guarantee rather than a convention:
two clinicians appending concurrently cannot both win, so neither one's text can silently vanish.
No fingerprint column, no version token, no row-locking protocol -- an immutable row plus a unique
constraint already gives the property, and this is the narrowest mechanism that does. The same
discipline ADR-045/048 established for corrected and amended lab results, applied to the one other
place in this product where a clinician makes a statement that may need correcting later.

``visit_id`` is added to **both** ``exchange_lab_order_intents`` and ``exchange_lab_orders``,
nullable on both, and the pair is the point rather than a redundancy:

- the **intent** is where the value is captured, because the intent is the immutable row created
  once inside the visit. ``exchange/order_intents.py``'s ``commit`` copies it forward and
  re-validates nothing, so a lost-response retry always lands the order on the visit the intent was
  created under -- it is not possible for a replay to move an order to a different visit.
- the **order** carries the copy so that "which orders belong to this visit" is one indexed read on
  the aggregate clinicians actually look at, and so an order that predates DH-CLINIC-003C (and
  therefore has no intent at all) could still be linked if that ever became necessary.

Both stay nullable **permanently, not transitionally.** Two populations legitimately have no visit
forever: every order created before today, and every standalone diagnostic order raised without a
clinic contact (a walk-in, or a connector-submitted order from a foreign system that has no
DHealth visit and never will). ``AGENTS.md``'s backward-compatibility rule is the floor here, but
the product reason outlives the migration.

Both foreign keys are ``RESTRICT``, which is deliberate and is the enforcement behind the
specification's "a completed visit is never deleted": once an order references a visit, no delete
path for that visit exists at all.

The audit ``event_type`` and ``resource_type`` constraints are widened for the six visit lifecycle
events. No ``visit_viewed`` event and no refusal events -- reading is not a mutation and a refusal
is not a mutation, and ``ExchangeSecurityAuditEvent``'s own contract is that a row here commits in
the same transaction as a real change. Refusals stay in ``exchange/audit.py``'s best-effort file
writer, where a failed write can never turn a refusal into an allow.

Downgrade narrows the audit constraints back and deliberately does **not** delete the audit rows
that would then violate them, exactly as 0032 and 0033 established: an audit row records that
something happened, and a schema downgrade is not a reason to erase it. Downgrade *does* drop the
two visit tables, which destroys clinical notes -- there is no way to reverse a table creation
without doing so, and pretending otherwise by leaving the tables behind would make the chain
irreversible instead. A deployment holding real visits must therefore treat this downgrade as
destructive and export first; it is stated here rather than discovered.

The revision id is **18 characters**, against ``alembic_version.version_num``'s ``VARCHAR(32)`` --
the boundary DH-CONNECT-CONTRACT-001 found the hard way (see ``tests/test_migration_hygiene.py``).

Revision ID: 0034_clinic_visits
Revises: 0033_staff_access_lifecycle
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_clinic_visits"
down_revision: str | Sequence[str] | None = "0033_staff_access_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled out in full rather than assembled from the ORM, because a migration must describe the
# database at *this* revision -- reading today's model would make an old migration silently change
# meaning the next time a value is added. Identical discipline to 0029/0032/0033.
_PRE_0034_EVENT_TYPES = (
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
    "'facility_onboarding_unblocked', 'provider_lab_route_activation_refused', "
    "'connector_registered', 'connector_suspended', 'connector_restored', "
    "'connector_retired', 'connector_configuration_changed', "
    "'connector_qualification_recorded', "
    "'connector_service_identity_set', 'connector_runtime_connected', "
    "'connector_message_refused', "
    "'patient_notification_requested', 'patient_notification_retry_requested', "
    "'membership_deactivated', 'membership_reactivated', 'role_removed'"
)

# Six, one per state mutation a clinician can actually perform. `visit_note_appended` names the
# visit and never the text: the body is clinical free content and belongs nowhere near an audit row.
_0034_NEW_EVENT_TYPES = (
    "'visit_created', 'visit_started', 'visit_completed', 'visit_cancelled', "
    "'visit_note_appended', 'visit_reason_amended'"
)

_PRE_0034_RESOURCE_TYPES = (
    "'catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
    "'patient_access_grant', 'patient_reference', 'patient', 'provider_lab_route', "
    "'lab_result_template', 'facility_enrollment', "
    "'facility_interface_capability', 'facility_enrollment_blocker', "
    "'connector_installation', 'patient_notification'"
)

# One, not two. Note revisions hang off a visit, so audit stays visit-centric rather than
# per-revision -- the convention 0029 and 0032 already set for qualifications and attempts. No
# 'patient' addition either: naming the patient on every visit event would turn a security audit
# table into a record of who was seen when.
_0034_NEW_RESOURCE_TYPES = "'visit'"

_EVENT_TYPE_CHECK = "ck_exchange_security_audit_events_event_type"
_RESOURCE_TYPE_CHECK = "ck_exchange_security_audit_events_resource_type"


def upgrade() -> None:
    op.create_table(
        "exchange_visits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column(
            "provider_organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # No single-column foreign key here on purpose -- the composite constraint below is the one
        # that carries the tenant guarantee, and a second plain FK to the same table would only
        # duplicate the half of it that is not the point.
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column(
            "clinician_identity_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason_for_visit", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "provider_organization_id",
            "idempotency_key",
            name="uq_exchange_visits_provider_idempotency_key",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id", "provider_organization_id"],
            ["exchange_patients.id", "exchange_patients.provider_organization_id"],
            name="fk_exchange_visits_patient_same_provider",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('CHECKED_IN', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
            name="ck_exchange_visits_status",
        ),
        sa.CheckConstraint(
            "(status IN ('IN_PROGRESS', 'COMPLETED')) = (started_at IS NOT NULL)",
            name="ck_exchange_visits_started_at_matches_status",
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)",
            name="ck_exchange_visits_completed_at_matches_status",
        ),
        sa.CheckConstraint(
            "(status = 'CANCELLED') = (cancelled_at IS NOT NULL)",
            name="ck_exchange_visits_cancelled_at_matches_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('IN_PROGRESS', 'COMPLETED') OR clinician_identity_id IS NOT NULL",
            name="ck_exchange_visits_taken_visit_has_clinician",
        ),
    )
    op.create_index(
        "ix_exchange_visits_provider_status",
        "exchange_visits",
        ["provider_organization_id", "status"],
    )
    op.create_index(
        "ix_exchange_visits_provider_patient",
        "exchange_visits",
        ["provider_organization_id", "patient_id"],
    )

    op.create_table(
        "exchange_visit_notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "visit_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_visits.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column(
            "author_identity_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # The append-only guarantee, enforced by the database: a concurrent second append to the
        # same visit at the same revision number is refused rather than allowed to overwrite.
        sa.UniqueConstraint(
            "visit_id", "revision_number", name="uq_exchange_visit_notes_visit_revision"
        ),
        sa.CheckConstraint(
            "revision_number >= 1", name="ck_exchange_visit_notes_revision_positive"
        ),
    )
    op.create_index(
        "ix_exchange_visit_notes_visit_id",
        "exchange_visit_notes",
        ["visit_id", "revision_number"],
    )

    # Nullable, no server_default, no backfill: every existing row keeps exactly the meaning it had
    # (an order with no clinic visit), which is the truth rather than a compatibility compromise.
    op.add_column(
        "exchange_lab_order_intents",
        sa.Column(
            "visit_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_visits.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "exchange_lab_orders",
        sa.Column(
            "visit_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_visits.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    # The "what was ordered during this visit" read, which the visit aggregate performs on every
    # open. Partial, because the overwhelming majority of rows in this column are and will remain
    # NULL (see this migration's docstring) and indexing them would be dead weight.
    op.create_index(
        "ix_exchange_lab_orders_visit_id",
        "exchange_lab_orders",
        ["visit_id"],
        postgresql_where=sa.text("visit_id IS NOT NULL"),
        sqlite_where=sa.text("visit_id IS NOT NULL"),
    )

    op.drop_constraint(_EVENT_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _EVENT_TYPE_CHECK,
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0034_EVENT_TYPES}, {_0034_NEW_EVENT_TYPES})",
    )
    op.drop_constraint(_RESOURCE_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _RESOURCE_TYPE_CHECK,
        "exchange_security_audit_events",
        "resource_type IS NULL OR resource_type IN "
        f"({_PRE_0034_RESOURCE_TYPES}, {_0034_NEW_RESOURCE_TYPES})",
    )


def downgrade() -> None:
    # The audit constraints narrow back first, and deliberately without deleting the rows that
    # would now violate them -- the reasoning 0032 recorded, unchanged: an audit row is a record
    # that something happened, and a schema downgrade is not a reason to erase it. A deployment
    # carrying visit audit rows must deal with them deliberately, and a constraint violation is the
    # correct way to be told.
    op.drop_constraint(_RESOURCE_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _RESOURCE_TYPE_CHECK,
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN ({_PRE_0034_RESOURCE_TYPES})",
    )
    op.drop_constraint(_EVENT_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _EVENT_TYPE_CHECK,
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0034_EVENT_TYPES})",
    )

    # Referencing columns before the referenced tables: both visit_id foreign keys are RESTRICT, so
    # the tables cannot be dropped while any order or intent still points at a visit.
    op.drop_index("ix_exchange_lab_orders_visit_id", table_name="exchange_lab_orders")
    op.drop_column("exchange_lab_orders", "visit_id")
    op.drop_column("exchange_lab_order_intents", "visit_id")

    op.drop_index("ix_exchange_visit_notes_visit_id", table_name="exchange_visit_notes")
    op.drop_table("exchange_visit_notes")
    op.drop_index("ix_exchange_visits_provider_patient", table_name="exchange_visits")
    op.drop_index("ix_exchange_visits_provider_status", table_name="exchange_visits")
    op.drop_table("exchange_visits")
