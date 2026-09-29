"""DHEALTH-LAB-BLOCKED-001: the order-level blocked / cannot-proceed workflow.

One new table, one widened status CHECK, one widened audit-event CHECK, and one redundant unique
constraint that exists purely so a foreign key can be composite. No data is migrated, no column is
retyped, no row is rewritten, and nothing clinical is touched.

── The status value ──

`ck_exchange_lab_orders_status` gains `'BLOCKED'`. Purely additive: every existing row already
satisfies the widened constraint, so there is no backfill and no order changes state because this
migration ran. `exchange_lab_orders.status` is already `VARCHAR(24)` and `'BLOCKED'` is seven
characters, so the column itself is untouched.

`BLOCKED` is a status rather than a flag beside one, and that choice is load-bearing rather than
stylistic: every laboratory mutation that must not happen while an order is blocked -- accept,
collection, start-processing, result entry, entry completion, release -- is *already* gated on a
specific status by code that predates this ticket, so all of them refuse a `BLOCKED` order without
a single new check. A boolean column would have required that same refusal to be re-added in six
places and would have been forgotten in one.

── The table ──

`exchange_lab_order_blocks` holds one row per block *episode*, not one per order. An order may be
blocked, resolved, worked further and blocked again for an unrelated reason
(`docs/NATIVE-OPERATING-PRODUCT.md` §16's own worked example), and a laboratory's real question is
whether this order has been trouble before -- which only survives if every episode does. Nothing
updates an episode after it is resolved, and there is no delete path.

The constraints worth naming:

* `uq_exchange_lab_order_blocks_one_active_per_order` -- a **partial** unique index over
  `lab_order_id WHERE resolved_at IS NULL`. Partial, because the whole point is that an order may
  accumulate any number of *resolved* episodes while never having two unresolved ones. `sqlite_
  where` mirrors `postgresql_where` so the SQLite unit-test database enforces the same rule, the
  same way `0004`/`0008`/`0018` already do -- though the *concurrency* claim behind it is only ever
  made against real PostgreSQL.
* `fk_exchange_lab_order_blocks_selection` -- a **composite** foreign key on
  `(lab_order_id, lab_organization_id)` targeting `exchange_lab_selections`. This is the tenant
  boundary expressed structurally: a block row cannot name a laboratory other than the one the
  order is genuinely assigned to. Two independent foreign keys would not do -- each would be
  satisfiable while the *pair* is a fabrication.
* `ck_exchange_lab_order_blocks_blocked_from_status` -- the resume state is `ACCEPTED` or
  `IN_PROGRESS` and nothing else, so no episode can claim it interrupted a released, rejected or
  cancelled order, and no resolution can restore one to a state it was never in.
* the resolution-consistency pair -- `resolved_at` and `resolved_by_identity_id` are both present
  or both absent, and `resolved_at >= blocked_at`. A resolution with no actor is an event nobody
  owns; a resolution before its own block is not a resolution.

`uq_exchange_lab_selections_order_lab` on `exchange_lab_selections` exists solely as that composite
FK's target. It is structurally redundant -- `lab_order_id` is already unique on that table, so the
pair trivially is too -- and that redundancy is why adding it is safe: it cannot reject any row the
existing constraint would have accepted. Identical shape and reasoning to `0035`'s
`uq_exchange_invoices_id_org` behind `exchange_payments`.

── What is deliberately absent ──

*No specimen table of any kind.* No accession, no container, no aliquot, no storage location, no
custody event, no recollection link, and no second specimen per order. A first-class specimen
entity is **D-3** in `docs/NATIVE-OPERATING-PRODUCT.md` §28, still open, and `RECOLLECTION` is not
even among this ticket's reasons -- `SPECIMEN_UNUSABLE` says the laboratory stopped, and says
nothing at all about what physically happens to the tube next.

*No reason or note column on the audit table.* The two new event types record that an order was
blocked and later resolved, by whom, on which order. The reason lives on the episode row, where a
second copy cannot drift from it -- `0035`'s own decision not to record a payment amount in the
audit table, applied again. The free-text notes are never audited at all, on the same footing as
`VisitNote.body`.

*No `lab_order_block` resource type.* Both events reuse `'lab_order'`, matching every laboratory
event already in that CHECK: audit here is order-centric, never per-sub-resource.

── Downgrade ──

**The status CHECK narrows first, on purpose, and it will fail loudly if any order is currently
`BLOCKED`.** That ordering is the design. Narrowing after dropping the table would destroy every
block episode and *then* discover the order that still needed one; narrowing first means an
operator is told by a constraint violation while the history explaining those orders is still
intact. Resolve or cancel the blocked orders first, deliberately, and the downgrade then proceeds.
This follows `0032`/`0035`/`0036`'s established rule -- narrow the constraint, never delete the
rows that would violate it -- and applies it to a clinical workflow state rather than to audit
history, where the argument for not silently rewriting is if anything stronger.

Dropping the table afterwards does destroy block history, and no reversal of a `CREATE TABLE` can
do otherwise; it is stated here rather than discovered. The audit rows recording that those blocks
happened are, as ever, left in place and the audit CHECK simply narrows around them.

The revision id is **21 characters**, against `alembic_version.version_num`'s `VARCHAR(32)` --
the boundary DH-CONNECT-CONTRACT-001 found the hard way (see `tests/test_migration_hygiene.py`).

Revision ID: 0037_lab_order_blocks
Revises: 0036_capability_overrides
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_lab_order_blocks"
down_revision: str | Sequence[str] | None = "0036_capability_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORDER_STATUS_CHECK = "ck_exchange_lab_orders_status"
_EVENT_TYPE_CHECK = "ck_exchange_security_audit_events_event_type"

#: The order-status vocabulary as everything before this revision left it. Spelled out rather than
#: read from the ORM, so this migration keeps saying what it meant even after the model moves on --
#: the rule every widening migration in this directory follows.
_ORDER_STATUSES_PRE_0037 = (
    "'AWAITING_LAB_SELECTION', 'SELECTED', 'ACCEPTED', 'IN_PROGRESS', "
    "'COMPLETED', 'REJECTED', 'CANCELLED'"
)
_ORDER_STATUSES_0037 = _ORDER_STATUSES_PRE_0037 + ", 'BLOCKED'"

#: The event-type vocabulary as 0036 left it, for the same reason.
_EVENT_TYPES_PRE_0037 = (
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
    "'capability_granted', 'capability_denied', 'capability_override_cleared'"
)
_EVENT_TYPES_0037 = _EVENT_TYPES_PRE_0037 + ", 'lab_order_blocked', 'lab_order_block_resolved'"


def upgrade() -> None:
    # The composite FK's target, created before the table that references it.
    op.create_unique_constraint(
        "uq_exchange_lab_selections_order_lab",
        "exchange_lab_selections",
        ["lab_order_id", "lab_organization_id"],
    )

    op.create_table(
        "exchange_lab_order_blocks",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("lab_order_id", sa.Uuid(), nullable=False),
        sa.Column("lab_organization_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("note", sa.String(length=1024), nullable=True),
        sa.Column("blocked_from_status", sa.String(length=24), nullable=False),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_by_identity_id", sa.Uuid(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_identity_id", sa.Uuid(), nullable=True),
        sa.Column("resolution_note", sa.String(length=1024), nullable=True),
        sa.ForeignKeyConstraint(["lab_order_id"], ["exchange_lab_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["lab_organization_id"], ["exchange_organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["blocked_by_identity_id"], ["exchange_identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_identity_id"], ["exchange_identities.id"], ondelete="RESTRICT"
        ),
        # The tenant boundary, structural rather than service-layer-only: the pair must name a real
        # assignment, so a block can never attribute itself to a laboratory this order was not
        # routed to. See this module's docstring for why two single-column FKs would not do.
        sa.ForeignKeyConstraint(
            ["lab_order_id", "lab_organization_id"],
            [
                "exchange_lab_selections.lab_order_id",
                "exchange_lab_selections.lab_organization_id",
            ],
            name="fk_exchange_lab_order_blocks_selection",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "reason IN ('SPECIMEN_UNUSABLE', 'SPECIMEN_INSUFFICIENT', "
            "'PATIENT_DID_NOT_ATTEND', 'CLARIFICATION_REQUIRED')",
            name="ck_exchange_lab_order_blocks_reason",
        ),
        sa.CheckConstraint(
            "blocked_from_status IN ('ACCEPTED', 'IN_PROGRESS')",
            name="ck_exchange_lab_order_blocks_blocked_from_status",
        ),
        sa.CheckConstraint(
            "(resolved_at IS NULL AND resolved_by_identity_id IS NULL) "
            "OR (resolved_at IS NOT NULL AND resolved_by_identity_id IS NOT NULL)",
            name="ck_exchange_lab_order_blocks_resolution_consistency",
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= blocked_at",
            name="ck_exchange_lab_order_blocks_resolved_after_blocked",
        ),
        sa.CheckConstraint(
            "resolution_note IS NULL OR resolved_at IS NOT NULL",
            # 53 characters. The obvious longer name is 64, against PostgreSQL's 63-character
            # identifier limit -- see this constraint's own note in `models.py`.
            name="ck_exchange_lab_order_blocks_note_requires_resolution",
        ),
    )
    op.create_index(
        "ix_exchange_lab_order_blocks_lab_order_id",
        "exchange_lab_order_blocks",
        ["lab_order_id"],
    )
    op.create_index(
        "ix_exchange_lab_order_blocks_lab_organization_id",
        "exchange_lab_order_blocks",
        ["lab_organization_id"],
    )
    # At most one *unresolved* episode per order, any number of resolved ones. The service layer
    # locks the LabOrder row FOR UPDATE first and that lock is what serializes two simultaneous
    # block attempts; this index is the second layer behind it, not the first.
    op.create_index(
        "uq_exchange_lab_order_blocks_one_active_per_order",
        "exchange_lab_order_blocks",
        ["lab_order_id"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
        sqlite_where=sa.text("resolved_at IS NULL"),
    )

    # Same discipline as 0035/0036: drop the CHECK, recreate it with the addition, never edit in
    # place.
    op.drop_constraint(_ORDER_STATUS_CHECK, "exchange_lab_orders", type_="check")
    op.create_check_constraint(
        _ORDER_STATUS_CHECK,
        "exchange_lab_orders",
        f"status IN ({_ORDER_STATUSES_0037})",
    )

    op.drop_constraint(_EVENT_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _EVENT_TYPE_CHECK,
        "exchange_security_audit_events",
        f"event_type IN ({_EVENT_TYPES_0037})",
    )


def downgrade() -> None:
    # **First**, and deliberately so. If any order is currently BLOCKED this raises, and it raises
    # while the episodes explaining those orders still exist. Doing it after the table drop would
    # destroy that history and only then discover the problem. Nothing is deleted to make this
    # pass -- an operator resolves or cancels the blocked orders themselves, which is the only
    # party entitled to decide what happened to laboratory work in progress.
    op.drop_constraint(_ORDER_STATUS_CHECK, "exchange_lab_orders", type_="check")
    op.create_check_constraint(
        _ORDER_STATUS_CHECK,
        "exchange_lab_orders",
        f"status IN ({_ORDER_STATUSES_PRE_0037})",
    )

    # The audit constraint narrows without deleting the rows that would now violate it --
    # 0032/0035/0036's reasoning, unchanged: an audit row records that something happened, and a
    # schema downgrade is not a reason to erase it.
    op.drop_constraint(_EVENT_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _EVENT_TYPE_CHECK,
        "exchange_security_audit_events",
        f"event_type IN ({_EVENT_TYPES_PRE_0037})",
    )

    op.drop_index(
        "uq_exchange_lab_order_blocks_one_active_per_order",
        table_name="exchange_lab_order_blocks",
    )
    op.drop_index(
        "ix_exchange_lab_order_blocks_lab_organization_id",
        table_name="exchange_lab_order_blocks",
    )
    op.drop_index(
        "ix_exchange_lab_order_blocks_lab_order_id", table_name="exchange_lab_order_blocks"
    )
    op.drop_table("exchange_lab_order_blocks")
    op.drop_constraint(
        "uq_exchange_lab_selections_order_lab", "exchange_lab_selections", type_="unique"
    )
