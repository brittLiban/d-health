"""DH-LAB-004/ADR-053 addendum: bind Native DHealth Lab result entry to the exact controlled
template version it was entered against.

Changed tables:
- ``exchange_lab_results`` gains ``result_template_id`` -- the exact ``LabResultTemplate`` this
  container was pinned to on its first Native write. Nullable: every pre-LAB-004 container and
  every container created by a non-Native (Connect/legacy free-structure) write path has no
  template at all -- an honest absence, never backfilled. ``ON DELETE RESTRICT`` -- no code
  anywhere deletes a ``LabResultTemplate`` row; this is defense-in-depth, not a response to a real
  deletion path.
- ``exchange_lab_observations`` gains ``template_observation_definition_id`` -- which controlled
  definition, within the container's own pinned template, this value answers. Same nullable/
  ``RESTRICT`` reasoning as above. A partial unique index on
  ``(lab_result_id, template_observation_definition_id)`` -- restricted to rows where the
  definition is set and the observation is still effective (``superseded_at IS NULL``) -- makes
  "at most one effective value per controlled definition, per container" a database-enforced fact,
  the backstop behind the native-value batch write's own ``LabOrder FOR UPDATE``-serialized
  replay/conflict discipline (mirroring ``supersedes_observation_id``'s own UNIQUE-constraint-as-
  backstop precedent from migration ``0017_observation_revisions``).

No changes to ``exchange_security_audit_events``: DH-LAB-004 reuses the existing
``lab_observation_recorded``/``lab_result_entry_completed``/``lab_results_released``/
``lab_order_completed`` event types unchanged -- no new event or resource type is introduced by
this ticket.

Revision ID: 0025_native_result_binding
Revises: 0024_result_templates
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_native_result_binding"
down_revision: str | Sequence[str] | None = "0024_result_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exchange_lab_results",
        sa.Column(
            "result_template_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_result_templates.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "exchange_lab_observations",
        sa.Column(
            "template_observation_definition_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_result_template_observations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_exchange_lab_observations_one_effective_per_definition",
        "exchange_lab_observations",
        ["lab_result_id", "template_observation_definition_id"],
        unique=True,
        postgresql_where=sa.text(
            "template_observation_definition_id IS NOT NULL AND superseded_at IS NULL"
        ),
        sqlite_where=sa.text(
            "template_observation_definition_id IS NOT NULL AND superseded_at IS NULL"
        ),
    )


class DowngradeBlockedByNativeResultBinding(RuntimeError):
    """0025 -> 0024 was refused because at least one row carries Native template-binding
    provenance (``exchange_lab_results.result_template_id`` or
    ``exchange_lab_observations.template_observation_definition_id`` is non-``NULL``), which the
    narrower 0024 schema has no column to represent. No schema change is made; the database
    remains at 0025_native_result_binding."""


def downgrade() -> None:
    bind = op.get_bind()

    bound_results = bind.execute(
        sa.text("SELECT COUNT(*) FROM exchange_lab_results WHERE result_template_id IS NOT NULL")
    ).scalar_one()
    bound_observations = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM exchange_lab_observations "
            "WHERE template_observation_definition_id IS NOT NULL"
        )
    ).scalar_one()
    if bound_results or bound_observations:
        raise DowngradeBlockedByNativeResultBinding(
            f"refusing to downgrade past 0025_native_result_binding: {bound_results} "
            f"exchange_lab_results row(s) and {bound_observations} exchange_lab_observations "
            f"row(s) carry Native template-binding provenance the narrower 0024 schema cannot "
            f"represent. No schema change has been made -- the database is unchanged and remains "
            f"at 0025_native_result_binding."
        )

    op.drop_index(
        "uq_exchange_lab_observations_one_effective_per_definition",
        table_name="exchange_lab_observations",
    )
    op.drop_column("exchange_lab_observations", "template_observation_definition_id")
    op.drop_column("exchange_lab_results", "result_template_id")
