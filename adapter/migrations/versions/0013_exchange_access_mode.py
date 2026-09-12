"""DH-EX-010A: persisted patient-access-grant capability mode.

Adds ``exchange_patient_access_grants.access_mode`` (``LAB_SELECTION`` | ``ORDER_READ``), a closed
capability persisted on the grant at issuance and never re-derived later from mutable ``LabOrder``
state (see ``exchange/models.py``'s ``PatientAccessMode`` docstring). Every pre-existing row was, by
construction, issued only while an order could still have its lab chosen -- ``LAB_SELECTION`` is the
correct and only truthful backfill value for row already in this table.

Backfilled via a column-level ``server_default`` at ``ALTER TABLE`` time (not a two-step nullable-
then-backfill-then-not-null dance): every existing row gets ``'LAB_SELECTION'`` atomically as part
of adding the column. The server default is then dropped immediately afterward -- every future
insert goes through ``PatientAccessGrantRepository._issue_in_session``, which always supplies an
explicit ``access_mode`` computed from the locked order's own current state; a silent implicit
default at the database level would hide a real application bug (an insert path that forgot to
decide the mode) rather than surfacing it as a `NOT NULL` violation.

Unlike ``0006`` through ``0012``'s CHECK-widening pattern, this migration adds a whole new column
rather than widening an existing closed enum, so its ``downgrade()`` does not need (and does not
have) a "real usage exists, refuse to downgrade" guard: dropping this column can never fail at the
DDL level the way re-narrowing a CHECK constraint against existing incompatible rows would, so
there is nothing here to fail *fast* in front of. Downgrading does lose the recorded distinction
between ``LAB_SELECTION`` and ``ORDER_READ`` grants -- an honest, ordinary schema-downgrade
information loss, not a blocked/irreversible one.

Revision id kept short deliberately (``0013_exchange_access_mode``, 25 characters), respecting
DH-EX-009's discovery that Alembic's own ``alembic_version.version_num`` column is ``VARCHAR(32)``.

Revision ID: 0013_exchange_access_mode
Revises: 0012_exchange_provider_assign
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_exchange_access_mode"
down_revision: str | Sequence[str] | None = "0012_exchange_provider_assign"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exchange_patient_access_grants",
        sa.Column(
            "access_mode",
            sa.String(length=16),
            nullable=False,
            server_default="LAB_SELECTION",
        ),
    )
    op.alter_column("exchange_patient_access_grants", "access_mode", server_default=None)
    op.create_check_constraint(
        "ck_exchange_patient_access_grants_access_mode",
        "exchange_patient_access_grants",
        "access_mode IN ('LAB_SELECTION', 'ORDER_READ')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_exchange_patient_access_grants_access_mode",
        "exchange_patient_access_grants",
        type_="check",
    )
    op.drop_column("exchange_patient_access_grants", "access_mode")
