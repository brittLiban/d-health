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
    "ExchangeIdentity",
    "ExchangeIdentityStatus",
    "ExchangeSecurityAuditEvent",
    "LabDirectoryProfile",
    "LabOffering",
    "LabObservation",
    "LabOrder",
    "LabOrderIntent",
    "LabOrderIntentItem",
    "LabOrderItem",
    "LabOrderStatus",
    "LabPrice",
    "LabResult",
    "LabResultAmendment",
    "LabResultAmendmentStatus",
    "LabResultRelease",
    "LabResultReleaseManifestEntry",
    "LabResultReleaseVersion",
    "LabResultType",
    "LabSampleCollection",
    "LabSelection",
    "Organization",
    "OrganizationMembership",
    "OrganizationMembershipStatus",
    "OrganizationRole",
    "OrganizationRoleAssignment",
    "OrganizationStatus",
    "OrganizationType",
    "PatientAccessGrant",
    "PatientAccessMode",
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


class LabResultAmendmentStatus(StrEnum):
    """DH-EX-014B/ADR-048 Decision 4: immediate/permanent revision semantics -- MVP has no
    discardable-draft workflow, so this is deliberately a two-state machine, not three or four.
    ``PREPARING``: at most one per order at a time (see ``LabResultAmendment``'s own partial-index
    docstring); a ``LAB_TECH`` may add any number of observation revisions. ``RELEASED``: terminal
    -- once released, an amendment episode is never reopened, discarded, cancelled, or rejected.
    No ``CANCELLED``/``DISCARDED``/``REJECTED`` value exists; none is added "for future use"."""

    PREPARING = "PREPARING"
    RELEASED = "RELEASED"


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
        UniqueConstraint("intent_id", name="uq_exchange_lab_orders_intent_id"),
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
    # NULL is retained solely for LabOrders created before DH-CLINIC-003C.
    # Every new provider order is committed from one immutable intent.
    intent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_lab_order_intents.id", ondelete="RESTRICT"), nullable=True
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


class LabOrderIntent(Base):
    """DH-CLINIC-003C: Backend-owned durable canonical diagnostic order intent.

    An immutable record of one intended clinical submission created prior to committing
    a LabOrder. Allows exact mutation recovery across browser crashes or page reloads
    without persisting diagnostic service IDs or test names in browser local storage.

    Uniqueness is scoped to ``(provider_organization_id, idempotency_key)``, preserving
    exact provider isolation and duplicate-safe replay semantics.
    """

    __tablename__ = "exchange_lab_order_intents"
    __table_args__ = (
        UniqueConstraint(
            "provider_organization_id",
            "idempotency_key",
            name="uq_exchange_lab_order_intents_provider_idempotency_key",
        ),
        UniqueConstraint(
            "committed_lab_order_id",
            name="uq_exchange_lab_order_intents_committed_order",
        ),
        Index("ix_exchange_lab_order_intents_provider_org_id", "provider_organization_id"),
        Index(
            "ix_exchange_lab_order_intents_patient_ref_id",
            "provider_organization_id",
            "patient_reference_id",
        ),
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
    committed_lab_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    items: Mapped[list[LabOrderIntentItem]] = relationship(
        back_populates="intent", cascade="all, delete-orphan"
    )
    committed_order: Mapped[LabOrder | None] = relationship(foreign_keys=[committed_lab_order_id])


class LabOrderIntentItem(Base):
    """One requested test on an order intent. Freezes canonical catalog_entry_id as well
    as catalog code and display name snapshots at intent creation time."""

    __tablename__ = "exchange_lab_order_intent_items"
    __table_args__ = (
        Index("ix_exchange_lab_order_intent_items_intent_id", "intent_id"),
        UniqueConstraint(
            "intent_id",
            "catalog_entry_id",
            name="uq_exchange_lab_order_intent_items_intent_entry",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    intent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_order_intents.id", ondelete="CASCADE"), nullable=False
    )
    catalog_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_test_catalog_entries.id", ondelete="RESTRICT"), nullable=False
    )
    catalog_code_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_display_name_snapshot: Mapped[str] = mapped_column(String(256), nullable=False)

    intent: Mapped[LabOrderIntent] = relationship(back_populates="items")


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
    # DH-EX-005: which scoped patient access grant authorized this selection -- nullable because
    # a pre-DH-EX-005 (or any future non-patient-driven) selection never had one; every selection
    # made through the new patient API populates it. This FK, not a generic audit-actor field, is
    # this row's own authoritative provenance -- a patient using an opaque grant is not an
    # ExchangeIdentity, and no fake one is ever created to force it into the existing audit shape
    # (see `exchange/patient_access.py`'s module docstring for the full reasoning).
    selected_via_patient_access_grant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_patient_access_grants.id", ondelete="RESTRICT"), nullable=True
    )


