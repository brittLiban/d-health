"""DHEALTH-SPECIMEN-001: the physical specimen instance becomes its own durable record.

Creates ``exchange_lab_specimens`` -- one immutable row per **physical sample instance** recorded
for a ``LabOrder``, any number per order -- and widens ``exchange_security_audit_events``'s closed
``event_type`` CHECK for the new ``lab_specimen_recorded`` event. Two changes, and deliberately
only two.

See ``exchange/models.py``'s ``LabSpecimen`` docstring for the full contract. The three decisions
this migration embodies, restated here because a migration is read on its own during an incident:

── 1. ``lab_order_id`` is NOT unique, and that is the point ──

``exchange_lab_sample_collections.lab_order_id`` carries a unique constraint, correctly, because it
is a once-per-order **workflow milestone**. This table is not that. It records physical samples, and
laboratory medicine is not one-order/one-specimen: a CBC and a urine culture cannot share a tube,
and an unusable sample is replaced by a second sample rather than by an edit to the first. A unique
constraint here would have written "an order has at most one physical sample, forever" into the
schema, after which every recollection must either overwrite the earlier row or go unrecorded.

The only unique constraint is ``(lab_order_id, idempotency_key)`` -- retry safety, not cardinality.

── 2. No backfill, and the absence is the honest answer ──

**Nothing is written into this table by this migration.** A pre-0039 order with an
``exchange_lab_sample_collections`` row and no specimen means exactly *"the collection milestone was
recorded in DHealth; how many physical samples there were, and who collected them, is not known"*.

Manufacturing one specimen row per existing milestone would assert two facts no migration can know:
a cardinality of exactly one, and a collector identity. ``recorded_by_identity_id`` is ``NOT NULL``
and there is no truthful value to put in it for a historical row -- the DH-EX-011 milestone never
recorded an actor at all, which is one of the gaps this ticket exists to close *going forward*.
0038's own docstring settled the identical question the identical way for ``checked_in_by``: *"for
every pre-0038 visit it could only ever be filled by guessing, which `AGENTS.md` forbids."*

The resulting asymmetry is therefore deliberate and permanent for those rows: from 0039 onward a
milestone is only ever created together with a first specimen (one domain operation, one
transaction, one ``utc_now()`` shared by both rows), so the two agree for every new order; for
pre-0039 orders the milestone stands alone and says only what it always said.

── 3. ``recorded_at``, not ``collected_at`` ──

Inherited deliberately from ``exchange_lab_sample_collections``. It is when DHealth's server clock
observed an authenticated laboratory worker record this sample -- never a caller-supplied or
backdated clinical instant. ADR-018's DH-011J2 addendum is why the rule exists (no reliable native
collection signal existed even on the OpenMRS/OpenELIS connector track), and a ``collected_at``
column here would have reversed that decision under a new ticket's cover.

── Downgrade safety: 0014's own contract, applied to this table ──

0014 established the pattern for exactly this shape of change and it is followed here without
variation. Two independent real-usage guards, both fail-fast **before any DDL**, so a refusal leaves
the database completely unchanged at ``0039_lab_specimens``:

1. refuses to narrow the ``event_type`` CHECK back while any ``lab_specimen_recorded`` audit row
   exists -- the contract ``0006`` through ``0014`` already established for CHECK-widening;
2. refuses to drop ``exchange_lab_specimens`` while it holds any row. Dropping a whole new table can
   never fail at the DDL level the way re-narrowing an incompatible CHECK does (DH-EX-010A1's
   finding), so without an explicit guard the only DHealth-side record of which physical samples a
   laboratory collected, and who recorded them, would silently disappear.

Guard 1 runs first, deliberately, matching 0037's reasoning: it raises while the specimen rows that
the audit history describes still exist, rather than after they have been destroyed.

An unused schema (zero rows in both places) remains conditionally downgradeable. **Nothing is
deleted to make a downgrade pass** -- that is an operator decision, never a migration's.

Revision id kept short (``0039_lab_specimens``, 19 characters), respecting DH-EX-009's discovery
that Alembic's own ``alembic_version.version_num`` column is ``VARCHAR(32)``.

Revision ID: 0039_lab_specimens
Revises: 0038_visit_check_in
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_lab_specimens"
down_revision: str | Sequence[str] | None = "0038_visit_check_in"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The event-type vocabulary as 0037 left it (0038 did not touch it). Spelled out rather than read
#: from the model, so this migration keeps saying what it meant even after the model moves on --
#: the rule every widening migration in this directory follows.
_EVENT_TYPES_PRE_0039 = (
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
    "'lab_order_blocked', 'lab_order_block_resolved'"
)
_EVENT_TYPES_0039 = _EVENT_TYPES_PRE_0039 + ", 'lab_specimen_recorded'"


def upgrade() -> None:
    op.create_table(
        "exchange_lab_specimens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_order_id", sa.Uuid(), nullable=False),
        sa.Column("lab_organization_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by_identity_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(
            ["lab_order_id"],
            ["exchange_lab_orders.id"],
            name="fk_exchange_lab_specimens_lab_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lab_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_lab_specimens_lab_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_identity_id"],
            ["exchange_identities.id"],
            name="fk_exchange_lab_specimens_recorded_by_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        # Retry safety, NOT cardinality -- the whole reason `lab_order_id` alone is not unique.
        # Scoped to the order because the order already fixes the tenant. `idempotency_key` is
        # nullable and PostgreSQL permits many NULLs in a unique constraint, so the DH-EX-011
        # keyless path (Connect's source-observed collection event) is not constrained by this --
        # it is constrained by its own rule, unchanged from DH-EX-011: a keyless call never creates
        # a second specimen.
        sa.UniqueConstraint(
            "lab_order_id",
            "idempotency_key",
            name="uq_exchange_lab_specimens_order_idempotency_key",
        ),
    )
    # No separate index on `lab_order_id`: the unique constraint above is a btree with
    # `lab_order_id` as its leading column, so it already serves both the per-order history read
    # and the per-key replay lookup. A second index would be dead weight on every insert.

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_EVENT_TYPES_0039})",
    )


class DowngradeBlockedBySpecimenAuditHistory(RuntimeError):
    """0039 -> 0038 was refused because real ``lab_specimen_recorded`` audit history exists that the
    narrower 0038 CHECK constraint cannot represent -- the downgrade contract 0006 through 0037
    already established for every CHECK widening in this directory."""


class DowngradeBlockedBySpecimenHistory(RuntimeError):
    """0039 -> 0038 was refused because real ``exchange_lab_specimens`` rows exist -- DH-EX-010A1's
    lesson applied to a whole new table rather than a single column. Dropping a new table can never
    fail at the DDL level the way re-narrowing an incompatible CHECK does, so without this guard the
    only DHealth-side record of which physical samples a laboratory collected for those orders, and
    who recorded each one, would silently disappear."""


def downgrade() -> None:
    bind = op.get_bind()

    # **First**, and deliberately so: this raises while the specimen rows whose recording the audit
    # history describes still exist. Doing it after the table drop would destroy that history and
    # only then discover the problem -- 0037's own reasoning, unchanged.
    specimen_audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events "
            "WHERE event_type = 'lab_specimen_recorded'"
        )
    ).scalar_one()
    if specimen_audit_count:
        raise DowngradeBlockedBySpecimenAuditHistory(
            f"refusing to downgrade past 0039_lab_specimens: {specimen_audit_count} "
            f"lab_specimen_recorded exchange_security_audit_events row(s) exist using an "
            f"event_type the narrower 0038 CHECK constraint cannot represent. Reversing this "
            f"migration would require deleting or rewriting that audit history -- and this "
            f"migration does none of that. No schema change has been made: the database is "
            f"unchanged and remains at 0039_lab_specimens. 0039 is conditionally reversible, only "
            f"before any laboratory has recorded a physical specimen through this ticket's API."
        )

    specimen_row_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_lab_specimens")
    ).scalar_one()
    if specimen_row_count:
        raise DowngradeBlockedBySpecimenHistory(
            f"refusing to downgrade past 0039_lab_specimens: {specimen_row_count} "
            f"exchange_lab_specimens row(s) exist -- dropping this table would destroy the only "
            f"DHealth-side record of which physical samples were collected for those orders, when, "
            f"and by whom. That is real physical-workflow provenance no narrower schema can "
            f"represent, and this migration deletes or rewrites none of it. No schema change has "
            f"been made: the database is unchanged and remains at 0039_lab_specimens. 0039 is "
            f"conditionally reversible, only before any laboratory has recorded a physical "
            f"specimen through this ticket's API."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_EVENT_TYPES_PRE_0039})",
    )

    op.drop_table("exchange_lab_specimens")
