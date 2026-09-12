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

**DH-EX-010A1 correction.** An earlier version of this migration's ``downgrade()`` unconditionally
dropped the column with no guard at all, reasoning that -- unlike ``0006`` through ``0012``'s
CHECK-widening pattern -- dropping a whole new column can never hit the DDL-level failure that
motivates those guards (re-narrowing a CHECK against existing incompatible rows). That reasoning
was correct about the *DDL mechanics* but wrong about the *stakes*: ``access_mode`` is not an
ordinary data column whose loss is an "ordinary schema-downgrade information loss" -- it is
authorization/capability state (see ``exchange/models.py``'s ``PatientAccessMode`` docstring).
Reproduced directly: downgrade past 0013 with a real ``ORDER_READ`` row present, then re-upgrade --
the surviving row silently comes back as ``LAB_SELECTION`` (the pre-0013 backfill value), a real
capability having been erased with no error, no confirmation, and no trace. ``downgrade()`` now
follows the exact same fail-fast-before-any-DDL contract ``0006`` through ``0012`` established for
CHECK-widening, applied here to a plain column: refuse if **any** row -- including a
revoked/expired grant, since a credential's own historical capability is still authoritative
security state regardless of whether it is still usable today -- has ``access_mode = 'ORDER_READ'``.
Downgrading remains safe, and permitted, whenever every existing patient access grant is
``LAB_SELECTION`` (including zero rows): pre-0013 patient grants were semantically
selection-capable by construction, so dropping the column in that case loses no distinction 0013
newly introduced.

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


class DowngradeBlockedByOrderReadGrantHistory(RuntimeError):
    """0013 -> 0012 was refused because real ``ORDER_READ`` patient-access-grant history exists
    that the pre-0013 schema (no ``access_mode`` column at all) cannot represent -- the same
    downgrade contract ``0006`` through ``0012`` already established for CHECK-widening, applied
    here to a plain added column. Every row is checked, not only currently unrevoked/unexpired
    ones: a grant's own historical ``ORDER_READ`` capability is still authoritative security state
    even after the credential itself has been revoked or has expired, and this repository's
    established discipline is to preserve authoritative history rather than rewrite or delete it
    merely to make a downgrade possible (see e.g. ``0011``/``0012``'s identical treatment of
    security-audit-event history). Downgrading a database where every existing grant is
    ``LAB_SELECTION`` (including zero grants) remains safe and is not blocked -- see this
    migration's own module docstring for why."""


def downgrade() -> None:
    bind = op.get_bind()
    order_read_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_patient_access_grants WHERE access_mode = 'ORDER_READ'"
        )
    ).scalar_one()
    if order_read_count:
        raise DowngradeBlockedByOrderReadGrantHistory(
            f"refusing to downgrade past 0013_exchange_access_mode: {order_read_count} "
            f"exchange_patient_access_grants row(s) exist with access_mode='ORDER_READ' "
            f"(including any revoked/expired grants -- historical capability state, not just "
            f"currently-usable credentials), a distinction the pre-0013 schema cannot represent. "
            f"Reversing this migration would require deleting or rewriting that authorization "
            f"state -- and this migration does neither. No schema change has been made -- the "
            f"database is unchanged and remains at 0013_exchange_access_mode. 0013 is "
            f"conditionally reversible: only while every existing patient access grant is "
            f"LAB_SELECTION."
        )

    op.drop_constraint(
        "ck_exchange_patient_access_grants_access_mode",
        "exchange_patient_access_grants",
        type_="check",
    )
    op.drop_column("exchange_patient_access_grants", "access_mode")