class LabSampleCollection(Base):
    """DH-EX-011: records exactly one fact -- an authorized ``LAB_TECH`` at the order's own
    selected lab explicitly advanced the order-level sample/specimen collection workflow
    milestone in DHealth. Nothing more. This is deliberately **not** a specimen/sample entity: no
    specimen type, container/tube, volume, collection site, handling/storage rule, or clinical
    adequacy judgment is modeled or implied here -- those require a real laboratory
    workflow/clinical decision this ticket does not make (`AGENTS.md`'s "never invent clinical
    content" rule). This row exists solely so a later ``ACCEPTED -> IN_PROGRESS`` transition has an
    explicit, non-inferred fact to require, per DH-EX-001's own original design note that DHealth
    must never infer a physical-world specimen event from another workflow timestamp/status --
    generalizing the same lesson DH-011J2 already proved the hard way on the OpenELIS connector
    track (no reliable native signal for specimen collection existed there either; see
    `DECISIONS.md`'s ADR-018 DH-011J2 addendum).

    ``recorded_at`` -- deliberately named, and deliberately **not** ``collected_at`` -- is when
    DHealth's own server clock observed this explicit action, never a caller-supplied or
    backdated clinical timestamp. It states "a lab user advanced this milestone at this instant,"
    never "physical collection definitively occurred at this exact historical moment" and never
    "this specimen is clinically adequate." One row per ``LabOrder`` maximum (the unique
    ``lab_order_id``, mirroring ``LabSelection``'s own one-row-per-order shape): this is an
    order-level workflow milestone, not a per-specimen/per-item record, matching this ticket's own
    explicit scope boundary against a specimen-management/LIS subsystem.

    No historical backfill exists or ever will for rows predating this ticket: a pre-DH-EX-011
    order already ``IN_PROGRESS``/``COMPLETED`` with no matching row here means "collection was not
    recorded in DHealth," never "collection definitely did not happen" -- the honest absence this
    ticket's own instruction requires, not a retroactively fabricated one."""

    __tablename__ = "exchange_lab_sample_collections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    lab_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LabResult(Base):
    """DH-EX-012C/ADR-047: a per-ordered-item **result-entry container**, not a value itself. Before
    this ticket, this row held both identity and the single scalar value directly (`result_type`,
    `numeric_value`/`text_value`, etc.); ADR-046's field evidence established that one ordered
    diagnostic service may return zero, one, or many returned observations, so every scalar value
    column has moved to the new child table, ``LabObservation`` (see its own docstring). This row is
    now purely identity plus one explicit workflow fact -- never a clinical one:

    ``entry_completed_at``/``entry_completed_by_identity_id``: **the single explicit signal that
    this item's result entry is finished**, set only by ``LabResultRepository.complete_entry`` (a
    native DHealth Lab action) or by the backward-compatible single-observation convenience path
    (``record_numeric``/``record_text``), which completes atomically with its one observation.
    **Never inferred from how many observations exist** -- ADR-047's own explicit instruction
    against letting "some observations exist" imply completeness.

    ``entry_completed_by_identity_id`` is nullable specifically because a pre-DH-EX-012C legacy
    row, migrated from the old one-scalar shape, cannot truthfully name an entering actor (the old
    ``LabResult`` carried no actor column of its own -- see migration
    ``0016_exchange_observations``); every *new* completion recorded through the API always
    populates it. A container existing at all is, by construction, proof that at least one
    ``LabObservation`` exists under it -- both creation paths (``record_observation``,
    ``record_numeric``/``record_text``) insert the container and its first observation together,
    in the same transaction, so this invariant is never briefly false.

    ``lab_organization_id`` is stored redundantly (also reachable via
    ``order_item.order.selection.lab_organization_id``) specifically so the repository can check
    "did this result come from the lab actually selected for this order" with a single-row
    comparison instead of a multi-hop join, and so that check cannot be silently bypassed by
    omitting a join elsewhere. One container per order item, unchanged from before this ticket
    (``lab_order_item_id`` stays database-`UNIQUE`) -- ADR-047 rejected letting an ordered item have
    more than one container; cardinality now varies only at the observation level beneath it.

    **DH-EX-013/ADR-045 (pre-release correction):** correcting one of this container's effective
    observations never touches this row -- ``entry_completed_at``/``entry_completed_by_identity_id``
    are never cleared or rewritten by a correction, even though a correction may happen after entry
    is already complete. Rewriting either would misrepresent when entry was originally completed;
    see ``LabResultRepository.correct_observation``'s own docstring for the full reasoning."""

    __tablename__ = "exchange_lab_results"
    __table_args__ = (Index("ix_exchange_lab_results_lab_organization_id", "lab_organization_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_order_items.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    lab_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    entry_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_completed_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=True
    )
    order_item: Mapped[LabOrderItem] = relationship(back_populates="result")
    # DH-EX-013: this relationship returns every observation ever recorded under this container,
    # superseded and effective alike -- no ordinary repository read in `exchange/results.py` uses
    # it directly for exactly that reason (each one queries `LabObservation` explicitly, filtered
    # to `superseded_at IS NULL`). Kept for ad hoc inspection/debugging only; treat any future use
    # of this relationship in application code as a bug unless it is genuinely a full-history read.
    observations: Mapped[list[LabObservation]] = relationship(
        back_populates="result", order_by="LabObservation.recorded_at"
    )


class LabObservation(Base):
    """DH-EX-012C/ADR-047: one returned value under a ``LabResult`` container -- the child row that
    now carries what ``LabResult`` itself used to hold directly. A container may have zero (while
    entry is incomplete), one (the common case), or many (a panel/grouped service) observations;
    nothing here caps the count, and no code anywhere infers a clinical meaning from how many exist.

    **Observation identity is the hybrid-namespace shape ADR-047 recommended**, mirroring the
    identical optional-DHealth-layer-plus-mandatory-source-layer pattern this codebase already uses
    for ``PatientReference``/``PractitionerReference`` and ``TestCatalogEntry``/``LabOffering.
    local_test_code``: ``source_code``/``source_system``/``display_label_snapshot`` are always
    populated (sufficient for a freeform DHealth Lab native entry, or a future dynamically-arriving
    Connect/LIS-reported observation with no DHealth-side template at all); an optional future
    DHealth-owned observation-catalog link is deliberately **not** added as a column here -- no such
    catalog table exists yet, and ADR-047 explicitly does not require one for this ticket. For
    today's only native-entry callers (`record_observation`, and the single-observation convenience
    path), ``source_system`` is always the same self-asserted sentinel ``exchange/api.py`` already
    uses for a DHealth-authored practitioner identity (`_CLINICIAN_PRACTITIONER_SOURCE_SYSTEM`,
    "dhealth-exchange") -- reused here under its own name for the identical fact: DHealth itself is
    asserting this identity, not an external source system. No LOINC dependency, no universal
    DHealth analyte ontology, and no hard-coded panel/analyte content is implied by this shape.

    **No uniqueness beyond the primary key is imposed** -- ADR-047 and this ticket's own instruction
    both refuse to invent a "one code per result" rule unsupported by real laboratory-workflow
    evidence; a real source may legitimately report a repeated measurement.

    **Append-only in the sense that matters: no value column is ever updated or deleted.** DH-EX-013
    adds exactly one permitted mutation -- setting ``superseded_at`` on a row once a correction
    supersedes it (see below) -- mirroring this codebase's own established pattern for
    ``LabSampleCollection``/``LabResultRelease``/``LabPrice.superseded_at`` of marking a fact
    retired rather than deleting or overwriting it. Once its container's ``entry_completed_at`` is
    set, no further *new* observation may be added under it (`LabResultRepository`'s freeze check,
    unaffected by DH-EX-013) -- but an existing *effective* observation may still be corrected, per
    ADR-045's accepted pre-release-correction decision (see `LabResultRepository.correct_
    observation`'s own docstring for the full reasoning this does not "reopen" entry).

    **DH-EX-013/ADR-045 (pre-release correction only): immutable supersession chain.**
    ``supersedes_observation_id`` is ``NULL`` for an original observation, or the id of the exact
    observation this one corrects. ``superseded_at`` is ``NULL`` while this row is the chain's
    *effective* value, and is set (once, never cleared, never overwritten) the instant a correction
    supersedes it. **The effective rule is exactly `superseded_at IS NULL` -- an explicit relational
    fact, never inferred from `recorded_at` ordering, list position, or API call order.** A
    ``UNIQUE`` constraint on ``supersedes_observation_id`` (see ``__table_args__``) makes "at most
    one row may ever supersede a given observation" a database-enforced fact, not merely an
    application check -- the real defense against two concurrent corrections of the same effective
    observation both succeeding (the `LabOrder FOR UPDATE` lock already serializes this in the
    common case; the constraint is the backstop for anything that reaches this table any other
    way). A ``CHECK`` constraint additionally refuses literal self-supersession
    (``id = supersedes_observation_id``). Cross-container supersession (correcting an observation
    that belongs to a *different* ``LabResult``) and correcting a non-effective (already-superseded)
    observation are both application-layer checks, not database constraints -- see
    ``LabResultRepository.correct_observation``'s own docstring for why a DB-level guard is not
    practical for either without a trigger, which this ticket's own instruction against "brittle
    database tricks" weighs against introducing for these two specific cases.

    ``recorded_by_identity_id`` is the exact persistent actor for *this* observation row -- who
    entered it (native `record_observation`/the single-observation convenience path) or who
    corrected it (`correct_observation`). **Nullable for the identical reason
    `LabResult.entry_completed_by_identity_id` is nullable**: a pre-DH-EX-013 observation (recorded
    by DH-EX-012C, or migrated from the pre-DH-EX-012C scalar `LabResult` shape by migration
    `0016_exchange_observations`) was never asked to carry this fact and DH-EX-013 does not
    retroactively fabricate one -- see migration `0017_observation_revisions`'s own
    docstring. Every *new* observation created through the API from this ticket forward populates
    it, whether an original entry or a correction.

    **DH-EX-014B/ADR-048: ``amendment_id`` is the post-release-amendment linkage ADR-048 §
    "observation-to-amendment linkage" requires.** ``NULL`` means this observation is an original
    entry or a *pre-release* (DH-EX-013) correction -- unchanged meaning from before this ticket,
    and every historical row keeps this ``NULL`` forever (no retroactive fabrication). Non-``NULL``
    means this exact revision was created by ``LabResultAmendmentRepository.correct_observation``
    as part of that exact ``LabResultAmendment`` episode, permanently -- immediate/permanent
    revision semantics (Decision 4), never a discardable draft. This column is the second half of
    the amendment-correction eligibility predicate (see
    ``LabResultAmendmentRepository.correct_observation``'s own docstring): a currently-effective
    observation is a valid correction target for a given ``PREPARING`` amendment if and only if it
    is named in that amendment's own ``base_release_version_id`` manifest, *or* its
    ``amendment_id`` equals that exact amendment's id -- the latter is what allows a tech to
    reconsider a not-yet-released amendment revision (B2 -> B3) without that reconsideration
    reading as "starting from an old historical value".

    Value shape (``result_type``, `numeric_value`/`numeric_unit`/`reference_range_low`/
    `reference_range_high`, `text_value`) is the identical CHECK-constrained NUMERIC-xor-TEXT
    contract `LabResult` itself enforced before this ticket -- moved, not redesigned. No automated
    interpretation and no normal/abnormal inference is computed or stored here, unchanged from
    before."""

    __tablename__ = "exchange_lab_observations"
    __table_args__ = (
        CheckConstraint(
            "result_type IN ('NUMERIC', 'TEXT')", name="ck_exchange_lab_observations_result_type"
        ),
        CheckConstraint(
            "(result_type = 'NUMERIC' AND numeric_value IS NOT NULL AND text_value IS NULL) OR "
            "(result_type = 'TEXT' AND text_value IS NOT NULL AND numeric_value IS NULL)",
            name="ck_exchange_lab_observations_value_matches_type",
        ),
        CheckConstraint(
            "supersedes_observation_id IS NULL OR supersedes_observation_id != id",
            name="ck_exchange_lab_observations_no_self_supersede",
        ),
        Index("ix_exchange_lab_observations_lab_result_id", "lab_result_id"),
        Index("ix_exchange_lab_observations_amendment_id", "amendment_id"),
        UniqueConstraint(
            "supersedes_observation_id",
            name="uq_exchange_lab_observations_supersedes_observation_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_results.id", ondelete="RESTRICT"), nullable=False
    )
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    display_label_snapshot: Mapped[str] = mapped_column(String(256), nullable=False)
    result_type: Mapped[str] = mapped_column(String(16), nullable=False)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    numeric_unit: Mapped[str | None] = mapped_column(String(32))
    reference_range_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    reference_range_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    text_value: Mapped[str | None] = mapped_column(String(2048))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    recorded_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=True
    )
    supersedes_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_lab_observations.id", ondelete="RESTRICT"), nullable=True
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # DH-EX-014B: NULL for an original entry or pre-release (DH-EX-013) correction -- see this
    # class's own docstring for the full eligibility-predicate reasoning.
    amendment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_amendments.id", ondelete="RESTRICT"), nullable=True
    )
    result: Mapped[LabResult] = relationship(back_populates="observations")


