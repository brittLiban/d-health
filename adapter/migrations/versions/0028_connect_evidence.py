"""DH-CONNECT-CONTRACT-001 correction: how strongly a capability row is evidenced.

Adds ``evidence_level`` to ``connector_qualification_capabilities``: ``DECLARED`` or ``EXERCISED``,
non-null, CHECK-constrained.

**Why this is a correction rather than a feature.** 0027 recorded ``ORDER_RECEIPT`` as
``passed=True`` with the limitation "declaration only" written in prose, and
``qualification_verdict_in_session`` counted every ``passed=True`` row toward the required
production bundle. Nothing was routable, so nothing was wrong today -- but DH-CONNECT-AGENT-001 is
precisely the ticket that supplies the missing runtime, and on the day it lands a connector that had
only *claimed* it could receive orders would have become production-routable without DHealth ever
having watched it receive one. Prose in a ``detail`` column cannot stop that; a constrained column
the verdict reads can.

**The backfill rule is capability-based, not row-inspected, and that is on purpose.** A 0027 row
does not record which harness produced it, so "was this particular row exercised" is not a question
the data can answer. What the data *can* answer is structural, and it is enough:

* ``ORDER_RECEIPT`` is the one laboratory capability whose direction is DHealth -> connector. It
  needs something to push a message over, and the 0027 build has no transport of any kind. No row
  written under 0027 can have exercised it. -> ``DECLARED``.
* The four provider capabilities (``ORDER_SUBMISSION``, ``RELEASED_RESULT_RECEIPT``,
  ``AMENDMENT_RECEIPT``, ``REVIEW_REPORTING``) had no harness at all in 0027 -- only
  ``LabQualificationHarness`` existed. Any provider row is therefore a recorded declaration.
  -> ``DECLARED``.
* Everything else is a laboratory capability the synthetic harness genuinely drove through the real
  Exchange repositories, writing and reading back real rows. -> ``EXERCISED``.

The ambiguous direction is resolved toward ``DECLARED``, which is the fail-closed direction: an
under-claimed capability blocks qualification until somebody reruns the suite, whereas an
over-claimed one would let unproven evidence gate clinical routing.

**The backfill governs history, not the verdict.** Every 0027 qualification row carries
``suite_version = '1'``, and this correction bumps ``QUALIFICATION_SUITE_VERSION`` to ``'2'`` and
makes suite version part of applicability. So no pre-existing qualification satisfies the new
verdict regardless of how its capability rows are labelled -- the backfill exists so the *history*
an operator reads is truthful, not so an old PASS can be salvaged.

No table is created or dropped. No secret is introduced; ``evidence_level`` holds one of two fixed
words.

The downgrade drops the CHECK and the column. Lossless in the sense that matters: the column is new
here, and the information it holds is re-derivable by rerunning a qualification.

**Revision id length.** ``alembic_version.version_num`` is ``VARCHAR(32)``. The first spelling of
this revision -- ``0028_connect_qualification_evidence`` -- is 35 characters, and real PostgreSQL
refused it with ``StringDataRightTruncation`` *after* applying the DDL. SQLite does not enforce
VARCHAR length, so the whole SQLite suite passed against a migration that could never be stamped on
the production database. Keep revision ids short.

Revision ID: 0028_connect_evidence
Revises: 0027_connect_contract
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_connect_evidence"
down_revision: str | Sequence[str] | None = "0027_connect_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Capabilities nothing in the 0027 build could have exercised. See the module docstring.
_UNEXERCISABLE_UNDER_0027 = (
    "ORDER_RECEIPT",
    "ORDER_SUBMISSION",
    "RELEASED_RESULT_RECEIPT",
    "AMENDMENT_RECEIPT",
    "REVIEW_REPORTING",
)


def upgrade() -> None:
    # Nullable first, so the backfill has somewhere to write before the constraint applies. The
    # column is tightened to NOT NULL below rather than given a server default: a default would
    # mean a future insert that forgot to say how it was evidenced silently inherits an answer,
    # and the whole point of this migration is that nothing inherits the stronger answer.
    op.add_column(
        "connector_qualification_capabilities",
        sa.Column("evidence_level", sa.String(length=16), nullable=True),
    )

    # Built as SQLAlchemy expressions rather than interpolated SQL. The capability names are
    # module constants and could not carry injection, but a migration is exactly the file where
    # "it's only constants" stops being true after somebody parameterises it later.
    capabilities = sa.table(
        "connector_qualification_capabilities",
        sa.column("capability", sa.String),
        sa.column("evidence_level", sa.String),
    )
    op.execute(
        capabilities.update()
        .where(capabilities.c.capability.in_(_UNEXERCISABLE_UNDER_0027))
        .values(evidence_level="DECLARED")
    )
    op.execute(
        capabilities.update()
        .where(capabilities.c.evidence_level.is_(None))
        .values(evidence_level="EXERCISED")
    )

    op.alter_column(
        "connector_qualification_capabilities",
        "evidence_level",
        existing_type=sa.String(length=16),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_connector_qualification_capabilities_evidence_level",
        "connector_qualification_capabilities",
        "evidence_level IN ('DECLARED', 'EXERCISED')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_connector_qualification_capabilities_evidence_level",
        "connector_qualification_capabilities",
        type_="check",
    )
    op.drop_column("connector_qualification_capabilities", "evidence_level")
