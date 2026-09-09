"""DH-EX-001 ORM models for the DHealth Exchange core.

Deliberately transaction-scoped, not a longitudinal medical record (DH-PIVOT-001's approved data
boundary): there is no general ``Patient`` table modeled like an EMR, no free-text clinical notes,
no medication list, no diagnosis history. ``PatientReference``/``PractitionerReference`` exist to
support one exchange transaction, holding only what the golden journey needs plus an explicit
source-identity binding -- never a fuzzy or demographic match (DH-PIVOT-001's patient/practitioner
identity model).

Shares ``Base`` with ``daher_adapter.models`` (imported, not redefined) so a single Alembic chain
and a single ``Base.metadata.create_all()`` test fixture cover both the connector-specific tables
and this Exchange core -- see this package's ``__init__.py`` for why that is safe (no import of
OpenMRS/OpenELIS-specific modules from here).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from daher_adapter.models import Base, utc_now

__all__ = [
    "DoctorReview",
    "DoctorReviewStatus",
    "LabOffering",
    "LabOrder",
    "LabOrderItem",
    "LabOrderStatus",
    "LabPrice",
    "LabResult",
    "LabResultType",
    "LabSelection",
    "Organization",
    "OrganizationStatus",
    "OrganizationType",
    "PatientReference",
    "PractitionerReference",
    "ResultDelivery",
    "ResultDeliveryStatus",
    "TestCatalogEntry",
]


class OrganizationType(StrEnum):
    PROVIDER = "PROVIDER"
    LAB = "LAB"


class OrganizationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class LabOrderStatus(StrEnum):
    """DH-PIVOT-001's approved minimal status machine. Deliberately excludes a physical
    specimen-collection state -- CURRENT_STATE.md's DH-011J2 finding (no reliable native signal
    for specimen collection exists even in the OpenMRS/OpenELIS connector) generalizes here: this
    Exchange core must never infer a physical-world event from a database timestamp. If a future
    lab workflow needs to record collection, it must be its own explicit lab action/event, not a
    status silently reached by this enum.
    """

    AWAITING_LAB_SELECTION = "AWAITING_LAB_SELECTION"
    SELECTED = "SELECTED"
    ACCEPTED = "ACCEPTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ResultDeliveryStatus(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class DoctorReviewStatus(StrEnum):
    PENDING = "PENDING"
    NO_FOLLOW_UP_REQUIRED = "NO_FOLLOW_UP_REQUIRED"
    FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED"


class LabResultType(StrEnum):
    NUMERIC = "NUMERIC"
    TEXT = "TEXT"


class Organization(Base):
    """A provider org or a lab org. Deliberately thin for DH-EX-001 -- no onboarding/auth fields
    (users, credentials, PORTAL-vs-INTEGRATION mode) belong to a later ticket, per DH-EX-001's own
    scope boundary ("Do NOT model full onboarding/auth yet")."""

    __tablename__ = "exchange_organizations"
    __table_args__ = (
        CheckConstraint(
            "org_type IN ('PROVIDER', 'LAB')", name="ck_exchange_organizations_org_type"
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')", name="ck_exchange_organizations_status"
        ),
        Index("ix_exchange_organizations_org_type", "org_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OrganizationStatus.ACTIVE.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PatientReference(Base):
    """A patient reference scoped to this exchange, never a national/longitudinal identity.

    ``(source_organization_id, source_system, source_identity_system, source_identity_value)`` is
    unique -- **not** just ``(source_identity_system, source_identity_value)`` alone. DH-EX-001R
    review found the narrower key would silently merge two different organizations' patients
    whenever they happen to reuse the same identity namespace/value (e.g. two independent
    Provider Portal accounts both minting sequential local patient ids starting at 1) into one
    ``PatientReference`` row -- a real cross-organization identity collision, not a hypothetical
    one, proven by test (``test_no_source_identity_collision_across_organizations``). Binding
    identity to the organization that asserted it, not just to the namespace string, is what
    ``openelis-config/patches/order-correlation/DaherPatientIdentity.java``'s pattern always
    implicitly relied on too -- that table only ever saw one facility's one OpenMRS instance, so
    the narrower key was never actually exercised across two independent organizations there. No
    fuzzy/demographic matching is implemented anywhere in this model, in either version of the key.
    """

    __tablename__ = "exchange_patient_references"
    __table_args__ = (
        UniqueConstraint(
            "source_organization_id",
            "source_system",
            "source_identity_system",
            "source_identity_value",
            name="uq_exchange_patient_references_source_identity",
        ),
        Index("ix_exchange_patient_references_source_organization_id", "source_organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_identity_system: Mapped[str] = mapped_column(String(256), nullable=False)
    source_identity_value: Mapped[str] = mapped_column(String(256), nullable=False)
    # Minimal snapshot only -- not an EMR demographic record (DH-EX-001's data-minimization rule).
    display_name: Mapped[str | None] = mapped_column(String(256))
    contact_channel_type: Mapped[str | None] = mapped_column(String(32))
    contact_value: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PractitionerReference(Base):
    """Same shape, same no-fuzzy-matching rule, and the same organization-scoped uniqueness fix as
    ``PatientReference`` (see its docstring) -- DH-PIVOT-001's finding that ADR-037's
    OpenMRS-Provider-UUID design is already this exact pattern, just connector-scoped; this table
    generalizes it rather than replacing it."""

    __tablename__ = "exchange_practitioner_references"
    __table_args__ = (
        UniqueConstraint(
            "source_organization_id",
            "source_system",
            "source_identity_system",
            "source_identity_value",
            name="uq_exchange_practitioner_references_source_identity",
        ),
        Index(
            "ix_exchange_practitioner_references_source_organization_id", "source_organization_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_identity_system: Mapped[str] = mapped_column(String(256), nullable=False)
    source_identity_value: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class TestCatalogEntry(Base):
    """DHealth-owned, versioned test identity -- deliberately NOT a mapping onto any real
    OpenMRS/OpenELIS concept (DH-011I found zero exact LOINC matches; that remains a separate,
    clinically-gated connector concern). ``is_synthetic`` defaults true because DH-EX-001 ships
    demo/synthetic catalog data only -- no clinical content is invented or implied approved."""

    __tablename__ = "exchange_test_catalog_entries"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_exchange_test_catalog_entries_code_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LabOffering(Base):
    """One lab's declaration that it performs one catalog test. Organization-type validity
    (``lab_organization_id`` must reference a ``LAB``, not a ``PROVIDER``) is enforced by the
    repository layer, not a cross-table DB constraint (SQL cannot CHECK a foreign table's column
    portably) -- see ``exchange/catalog.py`` and its negative-control test."""

    __tablename__ = "exchange_lab_offerings"
    __table_args__ = (
        UniqueConstraint(
            "lab_organization_id",
            "catalog_entry_id",
            name="uq_exchange_lab_offerings_lab_catalog",
        ),
        Index("ix_exchange_lab_offerings_catalog_entry_id", "catalog_entry_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    catalog_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_test_catalog_entries.id", ondelete="RESTRICT"), nullable=False
    )
    local_test_code: Mapped[str | None] = mapped_column(String(128))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    prices: Mapped[list[LabPrice]] = relationship(
        back_populates="lab_offering", order_by="LabPrice.effective_at"
    )


class LabPrice(Base):
    """Informational/selection data only -- no payment, no billing ledger (DH-EX-001's scope
    boundary). Exactly one row per offering has ``superseded_at IS NULL`` (the current price);
    updating price supersedes the old row rather than mutating it, so price history is never
    destroyed. This table alone is NOT what protects a patient's already-made selection from a
    later price change -- ``LabOrderItem`` carries its own frozen snapshot for that (see
    ``exchange/selection.py``); this table is the source those snapshots are read from."""

    __tablename__ = "exchange_lab_prices"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_exchange_lab_prices_amount_non_negative"),
        Index(
            "uq_exchange_lab_prices_current_per_offering",
            "lab_offering_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
            sqlite_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_offerings.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # DJF (Djiboutian franc) is the pilot default; the column is a plain code, not a hard enum,
    # so a future multi-currency lab is not a schema migration -- but nothing here invents
    # multi-currency behavior (conversion, display) beyond storing the code (DH-EX-001 §Pricing).
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="DJF")
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lab_offering: Mapped[LabOffering] = relationship(back_populates="prices")


class LabOrder(Base):
    """The DHealth-owned order aggregate. No assumption that OpenMRS (or any connector) created
    it -- ``provider_organization_id`` is the only required sender identity.

    ``idempotency_key`` is the request-level opaque identity DH-EX-001 requires: a client
    (eventually the Provider Portal) generates one value per *intended* submission and retries
    with the same value on a lost response or a duplicate click. It is never derived from
    clinical payload (patient/test/practitioner/time), so a genuinely new order with identical
    clinical content but a new key is a legitimate second order -- this is the DH-011H lesson
    (the OpenMRS TestOrder endpoint has no such mechanism today) applied at the boundary that
    actually needs it for the Exchange MVP.

    Uniqueness is scoped to ``(provider_organization_id, idempotency_key)``, not
    ``idempotency_key`` alone (DH-EX-001R finding). Independent organizations generate their own
    client request keys with no cross-org coordination; a bare global-unique key means two
    different provider organizations' clients could collide on the same key value by pure
    accident (e.g. both starting a simple counter at "1") and, without this fix, one
    organization's caller would have silently received *another organization's* order back as if
    it were their own successful submission -- a cross-tenant data leak, not just a UX bug. A
    UUIDv4 collision is improbable; the semantics must be explicit regardless of probability.
    """

    __tablename__ = "exchange_lab_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('AWAITING_LAB_SELECTION', 'SELECTED', 'ACCEPTED', 'IN_PROGRESS', "
            "'COMPLETED', 'REJECTED', 'CANCELLED')",
            name="ck_exchange_lab_orders_status",
        ),
        UniqueConstraint(
            "provider_organization_id",
            "idempotency_key",
            name="uq_exchange_lab_orders_provider_idempotency_key",
        ),
        Index("ix_exchange_lab_orders_provider_organization_id", "provider_organization_id"),
        Index("ix_exchange_lab_orders_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    patient_reference_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_patient_references.id", ondelete="RESTRICT"), nullable=False
    )
    practitioner_reference_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_practitioner_references.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=LabOrderStatus.AWAITING_LAB_SELECTION.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    items: Mapped[list[LabOrderItem]] = relationship(back_populates="order")


class LabOrderItem(Base):
    """One requested test on an order. ``catalog_code_snapshot``/``catalog_display_name_snapshot``
    freeze what was ordered even if the catalog entry is later revised (a new ``version`` row),
    matching the immutability discipline applied to pricing.

    Selection-time price snapshot lives here (not on a separate per-selection-item table) --
    populated exactly once, by ``exchange/selection.py``, at the moment a lab is chosen for the
    order, and never rewritten afterward even if the lab's current price later changes."""

    __tablename__ = "exchange_lab_order_items"
    __table_args__ = (
        Index("ix_exchange_lab_order_items_lab_order_id", "lab_order_id"),
        CheckConstraint(
            "(selected_price_amount IS NULL) = (selected_price_currency IS NULL)",
            name="ck_exchange_lab_order_items_price_snapshot_pairing",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False
    )
    catalog_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_test_catalog_entries.id", ondelete="RESTRICT"), nullable=False
    )
    catalog_code_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_display_name_snapshot: Mapped[str] = mapped_column(String(256), nullable=False)
    selected_price_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    selected_price_currency: Mapped[str | None] = mapped_column(String(3))
    order: Mapped[LabOrder] = relationship(back_populates="items")
    result: Mapped[LabResult | None] = relationship(back_populates="order_item")