class LabResultRelease(Base):
    """DH-EX-012/ADR-044: records exactly one fact -- an authorized ``LAB_RESULT_RELEASER`` at the
    order's own selected lab explicitly authorized this order's already-entered result set to leave
    the lab workflow and become visible to the ordering provider. This is a workflow-authorization
    record, not a clinical one: DHealth does not claim to medically, scientifically, or clinically
    validate the result by this row existing -- see ``docs/MVP-1.0.md`` §13's "does not
    independently diagnose, interpret, or recommend" boundary, which this model does not cross.

    Order-level only (ADR-044): one release per ``LabOrder`` (``lab_order_id`` UNIQUE), never
    per-item -- partial/item-level release is explicitly out of MVP scope. Immutable once created:
    no revision, no un-release, no delete anywhere in this codebase. ``released_at`` is
    system-generated (``utc_now()``), never caller-supplied, mirroring ``LabSampleCollection.
    recorded_at``'s identical discipline. ``released_by_identity_id`` is the authenticated
    identity that actually committed the release -- on a lost-response retry or a different
    authorized releaser's later retry, this value is never overwritten; the fact is who actually
    released it first, not who asked most recently.

    No historical backfill: a pre-DH-EX-012 ``LabOrder`` already ``COMPLETED`` with no matching row
    here predates this ticket's explicit release step -- that is honestly "no release recorded",
    never fabricated after the fact (ADR-044's grandfather rule, mirroring DH-EX-011's identical
    no-retroactive-fabrication discipline for ``LabSampleCollection``)."""

    __tablename__ = "exchange_lab_result_releases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    lab_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    released_by_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    released_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LabResultReleaseVersion(Base):
    """DH-EX-014A/ADR-048: the immutable versioning foundation Model 3 requires -- one row per
    *released* version of an order's result set, explicitly ordered by ``version_number`` (never
    inferred from ``created_at``). Version 1 always binds to the pre-existing, unmodified
    ``LabResultRelease`` row via ``lab_result_release_id`` -- this table does not replace,
    duplicate, or reinterpret that row's own authorization fact (who released, when); it is purely
    an additional, additive layer naming *which exact observations* that release authorized (see
    ``LabResultReleaseManifestEntry``). ``LabResultRelease`` itself remains exactly as DH-EX-012/
    ADR-044 shipped it -- immutable, no revision, no un-release, no delete -- forever.

    **DH-EX-014B/ADR-048: a version 2+ row binds instead to the ``LabResultAmendment`` episode that
    authorized it, via ``amendment_id`` -- ``UNIQUE``, so one amendment produces exactly one
    released version, and the reverse-direction ``CHECK`` below (replacing DH-EX-014A's original,
    narrower one) now states the full binding contract at the schema level: version 1 is always
    release-bound and never amendment-bound; every later version is always amendment-bound and
    never release-bound. Neither ``LabResultRelease`` nor Version 1 is ever mutated by an amendment
    release -- this column only ever gets populated on a brand-new row for a brand-new version."""

    __tablename__ = "exchange_release_versions"
    __table_args__ = (
        UniqueConstraint(
            "lab_order_id",
            "version_number",
            name="uq_exchange_release_versions_order_version",
        ),
        CheckConstraint(
            "version_number >= 1", name="ck_exchange_release_versions_version_positive"
        ),
        # DH-EX-014B: replaces DH-EX-014A's original `ck_exchange_release_versions_v1_bound_to_
        # release` (same name, widened definition) -- a version's binding is now fully determined
        # by its own number: version 1 binds only to the original release, every later version
        # binds only to the amendment that authorized it, and neither binding is ever optional.
        CheckConstraint(
            "(version_number = 1 AND lab_result_release_id IS NOT NULL "
            "AND amendment_id IS NULL) OR (version_number > 1 AND lab_result_release_id IS NULL "
            "AND amendment_id IS NOT NULL)",
            name="ck_exchange_release_versions_v1_bound_to_release",
        ),
        Index("ix_exchange_release_versions_lab_order_id", "lab_order_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lab_result_release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_lab_result_releases.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    # DH-EX-014B: nullable, UNIQUE -- populated only for version_number > 1, exactly one released
    # version per amendment episode.
    amendment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_amendments.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LabResultReleaseManifestEntry(Base):
    """DH-EX-014A/ADR-048: one row means exactly "this exact ``LabObservation`` id was part of this
    exact released version, under this exact item" -- the immutable binding that makes a historical
    release exactly reconstructable regardless of what happens to the observation's own
    ``superseded_at`` afterward. **Deliberately binds by id only** -- no value, unit, range, source
    code, or display label is copied here; the existing immutable ``LabObservation`` row is the
    single source of truth for content, exactly as this ticket's own instruction requires. A single
    item may have more than one manifest entry for the same version (a multi-observation panel),
    which is why uniqueness is scoped to ``(release_version_id, lab_observation_id)``, not to the
    item -- ADR-047's own "do not impose one observation per item" rule applies identically here.

    **Cross-item/cross-order integrity is an application-layer check, not a database constraint** --
    verifying "this observation's own container belongs to this exact item" and "this item belongs
    to this exact order" requires a multi-hop join no `CHECK`/`UNIQUE` constraint alone can express
    without a trigger; both are verified by ``LabResultReleaseRepository`` at the one place a
    manifest is ever created (see its own docstring), inside the same ``LabOrder``-locked
    transaction that already determines the effective set, so there is no window for either check to
    be bypassed."""

    __tablename__ = "exchange_release_manifest_entries"
    __table_args__ = (
        UniqueConstraint(
            "release_version_id",
            "lab_observation_id",
            name="uq_exchange_release_manifest_entries_version_observation",
        ),
        Index(
            "ix_exchange_release_manifest_entries_release_version_id",
            "release_version_id",
        ),
        Index("ix_exchange_release_manifest_entries_lab_order_item_id", "lab_order_item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    release_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_release_versions.id", ondelete="RESTRICT"), nullable=False
    )
    lab_order_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_order_items.id", ondelete="RESTRICT"), nullable=False
    )
    lab_observation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_observations.id", ondelete="RESTRICT"), nullable=False
    )


