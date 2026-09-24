"""DH-NOTIFY-001: the patient-notification domain, deliberately separate from every other delivery.

Three new tables and **no change to any existing one**. That is the headline: ``ResultDelivery``,
``DoctorReview``, ``PatientAccessGrant``, ``LabResultReleaseVersion`` and every clinical table are
untouched, no column is added to them, and nothing is backfilled. Patient notification is a new fact
about an order, not a reinterpretation of an existing one -- and in particular
``exchange_result_deliveries`` keeps meaning "the ordering *provider organization* received the
result", whose semantics remain an open product decision (``docs/MVP-1.0.md`` §10 item 6) that this
migration does not touch.

``patient_notification_enrollments`` -- one live record per order of where a patient agreed to be
messaged and on which channel. Consent is two columns, ``consent_recorded_at`` and
``consent_recorded_by_identity_id``: who affirmed it and when. There is deliberately no free-text
note column, because a free-text consent field is where jurisdiction-specific legal language gets
invented by whoever is typing, and this ticket invents none.

``patient_notifications`` -- one durable owed message. The unique ``idempotency_key`` is what makes
"a release fired its hook twice" and "an amendment was released" distinguishable at the database
level rather than by hopeful application logic: the first collapses onto one row, the second is a
new one, because the key names the immutable release version.

``patient_notification_attempts`` -- the hand-over history, classified. ``failure_detail`` is an
adapter-authored sentence, never the provider's response body: Twilio's error bodies echo the ``To``
value and the ``Body``, and for an access-link message that body contains a live patient bearer
credential.

**No column anywhere below stores a token, a rendered message, or a patient-access URL.** That is
the whole of Liban's A′ decision for this ticket, made structural: ``template_id`` plus
``template_version`` reconstruct exactly what was sent from ``notify/templates.py``, which is in
version control where a wording change is reviewable.

The E.164 destination check is emitted in two forms. PostgreSQL gets the exact pattern; the portable
``LIKE``/``length`` shape guard is what the SQLite unit-test database can enforce, since SQLite has
no ``~`` operator and no built-in ``REGEXP``. ``notify/phone.py`` (libphonenumber) is the
authoritative validator on every write path; both database constraints are defence in depth.

Downgrade drops all three tables. Lossy in one direction that matters and is stated rather than
discovered: a ``PENDING`` notification is a message DHealth owes a patient about a released result,
and dropping the table discards the record that it was owed. Drain the queue before downgrading.

The revision id is **26 characters**, against ``alembic_version.version_num``'s ``VARCHAR(32)`` --
the boundary DH-CONNECT-CONTRACT-001 found the hard way when a 35-character id passed the whole
SQLite suite and could never have been stamped on production.

Revision ID: 0032_patient_notifications
Revises: 0031_connect_namespace
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_patient_notifications"
down_revision: str | Sequence[str] | None = "0031_connect_namespace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PORTABLE_E164 = (
    "destination LIKE '+%' AND destination NOT LIKE '+0%' "
    "AND length(destination) >= 8 AND length(destination) <= 16"
)
_POSTGRES_E164 = r"destination ~ '^\+[1-9][0-9]{6,14}$'"

# The audit table's own two CHECK constraints enumerate every permitted value, so adding an event
# type means widening them -- exactly as 0029 did for the connector events. Spelled out in full
# rather than assembled from the ORM, because a migration must describe the database at *this*
# revision and reading today's model would make an old migration silently change meaning.
_PRE_0032_EVENT_TYPES = (
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
    "'connector_message_refused'"
)

# Two, and **no per-message event**. A dispatcher hand-over, a provider acceptance and a delivery
# receipt are operational telemetry, not security mutations -- the same reasoning 0029 applied to
# heartbeats. Only a human act is recorded: a clinician enrolling an order for messaging (which is
# where consent is affirmed), and a clinician asking for a failed message to be tried again.
_0032_NEW_EVENT_TYPES = "'patient_notification_requested', 'patient_notification_retry_requested'"

_PRE_0032_RESOURCE_TYPES = (
    "'catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
    "'patient_access_grant', 'patient_reference', 'patient', 'provider_lab_route', "
    "'lab_result_template', 'facility_enrollment', "
    "'facility_interface_capability', 'facility_enrollment_blocker', "
    "'connector_installation'"
)

_0032_NEW_RESOURCE_TYPES = "'patient_notification'"

_EVENT_TYPE_CHECK = "ck_exchange_security_audit_events_event_type"
_RESOURCE_TYPE_CHECK = "ck_exchange_security_audit_events_resource_type"


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.create_table(
        "patient_notification_enrollments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "lab_order_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "patient_access_grant_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_patient_access_grants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("destination", sa.String(16), nullable=False),
        sa.Column("consent_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "consent_recorded_by_identity_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "channel IN ('SMS', 'WHATSAPP')",
            name="ck_patient_notification_enrollments_channel",
        ),
        sa.CheckConstraint(
            _PORTABLE_E164, name="ck_patient_notification_enrollments_destination_shape"
        ),
        sa.CheckConstraint(
            "withdrawn_at IS NULL OR withdrawn_at >= created_at",
            name="ck_patient_notification_enrollments_withdrawn_after_create",
        ),
    )
    # "One live enrollment per order", enforced by the database rather than by application logic --
    # the same partial-index shape `exchange_patient_access_grants` already uses for "one live
    # grant per order", and for the same reason: two live destinations for one order would leave
    # nothing saying which is current.
    op.create_index(
        "uq_patient_notification_enrollments_one_live_per_order",
        "patient_notification_enrollments",
        ["lab_order_id"],
        unique=True,
        postgresql_where=sa.text("withdrawn_at IS NULL"),
        sqlite_where=sa.text("withdrawn_at IS NULL"),
    )

    op.create_table(
        "patient_notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("idempotency_key", sa.String(320), nullable=False),
        sa.Column(
            "lab_order_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "release_version_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_release_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "patient_access_grant_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_patient_access_grants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "enrollment_id",
            sa.Uuid(),
            sa.ForeignKey("patient_notification_enrollments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("destination", sa.String(16), nullable=False),
        sa.Column("template_id", sa.String(64), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(8), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by_worker_id", sa.String(128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(128), nullable=True),
        sa.Column("failure_category", sa.String(32), nullable=True),
        sa.Column(
            "requested_by_identity_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_patient_notifications_idempotency_key"),
        sa.CheckConstraint(
            "event_type IN ('ACCESS_LINK_ISSUED', 'RESULT_RELEASED', 'AMENDMENT_RELEASED')",
            name="ck_patient_notifications_event_type",
        ),
        sa.CheckConstraint(
            "channel IN ('SMS', 'WHATSAPP')", name="ck_patient_notifications_channel"
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'SENDING', 'SENT', 'DELIVERED', 'FAILED')",
            name="ck_patient_notifications_state",
        ),
        sa.CheckConstraint(_PORTABLE_E164, name="ck_patient_notifications_destination_shape"),
        sa.CheckConstraint(
            "failure_category IS NULL OR failure_category IN ("
            "'TRANSIENT_PROVIDER', 'RATE_LIMIT', 'AUTHENTICATION', 'INVALID_DESTINATION', "
            "'PROVIDER_REJECTED', 'CONFIGURATION', 'UNKNOWN')",
            name="ck_patient_notifications_failure_category",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_patient_notifications_attempt_count"),
        sa.CheckConstraint(
            "(claimed_by_worker_id IS NULL) = (claim_expires_at IS NULL)",
            name="ck_patient_notifications_lease_paired",
        ),
        sa.CheckConstraint(
            "state <> 'SENT' OR sent_at IS NOT NULL", name="ck_patient_notifications_sent_at"
        ),
        sa.CheckConstraint(
            "state <> 'DELIVERED' OR delivered_at IS NOT NULL",
            name="ck_patient_notifications_delivered_at",
        ),
        # A release notification must name the version it announces; an access-link message has no
        # release yet and must not pretend to. "Which release was this about?" is the question an
        # amendment makes load-bearing, so it is a constraint rather than a comment.
        sa.CheckConstraint(
            "(event_type = 'ACCESS_LINK_ISSUED') = (release_version_id IS NULL)",
            name="ck_patient_notifications_release_version_presence",
        ),
    )
    # Resolving an authenticated provider receipt back to exactly one row is the webhook's whole
    # job; two rows sharing a provider id would make that ambiguous. Partial, because the great
    # majority of rows have no provider id yet.
    op.create_index(
        "uq_patient_notifications_provider_message_id",
        "patient_notifications",
        ["provider_message_id"],
        unique=True,
        postgresql_where=sa.text("provider_message_id IS NOT NULL"),
        sqlite_where=sa.text("provider_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_patient_notifications_due", "patient_notifications", ["state", "next_attempt_at"]
    )

    op.create_table(
        "patient_notification_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "patient_notification_id",
            sa.Uuid(),
            sa.ForeignKey("patient_notifications.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("provider_status_code", sa.Integer(), nullable=True),
        sa.Column("provider_message_id", sa.String(128), nullable=True),
        sa.Column("failure_category", sa.String(32), nullable=True),
        sa.Column("failure_detail", sa.String(256), nullable=True),
        sa.UniqueConstraint(
            "patient_notification_id",
            "attempt_number",
            name="uq_patient_notification_attempts_number",
        ),
        sa.CheckConstraint(
            "outcome IN ('ACCEPTED', 'FAILED')", name="ck_patient_notification_attempts_outcome"
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name="ck_patient_notification_attempts_attempt_number"
        ),
    )
    op.create_index(
        "ix_patient_notification_attempts_notification",
        "patient_notification_attempts",
        ["patient_notification_id"],
    )

    if is_postgres:
        # The exact E.164 pattern, only where the dialect can express it. Added after the tables
        # exist rather than inline, so the SQLite path is not merely "skip a constraint" but
        # "never attempt DDL SQLite cannot parse".
        op.create_check_constraint(
            "ck_patient_notification_enrollments_destination_e164",
            "patient_notification_enrollments",
            _POSTGRES_E164,
        )
        op.create_check_constraint(
            "ck_patient_notifications_destination_e164",
            "patient_notifications",
            _POSTGRES_E164,
        )

    op.drop_constraint(_EVENT_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _EVENT_TYPE_CHECK,
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0032_EVENT_TYPES}, {_0032_NEW_EVENT_TYPES})",
    )
    op.drop_constraint(_RESOURCE_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _RESOURCE_TYPE_CHECK,
        "exchange_security_audit_events",
        "resource_type IS NULL OR resource_type IN "
        f"({_PRE_0032_RESOURCE_TYPES}, {_0032_NEW_RESOURCE_TYPES})",
    )


def downgrade() -> None:
    # The audit constraints narrow back first. Deliberately **not** deleting the rows that would
    # now violate them: an audit row is a record that something happened, and a schema downgrade is
    # not a reason to erase it. A deployment carrying notification audit rows must therefore delete
    # or relabel them before downgrading, and finding out via a constraint violation is the correct
    # way to be told -- a migration that quietly dropped authoritative security history to make
    # itself succeed would be the worse failure by a wide margin.
    op.drop_constraint(_RESOURCE_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _RESOURCE_TYPE_CHECK,
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN ({_PRE_0032_RESOURCE_TYPES})",
    )
    op.drop_constraint(_EVENT_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _EVENT_TYPE_CHECK,
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0032_EVENT_TYPES})",
    )

    # Children before parents: attempts reference notifications, notifications reference
    # enrollments, and every one of those foreign keys is RESTRICT.
    op.drop_index(
        "ix_patient_notification_attempts_notification",
        table_name="patient_notification_attempts",
    )
    op.drop_table("patient_notification_attempts")
    op.drop_index("ix_patient_notifications_due", table_name="patient_notifications")
    op.drop_index(
        "uq_patient_notifications_provider_message_id", table_name="patient_notifications"
    )
    op.drop_table("patient_notifications")
    op.drop_index(
        "uq_patient_notification_enrollments_one_live_per_order",
        table_name="patient_notification_enrollments",
    )
    op.drop_table("patient_notification_enrollments")
