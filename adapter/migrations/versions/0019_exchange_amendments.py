"""DH-EX-014B/ADR-048: post-release amendment, amendment release, and versioned clinician re-review.

This migration implements the amendment-episode workflow DH-EX-014A's manifest/version foundation
was built ahead of. It does not touch `exchange_lab_result_releases` (Version 1's original
authorization fact stays exactly as ADR-044 shipped it), does not change `LabOrderStatus`'s state
machine (`COMPLETED` remains terminal), and does not add a reason field anywhere (ADR-048 Decision
5 -- no mandatory amendment reason for MVP).

**New table: `exchange_amendments`.** The amendment-episode workflow container ADR-048 Model 3
recommended -- `PREPARING` -> `RELEASED`, no other states (no `CANCELLED`/`DISCARDED`/`REJECTED`;
ADR-048 Decision 4 -- immediate/permanent revision semantics, no discardable draft for MVP).
`amendment_number` is explicit and ordered (`UNIQUE(lab_order_id, amendment_number)`), never
inferred from `started_at`. `base_release_version_id` is non-nullable and immutable once set -- it
is the left operand of the amendment-correction eligibility predicate (see `exchange/results.py`'s
`LabResultAmendmentRepository.correct_observation`), not merely an audit convenience: an old
`PREPARING` amendment must never be able to release against a baseline other than the one it
actually started against. A genuine partial unique index enforces "at most one `PREPARING`
amendment per order at a time" -- the identical shape `uq_exchange_patient_access_grants_
one_live_per_order` already established for a different status predicate. `released_by_identity_id`/
`released_at` are both `NULL` while `PREPARING` and both non-`NULL` exactly when `RELEASED`,
enforced by `CHECK`.

**`exchange_lab_observations` gains `amendment_id`** (nullable FK to `exchange_amendments.id`).
`NULL` for every existing row (an original entry or a DH-EX-013 pre-release correction -- unchanged
meaning, no retroactive fabrication); populated only for a post-release amendment revision created
by `LabResultAmendmentRepository.correct_observation` from this ticket forward.

**`exchange_release_versions` gains `amendment_id`** (nullable, `UNIQUE` FK to
`exchange_amendments.id` -- one released version per amendment episode). The existing
`ck_exchange_release_versions_v1_bound_to_release` CHECK (shipped by 0018, same name here, widened
definition) is dropped and recreated: version 1 is always release-bound and never amendment-bound;
every later version is always amendment-bound and never release-bound. Neither `LabResultRelease`
nor the existing Version 1 row is mutated by this migration or by any code this ticket adds --
this is purely an additive column plus a wider CHECK on future rows.

**No historical amendment data exists to backfill.** Every existing `exchange_release_versions` row
predates this ticket and is version 1 (0018's own invariant); every existing
`exchange_lab_observations` row predates any amendment mechanism, so `amendment_id` is correctly
`NULL` for all of them via the column's own default -- nothing is fabricated.

**`exchange_security_audit_events` `event_type` CHECK widened** for
`lab_result_amendment_started`/`lab_observation_amended`/`lab_result_amendment_released`, matching
0017's own widen-plus-downgrade-guard pattern exactly.

**Downgrade safety.** Fails closed if any `exchange_amendments` row exists at all (real amendment
history the pre-0019 schema cannot represent even partially), or if any
`exchange_security_audit_events` row already uses one of the three new event types. With neither
guard triggered: the `event_type` CHECK is narrowed back, `exchange_release_versions.amendment_id`
and its CHECK/FK/UNIQUE are dropped and the original 0018 CHECK is restored, `exchange_lab_
observations.amendment_id` and its FK/index are dropped, and `exchange_amendments` itself is
dropped.

Revision ID: 0019_exchange_amendments
Revises: 0018_exchange_release_versions
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_exchange_amendments"
down_revision: str | Sequence[str] | None = "0018_exchange_release_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Matches the exact literal list `0018_exchange_release_versions.py`'s own migration built on top
# of -- reproduced here (not imported from that module) since Alembic migration files are meant to
# stand alone, matching this repo's own established convention (see 0017's identical choice).
_PRE_0019_EVENT_TYPES = (
    "'organization_created', 'membership_created', 'role_assigned', "
    "'catalog_entry_created', 'lab_directory_profile_created', "
    "'lab_directory_profile_updated', 'lab_offering_created', "
    "'lab_offering_status_changed', 'lab_price_changed', "
    "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
    "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started', "
    "'lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded', "
    "'patient_reference_bound', 'lab_order_assigned', 'lab_sample_collection_recorded', "
    "'lab_results_released', 'lab_observation_recorded', 'lab_result_entry_completed', "
    "'lab_observation_corrected'"
)
_0019_NEW_EVENT_TYPES = (
    "'lab_result_amendment_started', 'lab_observation_amended', 'lab_result_amendment_released'"
)


def upgrade() -> None:
    op.create_table(
        "exchange_amendments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lab_order_id", sa.Uuid(), nullable=False),
        sa.Column("amendment_number", sa.Integer(), nullable=False),
        sa.Column("base_release_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_by_identity_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_by_identity_id", sa.Uuid(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["lab_order_id"],
            ["exchange_lab_orders.id"],
            name="fk_exchange_amendments_lab_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["base_release_version_id"],
            ["exchange_release_versions.id"],
            name="fk_exchange_amendments_base_release_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["started_by_identity_id"],
            ["exchange_identities.id"],
            name="fk_exchange_amendments_started_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["released_by_identity_id"],
            ["exchange_identities.id"],
            name="fk_exchange_amendments_released_by",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "lab_order_id", "amendment_number", name="uq_exchange_amendments_order_number"
        ),
        sa.CheckConstraint("amendment_number >= 1", name="ck_exchange_amendments_number_positive"),
        sa.CheckConstraint(
            "status IN ('PREPARING', 'RELEASED')", name="ck_exchange_amendments_status"
        ),
        sa.CheckConstraint(
            "(status = 'PREPARING' AND released_by_identity_id IS NULL "
            "AND released_at IS NULL) OR (status = 'RELEASED' AND "
            "released_by_identity_id IS NOT NULL AND released_at IS NOT NULL)",
            name="ck_exchange_amendments_release_consistency",
        ),
    )
    op.create_index("ix_exchange_amendments_lab_order_id", "exchange_amendments", ["lab_order_id"])
    op.create_index(
        "ix_exchange_amendments_base_release_version_id",
        "exchange_amendments",
        ["base_release_version_id"],
    )
    op.create_index(
        "uq_exchange_amendments_one_preparing_per_order",
        "exchange_amendments",
        ["lab_order_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PREPARING'"),
        sqlite_where=sa.text("status = 'PREPARING'"),
    )

    op.add_column(
        "exchange_lab_observations",
        sa.Column("amendment_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_exchange_lab_observations_amendment",
        "exchange_lab_observations",
        "exchange_amendments",
        ["amendment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_exchange_lab_observations_amendment_id",
        "exchange_lab_observations",
        ["amendment_id"],
    )

    op.add_column(
        "exchange_release_versions",
        sa.Column("amendment_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_exchange_release_versions_amendment",
        "exchange_release_versions",
        "exchange_amendments",
        ["amendment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_exchange_release_versions_amendment_id",
        "exchange_release_versions",
        ["amendment_id"],
    )
    op.drop_constraint(
        "ck_exchange_release_versions_v1_bound_to_release",
        "exchange_release_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_release_versions_v1_bound_to_release",
        "exchange_release_versions",
        "(version_number = 1 AND lab_result_release_id IS NOT NULL AND amendment_id IS NULL) "
        "OR (version_number > 1 AND lab_result_release_id IS NULL AND amendment_id IS NOT NULL)",
    )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0019_EVENT_TYPES}, {_0019_NEW_EVENT_TYPES})",
    )


class DowngradeBlockedByAmendmentHistory(RuntimeError):
    """0019 -> 0018 was refused because at least one `exchange_amendments` row exists -- real
    post-release amendment history the pre-0019 schema cannot represent at all, even partially."""


class DowngradeBlockedByAmendmentAuditVocabulary(RuntimeError):
    """0019 -> 0018 was refused because a real `exchange_security_audit_events` row already uses
    one of this migration's new `event_type` values -- narrowing the CHECK back would make an
    already-committed, truthful audit row violate a constraint retroactively."""


def downgrade() -> None:
    bind = op.get_bind()

    amendment_count = bind.execute(sa.text("SELECT COUNT(*) FROM exchange_amendments")).scalar_one()
    if amendment_count:
        raise DowngradeBlockedByAmendmentHistory(
            f"refusing to downgrade past 0019_exchange_amendments: {amendment_count} "
            f"exchange_amendments row(s) carry real post-release amendment history the pre-0019 "
            f"schema cannot represent. No schema change has been made -- the database is "
            f"unchanged and remains at 0019_exchange_amendments."
        )

    audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events WHERE event_type IN "
            "('lab_result_amendment_started', 'lab_observation_amended', "
            "'lab_result_amendment_released')"
        )
    ).scalar_one()
    if audit_count:
        raise DowngradeBlockedByAmendmentAuditVocabulary(
            f"refusing to downgrade past 0019_exchange_amendments: {audit_count} "
            f"exchange_security_audit_events row(s) already use a post-0018 event_type the "
            f"pre-0019 CHECK constraint cannot represent. No schema change has been made -- the "
            f"database is unchanged and remains at 0019_exchange_amendments."
        )

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0019_EVENT_TYPES})",
    )

    op.drop_constraint(
        "ck_exchange_release_versions_v1_bound_to_release",
        "exchange_release_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_release_versions_v1_bound_to_release",
        "exchange_release_versions",
        "(version_number = 1 AND lab_result_release_id IS NOT NULL) OR "
        "(version_number > 1 AND lab_result_release_id IS NULL)",
    )
    op.drop_constraint(
        "uq_exchange_release_versions_amendment_id",
        "exchange_release_versions",
        type_="unique",
    )
    op.drop_constraint(
        "fk_exchange_release_versions_amendment", "exchange_release_versions", type_="foreignkey"
    )
    op.drop_column("exchange_release_versions", "amendment_id")

    op.drop_index("ix_exchange_lab_observations_amendment_id", "exchange_lab_observations")
    op.drop_constraint(
        "fk_exchange_lab_observations_amendment", "exchange_lab_observations", type_="foreignkey"
    )
    op.drop_column("exchange_lab_observations", "amendment_id")

    op.drop_index("uq_exchange_amendments_one_preparing_per_order", "exchange_amendments")
    op.drop_index("ix_exchange_amendments_base_release_version_id", "exchange_amendments")
    op.drop_index("ix_exchange_amendments_lab_order_id", "exchange_amendments")
    op.drop_table("exchange_amendments")