class LabSelection(Base):
    """One row per order (unique FK) -- DH-EX-001's approved MVP scope is a single destination
    lab per order, not per-item lab selection. Per-item price snapshots live on
    ``LabOrderItem``, populated at the same moment this row is created (see
    ``exchange/selection.py``); this row itself carries no price to avoid a second, potentially
    divergent copy of the same fact."""

    __tablename__ = "exchange_lab_selections"
    __table_args__ = (
        Index("ix_exchange_lab_selections_lab_organization_id", "lab_organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    lab_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LabResult(Base):
    """Minimum representation for the synthetic MVP: numeric-with-unit-and-range, or text --
    never both, enforced by a CHECK constraint. No automated interpretation and no
    normal/abnormal inference is computed or stored anywhere in this model (DH-EX-001's explicit
    boundary). One result per order item for V1 -- no amendment/correction workflow is modeled.

    ``lab_organization_id`` is stored redundantly (also reachable via
    ``order_item.order.selection.lab_organization_id``) specifically so the repository can check
    "did this result come from the lab actually selected for this order" with a single-row
    comparison instead of a multi-hop join, and so that check cannot be silently bypassed by
    omitting a join elsewhere."""

    __tablename__ = "exchange_lab_results"
    __table_args__ = (
        CheckConstraint(
            "result_type IN ('NUMERIC', 'TEXT')", name="ck_exchange_lab_results_result_type"
        ),
        CheckConstraint(
            "(result_type = 'NUMERIC' AND numeric_value IS NOT NULL AND text_value IS NULL) OR "
            "(result_type = 'TEXT' AND text_value IS NOT NULL AND numeric_value IS NULL)",
            name="ck_exchange_lab_results_value_matches_type",
        ),
        Index("ix_exchange_lab_results_lab_organization_id", "lab_organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_order_items.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    lab_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    result_type: Mapped[str] = mapped_column(String(16), nullable=False)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    numeric_unit: Mapped[str | None] = mapped_column(String(32))
    reference_range_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    reference_range_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    text_value: Mapped[str | None] = mapped_column(String(2048))
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    order_item: Mapped[LabOrderItem] = relationship(back_populates="result")


class ResultDelivery(Base):
    """Delivery to the ordering provider organization is its own state, independent from
    ``LabResult`` existing -- DH-EX-001 forbids inferring delivery from result completion. Starts
    ``PENDING`` and only an explicit repository call may move it to ``DELIVERED``/``FAILED``; one
    row per order (unique FK), not per item, matching V1's "doctor receives the order's result(s)
    as one unit" framing."""

    __tablename__ = "exchange_result_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'DELIVERED', 'FAILED')",
            name="ck_exchange_result_deliveries_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    provider_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ResultDeliveryStatus.PENDING.value
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DoctorReview(Base):
    """The follow-up disposition flag. Created ``PENDING`` at order-creation time (see
    ``exchange/orders.py``) so "review remains pending until explicit provider action" is a real
    row from the start, not a state inferred by the absence of one. No clinical interpretation is
    encoded beyond this workflow flag -- DH-EX-001's explicit boundary."""

    __tablename__ = "exchange_doctor_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'NO_FOLLOW_UP_REQUIRED', 'FOLLOW_UP_REQUIRED')",
            name="ck_exchange_doctor_reviews_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DoctorReviewStatus.PENDING.value
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_practitioner_reference_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_practitioner_references.id", ondelete="RESTRICT")
    )