class LabResultAmendment(Base):
    """DH-EX-014B/ADR-048 Model 3: the amendment-episode workflow container ADR-048 §5/§8
    recommended -- ``PREPARING`` -> ``RELEASED``, no other states (see
    ``LabResultAmendmentStatus``'s own docstring for why no discard/cancel state exists).

    **At most one ``PREPARING`` amendment per order at a time** -- a genuine partial unique index
    (see ``__table_args__``), the identical shape ``PatientAccessGrant``'s own "one live grant per
    order" constraint already established (``uq_exchange_patient_access_grants_one_live_per_order``)
    applied to a different status predicate. Sequential amendments are fully supported: once an
    amendment reaches ``RELEASED`` (terminal, never reopened), a new ``PREPARING`` row for the same
    order is unblocked by this same index.

    ``amendment_number`` is explicit and ordered (``UNIQUE(lab_order_id, amendment_number)``,
    ``CHECK(amendment_number >= 1)``) -- assigned once, at creation, as
    ``COALESCE(MAX(amendment_number), 0) + 1`` under the same ``LabOrder FOR UPDATE`` lock every
    other order-scoped mutation in this package already acquires first; never inferred from
    ``started_at`` ordering.

    ``base_release_version_id`` is **not** merely an audit convenience -- it is the left operand of
    the amendment-correction eligibility predicate (see
    ``LabResultAmendmentRepository.correct_observation``'s own docstring): an old ``PREPARING``
    amendment must never be able to release against a release-version baseline other than the one
    it actually started against. Non-nullable and immutable once set, so this fact can never
    silently drift for as long as the amendment stays open.

    ``released_by_identity_id``/``released_at`` are both ``NULL`` while ``PREPARING`` and both
    non-``NULL`` exactly when ``RELEASED`` -- enforced by ``CHECK``, not merely application
    discipline. **No reason field exists anywhere on this table** -- ADR-048 Decision 5 (no
    mandatory amendment reason for MVP); nothing here is a placeholder for one."""

    __tablename__ = "exchange_amendments"
    __table_args__ = (
        UniqueConstraint(
            "lab_order_id", "amendment_number", name="uq_exchange_amendments_order_number"
        ),
        CheckConstraint("amendment_number >= 1", name="ck_exchange_amendments_number_positive"),
        CheckConstraint(
            "status IN ('PREPARING', 'RELEASED')", name="ck_exchange_amendments_status"
        ),
        CheckConstraint(
            "(status = 'PREPARING' AND released_by_identity_id IS NULL AND released_at IS NULL) "
            "OR (status = 'RELEASED' AND released_by_identity_id IS NOT NULL "
            "AND released_at IS NOT NULL)",
            name="ck_exchange_amendments_release_consistency",
        ),
        Index("ix_exchange_amendments_lab_order_id", "lab_order_id"),
        Index("ix_exchange_amendments_base_release_version_id", "base_release_version_id"),
        Index(
            "uq_exchange_amendments_one_preparing_per_order",
            "lab_order_id",
            unique=True,
            postgresql_where=text("status = 'PREPARING'"),
            sqlite_where=text("status = 'PREPARING'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False
    )
    amendment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    base_release_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_release_versions.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=LabResultAmendmentStatus.PREPARING.value
    )
    started_by_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    released_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    encoded beyond this workflow flag -- DH-EX-001's explicit boundary.

    **DH-EX-014A/ADR-048 (ADR-045 Question 4, Option C -- versioned review history):**
    ``lab_order_id`` is no longer database-``unique`` -- an order may eventually carry more than one
    review row, one per released version, once DH-EX-014B's amendment releases exist.
    ``release_version_id`` is the explicit, never-inferred-from-timestamp binding: ``NULL`` means
    this is the one legacy/pre-release row created at order-creation time before any release has
    occurred yet (unchanged from before this ticket); non-``NULL`` means this review's obligation is
    for *exactly* that ``LabResultReleaseVersion``, permanently -- a historical review is never
    reset, rewritten, reassigned to a different release, or treated as covering content it never
    saw. Two constraints, not one, enforce the intended shape at the database level (see
    ``__table_args__``): at most one review per non-``NULL`` release version (a plain ``UNIQUE``
    already gives this for free, since every SQL dialect this codebase targets treats ``NULL`` as
    distinct from every other ``NULL`` under ``UNIQUE``); at most one *unbound* legacy row per order
    (a genuine partial index, since the plain `UNIQUE` above does not by itself prevent two
    ``NULL``-bound rows for the same order). A grandfathered ``COMPLETED``-without-
    ``LabResultRelease`` order's review row keeps ``release_version_id = NULL`` forever -- it is
    not amendment-eligible
    (DH-EX-014A's Decision 6), so no version ever exists for it to bind to."""

    __tablename__ = "exchange_doctor_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'NO_FOLLOW_UP_REQUIRED', 'FOLLOW_UP_REQUIRED')",
            name="ck_exchange_doctor_reviews_status",
        ),
        UniqueConstraint(
            "release_version_id",
            name="uq_exchange_doctor_reviews_release_version_id",
        ),
        Index(
            "uq_exchange_doctor_reviews_unbound_lab_order_id",
            "lab_order_id",
            unique=True,
            postgresql_where=text("release_version_id IS NULL"),
            sqlite_where=text("release_version_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False
    )
    release_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_release_versions.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DoctorReviewStatus.PENDING.value
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_practitioner_reference_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_practitioner_references.id", ondelete="RESTRICT")
    )


# --- DH-EX-002: organization onboarding, identity, membership, and role assignment -------------


class ExchangeIdentityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class OrganizationMembershipStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class OrganizationRole(StrEnum):
    """MVP organization roles only -- no billing/reception/pharmacy roles. Validity against
    organization type (PROVIDER: ORG_ADMIN/CLINICIAN; LAB: ORG_ADMIN/LAB_TECH/LAB_RESULT_RELEASER)
    is enforced by ``exchange/membership.py``'s repository layer, not a DB constraint, for the same
    reason ``LabOffering``'s lab-organization-type check is repository-enforced elsewhere in this
    file: SQL cannot portably CHECK a value against a column on a different, joined-through table.

    ``LAB_RESULT_RELEASER`` (DH-EX-012, ADR-044): the explicit result-release authority, distinct
    from ``LAB_TECH``'s result-*entry* authority. ADR-044 binds this as an additive role, not a
    replacement -- the same human may hold both simultaneously (no forced second-person workflow),
    and a membership holding only one of the two may do exactly that one thing (enter, or release)
    and not the other."""

    ORG_ADMIN = "ORG_ADMIN"
    CLINICIAN = "CLINICIAN"
    LAB_TECH = "LAB_TECH"
    LAB_RESULT_RELEASER = "LAB_RESULT_RELEASER"


class ExchangeIdentity(Base):
    """One authenticated human, identified **exactly** by their OIDC ``(issuer, subject)`` pair --
    never by email, name, or phone, none of which are stable (DH-EX-002's explicit instruction).
    Authentication and organization authorization are deliberately separate: a row existing here
    means "DHealth has seen a validly-signed token for this issuer+subject", nothing more --
    creating this row grants zero organization access on its own (see ``OrganizationMembership``).
    Never stores a password, password hash, reset token, or any other credential material; those
    live exclusively in the identity provider.
    """

    __tablename__ = "exchange_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_exchange_identities_issuer_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # Snapshots only, refreshed opportunistically from later tokens -- never the identity key,
    # never compared for matching/merging purposes anywhere in this codebase.
    display_name: Mapped[str | None] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ExchangeIdentityStatus.ACTIVE.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class OrganizationMembership(Base):
    """One identity's membership in one organization. An identity may hold memberships in more
    than one organization -- V1 does not force a single global org per human. Inactive memberships
    must fail authorization (``exchange/authorization.py``); historical rows are never physically
    deleted, only inactivated."""

    __tablename__ = "exchange_organization_memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "identity_id", name="uq_exchange_org_memberships_org_identity"
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')", name="ck_exchange_org_memberships_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OrganizationMembershipStatus.ACTIVE.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    role_assignments: Mapped[list[OrganizationRoleAssignment]] = relationship(
        back_populates="membership"
    )


