"""DHEALTH-APPOINTMENTS-001: the clinic appointment, and the arrival that links it to a visit.

Creates ``exchange_appointments`` -- one row per booked clinic encounter -- adds one additive unique
constraint to ``exchange_visits`` that exists only so the new table can carry a composite foreign
key, and widens ``exchange_security_audit_events``'s two closed CHECK constraints for the four new
scheduling events and the new ``appointment`` resource type. Four changes, and deliberately only
four.

See ``exchange/models.py``'s ``Appointment`` docstring for the full contract and ADR-073 for the
argument. The decisions this migration embodies, restated here because a migration is read on its
own during an incident:

── 1. An appointment is not a visit, and ``VisitStatus`` is untouched ──

``docs/NATIVE-OPERATING-PRODUCT.md`` §12 and two earlier ADRs expected *this* ticket to add
``PLANNED`` and ``NO_SHOW`` to the visit lifecycle, so a reader comparing the specification against
the schema will find them still absent and should not conclude a migration was forgotten.
**ADR-073 supersedes that deliberately.** A booking is an expectation; a visit is an encounter.
``ck_exchange_visits_status`` therefore still enumerates exactly four states, and nothing in this
migration touches it.

── 2. The link lives on the appointment, and the visit table gains only a unique constraint ──

§11's field table proposed ``Visit.appointment_id``. This migration inverts it:
``exchange_appointments.visit_id``, nullable and unique. A walk-in is the overwhelmingly common
arrival and it needs no column at all -- the inversion keeps ``exchange_visits`` free of a field
that would be NULL for most of its rows, and makes "did this booking produce an encounter?" a
property of the booking, which is the thing that has the question.

``uq_exchange_visits_id_provider`` is the one change to ``exchange_visits``. It is **purely
additive** and cannot reject any row the primary key already accepted -- ``id`` is the primary key,
so ``(id, provider_organization_id)`` is trivially unique too. It exists so
``fk_exchange_appointments_visit_same_provider`` can reference the *pair*, which is what makes the
database itself refuse a booking that links another clinic's visit. Same shape and same reasoning as
0037's ``uq_exchange_lab_selections_order_lab`` and 0035's ``uq_exchange_invoices_id_org``.

── 3. No backfill, and the absence is the honest answer ──

**Nothing is written into this table by this migration.** Every pre-0040 visit keeps no appointment,
which is exactly right: a past visit does **not** prove there was a prior booking. Manufacturing one
appointment per historical visit would assert a fact no migration can know -- that the patient was
expected -- and would invent a ``scheduled_at`` and a ``created_by_identity_id`` out of nothing.
0039 settled the identical question the identical way for specimens, and 0038 before it for
``checked_in_by``: *"for every pre-0038 visit it could only ever be filled by guessing, which
``AGENTS.md`` forbids."*

So the asymmetry is deliberate and permanent: from 0040 onward a booked arrival carries its booking,
and every earlier visit says only what it always said -- somebody arrived.

── 4. No capacity constraint, and its absence is a decision ──

There is deliberately **no** unique constraint over ``(provider_organization_id, scheduled_at)`` or
any variant including the patient. Without a duration or a resource model the schema cannot
truthfully say an instant is full, and two patients booked at 10:00 is ordinary outpatient clinic
work rather than an error. The only unique constraints are retry safety
(``provider_organization_id, idempotency_key``) and link cardinality (``visit_id``).

── 5. ``NO_SHOW`` is absent from the status CHECK on purpose ──

``ck_exchange_appointments_status`` enumerates ``SCHEDULED`` and ``CANCELLED`` and nothing else. A
no-show is an operational judgement -- how late is late, who may record it, whether a telephone call
changes it -- and no such policy exists in this product (ADR-073 Human Decision 4). A state the
schema permits but nothing can truthfully enter is decoration, which is §12's own standard for
rejecting a state.

── Downgrade safety: 0014's contract, applied to this table ──

0014 established the pattern and it is followed here without variation. Three independent real-usage
guards, all fail-fast **before any DDL**, so a refusal leaves the database completely unchanged at
``0040_clinic_appointments``:

1. refuses to narrow the ``event_type`` CHECK while any of the four ``appointment_*`` audit rows
   exists;
2. refuses to narrow the ``resource_type`` CHECK while any ``'appointment'`` audit row exists;
3. refuses to drop ``exchange_appointments`` while it holds any row. Dropping a whole new table can
   never fail at the DDL level the way re-narrowing an incompatible CHECK does (DH-EX-010A1's
   finding), so without an explicit guard every booking a clinic had made -- including the link
   recording which visit each one produced -- would silently disappear.

Guards 1 and 2 run **before** guard 3, deliberately, matching 0037's and 0039's reasoning: they
raise while the bookings the audit history describes still exist, rather than after they have been
destroyed.

``uq_exchange_visits_id_provider`` is dropped on downgrade and needs no guard: it constrains nothing
a reader depends on and removing it destroys no history.

An unused schema (zero rows everywhere) remains conditionally downgradeable. **Nothing is deleted to
make a downgrade pass** -- that is an operator decision, never a migration's.

Revision id kept short (``0040_clinic_appointments``, 24 characters), respecting DH-EX-009's
discovery that Alembic's own ``alembic_version.version_num`` column is ``VARCHAR(32)``.

Revision ID: 0040_clinic_appointments
Revises: 0039_lab_specimens
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_clinic_appointments"
down_revision: str | Sequence[str] | None = "0039_lab_specimens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The event-type vocabulary as 0039 left it. Spelled out rather than read from the model, so this
#: migration keeps saying what it meant even after the model moves on -- the rule every widening
#: migration in this directory follows.
_EVENT_TYPES_PRE_0040 = (
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
    "'membership_deactivated', 'membership_reactivated', 'role_removed', "
    "'visit_created', 'visit_started', 'visit_completed', 'visit_cancelled', "
    "'visit_note_appended', 'visit_reason_amended', "
    "'clinic_service_created', 'clinic_service_price_changed', 'invoice_created', "
    "'payment_recorded', "
    "'capability_granted', 'capability_denied', 'capability_override_cleared', "
    "'lab_order_blocked', 'lab_order_block_resolved', 'lab_specimen_recorded'"
)

#: The four scheduling mutations. No `appointment_viewed` -- reading is not a mutation (D-8) -- and
#: no no-show event, because no no-show policy exists to record.
_APPOINTMENT_EVENT_TYPES = (
    "'appointment_created', 'appointment_rescheduled', 'appointment_cancelled', "
    "'appointment_checked_in'"
)
_EVENT_TYPES_0040 = f"{_EVENT_TYPES_PRE_0040}, {_APPOINTMENT_EVENT_TYPES}"

#: The resource-type vocabulary as 0035 left it (0036 through 0039 did not touch it).
_RESOURCE_TYPES_PRE_0040 = (
    "'catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
    "'patient_access_grant', 'patient_reference', 'patient', 'provider_lab_route', "
    "'lab_result_template', 'facility_enrollment', "
    "'facility_interface_capability', 'facility_enrollment_blocker', "
    "'connector_installation', 'patient_notification', 'visit', "
    "'invoice', 'clinic_service'"
)
_RESOURCE_TYPES_0040 = f"{_RESOURCE_TYPES_PRE_0040}, 'appointment'"


def upgrade() -> None:
    # The additive unique constraint on `exchange_visits`, created *before* the table whose
    # composite foreign key references it.
    op.create_unique_constraint(
        "uq_exchange_visits_id_provider",
        "exchange_visits",
        ["id", "provider_organization_id"],
    )

    op.create_table(
        "exchange_appointments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("provider_organization_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("visit_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_identity_id", sa.Uuid(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_identity_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["provider_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_appointments_provider_organization",
            ondelete="RESTRICT",
        ),
        # The patient must belong to the clinic that owns the booking, enforced by the database.
        # Same composite shape as 0034's `fk_exchange_visits_patient_same_provider`.
        sa.ForeignKeyConstraint(
            ["patient_id", "provider_organization_id"],
            ["exchange_patients.id", "exchange_patients.provider_organization_id"],
            name="fk_exchange_appointments_patient_same_provider",
            ondelete="RESTRICT",
        ),
        # And the visit must belong to that same clinic -- the half a plain `FOREIGN KEY (visit_id)`
        # cannot express. This is what `uq_exchange_visits_id_provider` above exists for.
        sa.ForeignKeyConstraint(
            ["visit_id", "provider_organization_id"],
            ["exchange_visits.id", "exchange_visits.provider_organization_id"],
            name="fk_exchange_appointments_visit_same_provider",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"],
            ["exchange_identities.id"],
            name="fk_exchange_appointments_created_by_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_identity_id"],
            ["exchange_identities.id"],
            name="fk_exchange_appointments_cancelled_by_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        # Retry safety, NOT cardinality. Deliberately not keyed on the patient: the same patient
        # booking another appointment on another date is ordinary clinic work.
        sa.UniqueConstraint(
            "provider_organization_id",
            "idempotency_key",
            name="uq_exchange_appointments_provider_idempotency_key",
        ),
        # Appointment 0..1 -> Visit. A second booking cannot claim the same encounter.
        sa.UniqueConstraint("visit_id", name="uq_exchange_appointments_visit_id"),
        sa.CheckConstraint(
            "status IN ('SCHEDULED', 'CANCELLED')",
            name="ck_exchange_appointments_status",
        ),
        sa.CheckConstraint(
            "(status = 'CANCELLED') = (cancelled_at IS NOT NULL)",
            name="ck_exchange_appointments_cancelled_at_matches_status",
        ),
        sa.CheckConstraint(
            "(cancelled_at IS NULL) = (cancelled_by_identity_id IS NULL)",
            name="ck_exchange_appointments_cancellation_actor_pairing",
        ),
        # The one state the ticket names as unacceptable, refused by the schema itself: a cancelled
        # booking holding a visit. The service refuses both orderings under a row lock; this is the
        # database saying the same thing independently.
        sa.CheckConstraint(
            "NOT (status = 'CANCELLED' AND visit_id IS NOT NULL)",
            name="ck_exchange_appointments_cancelled_has_no_visit",
        ),
    )
    # The day query: one clinic's bookings over a half-open instant range, in time order. Not
    # covered by either unique constraint above (`idempotency_key` leads one, `visit_id` the other).
    op.create_index(
        "ix_exchange_appointments_provider_scheduled_at",
        "exchange_appointments",
        ["provider_organization_id", "scheduled_at"],
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_EVENT_TYPES_0040})",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN ({_RESOURCE_TYPES_0040})",
    )


class DowngradeBlockedByAppointmentAuditHistory(RuntimeError):
    """0040 -> 0039 was refused because real ``appointment_*`` audit history exists that the
    narrower 0039 CHECK constraint cannot represent -- the downgrade contract 0006 through 0039
    already established for every CHECK widening in this directory."""


class DowngradeBlockedByAppointmentResourceAuditHistory(RuntimeError):
    """0040 -> 0039 was refused because audit rows naming ``resource_type = 'appointment'`` exist
    that the narrower 0039 CHECK constraint cannot represent."""


class DowngradeBlockedByAppointmentHistory(RuntimeError):
    """0040 -> 0039 was refused because real ``exchange_appointments`` rows exist -- DH-EX-010A1's
    lesson applied to a whole new table rather than a single column. Dropping a new table can never
    fail at the DDL level the way re-narrowing an incompatible CHECK does, so without this guard
    every booking a clinic had made, and the link recording which visit each produced, would
    silently disappear."""


def downgrade() -> None:
    bind = op.get_bind()

    # **First**, and deliberately so: these raise while the bookings whose scheduling the audit
    # history describes still exist. Doing it after the table drop would destroy that history and
    # only then discover the problem -- 0037's and 0039's reasoning, unchanged.
    # The four literals are spelled out rather than interpolated from the constant above: an
    # f-string into `sa.text` is an SQL-injection shape even when every value is our own, and 0039
    # spells its single event type out at the call site for the same reason.
    event_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events WHERE event_type IN ("
            "'appointment_created', 'appointment_rescheduled', 'appointment_cancelled', "
            "'appointment_checked_in')"
        )
    ).scalar_one()
    if event_count:
        raise DowngradeBlockedByAppointmentAuditHistory(
            f"refusing to downgrade past 0040_clinic_appointments: {event_count} appointment_* "
            f"exchange_security_audit_events row(s) exist using an event_type the narrower 0039 "
            f"CHECK constraint cannot represent. Reversing this migration would require deleting "
            f"or rewriting that audit history -- and this migration does none of that. No schema "
            f"change has been made: the database is still at 0040_clinic_appointments."
        )

    resource_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events "
            "WHERE resource_type = 'appointment'"
        )
    ).scalar_one()
    if resource_count:
        raise DowngradeBlockedByAppointmentResourceAuditHistory(
            f"refusing to downgrade past 0040_clinic_appointments: {resource_count} "
            f"exchange_security_audit_events row(s) name resource_type 'appointment', which the "
            f"narrower 0039 CHECK constraint cannot represent. No schema change has been made: the "
            f"database is still at 0040_clinic_appointments."
        )

    appointment_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_appointments")
    ).scalar_one()
    if appointment_count:
        raise DowngradeBlockedByAppointmentHistory(
            f"refusing to downgrade past 0040_clinic_appointments: {appointment_count} "
            f"exchange_appointments row(s) exist. Dropping the table would destroy every booking "
            f"this clinic had made and the link recording which visit each one produced. Deleting "
            f"them is an operator decision, never a migration's. No schema change has been made: "
            f"the database is still at 0040_clinic_appointments."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN ({_RESOURCE_TYPES_PRE_0040})",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_EVENT_TYPES_PRE_0040})",
    )

    op.drop_index(
        "ix_exchange_appointments_provider_scheduled_at",
        table_name="exchange_appointments",
    )
    op.drop_table("exchange_appointments")
    op.drop_constraint("uq_exchange_visits_id_provider", "exchange_visits", type_="unique")
