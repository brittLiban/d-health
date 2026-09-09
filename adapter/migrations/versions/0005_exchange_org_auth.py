"""DH-EX-002 / DH-EX-002R: organization onboarding, identity, membership, role assignment, and
the transactional security-mutation audit table.

Adds four new tables: ``exchange_identities`` (OIDC issuer+subject, never a credential),
``exchange_organization_memberships`` (one identity's membership in one organization),
``exchange_organization_role_assignments`` (roles held by one membership), and
``exchange_security_audit_events`` (DH-EX-002R: the authoritative audit row for a security
mutation, always written in the same database transaction as the mutation it records -- see
``exchange/security_audit.py``). See ``adapter/src/daher_adapter/exchange/models.py`` for the ORM
definitions this migration must exactly match, and each table's own docstring there for the
reasoning. Folded into this still-uncommitted 0005 migration rather than a new 0006, per
DH-EX-002R's explicit instruction not to create artificial migration history for a still-unreviewed
ticket.

Purely additive: creates four new tables and touches nothing that already exists. No existing
row anywhere is read, mutated, or deleted by this migration.

Revision ID: 0005_exchange_org_auth
Revises: 0004_exchange_core
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_exchange_org_auth"
down_revision: str | Sequence[str] | None = "0004_exchange_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exchange_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_exchange_identities_issuer_subject"),
    )

    op.create_table(
        "exchange_organization_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_org_memberships_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["exchange_identities.id"],
            name="fk_exchange_org_memberships_identity",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')", name="ck_exchange_org_memberships_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "identity_id",
            name="uq_exchange_org_memberships_org_identity",
        ),
    )
    op.create_index(
        "ix_exchange_org_memberships_identity_id",
        "exchange_organization_memberships",
        ["identity_id"],
    )

    op.create_table(
        "exchange_organization_role_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["exchange_organization_memberships.id"],
            name="fk_exchange_org_role_assignments_membership",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "role IN ('ORG_ADMIN', 'CLINICIAN', 'LAB_TECH')",
            name="ck_exchange_org_role_assignments_role",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "membership_id", "role", name="uq_exchange_org_role_assignments_membership_role"
        ),
    )

    op.create_table(
        "exchange_security_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("actor_identity_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("subject_identity_id", sa.Uuid(), nullable=True),
        sa.Column("membership_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_identity_id"],
            ["exchange_identities.id"],
            name="fk_exchange_security_audit_events_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["exchange_organizations.id"],
            name="fk_exchange_security_audit_events_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_identity_id"],
            ["exchange_identities.id"],
            name="fk_exchange_security_audit_events_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["exchange_organization_memberships.id"],
            name="fk_exchange_security_audit_events_membership",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "event_type IN ('organization_created', 'membership_created', 'role_assigned')",
            name="ck_exchange_security_audit_events_event_type",
        ),
        sa.CheckConstraint(
            "outcome IN ('created', 'returned_existing')",
            name="ck_exchange_security_audit_events_outcome",
        ),
        sa.CheckConstraint(
            "role IS NULL OR role IN ('ORG_ADMIN', 'CLINICIAN', 'LAB_TECH')",
            name="ck_exchange_security_audit_events_role",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("exchange_security_audit_events")
    op.drop_table("exchange_organization_role_assignments")
    op.drop_index(
        "ix_exchange_org_memberships_identity_id",
        table_name="exchange_organization_memberships",
    )
    op.drop_table("exchange_organization_memberships")
    op.drop_table("exchange_identities")