class OrganizationRoleAssignment(Base):
    """One role held by one membership. A membership may hold more than one role (e.g. a small
    provider org's sole user might be both ``ORG_ADMIN`` and ``CLINICIAN``); it may never hold the
    same role twice, and it may never hold a role invalid for its organization's type -- both
    enforced in ``exchange/membership.py``, the second because it requires joining through to the
    organization's own type, which a same-table CHECK constraint cannot express."""

    __tablename__ = "exchange_organization_role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "membership_id", "role", name="uq_exchange_org_role_assignments_membership_role"
        ),
        CheckConstraint(
            "role IN ('ORG_ADMIN', 'CLINICIAN', 'LAB_TECH', 'LAB_RESULT_RELEASER')",
            name="ck_exchange_org_role_assignments_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organization_memberships.id", ondelete="RESTRICT"), nullable=False
    )
    # DH-EX-012: widened from String(16) to String(32) -- 'LAB_RESULT_RELEASER' is 19 characters,
    # which does not fit the original width sized only for 'ORG_ADMIN'/'CLINICIAN'/'LAB_TECH'.
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    membership: Mapped[OrganizationMembership] = relationship(back_populates="role_assignments")


class LabDirectoryProfile(Base):
    """DH-EX-003: one lab organization's marketplace visibility/location profile. Strictly
    one-to-one with a LAB organization -- a PROVIDER organization never has a row here, enforced
    at the repository layer (the same "repository is the enforcement point" rule already used for
    ``LabOffering``'s organization-type check).

    ``listed``/``accepting_orders`` both default false: a newly-created lab organization is never
    automatically marketplace-visible just by existing. ``listed`` controls whether the lab appears
    in directory browsing at all; ``accepting_orders`` is the coarser, separate switch that also
    gates whole-order eligibility -- a listed-but-not-accepting lab may still appear in directory
    detail, but never as an order-eligible option (see ``exchange/directory.py``). No geocoding, no
    opening-hours/availability schedule -- ``public_address`` is free-text, ``latitude``/
    ``longitude`` are optional operator-entered coordinates, never inferred."""

    __tablename__ = "exchange_lab_directory_profiles"
    __table_args__ = (
        UniqueConstraint(
            "lab_organization_id", name="uq_exchange_lab_directory_profiles_lab_organization"
        ),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) "
            "OR (latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_exchange_lab_directory_profiles_lat_lon_pair",
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_exchange_lab_directory_profiles_latitude_range",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_exchange_lab_directory_profiles_longitude_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    listed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accepting_orders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    public_address: Mapped[str] = mapped_column(String(512), nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    public_phone: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ExchangeSecurityAuditEvent(Base):
    """DH-EX-002R: the authoritative, transactional audit record for Exchange security mutations.
    Inserted in the **same database transaction** as the mutation it records (see
    ``exchange/security_audit.py``) -- never written after a separate commit, and never the target
    of a best-effort fallback. If this insert fails, the mutation it would have recorded is rolled
    back with it; nothing here is reachable independent of a real, committed mutation.

    DH-EX-003 generalized this table's original organization/membership/role-only shape to also
    cover catalog/directory/offering/price mutations, which have no single owning organization
    (a platform-admin catalog-entry creation, in particular) -- `organization_id` is therefore
    nullable as of DH-EX-003, and `resource_type`/`resource_id` identify the mutated
    catalog/directory/offering/price row generically rather than overloading `membership_id`/
    `role` (which stay exactly what DH-EX-002 defined them as, for org/membership/role events
    only). This is a purely additive generalization -- every DH-EX-002 row and column keeps its
    original meaning; nothing here narrows what already existed.

    Deliberately narrow and closed: no JWT, no bearer token, no password, no Keycloak secret, no
    clinical data, no free-text payload. `event_type`/`outcome`/`resource_type` are limited by
    CHECK constraint to exactly the values real callers produce -- widen only when a real new
    caller exists, matching this package's established discipline (`exchange/audit.py`).

    Authorization *refusals* are explicitly NOT recorded here -- a refusal is not a state mutation,
    and access denial must never depend on a database write succeeding. Refusal telemetry stays in
    the existing best-effort file-based `ExchangeAuditWriter` (`exchange/audit.py`), a deliberately
    separate, non-authoritative mechanism.
    """

    __tablename__ = "exchange_security_audit_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
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
            "'lab_observation_amended', 'lab_result_amendment_released'"
            ")",
            name="ck_exchange_security_audit_events_event_type",
        ),
        CheckConstraint(
            "outcome IN ('created', 'returned_existing', 'updated', 'unchanged')",
            name="ck_exchange_security_audit_events_outcome",
        ),
        CheckConstraint(
            "role IS NULL OR role IN ('ORG_ADMIN', 'CLINICIAN', 'LAB_TECH', 'LAB_RESULT_RELEASER')",
            name="ck_exchange_security_audit_events_role",
        ),
        CheckConstraint(
            "resource_type IS NULL OR resource_type IN "
            "('catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
            "'patient_access_grant', 'patient_reference')",
            name="ck_exchange_security_audit_events_resource_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    # Nullable as of DH-EX-003 -- a platform-level catalog mutation has no owning organization.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=True
    )
    subject_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=True
    )
    membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_organization_memberships.id", ondelete="RESTRICT"), nullable=True
    )
    # DH-EX-012: widened from String(16) to String(32), matching
    # `OrganizationRoleAssignment.role`'s identical widening -- 'LAB_RESULT_RELEASER' is 19 chars.
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # DH-EX-003: generic target identification for catalog/directory/offering/price mutations.
    # Deliberately not a FK -- the target table varies by `resource_type`, and a single polymorphic
    # FK is not portably expressible; the CHECK constraint above is the closed-set guard instead.
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)


