"""DH-CONNECT-AGENT-001 correction: an external identifier outlives the connector that reported it.

Two related changes, both about lifetime.

**1. ``connector_correlations`` is rescoped to the external namespace.** Its identity was
``(connector_installation_id, correlation_kind, external_id)``, which tied a partner's own order
number to one DHealth adapter build. Those lifetimes are not the same: an installation is retired
and replaced whenever a version or a fingerprint moves, while ``ORDER-123`` goes on meaning what the
partner's system has always meant by it. Under the old key, a routine connector upgrade made every
existing correlation unresolvable -- so a result released afterwards could not be filed back into
the EMR that ordered it, through no act of the partner's.

Production scope becomes ``(organization_id, source_system, correlation_kind, external_id)``. The
installation stays as ``created_by_connector_installation_id``: provenance, part of no key.

The backfill joins each row to its originating installation for the organization. It **fails
closed**
-- if any row cannot be resolved, or if rescoping would collapse two distinct rows into one, the
migration raises rather than guessing which binding to keep. There is no production Connect data
anywhere yet, which is exactly why this is the right moment to move the key.

**2. ``connector_delivery_obligations`` is new.** Before it, a released report whose notification
could not be built produced a log line and nothing else. A log line is not a state: after a restart
nothing could answer "which released results were never queued, why, and can they be recovered".
The table makes the invariant true -- a professional release for a correlated CONNECT provider
leaves behind either a durable delivery or a durable record that one is owed.

It stores **no clinical content**. It names the ``LabResultReleaseVersion`` whose manifest is
already
immutable, and reconciliation rebuilds the message from that. ``failure_detail`` is the shape of a
fault -- field names and error kinds -- never a value, because a validation error's own text quotes
the input that failed and for an observation that input is patient data (§57).

Downgrade drops the obligations table and restores the old correlation key. It is lossy in one
direction that matters: obligations recorded but not yet queued are delivery work DHealth owes, and
dropping the table discards the record of it. Drain before downgrading, as ``docs/CONNECT-AGENT.md``
already says for queued messages.

The revision id is **22 characters**, against ``alembic_version.version_num``'s ``VARCHAR(32)``.

Revision ID: 0031_connect_namespace
Revises: 0030_connect_provider
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_connect_namespace"
down_revision: str | Sequence[str] | None = "0030_connect_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fail_closed_on_unbackfillable(connection) -> None:
    """Refuse to continue if any correlation cannot be given a namespace, or if rescoping collides.

    Two separate checks because they are two separate faults. An unresolved organization means a row
    whose installation vanished, which should be impossible under a RESTRICT foreign key and is
    therefore a sign something else is wrong. A collision means two rows that were distinct
    under the installation key and are the same binding under the namespace key -- which can only
    happen if one
    facility ran two installations for one source system that disagreed about what an identifier
    meant, and picking a winner would move a clinical result.
    """
    unresolved = connection.execute(
        sa.text("SELECT COUNT(*) FROM connector_correlations WHERE organization_id IS NULL")
    ).scalar_one()
    if unresolved:
        raise RuntimeError(
            f"{unresolved} connector_correlations rows could not be given an organization from "
            "their installation; refusing to rescope on a guess"
        )

    collisions = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "  SELECT organization_id, source_system, correlation_kind, external_id"
            "  FROM connector_correlations"
            "  GROUP BY organization_id, source_system, correlation_kind, external_id"
            "  HAVING COUNT(DISTINCT lab_order_id) > 1"
            ") AS clashes"
        )
    ).scalar_one()
    if collisions:
        raise RuntimeError(
            f"{collisions} external identifiers would bind to more than one DHealth order under "
            "the namespace key; refusing to choose which binding survives"
        )


def upgrade() -> None:
    connection = op.get_bind()

    op.add_column(
        "connector_correlations",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
    )
    connection.execute(
        sa.text(
            "UPDATE connector_correlations SET organization_id = ("
            "  SELECT ci.organization_id FROM connector_installations ci"
            "  WHERE ci.id = connector_correlations.connector_installation_id"
            ")"
        )
    )
    _fail_closed_on_unbackfillable(connection)

    # SQLite cannot ALTER a column to NOT NULL, drop a constraint, or rename a column in place, so
    # every structural step runs inside batch_alter_table -- which is a no-op wrapper on PostgreSQL
    # and a table rebuild on SQLite. The suite builds its schema from metadata rather than from
    # migrations, so this path is exercised only by the real-PostgreSQL run and by an operator.
    with op.batch_alter_table("connector_correlations") as batch:
        batch.alter_column("organization_id", existing_type=sa.Uuid(), nullable=False)
        batch.alter_column(
            "connector_installation_id",
            new_column_name="created_by_connector_installation_id",
            existing_type=sa.Uuid(),
            existing_nullable=False,
        )

    op.drop_constraint(
        "uq_connector_correlations_external", "connector_correlations", type_="unique"
    )
    op.drop_index("uq_connector_correlations_one_order", table_name="connector_correlations")
    op.create_unique_constraint(
        "uq_connector_correlations_external",
        "connector_correlations",
        ["organization_id", "source_system", "correlation_kind", "external_id"],
    )
    op.create_index(
        "uq_connector_correlations_one_order",
        "connector_correlations",
        ["organization_id", "source_system", "lab_order_id"],
        unique=True,
        postgresql_where=sa.text("correlation_kind = 'ORDER'"),
        sqlite_where=sa.text("correlation_kind = 'ORDER'"),
    )
    # The foreign key on the renamed column still carries PostgreSQL's auto-generated name from
    # 0030, which now names a column that no longer exists -- the sort of thing that costs somebody
    # ten minutes during an incident. Renamed only if that exact name is there, so a deployment
    # whose constraint was named differently is left alone rather than failed. On SQLite the batch
    # rebuild above already discarded the old name, hence the dialect guard.
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "DO $$ BEGIN "
                "IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = "
                "'connector_correlations_connector_installation_id_fkey') THEN "
                "ALTER TABLE connector_correlations RENAME CONSTRAINT "
                "connector_correlations_connector_installation_id_fkey TO "
                "fk_connector_correlations_created_by_installation; "
                "END IF; END $$;"
            )
        )

    op.create_foreign_key(
        "fk_connector_correlations_organization",
        "connector_correlations",
        "exchange_organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "connector_delivery_obligations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column(
            "lab_order_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "release_version_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_release_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("intended_message_type", sa.String(length=32), nullable=False),
        sa.Column("source_event_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column(
            "connector_delivery_id",
            sa.Uuid(),
            sa.ForeignKey("connector_deliveries.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("failure_category", sa.String(length=32), nullable=True),
        sa.Column("failure_detail", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "release_version_id", name="uq_connector_delivery_obligations_release_version"
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'QUEUED', 'FAILED')",
            name="ck_connector_delivery_obligations_state",
        ),
        sa.CheckConstraint(
            "intended_message_type IN ('RESULT_NOTIFICATION', 'AMENDMENT_NOTIFICATION')",
            name="ck_connector_delivery_obligations_message_type",
        ),
    )
    op.create_index(
        "ix_connector_delivery_obligations_unresolved",
        "connector_delivery_obligations",
        ["state", "created_at"],
        postgresql_where=sa.text("state <> 'QUEUED'"),
    )
    op.create_index(
        "ix_connector_delivery_obligations_order",
        "connector_delivery_obligations",
        ["lab_order_id"],
    )
    op.create_index(
        "ix_connector_delivery_obligations_org",
        "connector_delivery_obligations",
        ["organization_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_connector_delivery_obligations_org", table_name="connector_delivery_obligations"
    )
    op.drop_index(
        "ix_connector_delivery_obligations_order", table_name="connector_delivery_obligations"
    )
    op.drop_index(
        "ix_connector_delivery_obligations_unresolved", table_name="connector_delivery_obligations"
    )
    op.drop_table("connector_delivery_obligations")

    op.drop_constraint(
        "fk_connector_correlations_organization", "connector_correlations", type_="foreignkey"
    )
    op.drop_index("uq_connector_correlations_one_order", table_name="connector_correlations")
    op.drop_constraint(
        "uq_connector_correlations_external", "connector_correlations", type_="unique"
    )
    with op.batch_alter_table("connector_correlations") as batch:
        batch.alter_column(
            "created_by_connector_installation_id",
            new_column_name="connector_installation_id",
            existing_type=sa.Uuid(),
            existing_nullable=False,
        )
    if op.get_bind().dialect.name == "postgresql":
        op.get_bind().execute(
            sa.text(
                "DO $$ BEGIN "
                "IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = "
                "'fk_connector_correlations_created_by_installation') THEN "
                "ALTER TABLE connector_correlations RENAME CONSTRAINT "
                "fk_connector_correlations_created_by_installation TO "
                "connector_correlations_connector_installation_id_fkey; "
                "END IF; END $$;"
            )
        )

    op.create_unique_constraint(
        "uq_connector_correlations_external",
        "connector_correlations",
        ["connector_installation_id", "correlation_kind", "external_id"],
    )
    op.create_index(
        "uq_connector_correlations_one_order",
        "connector_correlations",
        ["connector_installation_id", "lab_order_id"],
        unique=True,
        postgresql_where=sa.text("correlation_kind = 'ORDER'"),
        sqlite_where=sa.text("correlation_kind = 'ORDER'"),
    )
    op.drop_column("connector_correlations", "organization_id")
