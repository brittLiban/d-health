"""DH-CONNECT-AGENT-001: the durable state a Connect runtime cannot work without.

Four new tables plus one column, and no data change to anything that already exists.

- ``connector_runtime_instances`` -- one row per agent process lineage that has authenticated for an
  installation. ``last_seen_at`` is stamped by DHealth on receipt; ``reported_at`` records what the
  agent's own clock claimed and is never used to decide liveness (§14). There is **no health
  column**: health is derived by ``connect.runtime_health`` from these observations, so there is
  nothing an operator could set to HEALTHY (§12).
- ``connector_deliveries`` -- the durable queue, both directions in one table, with claim leases
  (``claimed_by_runtime_instance_id`` + ``claim_expires_at``) rather than in-process locks. A lease
  expires by the passage of time, so an agent that dies holding work blocks it for one lease rather
  than forever.
- ``connector_message_receipts`` -- ``contract.ReceiptStore`` made durable. Keyed on
  ``(replay_scope, source_event_id)`` and deliberately nothing else; the in-memory store forgets
  everything on restart, which makes replay protection exactly as strong as the agent's uptime.
- ``connector_correlations`` -- the exact, immutable binding between a DHealth order and the
  partner's own identifier. This table is how §43's "no fuzzy matching" is enforced rather than
  intended: there is no similarity function because there is nothing to be similar to.
- ``connector_installations.service_client_id`` -- the OIDC client *identifier* an agent's token
  carries in ``azp``. A public name, not a credential; the secret is issued and verified by the
  identity provider and never enters DHealth, so this column cannot authenticate anything on its
  own. Globally unique **including across retired rows**: were it unique only among live ones,
  retiring an installation would free the client id for reuse and a still-valid credential would
  then resolve to a different facility, which is precisely the cross-tenant path §8 forbids.

**No backfill, and no synthesised runtime.** Existing installations get no runtime instance row, so
every one of them derives ``UNKNOWN`` health and stays non-operational until a real agent actually
connects. Inventing a row would be inventing an observation -- the same rule 0027 followed when it
declined to fabricate installations for existing CONNECT enrollments.

**Queued work is never deleted.** §51: the downgrade drops these tables, which is lossless only in
the sense that they are new here. An operator downgrading a deployment that has messages in flight
loses them, and that is stated rather than papered over -- ``docs/CONNECT-AGENT.md`` records drain
before downgrade as the supported sequence.

The revision id is **20 characters**. ``alembic_version.version_num`` is ``VARCHAR(32)``, and
DH-CONNECT-CONTRACT-001's correction shipped a 35-character id that applied its DDL and then could
not be stamped -- invisible to the whole SQLite suite, because SQLite does not enforce VARCHAR
length. ``tests/test_migration_hygiene.py`` now measures every id in the tree, so this is checked
rather than remembered.

Revision ID: 0029_connect_runtime
Revises: 0028_connect_evidence
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_connect_runtime"
down_revision: str | Sequence[str] | None = "0028_connect_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Audit vocabulary, extended with the drop-and-recreate discipline 0024, 0026 and 0027 used. The
# CHECK is what makes an unrecognised event type a database error rather than an accepted string.
_PRE_0029_EVENT_TYPES = (
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
    "'connector_qualification_recorded'"
)

# Three, and **no heartbeat event**. §64: heartbeat is operational telemetry, not a security
# mutation, and a table with one row per agent poll would bury the events that matter under its own
# noise. `connector_runtime_connected` fires once per genuinely new runtime instance -- a new
# principal appearing -- not once per beat.
_0029_NEW_EVENT_TYPES = (
    "'connector_service_identity_set', 'connector_runtime_connected', 'connector_message_refused'"
)

_MESSAGE_TYPES = (
    "'ORDER_SUBMISSION', 'ORDER_NOTIFICATION', 'TECHNICAL_ACK', "
    "'BUSINESS_ACCEPTANCE', 'BUSINESS_REJECTION', 'COLLECTION_EVENT', "
    "'RELEASED_RESULT', 'RELEASED_AMENDMENT'"
)


def upgrade() -> None:
    op.add_column(
        "connector_installations",
        sa.Column("service_client_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "uq_connector_installations_service_client",
        "connector_installations",
        ["service_client_id"],
        unique=True,
        postgresql_where=sa.text("service_client_id IS NOT NULL"),
        sqlite_where=sa.text("service_client_id IS NOT NULL"),
    )

    op.create_table(
        "connector_runtime_instances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connector_installation_id",
            sa.Uuid(),
            sa.ForeignKey("connector_installations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("runtime_instance_id", sa.String(length=64), nullable=False),
        sa.Column("agent_version", sa.String(length=32), nullable=False),
        sa.Column("contract_version", sa.String(length=16), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        # Server receive time, authoritative.
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        # The agent's own clock. Observation only -- never consulted for liveness.
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_count", sa.Integer(), nullable=False),
        sa.Column("local_queue_depth", sa.Integer(), nullable=False),
        sa.Column("local_dead_letters", sa.Integer(), nullable=False),
        sa.Column("adapter_reachable", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "connector_installation_id",
            "runtime_instance_id",
            name="uq_connector_runtime_instances_instance",
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_connector_runtime_instances_seen_order",
        ),
        sa.CheckConstraint(
            "heartbeat_count >= 1", name="ck_connector_runtime_instances_heartbeat_count"
        ),
        sa.CheckConstraint(
            "local_queue_depth >= 0 AND local_dead_letters >= 0",
            name="ck_connector_runtime_instances_counts",
        ),
    )
    op.create_index(
        "ix_connector_runtime_instances_installation_id",
        "connector_runtime_instances",
        ["connector_installation_id"],
    )

    op.create_table(
        "connector_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connector_installation_id",
            sa.Uuid(),
            sa.ForeignKey("connector_installations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("direction", sa.String(length=24), nullable=False),
        sa.Column("message_type", sa.String(length=32), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("replay_scope", sa.String(length=128), nullable=False),
        sa.Column("source_event_id", sa.String(length=128), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "lab_order_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        # The canonical envelope as JSON. Clinical content for most message types: it stays inside
        # the Exchange boundary and is never copied into audit rows or logs. Stored rather than
        # reconstructed because a fingerprint proves a payload was seen, not what it said, and a
        # queue that cannot redeliver is not durable (§21).
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by_runtime_instance_id", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_category", sa.String(length=32), nullable=True),
        sa.Column("last_failure_detail", sa.String(length=512), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # The idempotency key, identical in both directions. Not `message_id`: an honest retry of
        # the same source event carries a new message id, which is the case this exists to collapse.
        sa.UniqueConstraint(
            "connector_installation_id",
            "direction",
            "replay_scope",
            "source_event_id",
            name="uq_connector_deliveries_replay",
        ),
        sa.CheckConstraint(
            "direction IN ('CONNECTOR_TO_DHEALTH', 'DHEALTH_TO_CONNECTOR')",
            name="ck_connector_deliveries_direction",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'CLAIMED', 'DELIVERED', 'DEAD_LETTER')",
            name="ck_connector_deliveries_state",
        ),
        sa.CheckConstraint(
            f"message_type IN ({_MESSAGE_TYPES})",
            name="ck_connector_deliveries_message_type",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_connector_deliveries_attempt_count"),
        # A lease is both halves or neither: a holder with no deadline never expires, and a deadline
        # with no holder frees nothing. Either half alone loses a message.
        sa.CheckConstraint(
            "(claimed_by_runtime_instance_id IS NULL) = (claim_expires_at IS NULL)",
            name="ck_connector_deliveries_lease_paired",
        ),
        sa.CheckConstraint(
            "state <> 'CLAIMED' OR claimed_by_runtime_instance_id IS NOT NULL",
            name="ck_connector_deliveries_claimed_has_holder",
        ),
        sa.CheckConstraint(
            "state <> 'DELIVERED' OR delivered_at IS NOT NULL",
            name="ck_connector_deliveries_delivered_at",
        ),
        sa.CheckConstraint(
            "state <> 'DEAD_LETTER' OR dead_lettered_at IS NOT NULL",
            name="ck_connector_deliveries_dead_lettered_at",
        ),
    )
    op.create_index(
        "ix_connector_deliveries_claimable",
        "connector_deliveries",
        ["connector_installation_id", "direction", "state", "next_attempt_at"],
    )
    op.create_index(
        "ix_connector_deliveries_lab_order_id",
        "connector_deliveries",
        ["lab_order_id"],
    )

    op.create_table(
        "connector_message_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("replay_scope", sa.String(length=128), nullable=False),
        sa.Column("source_event_id", sa.String(length=128), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False),
        # No installation column, deliberately. The scope already begins with the organization and
        # at most one live installation exists per organization, so adding it would make the same
        # partner event re-appliable by a replacement installation at the same facility.
        sa.UniqueConstraint(
            "replay_scope",
            "source_event_id",
            name="uq_connector_message_receipts_event",
        ),
    )

    op.create_table(
        "connector_correlations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connector_installation_id",
            sa.Uuid(),
            sa.ForeignKey("connector_installations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("correlation_kind", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column(
            "lab_order_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "connector_installation_id",
            "correlation_kind",
            "external_id",
            name="uq_connector_correlations_external",
        ),
        sa.CheckConstraint(
            "correlation_kind IN ('ORDER', 'REPORT')",
            name="ck_connector_correlations_kind",
        ),
    )
    # The reverse direction, and only for orders: one order has one accession at one laboratory, so
    # a second would mean their system duplicated it. Reports are exempt because the original
    # release and every amendment are separate reports about one order.
    op.create_index(
        "uq_connector_correlations_one_order",
        "connector_correlations",
        ["connector_installation_id", "lab_order_id"],
        unique=True,
        postgresql_where=sa.text("correlation_kind = 'ORDER'"),
        sqlite_where=sa.text("correlation_kind = 'ORDER'"),
    )
    op.create_index(
        "ix_connector_correlations_lab_order_id",
        "connector_correlations",
        ["lab_order_id"],
    )

    name = "ck_exchange_security_audit_events_event_type"
    op.drop_constraint(name, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        name,
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0029_EVENT_TYPES}, {_0029_NEW_EVENT_TYPES})",
    )


def downgrade() -> None:
    name = "ck_exchange_security_audit_events_event_type"
    op.drop_constraint(name, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        name,
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0029_EVENT_TYPES})",
    )

    op.drop_index("ix_connector_correlations_lab_order_id", table_name="connector_correlations")
    op.drop_index("uq_connector_correlations_one_order", table_name="connector_correlations")
    op.drop_table("connector_correlations")
    op.drop_table("connector_message_receipts")
    op.drop_index("ix_connector_deliveries_lab_order_id", table_name="connector_deliveries")
    op.drop_index("ix_connector_deliveries_claimable", table_name="connector_deliveries")
    op.drop_table("connector_deliveries")
    op.drop_index(
        "ix_connector_runtime_instances_installation_id",
        table_name="connector_runtime_instances",
    )
    op.drop_table("connector_runtime_instances")
    op.drop_index("uq_connector_installations_service_client", table_name="connector_installations")
    op.drop_column("connector_installations", "service_client_id")