# --- DH-EX-005: opaque, order-scoped patient access -----------------------------------------


class PatientAccessMode(StrEnum):
    """DH-EX-010A: a closed, persisted statement of what a ``PatientAccessGrant`` actually
    authorizes -- an explicit capability, never inferred later from mutable ``LabOrder`` state
    alone (a grant issued while a lab could still be chosen must keep meaning that, even after a
    provider assigns a lab minutes later; re-deriving "can this grant choose a lab?" from current
    order status on every read would silently downgrade an already-issued, already-delivered
    link). Decided once, inside the locked issuance mutation (see
    ``PatientAccessGrantRepository._issue_in_session``), and never changed afterward.

    ``LAB_SELECTION``: the order has no canonical ``LabSelection`` yet and is still
    ``AWAITING_LAB_SELECTION`` -- this grant may read the order, browse eligible lab options, and
    select a lab (DH-EX-005's full patient marketplace workflow).

    ``ORDER_READ``: the order already has a canonical ``LabSelection`` (patient-selected or
    provider-assigned, DH-EX-010 -- the selection row itself, not price presence, is what proves a
    destination lab exists). This grant may only read the order; it can never select or replace a
    lab, regardless of who made the original choice or what the order's current downstream status
    is (ACCEPTED/IN_PROGRESS/COMPLETED/REJECTED all still carry the same canonical
    ``LabSelection`` row a lab-choice action would need to exist -- see
    ``exchange/patient_access.py``'s issuance-rule docstring for why status is not the gate)."""

    LAB_SELECTION = "LAB_SELECTION"
    ORDER_READ = "ORDER_READ"


