"""DHEALTH-BILLING-001: facility billing -- services, prices, invoices, lines, payments.

Six new tables, one new organization role value, and four new audit event types. Nothing existing is
narrowed, retyped or backfilled, and no clinical table is touched at all -- which is itself part of
the design: billing observes clinical state and never alters it (ADR-066).

── The role ──

``BILLING_CLERK`` joins ``ck_exchange_org_role_assignments_role`` and the audit table's own ``role``
CHECK. It is valid at **both** organization types, unlike every other non-admin role, because a
laboratory bills for the tests it performed and a clinic for the consultation it performed. The
type-validity matrix itself stays in ``exchange/membership.py`` for the reason ``OrganizationRole``
has always documented: SQL cannot portably CHECK a value against a column on a joined-through table.

It is additive and never implied -- ``ORG_ADMIN`` does not confer it. That is a service-layer
guarantee, not a schema one, and it is tested rather than assumed.

── Money ──

``Numeric(12, 2)`` everywhere, never a float. Binary floating point cannot represent 0.1, so sums
drift and a balance that should be exactly zero is not; this is the same type
``exchange_lab_prices`` already uses, so the repository has one money representation rather than
two.

Currency is a three-character code with a ``length(currency) = 3`` CHECK and a DJF default. The
pilot is Djibouti and the column does not assume it stays that way, but **nothing here implements
multi-currency behaviour** -- no conversion, no rates. One invoice, one currency, enforced by a
composite foreign key rather than by hope: ``exchange_invoice_lines.(invoice_id, currency)`` targets
``uq_exchange_invoices_id_currency``, so a line whose currency disagrees with its invoice cannot be
inserted at all.

── The polymorphic source, done safely ──

``exchange_invoice_lines`` does **not** carry an untyped ``source_id``. It carries two nullable
typed foreign keys -- ``clinic_service_id`` and ``lab_order_item_id`` -- and a pair of CHECK
constraints making exactly one of them non-null, matching ``source_type``. The database therefore
knows a ``CLINIC_SERVICE`` line points at a real service row and a ``LAB_ORDER_ITEM`` line at a real
order item, instead of a dangling UUID whose meaning depends on a string column nobody constrained.

── Tenant boundaries the database enforces ──

``exchange_payments.(invoice_id, organization_id)`` targets ``uq_exchange_invoices_id_org``, so a
payment can never be attached to another organization's invoice. ``organization_id`` is denormalised
onto the payment **solely** so that constraint can exist.

``exchange_invoices.patient_id`` is a plain foreign key and deliberately **not** a composite tenant
key, unlike ``exchange_visits``. ``Patient`` is owned by the provider that registered them, so a
laboratory's invoice legitimately names a patient row another organization owns. What proves the
laboratory may bill that person is not ownership of their directory record but the fact that it
performed the work -- carried by the line's source and checked in ``exchange/billing.py``. (Whether
a laboratory may hold its own patient records is **D-6**, still open.)

── Invoice numbers ──

``exchange_invoice_number_sequences`` is one counter row per organization per year, taken ``FOR
UPDATE`` inside the invoice-creation transaction. ``count(*) + 1`` is the classic double-issue bug:
two cashiers invoicing simultaneously both count *n* and both write *n+1*. The unique constraint on
``(organization_id, number)`` is the second layer behind the lock, not the first.

Numbers restart annually, which is what a facility expects of a paper book. **This is not a legal
numbering scheme** -- gapless fiscal sequences and mandated prefixes are a jurisdictional policy
question nobody has answered here, recorded in ADR-066 rather than invented.

── What is deliberately absent ──

No ``status`` column on the invoice and no ``balance`` column anywhere. Both are pure functions of
the total and the recorded payments, and a stored copy is a second source of truth that drifts --
§18's Law 8: *balance is derived, never stored.* No ``voided_at`` either: voiding is a policy
question the operating-product specification left undecided, so this ticket does not invent it, and
there is consequently no delete path for an invoice or a payment. Financial history is permanent.

Downgrade narrows the audit constraints back without deleting the rows that would then violate them
-- 0032/0033/0034's established reasoning: an audit row records that something happened, and a
schema downgrade is not a reason to erase it. It then drops the six tables, which destroys financial
records; that is stated here rather than discovered, because no reversal of a table creation can do
otherwise. The ``BILLING_CLERK`` role value is also narrowed out, so a deployment holding such a
grant must remove it first and will be told by a constraint violation.

The revision id is **22 characters**, against ``alembic_version.version_num``'s ``VARCHAR(32)`` --
the boundary DH-CONNECT-CONTRACT-001 found the hard way (see ``tests/test_migration_hygiene.py``).

Revision ID: 0035_facility_billing
Revises: 0034_clinic_visits
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_facility_billing"
down_revision: str | Sequence[str] | None = "0034_clinic_visits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled out in full rather than assembled from the ORM, because a migration must describe the
# database at *this* revision -- reading today's model would make an old migration silently change
# meaning the next time a value is added. Identical discipline to 0029/0032/0033/0034.
_PRE_0035_EVENT_TYPES = (
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
    "'visit_note_appended', 'visit_reason_amended'"
)

# Four, one per financial mutation a clerk can perform. No amount is carried -- the authoritative
# figure lives on the immutable Invoice/Payment row, and a second copy could diverge from it. No
# void or reversal event: neither operation is built, because neither policy is decided.
_0035_NEW_EVENT_TYPES = (
    "'clinic_service_created', 'clinic_service_price_changed', 'invoice_created', "
    "'payment_recorded'"
)

_PRE_0035_RESOURCE_TYPES = (
    "'catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
    "'patient_access_grant', 'patient_reference', 'patient', 'provider_lab_route', "
    "'lab_result_template', 'facility_enrollment', "
    "'facility_interface_capability', 'facility_enrollment_blocker', "
    "'connector_installation', 'patient_notification', 'visit'"
)

# Payments hang off an invoice, so audit stays invoice-centric -- the convention every block in this
# chain has followed since 0029.
_0035_NEW_RESOURCE_TYPES = "'invoice', 'clinic_service'"

_PRE_0035_ROLES = "'ORG_ADMIN', 'CLINICIAN', 'LAB_TECH', 'LAB_RESULT_RELEASER'"
_0035_NEW_ROLE = "'BILLING_CLERK'"

_EVENT_TYPE_CHECK = "ck_exchange_security_audit_events_event_type"
_RESOURCE_TYPE_CHECK = "ck_exchange_security_audit_events_resource_type"
_AUDIT_ROLE_CHECK = "ck_exchange_security_audit_events_role"
_ASSIGNMENT_ROLE_CHECK = "ck_exchange_org_role_assignments_role"


def upgrade() -> None:
    # -- the facility's own services and their price history ------------------------------------
    op.create_table(
        "exchange_clinic_services",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # Targeted by `exchange_invoice_lines`' own composite key in a later ticket if line-level
        # tenant proof is ever wanted at the database layer; today it also simply prevents one
        # facility listing the same service twice.
        sa.UniqueConstraint("id", "organization_id", name="uq_exchange_clinic_services_id_org"),
        sa.UniqueConstraint("organization_id", "name", name="uq_exchange_clinic_services_org_name"),
    )
    op.create_index(
        "ix_exchange_clinic_services_organization_id",
        "exchange_clinic_services",
        ["organization_id"],
    )

    op.create_table(
        "exchange_clinic_service_prices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clinic_service_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_clinic_services.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "amount >= 0", name="ck_exchange_clinic_service_prices_amount_non_negative"
        ),
        sa.CheckConstraint(
            "length(currency) = 3", name="ck_exchange_clinic_service_prices_currency_shape"
        ),
    )
    # "Exactly one current price per service", enforced by the database rather than by application
    # logic -- the identical partial-index shape `exchange_lab_prices` already uses, for the
    # identical reason: two live prices for one service would leave nothing saying which is charged.
    op.create_index(
        "uq_exchange_clinic_service_prices_current",
        "exchange_clinic_service_prices",
        ["clinic_service_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        sqlite_where=sa.text("superseded_at IS NULL"),
    )

    # -- invoice numbering ----------------------------------------------------------------------
    op.create_table(
        "exchange_invoice_number_sequences",
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column("next_value", sa.Integer(), nullable=False),
        sa.CheckConstraint("next_value >= 1", name="ck_exchange_invoice_number_seq_positive"),
    )

    # -- invoices, lines, payments ----------------------------------------------------------------
    op.create_table(
        "exchange_invoices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Plain, not composite -- see this migration's docstring for why a laboratory legitimately
        # invoices a patient row another organization owns.
        sa.Column(
            "patient_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_patients.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("number", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by_identity_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "number", name="uq_exchange_invoices_org_number"),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_exchange_invoices_org_idempotency_key"
        ),
        # The two composite keys other tables target, so tenant and currency integrity are the
        # database's job rather than the application's.
        sa.UniqueConstraint("id", "organization_id", name="uq_exchange_invoices_id_org"),
        sa.UniqueConstraint("id", "currency", name="uq_exchange_invoices_id_currency"),
        sa.CheckConstraint("length(currency) = 3", name="ck_exchange_invoices_currency_shape"),
    )
    op.create_index(
        "ix_exchange_invoices_organization_id", "exchange_invoices", ["organization_id"]
    )
    op.create_index(
        "ix_exchange_invoices_org_patient",
        "exchange_invoices",
        ["organization_id", "patient_id"],
    )

    op.create_table(
        "exchange_invoice_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column(
            "clinic_service_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_clinic_services.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "lab_order_item_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_lab_order_items.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("description_snapshot", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # One invoice, one currency -- unrepresentable otherwise.
        sa.ForeignKeyConstraint(
            ["invoice_id", "currency"],
            ["exchange_invoices.id", "exchange_invoices.currency"],
            name="fk_exchange_invoice_lines_currency_matches_invoice",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_exchange_invoice_lines_quantity_positive"),
        sa.CheckConstraint(
            "unit_amount >= 0", name="ck_exchange_invoice_lines_unit_amount_non_negative"
        ),
        sa.CheckConstraint(
            "source_type IN ('CLINIC_SERVICE', 'LAB_ORDER_ITEM')",
            name="ck_exchange_invoice_lines_source_type",
        ),
        # Exactly one typed source, and it must be the one `source_type` names.
        sa.CheckConstraint(
            "(source_type = 'CLINIC_SERVICE') = (clinic_service_id IS NOT NULL)",
            name="ck_exchange_invoice_lines_clinic_source_matches",
        ),
        sa.CheckConstraint(
            "(source_type = 'LAB_ORDER_ITEM') = (lab_order_item_id IS NOT NULL)",
            name="ck_exchange_invoice_lines_lab_source_matches",
        ),
    )
    op.create_index(
        "ix_exchange_invoice_lines_invoice_id", "exchange_invoice_lines", ["invoice_id"]
    )

    op.create_table(
        "exchange_payments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column(
            "recorded_by_identity_id",
            sa.Uuid(),
            sa.ForeignKey("exchange_identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        # The database's own proof that a payment cannot be attached to another organization's
        # invoice. This is why `organization_id` is denormalised onto the row at all.
        sa.ForeignKeyConstraint(
            ["invoice_id", "organization_id"],
            ["exchange_invoices.id", "exchange_invoices.organization_id"],
            name="fk_exchange_payments_invoice_same_org",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "invoice_id", "sequence_number", name="uq_exchange_payments_invoice_sequence"
        ),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_exchange_payments_org_idempotency_key"
        ),
        sa.CheckConstraint("amount > 0", name="ck_exchange_payments_amount_positive"),
        sa.CheckConstraint("sequence_number >= 1", name="ck_exchange_payments_sequence_positive"),
        sa.CheckConstraint(
            "method IN ('CASH', 'MOBILE_MONEY', 'CARD', 'BANK_TRANSFER')",
            name="ck_exchange_payments_method",
        ),
    )
    op.create_index("ix_exchange_payments_invoice_id", "exchange_payments", ["invoice_id"])

    # -- the new role, and the audit vocabulary ---------------------------------------------------
    op.drop_constraint(
        _ASSIGNMENT_ROLE_CHECK, "exchange_organization_role_assignments", type_="check"
    )
    op.create_check_constraint(
        _ASSIGNMENT_ROLE_CHECK,
        "exchange_organization_role_assignments",
        f"role IN ({_PRE_0035_ROLES}, {_0035_NEW_ROLE})",
    )
    op.drop_constraint(_AUDIT_ROLE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _AUDIT_ROLE_CHECK,
        "exchange_security_audit_events",
        f"role IS NULL OR role IN ({_PRE_0035_ROLES}, {_0035_NEW_ROLE})",
    )
    op.drop_constraint(_EVENT_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _EVENT_TYPE_CHECK,
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0035_EVENT_TYPES}, {_0035_NEW_EVENT_TYPES})",
    )
    op.drop_constraint(_RESOURCE_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _RESOURCE_TYPE_CHECK,
        "exchange_security_audit_events",
        "resource_type IS NULL OR resource_type IN "
        f"({_PRE_0035_RESOURCE_TYPES}, {_0035_NEW_RESOURCE_TYPES})",
    )


def downgrade() -> None:
    # The constraints narrow back first, and deliberately without deleting the rows that would now
    # violate them -- 0032's reasoning, unchanged: an audit row is a record that something happened,
    # and a schema downgrade is not a reason to erase it. A deployment carrying billing audit rows
    # or a live BILLING_CLERK grant must deal with them deliberately, and a constraint violation is
    # the correct way to be told.
    op.drop_constraint(_RESOURCE_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _RESOURCE_TYPE_CHECK,
        "exchange_security_audit_events",
        f"resource_type IS NULL OR resource_type IN ({_PRE_0035_RESOURCE_TYPES})",
    )
    op.drop_constraint(_EVENT_TYPE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _EVENT_TYPE_CHECK,
        "exchange_security_audit_events",
        f"event_type IN ({_PRE_0035_EVENT_TYPES})",
    )
    op.drop_constraint(_AUDIT_ROLE_CHECK, "exchange_security_audit_events", type_="check")
    op.create_check_constraint(
        _AUDIT_ROLE_CHECK,
        "exchange_security_audit_events",
        f"role IS NULL OR role IN ({_PRE_0035_ROLES})",
    )
    op.drop_constraint(
        _ASSIGNMENT_ROLE_CHECK, "exchange_organization_role_assignments", type_="check"
    )
    op.create_check_constraint(
        _ASSIGNMENT_ROLE_CHECK,
        "exchange_organization_role_assignments",
        f"role IN ({_PRE_0035_ROLES})",
    )

    # Children before parents: payments and lines reference invoices, invoices reference services,
    # and every one of those foreign keys is RESTRICT.
    op.drop_index("ix_exchange_payments_invoice_id", table_name="exchange_payments")
    op.drop_table("exchange_payments")
    op.drop_index("ix_exchange_invoice_lines_invoice_id", table_name="exchange_invoice_lines")
    op.drop_table("exchange_invoice_lines")
    op.drop_index("ix_exchange_invoices_org_patient", table_name="exchange_invoices")
    op.drop_index("ix_exchange_invoices_organization_id", table_name="exchange_invoices")
    op.drop_table("exchange_invoices")
    op.drop_table("exchange_invoice_number_sequences")
    op.drop_index(
        "uq_exchange_clinic_service_prices_current", table_name="exchange_clinic_service_prices"
    )
    op.drop_table("exchange_clinic_service_prices")
    op.drop_index(
        "ix_exchange_clinic_services_organization_id", table_name="exchange_clinic_services"
    )
    op.drop_table("exchange_clinic_services")
