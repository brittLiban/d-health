"""DHEALTH-FACILITY-PROFILE-001: each facility's own wall clock.

Creates ``exchange_facility_profiles`` -- one row per ``Organization``, carrying exactly one fact,
the facility's IANA timezone -- backfills a row for every organization that already exists, and
widens ``exchange_security_audit_events``'s two closed CHECK constraints for one new event type and
one new resource type. Three changes, and deliberately only three.

See ``exchange/models.py``'s ``FacilityProfile`` docstring for why this is a fourth facility table
rather than a column on one of the three that already existed, and ADR-074 for the argument. The
decisions this migration embodies, restated here because a migration is read on its own during an
incident:

── 1. The backfill literal is ``Africa/Djibouti``, and it is a fact about the code, not a machine ──

Every organization that exists when this migration runs gets ``timezone_name = 'Africa/Djibouti'``,
written as a **literal**. This is not a guess and it is not read from the environment.

``config.py``'s ``Settings.from_env`` has, for the whole life of this repository, done exactly this:

    timezone = env.get("TZ", DEPLOYMENT_TIMEZONE)
    if timezone != DEPLOYMENT_TIMEZONE:
        raise ConfigurationError(f"TZ must be {DEPLOYMENT_TIMEZONE}")

A deployment whose ``TZ`` was anything other than ``Africa/Djibouti`` **refused to start**. So every
appointment that exists in any database this migration will ever meet was converted from a local
wall clock under ``Africa/Djibouti`` -- not probably, not by default, but because the adapter would
not boot otherwise. Backfilling that literal therefore preserves the exact semantics under which
every existing row was created.

This is why the value is hardcoded rather than taken from ``os.environ['TZ']`` or from
``DEPLOYMENT_TIMEZONE`` at import time. A migration that read the running operator's environment
would bake a different history into different databases from identical source, and
``alembic upgrade head`` would stop being deterministic. ``AGENTS.md``'s rule against guessing
applies to migrations most of all.

**That enforcement is removed by this ticket** (a deployment that refuses ``TZ≠Djibouti`` cannot
host a clinic in Vancouver), which is precisely why the literal is captured here, now, while the
history it describes is still provable from the code that produced it.

── 2. No appointment instant is rewritten, and none ever should be ──

``exchange_appointments.scheduled_at`` is **not touched**. Not by this migration, and not by any
later timezone change.

Each stored instant is what staff booked under the timezone authority in effect at the moment they
booked it. Re-deriving those instants from a newly configured timezone would silently move every
historical appointment -- an operator changing a setting would discover that last month's clinic
day had moved by hours. What a later timezone change legitimately alters is how a stored instant is
*presented*; what it must never alter is the instant. ADR-074 §4, and a negative control
(``NC-FP-7``) exists specifically to catch a future change that starts rewriting them.

── 3. One row per organization, and a missing row is a configuration error, not a default ──

``uq_exchange_facility_profiles_organization`` makes the one-row-per-facility rule the database's,
not a convention. Combined with the backfill above and profile creation on organization creation, an
organization without a profile row is unreachable in production.

The domain layer therefore **raises** rather than falling back when a profile is absent: an
appointment converted under a silently assumed zone is the failure this whole ticket exists to end,
and a fallback that only shows up as wrong appointment times months later is worse than a refusal an
operator can read. ``AppointmentRepository`` raises ``FacilityTimezoneNotConfigured``.

── 4. The timezone is validated in the domain, not by a CHECK ──

``ck_exchange_facility_profiles_timezone_nonblank`` refuses the empty string and that is all the
database attempts. The IANA database is not expressible as a CHECK: it is amended several times a
year, so a constraint enumerating today's zone names would be wrong by the next release and would
need a migration to accept a newly created zone. ``ZoneInfo`` is the validator, in the application,
where it tracks whatever tz database the runtime actually has.

Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_facility_profiles"
down_revision: str | Sequence[str] | None = "0040_clinic_appointments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The historical deployment timezone, as a literal. See §1 of the module docstring for why this is
#: hardcoded rather than imported from `config.DEPLOYMENT_TIMEZONE` or read from the environment: a
#: migration must produce the same history on every machine, and this value is a fact about what the
#: adapter would boot with, established by a startup check that refused anything else.
_HISTORICAL_DEPLOYMENT_TIMEZONE = "Africa/Djibouti"

#: The event-type vocabulary as 0040 left it. Spelled out rather than read from the model, so this
#: migration keeps saying what it meant even after the model moves on -- the rule every widening
#: migration in this directory follows.
_EVENT_TYPES_PRE_0041 = (
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
    "'lab_order_blocked', 'lab_order_block_resolved', 'lab_specimen_recorded', "
    "'appointment_created', 'appointment_rescheduled', 'appointment_cancelled', "
    "'appointment_checked_in'"
)

#: One event, for one mutation. No `facility_profile_viewed` -- reading is not a mutation (D-8) --
#: and no separate created/updated pair, because the row is created by backfill or alongside the
#: organization and only ever changes in one way.
#:
#: The event records **that** the timezone changed, by whom and when. It does **not** record the old
#: and new values: `exchange_security_audit_events` has no value or metadata column, this migration
#: does not add one, and widening a security audit table to carry arbitrary payloads is not a thing
#: to do in passing. Identical limitation, identical reasoning, to `appointment_rescheduled`'s
#: missing previous instant (ADR-073 §9). Recorded as ADR-074's Human Decision 4.
_FACILITY_PROFILE_EVENT_TYPES = "'facility_timezone_changed'"
_EVENT_TYPES_0041 = f"{_EVENT_TYPES_PRE_0041}, {_FACILITY_PROFILE_EVENT_TYPES}"

#: The resource-type vocabulary as 0040 left it.
_RESOURCE_TYPES_PRE_0041 = (
    "'catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
    "'patient_access_grant', 'patient_reference', 'patient', 'provider_lab_route', "
    "'lab_result_template', 'facility_enrollment', "
    "'facility_interface_capability', 'facility_enrollment_blocker', "
    "'connector_installation', 'patient_notification', 'visit', "
    "'invoice', 'clinic_service', 'appointment'"
)
_RESOURCE_TYPES_0041 = f"{_RESOURCE_TYPES_PRE_0041}, 'facility_profile'"


def upgrade() -> None:
    op.create_table(
        "exchange_facility_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("timezone_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_exchange_facility_profiles"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_facility_profiles_organization",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("organization_id", name="uq_exchange_facility_profiles_organization"),
        sa.CheckConstraint(
            "length(trim(timezone_name)) > 0",
            name="ck_exchange_facility_profiles_timezone_nonblank",
        ),
    )

    # The backfill. One row per existing organization, carrying the literal every one of their
    # existing appointments was already interpreted under -- see §1. `gen_random_uuid()` is
    # available without an extension on PostgreSQL 13+, which `docker-compose.native.yml`'s
    # postgres:16-alpine comfortably satisfies; SQLite (used by the unit suite, which creates
    # tables from metadata rather than running migrations) never reaches this statement.
    op.execute(
        sa.text(
            "INSERT INTO exchange_facility_profiles "
            "(id, organization_id, timezone_name, created_at, updated_at) "
            "SELECT gen_random_uuid(), o.id, :timezone_name, NOW(), NOW() "
            "FROM exchange_organizations o"
        ).bindparams(timezone_name=_HISTORICAL_DEPLOYMENT_TIMEZONE)
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_EVENT_TYPES_0041})",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN ({_RESOURCE_TYPES_0041})",
    )


class DowngradeBlockedByFacilityProfileAuditHistory(RuntimeError):
    """A ``facility_timezone_changed`` audit row exists and 0040's narrower CHECK cannot represent
    it. Re-narrowing the constraint would require deleting or rewriting security audit history,
    which a migration does not get to do."""


class DowngradeBlockedByFacilityProfileResourceAuditHistory(RuntimeError):
    """An audit row names ``resource_type = 'facility_profile'``, which 0040's narrower CHECK cannot
    represent. Same reasoning as above."""


class DowngradeBlockedByConfiguredFacilityTimezone(RuntimeError):
    """A facility's configured timezone differs from the historical deployment value, so dropping
    this table would destroy a real operational decision somebody made deliberately.

    The backfilled rows are *not* protected: a row still reading ``Africa/Djibouti`` carries no
    information the deployment-global constant did not already carry, so dropping it loses nothing.
    A row reading anything else is a configured fact that exists nowhere else, and deleting it is an
    operator decision rather than a migration's. This is the same shape as 0037's and 0039's
    history guards, narrowed to the rows that actually hold new information."""


def downgrade() -> None:
    bind = op.get_bind()

    # **First**, and deliberately so: these raise while the history they describe still exists.
    # Doing it after the table drop would destroy that history and only then discover the problem --
    # 0037's, 0039's and 0040's reasoning, unchanged.
    event_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events "
            "WHERE event_type = 'facility_timezone_changed'"
        )
    ).scalar_one()
    if event_count:
        raise DowngradeBlockedByFacilityProfileAuditHistory(
            f"refusing to downgrade past 0041_facility_profiles: {event_count} "
            f"facility_timezone_changed exchange_security_audit_events row(s) exist using an "
            f"event_type the narrower 0040 CHECK constraint cannot represent. Reversing this "
            f"migration would require deleting or rewriting that audit history -- and this "
            f"migration does none of that. No schema change has been made: the database is still "
            f"at 0041_facility_profiles."
        )

    resource_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events "
            "WHERE resource_type = 'facility_profile'"
        )
    ).scalar_one()
    if resource_count:
        raise DowngradeBlockedByFacilityProfileResourceAuditHistory(
            f"refusing to downgrade past 0041_facility_profiles: {resource_count} "
            f"exchange_security_audit_events row(s) name resource_type 'facility_profile', which "
            f"the narrower 0040 CHECK constraint cannot represent. No schema change has been made: "
            f"the database is still at 0041_facility_profiles."
        )

    configured_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_facility_profiles WHERE timezone_name <> :historical"
        ).bindparams(historical=_HISTORICAL_DEPLOYMENT_TIMEZONE)
    ).scalar_one()
    if configured_count:
        raise DowngradeBlockedByConfiguredFacilityTimezone(
            f"refusing to downgrade past 0041_facility_profiles: {configured_count} facility "
            f"profile row(s) carry a timezone other than the historical "
            f"{_HISTORICAL_DEPLOYMENT_TIMEZONE!r}. Dropping the table would destroy a real "
            f"operational decision -- after a downgrade every one of those facilities would "
            f"silently be interpreted in the deployment timezone again, moving their future "
            f"appointment conversions by hours with nothing recording that it happened. Deleting "
            f"them is an operator decision, never a migration's. No schema change has been made: "
            f"the database is still at 0041_facility_profiles."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_resource_type",
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN ({_RESOURCE_TYPES_PRE_0041})",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_EVENT_TYPES_PRE_0041})",
    )

    op.drop_table("exchange_facility_profiles")