class PatientAccessGrant(Base):
    """DH-EX-005: an opaque, high-entropy bearer credential scoping a patient's access to
    exactly one ``LabOrder`` -- never a patient account, never an ``ExchangeIdentity``, never
    OIDC. Only ``token_hash`` (a SHA-256 digest) is ever persisted; the raw token exists only in
    the issuance HTTP response and the patient's own possession of it -- see
    ``exchange/patient_access.py`` for why a fast cryptographic hash (not a slow password KDF) is
    the correct choice for an already-256-bit-random secret.

    At most one row per ``lab_order_id`` may have ``revoked_at IS NULL`` at a time (the partial
    unique index below) -- "one live grant per order", enforced by the database, not just
    application logic. An *expired* but not-yet-revoked grant still counts as "live" for that
    constraint's purposes; ``exchange/patient_access.py``'s issuance path explicitly retires an
    expired grant (sets ``revoked_at``) before inserting its replacement, in the same transaction,
    rather than ever holding two unrevoked rows for one order."""

    __tablename__ = "exchange_patient_access_grants"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_exchange_patient_access_grants_token_hash"),
        CheckConstraint(
            "expires_at > issued_at",
            name="ck_exchange_patient_access_grants_expiry_after_issue",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_exchange_patient_access_grants_revoked_after_issue",
        ),
        CheckConstraint(
            "access_mode IN ('LAB_SELECTION', 'ORDER_READ')",
            name="ck_exchange_patient_access_grants_access_mode",
        ),
        Index(
            "uq_exchange_patient_access_grants_one_live_per_order",
            "lab_order_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PatientAccessMode.LAB_SELECTION.value
    )
