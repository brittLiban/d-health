"""DH-EX-012/ADR-044: explicit order-level lab-result release.

Creates ``exchange_lab_result_releases`` -- one narrow, immutable row per ``LabOrder``
(``lab_order_id`` unique) recording exactly one fact: an authorized ``LAB_RESULT_RELEASER`` at the
order's own selected lab explicitly authorized the already-entered result set to leave the lab
workflow and become visible to the ordering provider. See ``exchange/models.py``'s
``LabResultRelease`` docstring for the full "workflow authorization, not clinical validation"
contract this table exists to satisfy.

Also widens, purely additively, matching this package's established discipline (``0006``-``0014``):

1. ``exchange_organization_role_assignments.role`` -- CHECK gains ``'LAB_RESULT_RELEASER'``, and
   the column itself widens from ``VARCHAR(16)`` to ``VARCHAR(32)`` since ``'LAB_RESULT_RELEASER'``
   is 19 characters, exceeding the width sized only for ``ORG_ADMIN``/``CLINICIAN``/``LAB_TECH``.
2. ``exchange_security_audit_events.role`` -- identical CHECK and width widening, since a
   ``role_assigned`` audit event can record this same role value.
3. ``exchange_security_audit_events.event_type`` -- CHECK gains ``'lab_results_released'``
   (already fits the existing ``VARCHAR(32)`` column, matching ``'lab_sample_collection_recorded'``
   at 31 characters).

No historical backfill: the ticket's own explicit instruction is that a pre-DH-EX-012 order
already ``COMPLETED`` (because DH-EX-007's original automatic-completion behavior reached it)
with no release row means "no release was recorded in DHealth", never "release definitely did not
happen" -- fabricating a row for such an order would assert something this migration cannot know
to be true. ADR-044 calls this the grandfather rule.

**Downgrade safety (DH-EX-010A1/DH-EX-011's lesson applied proactively, not discovered after the
fact).** Four independent real-usage guards, all fail-fast before any destructive DDL: (1) refuses
to narrow ``exchange_organization_role_assignments.role`` back if any real ``LAB_RESULT_RELEASER``
role assignment exists; (2) refuses to narrow the widened ``event_type`` CHECK if any real
``lab_results_released`` audit row exists; (3) refuses to narrow
``exchange_security_audit_events.role`` back if any real audit row recorded
``role = 'LAB_RESULT_RELEASER'`` (independently of whether a ``lab_results_released`` event
exists -- a ``role_assigned`` event for this role is enough); (4) refuses to drop
``exchange_lab_result_releases`` itself if it contains any real row. All four checks run before
any DDL; any one failing leaves the database completely unchanged at
``0015_exchange_result_release``. An unused schema (zero rows in all three places) remains
conditionally downgradeable.

Revision id kept short deliberately (``0015_exchange_result_release``, 28 characters), respecting
DH-EX-009's discovery that Alembic's own ``alembic_version.version_num`` column is ``VARCHAR(32)``.

Revision ID: 0015_exchange_result_release
Revises: 0014_exchange_collection
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_exchange_result_release"
down_revision: str | Sequence[str] | None = "0014_exchange_collection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRE_0015_ROLE_VALUES = "'ORG_ADMIN', 'CLINICIAN', 'LAB_TECH'"
_0015_NEW_ROLE_VALUE = "'LAB_RESULT_RELEASER'"

_PRE_0015_EVENT_TYPES = (
    "'organization_created', 'membership_created', 'role_assigned', "
    "'catalog_entry_created', 'lab_directory_profile_created', "
    "'lab_directory_profile_updated', 'lab_offering_created', "
    "'lab_offering_status_changed', 'lab_price_changed', "
    "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
    "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started', "
    "'lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded', "
    "'patient_reference_bound', 'lab_order_assigned', 'lab_sample_collection_recorded'"
)
_0015_NEW_EVENT_TYPE = "'lab_results_released'"


def upgrade() -> None:
    op.alter_column(
        "exchange_organization_role_assignments",
        "role",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=False,
    )
    op.drop_constraint(
        "ck_exchange_org_role_assignments_role",
        "exchange_organization_role_assignments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_org_role_assignments_role",
        "exchange_organization_role_assignments",
        f"role IN ({_PRE_0015_ROLE_VALUES}, {_0015_NEW_ROLE_VALUE})",
    )

    op.alter_column(
        "exchange_security_audit_events",
        "role",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=True,
    )
    op.drop_constraint(
        "ck_exchange_security_audit_events_role", "exchange_security_audit_events", type_="check"
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_role",
        "exchange_security_audit_events",
        f"role IS NULL OR role IN ({_PRE_0015_ROLE_VALUES}, {_0015_NEW_ROLE_VALUE})",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0015_EVENT_TYPES}, {_0015_NEW_EVENT_TYPE})",
    )

    op.create_table(
        "exchange_lab_result_releases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_order_id", sa.Uuid(), nullable=False),
        sa.Column("lab_organization_id", sa.Uuid(), nullable=False),
        sa.Column("released_by_identity_id", sa.Uuid(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lab_order_id"],
            ["exchange_lab_orders.id"],
            name="fk_exchange_lab_result_releases_lab_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lab_organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_lab_result_releases_lab_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["released_by_identity_id"],
            ["exchange_identities.id"],
            name="fk_exchange_lab_result_releases_released_by_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lab_order_id", name="uq_exchange_lab_result_releases_lab_order_id"),
    )


class DowngradeBlockedByReleaserRoleHistory(RuntimeError):
    """0015 -> 0014 was refused because a real membership holds the ``LAB_RESULT_RELEASER`` role,
    which the narrower 0014 CHECK/width cannot represent -- the same downgrade contract 0006
    through 0014 already established, applied here to this ticket's own new role value."""


