"""DHEALTH-FRONT-DESK-001: walk-in arrival as a capability of its own.

Two changes, and deliberately only two. **No new table, no new state, no new timestamp.**

── What is *not* here, and why each absence is a decision ──

**No `WAITING` status.** `CHECKED_IN` already means "the patient has arrived and is waiting to be
seen" -- 0034's own docstring says so ("the waiting list *is* that state"), and `VisitStatus` names
it. A second status meaning the identical fact would make two state machines describe one patient,
and the waiting queue is therefore a *projection* over `CHECKED_IN` rather than a domain of its own.

**No `checked_in_at` column.** `created_at` is already server-derived and already means exactly "the
moment this arrival was recorded", because a visit row is created by the check-in operation and by
nothing else -- there is no other creation path, before or after this migration. Adding a second
timestamp would make two columns carry one fact, and the new one would have to be backfilled from
the old one for every historical row, which is a copy dressed up as provenance. The one thing that
*did* change is who may perform the operation, and that does not change what the timestamp records.

**No `checked_in_by` column, and nothing is backfilled.** Who registered an arrival is already
recorded, truthfully and for every row including the historical ones: the `visit_created` audit
event carries `actor_identity_id`. A column would be a second, weaker copy of an audit row -- and
for every pre-0038 visit it could only ever be filled by guessing, which `AGENTS.md` forbids.

**`clinician_identity_id` is untouched.** 0034 already made it nullable and already pinned
`ck_exchange_visits_taken_visit_has_clinician` to the two states that require one. The semantic rule
this ticket needs -- a checked-in visit has no clinician, a started one cannot exist without one --
was therefore already enforced by the database before this migration existed. Nothing to add.

── 1. `reason_for_visit` becomes nullable ──

The only column change, and it is a narrowing of what the schema *claims* rather than of what it
holds. `NOT NULL` was truthful while the sole caller was a clinician, who had a reason to record.
ADR-069 lets a person holding `visit.check_in` and no clinical authority register an arrival, and
that person is deliberately not asked for a reason: a chief complaint typed at reception is clinical
documentation under another name, which the ticket's §15 forbids and `AGENTS.md` puts behind the
clinical lead's sign-off.

**Every existing row keeps its exact value.** Nothing is nulled, nothing is defaulted, nothing is
rewritten -- dropping `NOT NULL` cannot alter a row that already has a value. A clinician's own
arrival form still offers the field, so the clinician-created population is unchanged going forward
too. NULL means "nobody stated a reason here", which is a fact about the arrival rather than a gap.

SQLite cannot `ALTER COLUMN`, so this runs inside `batch_alter_table` -- the table is recreated with
the same constraints, which is why `_VISIT_TABLE_CONSTRAINTS` below spells them out. On PostgreSQL
the same call emits a plain `ALTER COLUMN ... DROP NOT NULL` and rewrites nothing.

── 2. At most one active visit per patient per provider organization ──

`uq_exchange_visits_one_active_per_patient` -- a **partial** unique index over
`(provider_organization_id, patient_id) WHERE status IN ('CHECKED_IN', 'IN_PROGRESS')`.

Partial is the whole point: a patient seen last week and back today must be able to return, so
`COMPLETED` and `CANCELLED` rows are unlimited and are not in the index at all. Only the two *live*
states are constrained, and that is the ambiguity worth removing -- a waiting queue containing the
same person twice cannot answer "is this patient waiting" at all, and the clinician who takes one of
the two rows leaves the other stranded forever.

**Why an index rather than an application check.** `exchange/visits.py`'s `start` does pre-read for
an active visit, and two reception workers submitting at the same instant both pass that read: it is
the textbook check-then-act race, and a pre-read cannot close it. Only a unique index can refuse the
second write. The service catches the resulting `IntegrityError` and returns 409, so the loser is
told what happened instead of receiving a 500. `sqlite_where` mirrors `postgresql_where` so the unit
suite enforces the same rule -- but the *concurrency* claim is only ever made against real
PostgreSQL (`tests/integration/test_dhealthfrontdesk001_postgres_qualification.py`), because
SQLite's writer serialization would make the test pass for a reason unrelated to the index.

── The upgrade refuses rather than repairing ──

Nothing before this migration stopped a provider from accumulating two active visits for one
patient, so a deployment may already hold a pair the new index would reject. `upgrade()` therefore
counts them **first** and raises with the count, naming nothing about any patient, rather than
choosing a row to cancel. Which of two real arrivals is the real one is a question about what
happened in a clinic; a migration that answered it by picking the newer row would be fabricating
clinical history, and an operator is the only party entitled to decide. This mirrors 0037's
downgrade, which refuses while any order is still BLOCKED for the same reason.

The check is skipped when `exchange_visits` does not exist yet, which is the ordinary path for a
fresh database built by running the whole chain.

── Downgrade ──

Fully reversible, and this one genuinely is: the index is dropped and the `NOT NULL` is restored.
Restoring `NOT NULL` **fails** if any visit was checked in without a stated reason while 0038 was
applied, and that failure is correct -- there is no truthful value to write into those rows, and
inventing a placeholder ("Arrivée", an empty string) would put invented clinical-adjacent content
into a clinical record to make a schema change succeed. An operator who must go back deals with
those visits deliberately. Stated here rather than discovered.

The revision id is **20 characters**, against `alembic_version.version_num`'s `VARCHAR(32)` -- the
boundary DH-CONNECT-CONTRACT-001 found the hard way (see `tests/test_migration_hygiene.py`).

Revision ID: 0038_visit_check_in
Revises: 0037_lab_order_blocks
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_visit_check_in"
down_revision: str | Sequence[str] | None = "0037_lab_order_blocks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_STATUSES = "status IN ('CHECKED_IN', 'IN_PROGRESS')"

#: Spelled out in full because `batch_alter_table` recreates the table on SQLite and a constraint
#: omitted here would be silently dropped. These are 0034's constraints verbatim -- this migration
#: changes none of them, and reading today's ORM instead would make an old migration's meaning
#: change the next time a constraint is added. Identical discipline to 0034/0037.
_VISIT_TABLE_CONSTRAINTS = (
    sa.UniqueConstraint(
        "provider_organization_id",
        "idempotency_key",
        name="uq_exchange_visits_provider_idempotency_key",
    ),
    sa.ForeignKeyConstraint(
        ["patient_id", "provider_organization_id"],
        ["exchange_patients.id", "exchange_patients.provider_organization_id"],
        name="fk_exchange_visits_patient_same_provider",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "status IN ('CHECKED_IN', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
        name="ck_exchange_visits_status",
    ),
    sa.CheckConstraint(
        "(status IN ('IN_PROGRESS', 'COMPLETED')) = (started_at IS NOT NULL)",
        name="ck_exchange_visits_started_at_matches_status",
    ),
    sa.CheckConstraint(
        "(status = 'COMPLETED') = (completed_at IS NOT NULL)",
        name="ck_exchange_visits_completed_at_matches_status",
    ),
    sa.CheckConstraint(
        "(status = 'CANCELLED') = (cancelled_at IS NOT NULL)",
        name="ck_exchange_visits_cancelled_at_matches_status",
    ),
    sa.CheckConstraint(
        "status NOT IN ('IN_PROGRESS', 'COMPLETED') OR clinician_identity_id IS NOT NULL",
        name="ck_exchange_visits_taken_visit_has_clinician",
    ),
)

#: The migration's own precondition, as one statement.
#:
#: The status predicate is spelled out here rather than interpolated from `_ACTIVE_STATUSES`, which
#: is this file's only duplication and is deliberate twice over: a migration must describe the
#: database at *this* revision rather than assemble itself from a value a later edit could change
#: (0034/0037's established discipline), and a literal has no dynamic construction for `S608` to
#: warn about, so the rule stays armed for a genuinely interpolated query added later.
_DUPLICATE_ACTIVE_VISITS = (
    "SELECT COUNT(*) FROM ("
    "  SELECT provider_organization_id, patient_id"
    "  FROM exchange_visits"
    "  WHERE status IN ('CHECKED_IN', 'IN_PROGRESS')"
    "  GROUP BY provider_organization_id, patient_id"
    "  HAVING COUNT(*) > 1"
    ") AS duplicated"
)


def _refuse_if_a_patient_already_has_two_active_visits() -> None:
    """Count the pairs the new index would reject, and raise with the count if there are any.

    **Counts only.** No patient id, no organization id, no name, nothing that identifies a person
    reaches the error message or any log line -- a migration failure is read in a terminal and
    copied into a ticket, which is not a place clinical identifiers may land (`AGENTS.md`).
    """
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("exchange_visits"):
        return

    offending_patients = bind.execute(sa.text(_DUPLICATE_ACTIVE_VISITS)).scalar_one()

    if offending_patients:
        raise RuntimeError(
            "0038_visit_check_in cannot apply: "
            f"{offending_patients} patient/organization pair(s) currently hold more than one "
            "active (CHECKED_IN or IN_PROGRESS) visit, which "
            "uq_exchange_visits_one_active_per_patient forbids. Nothing has been changed. "
            "Resolve each one deliberately -- complete or cancel the visits that did not happen -- "
            "and run the upgrade again. This migration will not choose for you: which of two real "
            "arrivals is the real one is a question about what happened in the clinic, not a "
            "question a schema change may answer."
        )


def upgrade() -> None:
    # First, before anything is altered, so a refusal leaves the schema exactly as it was.
    _refuse_if_a_patient_already_has_two_active_visits()

    with op.batch_alter_table("exchange_visits", table_args=_VISIT_TABLE_CONSTRAINTS) as batch:
        batch.alter_column(
            "reason_for_visit",
            existing_type=sa.String(500),
            existing_nullable=False,
            nullable=True,
        )

    op.create_index(
        "uq_exchange_visits_one_active_per_patient",
        "exchange_visits",
        ["provider_organization_id", "patient_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_STATUSES),
        sqlite_where=sa.text(_ACTIVE_STATUSES),
    )


def downgrade() -> None:
    op.drop_index("uq_exchange_visits_one_active_per_patient", table_name="exchange_visits")

    # Deliberately no UPDATE filling the NULLs first. See this module's docstring: a visit checked
    # in without a stated reason has no truthful value to restore, and manufacturing one to make a
    # schema change succeed would write invented content into a clinical record. This raises, and
    # the raise is the correct outcome.
    with op.batch_alter_table("exchange_visits", table_args=_VISIT_TABLE_CONSTRAINTS) as batch:
        batch.alter_column(
            "reason_for_visit",
            existing_type=sa.String(500),
            existing_nullable=True,
            nullable=False,
        )
