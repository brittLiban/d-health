"""DH-CONNECT-AGENT-001 (provider side): admitting contract 1.2's two outbound message types.

One CHECK constraint, rewritten. ``connector_deliveries.message_type`` enumerates the message types
a durable row may carry, and contract 1.2 adds ``RESULT_NOTIFICATION`` and
``AMENDMENT_NOTIFICATION`` -- DHealth telling a provider's own system that a report it ordered has
been released, and that one has been corrected.

**Why this needed its own migration rather than riding along with the model change.** The SQLite
test suite builds its schema from ``Base.metadata.create_all``, so updating the ``CheckConstraint``
in ``runtime_models.py`` alone makes the whole suite pass while every PostgreSQL deployment rejects
the first result notification it is asked to queue. That is the same class of divergence 0028's
over-long revision id belonged to: real only on the database nothing in CI runs. The
constraint is therefore stated twice on purpose -- once for the model, once here -- and
``tests/test_dhconnect001_postgres_qualification.py`` runs the real one.

The vocabulary is enumerated rather than opened up because the column is what a **replay** reads
back. A row carrying a type the applier cannot dispatch is durable, accepted, and undeliverable,
and nothing would notice until an operator went looking for a message that never arrived.

Downgrade restores the 1.1 vocabulary. It does **not** delete rows carrying the new types (§51):
a downgrade with provider notifications in flight will fail on the constraint rather than quietly
discard queued clinical messages, and failing loudly is the correct outcome.
``docs/CONNECT-AGENT.md`` records drain-before-downgrade as the supported sequence.

The revision id is **21 characters**, against ``alembic_version.version_num``'s ``VARCHAR(32)``.

Revision ID: 0030_connect_provider
Revises: 0029_connect_runtime
Create Date: 2026-09-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030_connect_provider"
down_revision: str | Sequence[str] | None = "0029_connect_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT = "ck_connector_deliveries_message_type"

_PRE_0030_MESSAGE_TYPES = (
    "'ORDER_SUBMISSION', 'ORDER_NOTIFICATION', 'TECHNICAL_ACK', "
    "'BUSINESS_ACCEPTANCE', 'BUSINESS_REJECTION', 'COLLECTION_EVENT', "
    "'RELEASED_RESULT', 'RELEASED_AMENDMENT'"
)

_MESSAGE_TYPES = f"{_PRE_0030_MESSAGE_TYPES}, 'RESULT_NOTIFICATION', 'AMENDMENT_NOTIFICATION'"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "connector_deliveries", type_="check")
    op.create_check_constraint(
        _CONSTRAINT, "connector_deliveries", f"message_type IN ({_MESSAGE_TYPES})"
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "connector_deliveries", type_="check")
    op.create_check_constraint(
        _CONSTRAINT, "connector_deliveries", f"message_type IN ({_PRE_0030_MESSAGE_TYPES})"
    )