class DowngradeBlockedByResultReleaseAuditHistory(RuntimeError):
    """0015 -> 0014 was refused because real ``lab_results_released`` audit history exists that
    the narrower 0014 ``event_type`` CHECK cannot represent."""


class DowngradeBlockedByReleaserRoleAuditHistory(RuntimeError):
    """0015 -> 0014 was refused because a real audit event recorded
    ``role = 'LAB_RESULT_RELEASER'`` (independently of whether a ``lab_results_released`` event
    exists -- a ``role_assigned`` event for this role is enough), which the narrower 0014
    ``role`` CHECK/width cannot represent."""


class DowngradeBlockedByResultReleaseHistory(RuntimeError):
    """0015 -> 0014 was refused because real ``exchange_lab_result_releases`` rows exist --
    DH-EX-010A1/DH-EX-011's lesson applied here to a whole new table: dropping it can never fail
    at the DDL level the way re-narrowing an incompatible CHECK does, so without this explicit
    guard the table (and the only DHealth-side record that a result release was ever authorized
    for those orders) would silently disappear."""


def downgrade() -> None:
    bind = op.get_bind()

    releaser_role_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_organization_role_assignments "
            "WHERE role = 'LAB_RESULT_RELEASER'"
        )
    ).scalar_one()
    if releaser_role_count:
        raise DowngradeBlockedByReleaserRoleHistory(
            f"refusing to downgrade past 0015_exchange_result_release: {releaser_role_count} "
            f"exchange_organization_role_assignments row(s) hold the 'LAB_RESULT_RELEASER' role, "
            f"which the narrower 0014 CHECK/width cannot represent. No schema change has been "
            f"made -- the database is unchanged and remains at 0015_exchange_result_release."
        )

    release_audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events "
            "WHERE event_type = 'lab_results_released'"
        )
    ).scalar_one()
    if release_audit_count:
        raise DowngradeBlockedByResultReleaseAuditHistory(
            f"refusing to downgrade past 0015_exchange_result_release: {release_audit_count} "
            f"lab_results_released exchange_security_audit_events row(s) exist using an "
            f"event_type the narrower 0014 CHECK constraint cannot represent. No schema change "
            f"has been made -- the database is unchanged and remains at "
            f"0015_exchange_result_release."
        )

    releaser_role_audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events WHERE role = 'LAB_RESULT_RELEASER'"
        )
    ).scalar_one()
    if releaser_role_audit_count:
        raise DowngradeBlockedByReleaserRoleAuditHistory(
            f"refusing to downgrade past 0015_exchange_result_release: "
            f"{releaser_role_audit_count} exchange_security_audit_events row(s) recorded "
            f"role = 'LAB_RESULT_RELEASER', which the narrower 0014 CHECK/width cannot "
            f"represent. No schema change has been made -- the database is unchanged and "
            f"remains at 0015_exchange_result_release."
        )

    release_row_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_lab_result_releases")
    ).scalar_one()
    if release_row_count:
        raise DowngradeBlockedByResultReleaseHistory(
            f"refusing to downgrade past 0015_exchange_result_release: {release_row_count} "
            f"exchange_lab_result_releases row(s) exist -- dropping this table would destroy "
            f"the only DHealth-side record that a result release was ever authorized for those "
            f"orders. No schema change has been made -- the database is unchanged and remains "
            f"at 0015_exchange_result_release."
        )

    op.drop_table("exchange_lab_result_releases")

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0015_EVENT_TYPES})",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_role", "exchange_security_audit_events", type_="check"
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_role",
        "exchange_security_audit_events",
        f"role IS NULL OR role IN ({_PRE_0015_ROLE_VALUES})",
    )
    op.alter_column(
        "exchange_security_audit_events",
        "role",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=True,
    )

    op.drop_constraint(
        "ck_exchange_org_role_assignments_role",
        "exchange_organization_role_assignments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_org_role_assignments_role",
        "exchange_organization_role_assignments",
        f"role IN ({_PRE_0015_ROLE_VALUES})",
    )
    op.alter_column(
        "exchange_organization_role_assignments",
        "role",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=False,
    )
