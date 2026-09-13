"""DH-EX-012C/ADR-047: multi-observation result domain -- ``LabResult`` becomes a per-item
result-entry container, ``LabObservation`` becomes the child table holding the actual value(s).

Satisfies ADR-046's field-derived requirement (one ordered diagnostic service may return zero, one,
or many returned observations) the way ADR-047 recommended: every scalar value column
(``result_type``, ``numeric_value``/``numeric_unit``/``reference_range_low``/
``reference_range_high``, ``text_value``) moves off ``exchange_lab_results`` onto the new
``exchange_lab_observations`` table (FK ``lab_result_id``, no uniqueness beyond its own primary key
-- a real source may legitimately report a repeated measurement, and this ticket does not invent an
unsupported "one code per result" rule). ``exchange_lab_results`` gains three columns instead:
``created_at`` (when the container was first created), ``entry_completed_at`` (the single explicit
"this item's result entry is finished" signal -- never inferred from observation count) and
``entry_completed_by_identity_id`` (nullable: a legacy migrated row cannot truthfully name an
entering actor, since the pre-DH-EX-012C ``LabResult`` carried no actor column of its own; every new
completion recorded through the API always populates it).

Also widens, purely additively, matching this package's established discipline (``0006``-``0015``):
``exchange_security_audit_events.event_type`` -- CHECK gains ``'lab_observation_recorded'`` and
``'lab_result_entry_completed'`` (both fit the existing ``VARCHAR(32)`` column: 25 and 27
characters respectively). ``lab_result_recorded`` stays in the CHECK's allowed list -- real
historical rows already carry it, and narrowing the constraint would require proving none exist, an
unnecessary destructive-adjacent step for what is otherwise a purely additive migration; no new code
path writes it (see ``exchange/security_audit.py``'s own comment).

**Legacy data migration, lossless, fabricates nothing.** For every existing
``exchange_lab_results`` row (which, before this migration runs, still carries the old scalar
columns directly): one child ``exchange_lab_observations`` row is inserted, copying
``result_type``/``numeric_value``/``numeric_unit``/``reference_range_low``/``reference_range_high``/
``text_value`` verbatim, with ``recorded_at`` set from the row's own ``completed_at``. The
observation's ``source_code``/``display_label_snapshot`` are read from the *same ordered item's*
``catalog_code_snapshot``/``catalog_display_name_snapshot`` -- semantically truthful reuse: under
the pre-DH-EX-012C model, that single value *was* the entirety of what would ever be returned for
that ordered service, so treating it as "the observation representing this ordered service" states
nothing the old row did not already imply. ``source_system`` is set to the same self-asserted
sentinel native DH-EX-012C entry uses (``exchange/results.py``'s
``NATIVE_OBSERVATION_SOURCE_SYSTEM``, ``"dhealth-exchange"``) -- truthful, since no external
LIS/Connect source has ever produced a real row in this table. The container's own
``created_at``/``entry_completed_at`` are both backfilled to
its existing ``completed_at`` -- truthful, since under the old model that single observation being
entered *was*, in fact, the item's entire and final result. **``entry_completed_by_identity_id`` is
left ``NULL`` for every migrated row, never fabricated** -- the old ``LabResult`` had no actor
column, and DH-EX-012A's own audit already found that correlating "who entered this specific
result" to a specific `lab_result_recorded` audit row requires matching by closest timestamp, an
inference, not a guaranteed exact join; this migration does not perform that inference to populate a
column the ticket explicitly forbids fabricating a value for.

**Legacy `COMPLETED`-without-release orders (ADR-044's grandfather rule) are unaffected**: this
migration does not read or write `exchange_lab_result_releases`, `LabOrder.status`, or
`DoctorReview` at all -- provider visibility and clinician review for such an order remain gated on
`LabOrder.status` alone, exactly as before, and the migrated container/observation pair for such an
order's items is exactly as complete and truthful as for any other pre-DH-EX-012C order.

**Downgrade safety (0015's own lesson, applied here to a genuinely irreversible-in-general
change).** Four independent real-usage guards, all fail-fast before any destructive DDL, matching
this package's established discipline exactly: (1) refuses if any container has more than one
observation -- collapsing to a single scalar would destroy real data, never silently picks "the
first" and discards the rest; (2) refuses if any container has zero observations or
``entry_completed_at IS NULL`` -- an in-progress or malformed entry has no representable old-model
equivalent (absence of a `LabResult` row entirely, which the old model also cannot express once this
migration's own new columns exist to say otherwise); (3) refuses if any container has a real
``entry_completed_by_identity_id`` -- proof a genuine DH-EX-012C-era native completion action
occurred, carrying actor+time provenance the pre-0016 schema cannot represent at all; (4) refuses if
any real ``lab_observation_recorded``/``lab_result_entry_completed`` audit row exists, which the
narrower pre-0016 ``event_type`` CHECK cannot represent. Only once all four pass does downgrade
reconstruct each container's sole observation back onto ``exchange_lab_results``'s old columns,
verbatim, before dropping the new table and columns -- an unused/still-simple schema (every
container still exactly one legacy-shaped observation, no native completion ever recorded, no new
audit vocabulary used) remains conditionally downgradeable.

Revision ID: 0016_exchange_observations
Revises: 0015_exchange_result_release
Create Date: 2026-09-13
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_exchange_observations"
down_revision: str | Sequence[str] | None = "0015_exchange_result_release"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRE_0016_EVENT_TYPES = (
    "'organization_created', 'membership_created', 'role_assigned', "
    "'catalog_entry_created', 'lab_directory_profile_created', "
    "'lab_directory_profile_updated', 'lab_offering_created', "
    "'lab_offering_status_changed', 'lab_price_changed', "
    "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
    "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started', "
    "'lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded', "
    "'patient_reference_bound', 'lab_order_assigned', 'lab_sample_collection_recorded', "
    "'lab_results_released'"
)
_0016_NEW_EVENT_TYPES = "'lab_observation_recorded', 'lab_result_entry_completed'"

_NATIVE_OBSERVATION_SOURCE_SYSTEM = "dhealth-exchange"


def upgrade() -> None:
    bind = op.get_bind()

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0016_EVENT_TYPES}, {_0016_NEW_EVENT_TYPES})",
    )

    op.create_table(
        "exchange_lab_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lab_result_id", sa.Uuid(), nullable=False),
        sa.Column("source_code", sa.String(64), nullable=False),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("display_label_snapshot", sa.String(256), nullable=False),
        sa.Column("result_type", sa.String(16), nullable=False),
        sa.Column("numeric_value", sa.Numeric(18, 4), nullable=True),
        sa.Column("numeric_unit", sa.String(32), nullable=True),
        sa.Column("reference_range_low", sa.Numeric(18, 4), nullable=True),
        sa.Column("reference_range_high", sa.Numeric(18, 4), nullable=True),
        sa.Column("text_value", sa.String(2048), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lab_result_id"],
            ["exchange_lab_results.id"],
            name="fk_exchange_lab_observations_lab_result",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "result_type IN ('NUMERIC', 'TEXT')",
            name="ck_exchange_lab_observations_result_type",
        ),
        sa.CheckConstraint(
            "(result_type = 'NUMERIC' AND numeric_value IS NOT NULL AND text_value IS NULL) OR "
            "(result_type = 'TEXT' AND text_value IS NOT NULL AND numeric_value IS NULL)",
            name="ck_exchange_lab_observations_value_matches_type",
        ),
    )
    op.create_index(
        "ix_exchange_lab_observations_lab_result_id",
        "exchange_lab_observations",
        ["lab_result_id"],
    )

    op.add_column(
        "exchange_lab_results",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "exchange_lab_results",
        sa.Column("entry_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "exchange_lab_results",
        sa.Column("entry_completed_by_identity_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_exchange_lab_results_entry_completed_by_identity",
        "exchange_lab_results",
        "exchange_identities",
        ["entry_completed_by_identity_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Legacy data migration -- one observation per existing container, verbatim, fabricating
    # nothing (see this module's own docstring for the full truthfulness argument).
    legacy_rows = bind.execute(
        sa.text(
            "SELECT r.id AS result_id, r.result_type, r.numeric_value, r.numeric_unit, "
            "r.reference_range_low, r.reference_range_high, r.text_value, r.completed_at, "
            "item.catalog_code_snapshot, item.catalog_display_name_snapshot "
            "FROM exchange_lab_results AS r "
            "JOIN exchange_lab_order_items AS item ON item.id = r.lab_order_item_id"
        )
    ).fetchall()

    for row in legacy_rows:
        bind.execute(
            sa.text(
                "INSERT INTO exchange_lab_observations "
                "(id, lab_result_id, source_code, source_system, display_label_snapshot, "
                "result_type, numeric_value, numeric_unit, reference_range_low, "
                "reference_range_high, text_value, recorded_at) "
                "VALUES (:obs_id, :result_id, :source_code, :source_system, :display_label, "
                ":result_type, :numeric_value, :numeric_unit, :reference_range_low, "
                ":reference_range_high, :text_value, :recorded_at)"
            ),
            {
                "obs_id": uuid.uuid4(),
                "result_id": row.result_id,
                "source_code": row.catalog_code_snapshot,
                "source_system": _NATIVE_OBSERVATION_SOURCE_SYSTEM,
                "display_label": row.catalog_display_name_snapshot,
                "result_type": row.result_type,
                "numeric_value": row.numeric_value,
                "numeric_unit": row.numeric_unit,
                "reference_range_low": row.reference_range_low,
                "reference_range_high": row.reference_range_high,
                "text_value": row.text_value,
                "recorded_at": row.completed_at,
            },
        )
        bind.execute(
            sa.text(
                "UPDATE exchange_lab_results "
                "SET created_at = :completed_at, entry_completed_at = :completed_at "
                "WHERE id = :result_id"
            ),
            {"completed_at": row.completed_at, "result_id": row.result_id},
        )

    op.drop_constraint(
        "ck_exchange_lab_results_value_matches_type", "exchange_lab_results", type_="check"
    )
    op.drop_constraint("ck_exchange_lab_results_result_type", "exchange_lab_results", type_="check")
    op.drop_column("exchange_lab_results", "result_type")
    op.drop_column("exchange_lab_results", "numeric_value")
    op.drop_column("exchange_lab_results", "numeric_unit")
    op.drop_column("exchange_lab_results", "reference_range_low")
    op.drop_column("exchange_lab_results", "reference_range_high")
    op.drop_column("exchange_lab_results", "text_value")
    op.drop_column("exchange_lab_results", "completed_at")

    op.alter_column("exchange_lab_results", "created_at", nullable=False)
    op.alter_column("exchange_lab_results", "entry_completed_at", nullable=True)


class DowngradeBlockedByMultiObservationHistory(RuntimeError):
    """0016 -> 0015 was refused because a real ``exchange_lab_results`` container has more than
    one ``exchange_lab_observations`` row -- collapsing to the pre-0016 single-scalar shape would
    silently destroy real observation data. No observation is ever picked and the rest discarded."""


class DowngradeBlockedByIncompleteEntryHistory(RuntimeError):
    """0016 -> 0015 was refused because a real ``exchange_lab_results`` container has zero
    observations, or has ``entry_completed_at IS NULL`` -- an in-progress or malformed entry has no
    representable equivalent in the pre-0016 schema."""


class DowngradeBlockedByNativeCompletionProvenanceHistory(RuntimeError):
    """0016 -> 0015 was refused because a real ``exchange_lab_results`` container has a non-NULL
    ``entry_completed_by_identity_id`` -- proof a genuine DH-EX-012C native completion action was
    recorded, carrying actor+time provenance the pre-0016 schema has no column to represent."""


class DowngradeBlockedByObservationAuditVocabularyHistory(RuntimeError):
    """0016 -> 0015 was refused because a real ``exchange_security_audit_events`` row used
    ``event_type`` ``'lab_observation_recorded'`` or ``'lab_result_entry_completed'``, which the
    narrower pre-0016 CHECK constraint cannot represent."""


def downgrade() -> None:
    bind = op.get_bind()

    multi_observation_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT lab_result_id FROM exchange_lab_observations "
            "GROUP BY lab_result_id HAVING COUNT(*) > 1"
            ") AS multi"
        )
    ).scalar_one()
    if multi_observation_count:
        raise DowngradeBlockedByMultiObservationHistory(
            f"refusing to downgrade past 0016_exchange_observations: {multi_observation_count} "
            f"container(s) have more than one observation, which the pre-0016 single-scalar "
            f"shape cannot represent without destroying data. No schema change has been made -- "
            f"the database is unchanged and remains at 0016_exchange_observations."
        )

    incomplete_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_lab_results r "
            "WHERE r.entry_completed_at IS NULL "
            "OR NOT EXISTS (SELECT 1 FROM exchange_lab_observations o "
            "WHERE o.lab_result_id = r.id)"
        )
    ).scalar_one()
    if incomplete_count:
        raise DowngradeBlockedByIncompleteEntryHistory(
            f"refusing to downgrade past 0016_exchange_observations: {incomplete_count} "
            f"container(s) are incomplete or have zero observations, which the pre-0016 schema "
            f"cannot represent. No schema change has been made -- the database is unchanged and "
            f"remains at 0016_exchange_observations."
        )

    native_completion_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_lab_results "
            "WHERE entry_completed_by_identity_id IS NOT NULL"
        )
    ).scalar_one()
    if native_completion_count:
        raise DowngradeBlockedByNativeCompletionProvenanceHistory(
            f"refusing to downgrade past 0016_exchange_observations: {native_completion_count} "
            f"container(s) carry real completion-actor provenance the pre-0016 schema has no "
            f"column to represent. No schema change has been made -- the database is unchanged "
            f"and remains at 0016_exchange_observations."
        )

    observation_audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events "
            "WHERE event_type IN ('lab_observation_recorded', 'lab_result_entry_completed')"
        )
    ).scalar_one()
    if observation_audit_count:
        raise DowngradeBlockedByObservationAuditVocabularyHistory(
            f"refusing to downgrade past 0016_exchange_observations: {observation_audit_count} "
            f"exchange_security_audit_events row(s) use an event_type the narrower pre-0016 "
            f"CHECK constraint cannot represent. No schema change has been made -- the database "
            f"is unchanged and remains at 0016_exchange_observations."
        )

    op.add_column("exchange_lab_results", sa.Column("result_type", sa.String(16), nullable=True))
    op.add_column(
        "exchange_lab_results", sa.Column("numeric_value", sa.Numeric(18, 4), nullable=True)
    )
    op.add_column("exchange_lab_results", sa.Column("numeric_unit", sa.String(32), nullable=True))
    op.add_column(
        "exchange_lab_results", sa.Column("reference_range_low", sa.Numeric(18, 4), nullable=True)
    )
    op.add_column(
        "exchange_lab_results", sa.Column("reference_range_high", sa.Numeric(18, 4), nullable=True)
    )
    op.add_column("exchange_lab_results", sa.Column("text_value", sa.String(2048), nullable=True))
    op.add_column(
        "exchange_lab_results",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    sole_observations = bind.execute(
        sa.text(
            "SELECT r.id AS result_id, o.result_type, o.numeric_value, o.numeric_unit, "
            "o.reference_range_low, o.reference_range_high, o.text_value, o.recorded_at "
            "FROM exchange_lab_results AS r "
            "JOIN exchange_lab_observations AS o ON o.lab_result_id = r.id"
        )
    ).fetchall()
    for row in sole_observations:
        bind.execute(
            sa.text(
                "UPDATE exchange_lab_results SET "
                "result_type = :result_type, numeric_value = :numeric_value, "
                "numeric_unit = :numeric_unit, reference_range_low = :reference_range_low, "
                "reference_range_high = :reference_range_high, text_value = :text_value, "
                "completed_at = :recorded_at "
                "WHERE id = :result_id"
            ),
            {
                "result_type": row.result_type,
                "numeric_value": row.numeric_value,
                "numeric_unit": row.numeric_unit,
                "reference_range_low": row.reference_range_low,
                "reference_range_high": row.reference_range_high,
                "text_value": row.text_value,
                "recorded_at": row.recorded_at,
                "result_id": row.result_id,
            },
        )

    op.alter_column("exchange_lab_results", "result_type", nullable=False)
    op.alter_column("exchange_lab_results", "completed_at", nullable=False)
    op.create_check_constraint(
        "ck_exchange_lab_results_result_type",
        "exchange_lab_results",
        "result_type IN ('NUMERIC', 'TEXT')",
    )
    op.create_check_constraint(
        "ck_exchange_lab_results_value_matches_type",
        "exchange_lab_results",
        "(result_type = 'NUMERIC' AND numeric_value IS NOT NULL AND text_value IS NULL) OR "
        "(result_type = 'TEXT' AND text_value IS NOT NULL AND numeric_value IS NULL)",
    )

    op.drop_constraint(
        "fk_exchange_lab_results_entry_completed_by_identity",
        "exchange_lab_results",
        type_="foreignkey",
    )
    op.drop_column("exchange_lab_results", "entry_completed_by_identity_id")
    op.drop_column("exchange_lab_results", "entry_completed_at")
    op.drop_column("exchange_lab_results", "created_at")

    op.drop_index("ix_exchange_lab_observations_lab_result_id", "exchange_lab_observations")
    op.drop_table("exchange_lab_observations")

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0016_EVENT_TYPES})",
    )
