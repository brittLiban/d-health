"""DH-EX-011: order-level sample/specimen collection workflow milestone.

Creates ``exchange_lab_sample_collections`` -- one narrow, immutable row per ``LabOrder``
(``lab_order_id`` unique) recording exactly one fact: an authorized ``LAB_TECH`` at the order's own
selected lab explicitly advanced the sample/specimen collection workflow milestone in DHealth. See
``exchange/models.py``'s ``LabSampleCollection`` docstring for the full "record a fact, invent no
clinical content" contract this table exists to satisfy, and for why ``recorded_at`` (not
``collected_at``) is the correct name for its only timestamp.

Also widens ``exchange_security_audit_events``'s closed ``event_type`` CHECK constraint for the new
``lab_sample_collection_recorded`` event -- the same purely-additive pattern ``0006`` through
``0012`` already established. ``resource_type`` is unchanged: this event reuses the existing
``lab_order`` value, matching ``lab_order_assigned``/``lab_order_accepted``/``lab_order_rejected``/
``lab_order_processing_started``'s own convention (a fact about the ``LabOrder``'s workflow, not a
new kind of resource).

No historical backfill: the ticket's own explicit instruction is that a pre-DH-EX-011 order already
``IN_PROGRESS``/``COMPLETED`` with no collection row means "collection was not recorded in DHealth,"
never "collection definitely did not happen" -- fabricating a row for such an order would assert
something this migration cannot know to be true.

**Downgrade safety (DH-EX-010A1's lesson applied immediately, not discovered after the fact this
time).** Two independent real-usage guards, both fail-fast before any destructive DDL:
(1) refuses to narrow the ``event_type`` CHECK back if any real ``lab_sample_collection_recorded``
audit row exists (the same contract ``0006``-``0012`` already established for CHECK-widening);
(2) refuses to drop ``exchange_lab_sample_collections`` itself if it contains any real row --
dropping a whole new table can never fail at the DDL level the way re-narrowing an incompatible
CHECK does (DH-EX-010A1's own finding, applied here to a table instead of a column), so without an
explicit guard the table would silently disappear along with the only DHealth-side record that
collection was ever performed for those orders. Both checks run in that order, both before any DDL;
either failing leaves the database completely unchanged at ``0014_exchange_collection``. An unused
schema (zero rows either place) remains conditionally downgradeable.

Revision id kept short deliberately (``0014_exchange_collection``, 24 characters), respecting
DH-EX-009's discovery that Alembic's own ``alembic_version.version_num`` column is ``VARCHAR(32)``.

Revision ID: 0014_exchange_collection
Revises: 0013_exchange_access_mode
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_exchange_collection"
down_revision: str | Sequence[str] | None = "0013_exchange_access_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRE_0014_EVENT_TYPES = (
    "'organization_created', 'membership_created', 'role_assigned', "
    "'catalog_entry_created', 'lab_directory_profile_created', "
    "'lab_directory_profile_updated', 'lab_offering_created', "
    "'lab_offering_status_changed', 'lab_price_changed', "
    "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
    "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started', "
    "'lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded', "
    "'patient_reference_bound', 'lab_order_assigned'"
)
_0014_NEW_EVENT_TYPES = "'lab_sample_collection_recorded'"


def upgrade() -> None:
    op.create_table(
        "exchange_lab_sample_collections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_order_id", sa.Uuid(), nullable=False),
        sa.Column("lab_organization_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lab_order_id"],
            ["exchange_lab_orders.id"],
            name="fk_exchange_lab_sample_collections_lab_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lab_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_lab_sample_collections_lab_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lab_order_id", name="uq_exchange_lab_sample_collections_lab_order_id"),
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0014_EVENT_TYPES}, {_0014_NEW_EVENT_TYPES})",
    )


class DowngradeBlockedByCollectionAuditHistory(RuntimeError):
    """0014 -> 0013 was refused because real DH-EX-011 sample-collection audit history exists
    that the narrower 0013 CHECK constraint cannot represent -- the same downgrade contract 0006
    through 0012 already established, applied here to 0014's own widening."""


class DowngradeBlockedByCollectionMilestoneHistory(RuntimeError):
    """0014 -> 0013 was refused because real ``exchange_lab_sample_collections`` rows exist --
    DH-EX-010A1's lesson applied here to a whole new table rather than a single column: dropping
    it can never fail at the DDL level the way re-narrowing an incompatible CHECK does, so without
    this explicit guard the table (and the only DHealth-side record that collection was ever
    performed for those orders) would silently disappear."""


def downgrade() -> None:
    bind = op.get_bind()

    collection_audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events "
            "WHERE event_type = 'lab_sample_collection_recorded'"
        )
    ).scalar_one()
    if collection_audit_count:
        raise DowngradeBlockedByCollectionAuditHistory(
            f"refusing to downgrade past 0014_exchange_collection: {collection_audit_count} "
            f"lab_sample_collection_recorded exchange_security_audit_events row(s) exist using "
            f"an event_type the narrower 0013 CHECK constraint cannot represent. Reversing this "
            f"migration would require deleting or rewriting that audit history -- and this "
            f"migration does none of that. No schema change has been made -- the database is "
            f"unchanged and remains at 0014_exchange_collection. 0014 is conditionally "
            f"reversible: only before any lab has recorded a sample/specimen collection "
            f"milestone through this ticket's API."
        )

    collection_row_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_lab_sample_collections")
    ).scalar_one()
    if collection_row_count:
        raise DowngradeBlockedByCollectionMilestoneHistory(
            f"refusing to downgrade past 0014_exchange_collection: {collection_row_count} "
            f"exchange_lab_sample_collections row(s) exist -- dropping this table would destroy "
            f"the only DHealth-side record that sample/specimen collection was ever performed "
            f"for those orders, real physical-workflow history no narrower schema can represent. "
            f"This migration deletes or rewrites none of it. No schema change has been made -- "
            f"the database is unchanged and remains at 0014_exchange_collection. 0014 is "
            f"conditionally reversible: only before any lab has recorded a sample/specimen "
            f"collection milestone through this ticket's API."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0014_EVENT_TYPES})",
    )

    op.drop_table("exchange_lab_sample_collections")
