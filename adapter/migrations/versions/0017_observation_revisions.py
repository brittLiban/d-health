"""DH-EX-013/ADR-045 (pre-release correction only): immutable supersession chain for
``LabObservation``, plus exact per-observation entering/correcting actor provenance.

Adds three nullable columns to ``exchange_lab_observations``:

- ``recorded_by_identity_id`` -- the exact persistent actor who created *this* observation row
  (entering it for the first time, or correcting a predecessor). Nullable because a pre-DH-EX-013
  observation (recorded by DH-EX-012C, or migrated by ``0016_exchange_observations`` from the
  pre-DH-EX-012C scalar ``LabResult`` shape) was never asked to carry this fact -- this migration
  does not retroactively fabricate one. Every *new* observation from this ticket forward (via
  ``record_observation``, the single-observation convenience path, or ``correct_observation``)
  populates it.
- ``supersedes_observation_id`` -- ``NULL`` for an original observation, or the id of the exact
  observation this one corrects. Self-referential FK to this same table.
- ``superseded_at`` -- ``NULL`` while this row is its chain's *effective* value; set once, never
  cleared, the instant a correction supersedes it. **The effective rule is exactly
  ``superseded_at IS NULL``** -- see ``exchange/models.py``'s own ``LabObservation`` docstring for
  why this is an explicit relational fact, never inferred from timestamp ordering.

**Database-enforced chain integrity, not merely an application check:** a ``UNIQUE`` constraint on
``supersedes_observation_id`` makes "at most one row may ever supersede a given observation" a real
database guarantee -- the backstop against two concurrent corrections of the same effective
observation both succeeding, defense-in-depth behind the ``LabOrder FOR UPDATE`` lock that already
serializes this in the normal application path (see ``exchange/results.py``'s
``correct_observation``). A ``CHECK`` constraint additionally refuses literal self-supersession.
Cross-container supersession and correcting an already-superseded (non-effective) observation are
both application-layer checks only -- a database-level guard for either would require a trigger,
which this ticket's own instruction against "brittle database tricks" weighs against introducing
for these two specific cases (both are already fully covered by the application check running
inside the same ``LabOrder``-locked transaction, with no concurrency window for either to be
bypassed).

Also widens, purely additively, matching this package's established discipline (``0006``-``0016``):
``exchange_security_audit_events.event_type`` -- CHECK gains ``'lab_observation_corrected'`` (25
characters, fits the existing ``VARCHAR(32)`` column).

**No legacy data migration is needed or performed.** Every existing ``LabObservation`` row
(DH-EX-012C-native or DH-EX-012C-migrated-from-legacy) is already truthfully described by the
default state of all three new columns: ``recorded_by_identity_id = NULL`` (actor unknown, never
fabricated), ``supersedes_observation_id = NULL`` (not a correction of anything), ``superseded_at =
NULL`` (still effective -- true for every observation that exists today, since correction did not
exist before this ticket). No `UPDATE` statement runs against any existing row.

**Downgrade safety.** Three independent fail-fast guards, matching this package's established
discipline exactly: (1) refuses if any observation has ``supersedes_observation_id IS NOT NULL`` or
``superseded_at IS NOT NULL`` -- real correction history the pre-0017 schema cannot represent at
all (downgrading would not just lose metadata, it would silently make a corrected value
indistinguishable from an original one, with the actual original value gone); (2) refuses if any
observation has ``recorded_by_identity_id IS NOT NULL`` -- proof a genuine per-observation actor was
recorded, which the pre-0017 schema has no column for, mirroring ``0016``'s own
``DowngradeBlockedByNativeCompletionProvenanceHistory`` guard for the identical class of fact one
level up (on ``LabResult``); (3) refuses if any real ``lab_observation_corrected`` audit row exists,
which the narrower pre-0017 ``event_type`` CHECK cannot represent. An unused schema (zero rows
triggering any guard) remains conditionally downgradeable -- the three new columns are simply
dropped, losing nothing since every remaining row already has all three at their default `NULL`.

Revision ID: 0017_observation_revisions
Revises: 0016_exchange_observations
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_observation_revisions"
down_revision: str | Sequence[str] | None = "0016_exchange_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRE_0017_EVENT_TYPES = (
    "'organization_created', 'membership_created', 'role_assigned', "
    "'catalog_entry_created', 'lab_directory_profile_created', "
    "'lab_directory_profile_updated', 'lab_offering_created', "
    "'lab_offering_status_changed', 'lab_price_changed', "
    "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
    "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started', "
    "'lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded', "
    "'patient_reference_bound', 'lab_order_assigned', 'lab_sample_collection_recorded', "
    "'lab_results_released', 'lab_observation_recorded', 'lab_result_entry_completed'"
)
_0017_NEW_EVENT_TYPE = "'lab_observation_corrected'"


def upgrade() -> None:
    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0017_EVENT_TYPES}, {_0017_NEW_EVENT_TYPE})",
    )

    op.add_column(
        "exchange_lab_observations",
        sa.Column("recorded_by_identity_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "exchange_lab_observations",
        sa.Column("supersedes_observation_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "exchange_lab_observations",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_exchange_lab_observations_recorded_by_identity",
        "exchange_lab_observations",
        "exchange_identities",
        ["recorded_by_identity_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_exchange_lab_observations_supersedes_observation",
        "exchange_lab_observations",
        "exchange_lab_observations",
        ["supersedes_observation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_exchange_lab_observations_supersedes_observation_id",
        "exchange_lab_observations",
        ["supersedes_observation_id"],
    )
    op.create_check_constraint(
        "ck_exchange_lab_observations_no_self_supersede",
        "exchange_lab_observations",
        "supersedes_observation_id IS NULL OR supersedes_observation_id != id",
    )


class DowngradeBlockedByCorrectionHistory(RuntimeError):
    """0017 -> 0016 was refused because a real ``exchange_lab_observations`` row has
    ``supersedes_observation_id IS NOT NULL`` or ``superseded_at IS NOT NULL`` -- real correction
    history the pre-0017 schema cannot represent: downgrading would not merely lose metadata, it
    would silently make a corrected value indistinguishable from an original one, with the actual
    original value gone."""


class DowngradeBlockedByObservationActorProvenanceHistory(RuntimeError):
    """0017 -> 0016 was refused because a real ``exchange_lab_observations`` row has a non-NULL
    ``recorded_by_identity_id`` -- proof a genuine per-observation actor was recorded, which the
    pre-0017 schema has no column to represent."""


class DowngradeBlockedByObservationCorrectionAuditVocabularyHistory(RuntimeError):
    """0017 -> 0016 was refused because a real ``exchange_security_audit_events`` row used
    ``event_type`` ``'lab_observation_corrected'``, which the narrower pre-0017 CHECK constraint
    cannot represent."""


def downgrade() -> None:
    bind = op.get_bind()

    correction_history_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_lab_observations "
            "WHERE supersedes_observation_id IS NOT NULL OR superseded_at IS NOT NULL"
        )
    ).scalar_one()
    if correction_history_count:
        raise DowngradeBlockedByCorrectionHistory(
            f"refusing to downgrade past 0017_observation_revisions: "
            f"{correction_history_count} exchange_lab_observations row(s) carry real correction "
            f"history the pre-0017 schema cannot represent. No schema change has been made -- the "
            f"database is unchanged and remains at 0017_observation_revisions."
        )

    actor_provenance_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_lab_observations "
            "WHERE recorded_by_identity_id IS NOT NULL"
        )
    ).scalar_one()
    if actor_provenance_count:
        raise DowngradeBlockedByObservationActorProvenanceHistory(
            f"refusing to downgrade past 0017_observation_revisions: "
            f"{actor_provenance_count} exchange_lab_observations row(s) carry real per-observation "
            f"actor provenance the pre-0017 schema has no column to represent. No schema change "
            f"has been made -- the database is unchanged and remains at "
            f"0017_observation_revisions."
        )

    correction_audit_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_security_audit_events "
            "WHERE event_type = 'lab_observation_corrected'"
        )
    ).scalar_one()
    if correction_audit_count:
        raise DowngradeBlockedByObservationCorrectionAuditVocabularyHistory(
            f"refusing to downgrade past 0017_observation_revisions: "
            f"{correction_audit_count} exchange_security_audit_events row(s) use an event_type "
            f"the narrower pre-0017 CHECK constraint cannot represent. No schema change has been "
            f"made -- the database is unchanged and remains at 0017_observation_revisions."
        )

    op.drop_constraint(
        "ck_exchange_lab_observations_no_self_supersede",
        "exchange_lab_observations",
        type_="check",
    )
    op.drop_constraint(
        "uq_exchange_lab_observations_supersedes_observation_id",
        "exchange_lab_observations",
        type_="unique",
    )
    op.drop_constraint(
        "fk_exchange_lab_observations_supersedes_observation",
        "exchange_lab_observations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_exchange_lab_observations_recorded_by_identity",
        "exchange_lab_observations",
        type_="foreignkey",
    )
    op.drop_column("exchange_lab_observations", "superseded_at")
    op.drop_column("exchange_lab_observations", "supersedes_observation_id")
    op.drop_column("exchange_lab_observations", "recorded_by_identity_id")

    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0017_EVENT_TYPES})",
    )
