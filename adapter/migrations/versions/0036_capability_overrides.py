"""DHEALTH-ACCESS-CAPABILITIES-001/ADR-067: member-specific capability overrides.

── What this migration does, and the short list of what it does not ──

It adds **one table** and **one column**, and it migrates **no data at all**. That is the design,
not an omission.

Existing authority is carried entirely by `exchange_organization_role_assignments`, which this
ticket re-reads as the *role-template assignment* table rather than replacing: a role **is** a
system template now, and `exchange/capabilities.py`'s `SYSTEM_ROLE_TEMPLATES` says which
capabilities each one carries. So every membership keeps exactly the authority it had, because the
rows that grant it are untouched and the templates reproduce the five old role gates capability for
capability. §18 calls authority drift a P0; the safest way to cause none is to move no rows, and
`tests/test_dhealthaccesscap001_authority_matrix.py` pins all 160 cells of the before/after matrix
to prove it.

**`exchange_membership_capability_overrides`** is the new table: one row per (membership,
capability), `GRANT` or `DENY`, with `DENY` winning. It is the whole of "configurable permissions"
in this release, and it is what lets two people with the same job title have different access
without inventing a third job title — the combinatorial role explosion §62 forbids by name.

**`exchange_security_audit_events.capability`** is the new column: which authority a capability
event was about. Nullable, because every pre-existing event type leaves it empty.

── What is deliberately absent ──

*No `role_templates` table.* The template→capability composition is code-defined and immutable in
this ticket (§24 permits deferring editable templates): a facility tailors a person through member
overrides, which cannot leave an organization without an administrator by surprise the way editing
a shared template can. Custom organization templates are recorded as follow-up in ADR-067 rather
than half-built here. The moment they exist, this is the migration that grows a table.

*No CHECK enumerating the capability catalog.* The catalog is code-defined; a CHECK would make
adding one capability a migration for every deployment, and the enforcement that matters happens at
the write path, where an unknown key is refused before it can be stored.

*No drop of anything.* `exchange_organization_role_assignments` and its role CHECK constraint stay
exactly as they are. There is no dual authority to reconcile (§42) because there is only one
resolver reading them.

── Downgrade ──

Drops the table and the column. An organization that had configured member overrides loses those
exceptions and its members fall back to their role templates — a **widening** for a denied member
and a **narrowing** for a directly granted one, which is why the downgrade is not silent about it
in the ticket's own documentation. Nothing else is touched, so no role assignment and no membership
is affected either way.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_capability_overrides"
down_revision: str | Sequence[str] | None = "0035_facility_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exchange_membership_capability_overrides",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("effect", sa.String(length=8), nullable=False),
        sa.Column("created_by_identity_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["exchange_organization_memberships.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"],
            ["exchange_identities.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "membership_id",
            "capability",
            name="uq_exchange_membership_capability_overrides_member_cap",
        ),
        sa.CheckConstraint(
            "effect IN ('GRANT', 'DENY')",
            name="ck_exchange_membership_capability_overrides_effect",
        ),
    )
    # The last-administrator invariant counts effective holders of `staff.access.manage` with a
    # `NOT EXISTS` over this table, inside the mutation's transaction. Without an index that is a
    # sequential scan per candidate member on every staff-access mutation.
    op.create_index(
        "ix_exchange_membership_capability_overrides_membership",
        "exchange_membership_capability_overrides",
        ["membership_id"],
    )

    op.add_column(
        "exchange_security_audit_events",
        sa.Column("capability", sa.String(length=64), nullable=True),
    )

    # The three new capability event types, and the widened role/resource sets they travel with.
    # Same discipline as 0035: drop the CHECK, recreate it with the additions, never edit in place.
    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_EVENT_TYPES_0036})",
    )


def downgrade() -> None:
    # The constraint narrows first, and deliberately **without** deleting the rows that would now
    # violate it -- 0032's and 0035's reasoning, unchanged: an audit row is a record that something
    # happened, and a schema downgrade is not a reason to erase it. A deployment carrying capability
    # audit rows is told by a constraint violation rather than losing its access history.
    op.drop_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exchange_security_audit_events_event_type",
        "exchange_security_audit_events",
        f"event_type IN ({_EVENT_TYPES_PRE_0036})",
    )

    op.drop_column("exchange_security_audit_events", "capability")
    op.drop_index(
        "ix_exchange_membership_capability_overrides_membership",
        table_name="exchange_membership_capability_overrides",
    )
    op.drop_table("exchange_membership_capability_overrides")


#: The event-type vocabulary as 0035 left it. Spelled out rather than read from the model, so this
#: migration keeps saying what it meant even after the model moves on -- the rule every widening
#: migration in this directory follows.
_EVENT_TYPES_PRE_0036 = (
    "'organization_created', 'membership_created', 'role_assigned', "
    "'catalog_entry_created', 'lab_directory_profile_created', "
    "'lab_directory_profile_updated', 'lab_offering_created', "
    "'lab_offering_status_changed', 'lab_price_changed', "
    "'lab_order_created', 'patient_access_issued', 'patient_access_revoked', "
    "'lab_order_accepted', 'lab_order_rejected', 'lab_order_processing_started', "
    "'lab_result_recorded', 'lab_order_completed', 'doctor_review_recorded', "
    "'patient_reference_bound', 'lab_order_assigned', 'lab_sample_collection_recorded', "
    "'lab_results_released', 'lab_observation_recorded', 'lab_result_entry_completed', "
    "'lab_observation_corrected', 'lab_result_amendment_started', "
    "'lab_observation_amended', 'lab_result_amendment_released', "
    "'patient_created', "
    "'provider_lab_route_created', 'provider_lab_route_updated', "
    "'lab_result_template_created', 'lab_result_template_activated', "
    "'facility_enrollment_created', 'facility_participation_mode_changed', "
    "'facility_onboarding_status_changed', 'facility_technical_discovery_updated', "
    "'facility_interface_capability_recorded', 'facility_onboarding_blocked', "
    "'facility_onboarding_unblocked', 'provider_lab_route_activation_refused', "
    "'connector_registered', 'connector_suspended', 'connector_restored', "
    "'connector_retired', 'connector_configuration_changed', "
    "'connector_qualification_recorded', "
    "'connector_service_identity_set', 'connector_runtime_connected', "
    "'connector_message_refused', "
    "'patient_notification_requested', 'patient_notification_retry_requested', "
    "'membership_deactivated', 'membership_reactivated', 'role_removed', "
    "'visit_created', 'visit_started', 'visit_completed', 'visit_cancelled', "
    "'visit_note_appended', 'visit_reason_amended', "
    "'clinic_service_created', 'clinic_service_price_changed', 'invoice_created', "
    "'payment_recorded'"
)

_EVENT_TYPES_0036 = (
    _EVENT_TYPES_PRE_0036
    + ", 'capability_granted', 'capability_denied', 'capability_override_cleared'"
)
