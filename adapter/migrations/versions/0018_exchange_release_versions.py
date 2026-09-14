"""DH-EX-014A/ADR-048: immutable release-version manifest + versioned ``DoctorReview`` foundation.

**What this migration does NOT do, stated up front:** it does not implement post-release amendment
(DH-EX-014B), does not create an amendment-preparation endpoint, does not let any observation be
mutated or added after an order reaches ``COMPLETED``, and does not create a second release version
for any order -- every version created by this migration or by the updated ``release()`` transaction
is, and can only be, version 1. This is the versioning *foundation* ADR-048's Model 3 requires,
built ahead of the mutation workflow that will use it.

**New tables.**

``exchange_release_versions`` -- one row per *released* version of an order's result set,
explicitly ordered by ``version_number`` (``UNIQUE(lab_order_id, version_number)``), never inferred
from ``created_at``. Version 1 always binds to the pre-existing, unmodified ``LabResultRelease`` row
via ``lab_result_release_id`` (itself database-``UNIQUE``, so no two versions could ever claim to be
"the" original release) -- a `CHECK` constraint enforces that version 1 is always release-bound and
every later version (DH-EX-014B's amendment releases) is not, since those will bind to their own
amendment-episode record instead, a table this migration deliberately does not create.

``exchange_release_manifest_entries`` -- one row means exactly "this exact
``LabObservation`` id was part of this exact released version, under this exact item." Binds by id
only -- no value, unit, range, source code, or display label is copied. A single item may have more
than one manifest entry for the same version (a multi-observation panel); uniqueness is scoped to
``(release_version_id, lab_observation_id)``, not to the item, matching ADR-047's own "do not impose
one observation per item" rule. Cross-item/cross-order integrity (this observation's container
belongs to this item; this item belongs to this order) is an application-layer check inside the same
``LabOrder``-locked transaction that already determines the effective set -- see
``exchange/results.py``'s ``LabResultReleaseRepository.release`` docstring.

**``exchange_doctor_reviews`` schema change (ADR-045 Question 4, Option C).** ``lab_order_id``
loses its database-``UNIQUE`` constraint (``uq_exchange_doctor_reviews_lab_order_id``, dropped) --
an order may eventually carry more than one review row, one per released version. A new nullable
``release_version_id`` column is the explicit, never-inferred-from-timestamp binding: ``NULL``
means the one legacy/pre-release row created at order-creation time before any release has
occurred (unchanged from before this ticket, and permanently the shape for a grandfathered
``COMPLETED``-without-``LabResultRelease`` order, which is not amendment-eligible); non-``NULL``
means this review's obligation is for exactly that release version, forever. Two constraints
enforce the intended shape at the database level: a plain ``UNIQUE`` on ``release_version_id``
(sufficient by itself for "at most one review per non-NULL version," since every SQL dialect this
codebase targets treats ``NULL`` as distinct from every other ``NULL`` under ``UNIQUE`` -- the
identical insight ``0017``'s ``supersedes_observation_id`` constraint already relies on); a genuine
partial unique index on ``lab_order_id`` scoped to ``release_version_id IS NULL`` (needed because
the plain ``UNIQUE`` above does not, by itself, prevent two *unbound* rows for the same order).

**Original-release backfill, proven, not assumed.** Every existing ``LabResultRelease`` row's
order is exhaustively verified (see this migration's own PR discussion / ``PROJECT_STATE.md``'s
DH-EX-014A entry for the full code-path audit) to have had every ``LabObservation``-mutating code
path (``record_observation``, ``correct_observation``, the single-observation convenience path)
gated, without exception, on the same ``LabOrder``-locked ``IN_PROGRESS`` check
``_lock_order_and_item_for_entry`` enforces -- once ``release()`` moves an order to ``COMPLETED``,
no code path anywhere in this package can create, mutate, or supersede an observation belonging to
it, until DH-EX-014B introduces a new mechanism (which does not exist on this migration's own
master). **Therefore the currently effective (``superseded_at IS NULL``) observation set for an
already-released order is, today, exactly and provably the set that was effective at the moment
release() ran** -- this is reconstruction from a proven structural invariant, not a fabrication of
historical clinical content. For every existing ``LabResultRelease`` row, this migration seeds
exactly one ``LabResultReleaseVersion`` (``version_number = 1``, ``created_at`` reused verbatim
from the release's own ``released_at`` -- not a new, separately-invented timestamp) plus one
manifest entry per currently-effective observation under that order, and binds the order's
existing ``DoctorReview`` row (``release_version_id`` set from ``NULL`` to the new version's id) --
preserving its existing ``status``/``reviewed_at``/``reviewed_by_practitioner_reference_id``
verbatim, never resetting or fabricating a review action.

**Grandfathered ``COMPLETED``-without-``LabResultRelease`` orders receive nothing** -- no version,
no manifest, no fabricated release, no fabricated releaser, no fabricated timestamp. Their
``DoctorReview`` row's ``release_version_id`` stays ``NULL`` forever (the column's own default),
exactly matching ADR-044's existing grandfather-rule discipline
(``LegacyCompletedOrderHasNoRelease``) applied one layer up. Unreleased/in-progress orders'
``PENDING`` review rows are identically untouched -- ``release_version_id`` stays ``NULL`` until
(and only if) a real, future release occurs.

**Downgrade safety.** Two independent fail-fast guards: (1) refuses if any
``exchange_release_versions`` row has ``version_number > 1`` -- real amendment-release
history (DH-EX-014B) the pre-0018 schema cannot represent at all, since it has no concept of a
second release for one order; (2) refuses if any ``lab_order_id`` appears more than once in
``exchange_doctor_reviews`` -- real multi-version review history that would make re-adding the
pre-0018 ``UNIQUE(lab_order_id)`` constraint either fail outright or silently require picking one
row and discarding the rest, an unacceptable data-loss choice this migration refuses to make
silently. Neither guard can trigger against this migration's own backfill or the initial
version-1-only shape
released orders reach going forward within this ticket's own scope (no amendment mechanism exists to
produce a second version or a second review row yet) -- both exist to protect **future** DH-EX-014B
history from an accidental downgrade run much later. With neither guard triggered, both new tables
are dropped (losing nothing meaningful -- their content is exactly reconstructable from the pre-0018
shape under the identical proven invariant this migration's own backfill relied on), the
``release_version_id`` column and its two constraints are dropped from ``exchange_doctor_reviews``,
and ``UNIQUE(lab_order_id)`` is restored.

Revision ID: 0018_exchange_release_versions
Revises: 0017_observation_revisions
Create Date: 2026-09-13
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_exchange_release_versions"
down_revision: str | Sequence[str] | None = "0017_observation_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "exchange_release_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lab_order_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("lab_result_release_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lab_order_id"],
            ["exchange_lab_orders.id"],
            name="fk_exchange_release_versions_lab_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lab_result_release_id"],
            ["exchange_lab_result_releases.id"],
            name="fk_exchange_release_versions_lab_result_release",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "lab_order_id",
            "version_number",
            name="uq_exchange_release_versions_order_version",
        ),
        sa.UniqueConstraint(
            "lab_result_release_id",
            name="uq_exchange_release_versions_lab_result_release_id",
        ),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_exchange_release_versions_version_positive"
        ),
        sa.CheckConstraint(
            "(version_number = 1 AND lab_result_release_id IS NOT NULL) OR "
            "(version_number > 1 AND lab_result_release_id IS NULL)",
            name="ck_exchange_release_versions_v1_bound_to_release",
        ),
    )
    op.create_index(
        "ix_exchange_release_versions_lab_order_id",
        "exchange_release_versions",
        ["lab_order_id"],
    )

    op.create_table(
        "exchange_release_manifest_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("release_version_id", sa.Uuid(), nullable=False),
        sa.Column("lab_order_item_id", sa.Uuid(), nullable=False),
        sa.Column("lab_observation_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["release_version_id"],
            ["exchange_release_versions.id"],
            name="fk_exchange_release_manifest_entries_release_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lab_order_item_id"],
            ["exchange_lab_order_items.id"],
            name="fk_exchange_release_manifest_entries_lab_order_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lab_observation_id"],
            ["exchange_lab_observations.id"],
            name="fk_exchange_release_manifest_entries_lab_observation",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "release_version_id",
            "lab_observation_id",
            name="uq_exchange_release_manifest_entries_version_observation",
        ),
    )
    op.create_index(
        "ix_exchange_release_manifest_entries_release_version_id",
        "exchange_release_manifest_entries",
        ["release_version_id"],
    )
    op.create_index(
        "ix_exchange_release_manifest_entries_lab_order_item_id",
        "exchange_release_manifest_entries",
        ["lab_order_item_id"],
    )

    op.drop_constraint(
        "uq_exchange_doctor_reviews_lab_order_id", "exchange_doctor_reviews", type_="unique"
    )
    op.add_column(
        "exchange_doctor_reviews",
        sa.Column("release_version_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_exchange_doctor_reviews_release_version",
        "exchange_doctor_reviews",
        "exchange_release_versions",
        ["release_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_exchange_doctor_reviews_release_version_id",
        "exchange_doctor_reviews",
        ["release_version_id"],
    )
    op.create_index(
        "uq_exchange_doctor_reviews_unbound_lab_order_id",
        "exchange_doctor_reviews",
        ["lab_order_id"],
        unique=True,
        postgresql_where=sa.text("release_version_id IS NULL"),
        sqlite_where=sa.text("release_version_id IS NULL"),
    )

    # Original-release backfill -- reconstruction from the proven invariant this migration's own
    # docstring establishes (no post-release observation mutation path has ever existed), never a
    # fabrication. Grandfathered COMPLETED-without-LabResultRelease orders and unreleased/
    # in-progress orders are untouched by this loop entirely -- their DoctorReview rows keep
    # `release_version_id = NULL` (the column's own default).
    release_rows = bind.execute(
        sa.text(
            "SELECT id AS release_id, lab_order_id, released_at FROM exchange_lab_result_releases"
        )
    ).fetchall()

    for release_row in release_rows:
        version_id = uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO exchange_release_versions "
                "(id, lab_order_id, version_number, lab_result_release_id, created_at) "
                "VALUES (:version_id, :lab_order_id, 1, :release_id, :created_at)"
            ),
            {
                "version_id": version_id,
                "lab_order_id": release_row.lab_order_id,
                "release_id": release_row.release_id,
                "created_at": release_row.released_at,
            },
        )

        effective_observations = bind.execute(
            sa.text(
                "SELECT item.id AS item_id, obs.id AS observation_id "
                "FROM exchange_lab_order_items AS item "
                "JOIN exchange_lab_results AS res ON res.lab_order_item_id = item.id "
                "JOIN exchange_lab_observations AS obs "
                "ON obs.lab_result_id = res.id AND obs.superseded_at IS NULL "
                "WHERE item.lab_order_id = :lab_order_id"
            ),
            {"lab_order_id": release_row.lab_order_id},
        ).fetchall()

        for obs_row in effective_observations:
            bind.execute(
                sa.text(
                    "INSERT INTO exchange_release_manifest_entries "
                    "(id, release_version_id, lab_order_item_id, lab_observation_id) "
                    "VALUES (:entry_id, :version_id, :item_id, :observation_id)"
                ),
                {
                    "entry_id": uuid.uuid4(),
                    "version_id": version_id,
                    "item_id": obs_row.item_id,
                    "observation_id": obs_row.observation_id,
                },
            )

        bind.execute(
            sa.text(
                "UPDATE exchange_doctor_reviews SET release_version_id = :version_id "
                "WHERE lab_order_id = :lab_order_id AND release_version_id IS NULL"
            ),
            {"version_id": version_id, "lab_order_id": release_row.lab_order_id},
        )


class DowngradeBlockedByAmendmentReleaseVersionHistory(RuntimeError):
    """0018 -> 0017 was refused because a real ``exchange_release_versions`` row has
    ``version_number > 1`` -- real DH-EX-014B amendment-release history the pre-0018 schema cannot
    represent at all, since it has no concept of a second release for one order."""


class DowngradeBlockedByMultipleReviewsPerOrderHistory(RuntimeError):
    """0018 -> 0017 was refused because a real ``lab_order_id`` has more than one
    ``exchange_doctor_reviews`` row -- real multi-version review history; re-adding the pre-0018
    ``UNIQUE(lab_order_id)`` constraint would either fail outright or require silently discarding
    every review but one, which this migration refuses to do."""


def downgrade() -> None:
    bind = op.get_bind()

    amendment_version_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_release_versions WHERE version_number > 1")
    ).scalar_one()
    if amendment_version_count:
        raise DowngradeBlockedByAmendmentReleaseVersionHistory(
            f"refusing to downgrade past 0018_exchange_release_versions: "
            f"{amendment_version_count} exchange_release_versions row(s) carry real "
            f"amendment-release history the pre-0018 schema cannot represent. No schema change has "
            f"been made -- the database is unchanged and remains at 0018_exchange_release_versions."
        )

    multiple_reviews_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT lab_order_id FROM exchange_doctor_reviews "
            "GROUP BY lab_order_id HAVING COUNT(*) > 1"
            ") AS orders_with_multiple_reviews"
        )
    ).scalar_one()
    if multiple_reviews_count:
        raise DowngradeBlockedByMultipleReviewsPerOrderHistory(
            f"refusing to downgrade past 0018_exchange_release_versions: "
            f"{multiple_reviews_count} lab order(s) carry more than one exchange_doctor_reviews "
            f"row -- real multi-version review history the pre-0018 schema cannot represent. No "
            f"schema change has been made -- the database is unchanged and remains at "
            f"0018_exchange_release_versions."
        )

    op.drop_index("uq_exchange_doctor_reviews_unbound_lab_order_id", "exchange_doctor_reviews")
    op.drop_constraint(
        "uq_exchange_doctor_reviews_release_version_id",
        "exchange_doctor_reviews",
        type_="unique",
    )
    op.drop_constraint(
        "fk_exchange_doctor_reviews_release_version", "exchange_doctor_reviews", type_="foreignkey"
    )
    op.drop_column("exchange_doctor_reviews", "release_version_id")
    op.create_unique_constraint(
        "uq_exchange_doctor_reviews_lab_order_id",
        "exchange_doctor_reviews",
        ["lab_order_id"],
    )

    op.drop_table("exchange_release_manifest_entries")
    op.drop_table("exchange_release_versions")
