"""DH-EX-001 ORM models for the DHealth Exchange core.

Deliberately transaction-scoped, not a longitudinal medical record (DH-PIVOT-001's approved data
boundary): no free-text clinical notes, no medication list, no diagnosis history anywhere in this
module. ``PatientReference``/``PractitionerReference`` exist to support one exchange transaction,
holding only what the golden journey needs plus an explicit source-identity binding -- never a
fuzzy or demographic match (DH-PIVOT-001's patient/practitioner identity model).

**2026-09-17 dated note (DH-IDENTITY-001):** the sentence above originally read "there is no
general ``Patient`` table modeled like an EMR" -- true when DH-EX-001 wrote it and left unedited
here as accurate history, not retroactively corrected. As of DH-IDENTITY-001, ``Patient`` now
exists below, but it does not contradict the original claim: it is a provider-scoped native
*directory* record (display name, optional exact DOB, optional contact), never a national/global
identity, never a longitudinal medical record, still no clinical notes/medication/diagnosis
anywhere in this module. ``PatientReference`` keeps its exact, no-fuzzy-matching original meaning
unchanged -- it is now explicitly the source-identity/provenance binding *for* a ``Patient``
(``patient_id``, DH-IDENTITY-001), not an overloaded stand-in for one.

**2026-09-27 dated note (DHEALTH-CLINIC-VISIT-001):** the first paragraph's "no free-text clinical
notes" is, as of today, no longer literally true of this module, and is left unedited above as
accurate history rather than quietly corrected. ``Visit``/``VisitNote`` now exist at the end of
this file, and ``VisitNote.body`` is exactly what that sentence said did not exist here. What the
sentence was protecting is nonetheless intact, so the boundary is restated rather than abandoned:
still **no medication list, no diagnosis history, no problem list, no vitals, no structured
examination, no coded clinical data, and no clinical interpretation anywhere in this module.** A
visit records that a clinician saw a patient, the reason the patient gave, and what that clinician
chose to write down; it is not a longitudinal medical record, and nothing in this codebase reads a
note back for any purpose other than showing it to the clinician who is looking at that visit.
Every structured clinical field remains out of scope pending the clinical lead's sign-off
(``AGENTS.md``), and the note is never transmitted to any third-party AI/ML service.

Shares ``Base`` with ``daher_adapter.models`` (imported, not redefined) so a single Alembic chain
and a single ``Base.metadata.create_all()`` test fixture cover both the connector-specific tables
and this Exchange core -- see this package's ``__init__.py`` for why that is safe (no import of
OpenMRS/OpenELIS-specific modules from here).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from daher_adapter.models import Base, utc_now

__all__ = [
    "Appointment",
    "AppointmentStatus",
    "DoctorReview",
    "DoctorReviewStatus",
    "ExchangeIdentity",
    "ExchangeIdentityStatus",
    "ExchangeSecurityAuditEvent",
    "LabDirectoryProfile",
    "LabOffering",
    "LabObservation",
    "BLOCKABLE_ORDER_STATUSES",
    "LabOrder",
    "LabOrderBlock",
    "LabOrderBlockReason",
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
    "SPECIMEN_RECORDABLE_ORDER_STATUSES",
    "LabSpecimen",
    "Organization",
    "OrganizationMembership",
    "OrganizationMembershipStatus",
    "OrganizationRole",
    "OrganizationRoleAssignment",
    "MembershipCapabilityOverride",
    "OrganizationStatus",
    "OrganizationType",
    "Patient",
    "PatientAccessGrant",
    "PatientAccessMode",
    "PatientReference",
    "PatientRegistrationRequest",
    "PractitionerReference",
    "ResultDelivery",
    "ResultDeliveryStatus",
    "BillableSourceType",
    "ClinicService",
    "ClinicServicePrice",
    "Invoice",
    "InvoiceLine",
    "InvoiceNumberSequence",
    "Payment",
    "PaymentMethod",
    "TestCatalogEntry",
    "Visit",
    "VisitNote",
    "VisitStatus",
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
    #: DHEALTH-LAB-BLOCKED-001 (`docs/NATIVE-OPERATING-PRODUCT.md` §16's P0-4): laboratory work
    #: this lab genuinely took and cannot currently continue. **Not** a cancellation, not a
    #: rejection, not a payment state, not a result-release state -- the order still exists and is
    #: expected to resume. Reachable only from ``ACCEPTED``/``IN_PROGRESS``, and only together
    #: with a ``LabOrderBlock`` episode created in the same transaction, which is what carries the
    #: reason, the actor, the instant, and the server-owned state to resume to.
    #:
    #: This enum's own opening paragraph stays true here: no physical-world event is inferred.
    #: ``BLOCKED`` records that a laboratory worker *said* work cannot continue and why -- never
    #: that a specimen is clinically inadequate, which is not DHealth's judgement to make.
    BLOCKED = "BLOCKED"


class LabOrderBlockReason(StrEnum):
    """DHEALTH-LAB-BLOCKED-001: why laboratory work cannot currently continue.

    **Operational, never clinical.** `docs/NATIVE-OPERATING-PRODUCT.md` §16 classifies this list
    that way explicitly, and that distinction is what keeps it inside `AGENTS.md`'s boundary: a
    reason states what stops the laboratory working, never what is wrong with the patient.
    ``SPECIMEN_UNUSABLE`` is a statement about a tube -- not a haemolysis finding, not a
    diagnosis -- and nothing in this codebase reads one back as a clinical fact.

    **These four are the ones this repository's own specification already names** -- §16's
    *"sample unusable, insufficient quantity, patient did not attend, clarification needed"* --
    and no fifth was invented to round the list out. `AGENTS.md`'s "any gap that obviously should
    be filled a particular way" rule applies at full force here: the ticket that produced this
    enum proposed a longer candidate list (a technical/analyser reason, a free ``OTHER``), and
    each of those was left out rather than added on an agent's authority.

    **The list is provisional, and §16 says so:** it *"must be confirmed with a laboratory
    participant before it is fixed."* That confirmation has not happened -- **D-11**, open, to be
    answered during human usability / workflow qualification. Neither these keys nor their French
    labels (``ui/src/features/lab/blockVocabulary.ts``) have been validated by anyone who works in
    a laboratory.

    Two kinds of gap, and the second is the worse one:

    * **No category at all**, so the free-text note carries it: analyser downtime, power outage,
      reagent shortage or unavailability, recollection needed.
    * **A category that exists only on an unverified assumption**: insufficient sample, damaged
      sample, incorrect/wrong sample, missing information. A missing category is visible and a
      worker writes a note; a subtly wrong one is simply used, and the note that would have carried
      the truth never gets written. ``SPECIMEN_UNUSABLE`` may not be what a technician means by a
      *damaged* tube, and ``CLARIFICATION_REQUIRED`` may not be what they mean by *missing
      information*.

    **No reason may be added here on an author's own reasoning**, ``TECHNICAL_ISSUE`` included --
    that is exactly what `AGENTS.md`'s rule forbids. The optional free-text note stays the honest
    home for anything that does not fit, and stays available for it.
    """

    #: The specimen cannot be used. §16's "sample unusable".
    SPECIMEN_UNUSABLE = "SPECIMEN_UNUSABLE"
    #: There is not enough specimen to perform the requested work. §16's "insufficient quantity".
    SPECIMEN_INSUFFICIENT = "SPECIMEN_INSUFFICIENT"
    #: The patient did not present. §16's "patient did not attend".
    PATIENT_DID_NOT_ATTEND = "PATIENT_DID_NOT_ATTEND"
    #: Something about the order must be clarified before work continues. §16's "clarification
    #: needed". Deliberately not "information missing": the laboratory is asking a question, not
    #: asserting that some required field is absent.
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"


#: DHEALTH-LAB-BLOCKED-001: the only order states from which laboratory work may be blocked, and
#: therefore also the only states a resolution may resume to. Derived from §16's own diagram
#: (`ACCEPTED / IN_PROGRESS -> BLOCKED -> back to the prior state`), not invented here.
#:
#: Everything else is excluded deliberately: `AWAITING_LAB_SELECTION`/`SELECTED` is work no
#: laboratory has taken yet (a lab that will not do the work rejects it -- that path already
#: exists); `COMPLETED` is a released clinical fact and blocking it would become an undo of
#: release, which the amendment domain owns instead; `REJECTED`/`CANCELLED` are terminal.
BLOCKABLE_ORDER_STATUSES: frozenset[str] = frozenset(
    {LabOrderStatus.ACCEPTED.value, LabOrderStatus.IN_PROGRESS.value}
)


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


class Patient(Base):
    """DH-IDENTITY-001/ADR-051: a provider-scoped native patient-*directory* record -- the human
    workflow identity a provider organization's own DHealth Clinic uses, never a national identity,
    never a cross-provider/global DHealth person registry, never an EMR, never a longitudinal
    medical record. Two different provider organizations may hold two separate ``Patient`` rows for
    the same real human on purpose -- cross-provider identity resolution is explicitly out of
    Launch-1 scope (ADR-051), not an oversight this table works around.

    **No fuzzy/demographic matching anywhere.** ``display_name``/``date_of_birth``/contact are
    directory fields, never an identity key -- two rows with identical demographics are always
    allowed to coexist (see ``exchange/patients.py``'s own module docstring for the concrete test
    proving this). The only thing that ever decides "is this the same ``Patient``" is an explicit
    ``PatientReference.patient_id`` link, created once, atomically, at registration time -- never
    inferred later by comparing names, phones, or birth dates.

    ``display_name`` is nullable **only** at the database level, for legacy-backfill compatibility
    (a pre-DH-IDENTITY-001 ``PatientReference`` may truthfully have no snapshot name) -- the native
    creation API (``exchange/patients.py``'s ``PatientRepository.create``) requires a non-blank one.
    ``date_of_birth`` is optional and exact only: no year-only/approximate semantics, and nothing in
    this codebase invents a placeholder date for an unknown birthday. Contact is optional and
    pair-validated (``ck_exchange_patients_contact_pairing``) -- exactly the same pairing rule
    ``PatientReference`` already established for its own contact snapshot.

    The composite ``UniqueConstraint(id, provider_organization_id)`` exists solely so
    ``PatientReference`` can target it with a composite foreign key (see that class's own
    docstring) -- the database-enforced guarantee that a reference can never point at a Patient
    belonging to a different provider organization than the reference's own
    ``source_organization_id`` (DH-IDENTITY-001 §8's explicit "the database itself must reject a
    cross-provider link" instruction, not merely an application-level check)."""

    __tablename__ = "exchange_patients"
    __table_args__ = (
        UniqueConstraint("id", "provider_organization_id", name="uq_exchange_patients_id_provider"),
        CheckConstraint(
            "(contact_channel_type IS NULL) = (contact_value IS NULL)",
            name="ck_exchange_patients_contact_pairing",
        ),
        Index("ix_exchange_patients_provider_organization_id", "provider_organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(256))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    contact_channel_type: Mapped[str | None] = mapped_column(String(32))
    contact_value: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PatientRegistrationRequest(Base):
    """DH-IDENTITY-001: the immutable idempotency/replay record for native ``Patient`` creation
    (``exchange/patients.py``'s ``PatientRepository.create``) -- deliberately a separate table from
    ``Patient`` itself, not a reused-column trick, so idempotency/conflict decisions never depend on
    ``Patient``'s own mutable state (there is no demographic-update API in this ticket, but this
    table's shape does not assume that stays true forever -- see its own repository docstring).

    ``request_fingerprint`` is a SHA-256 hex digest over a fixed, deterministically-canonicalized
    field tuple (``exchange/patients.py``'s ``_fingerprint`` is the single place this is computed,
    both at creation and at replay-comparison time) -- never Python's process-randomized ``hash()``,
    which would make a replay's own fingerprint uncomparable across process restarts.

    Uniqueness is ``(provider_organization_id, idempotency_key)``, the identical cross-tenant-safe
    shape ``LabOrder``/``LabOrderIntent`` already established -- a bare global-unique
    ``idempotency_key`` would let two different provider organizations' clients collide by pure
    accident and silently receive each other's patient."""

    __tablename__ = "exchange_patient_registration_requests"
    __table_args__ = (
        UniqueConstraint(
            "provider_organization_id",
            "idempotency_key",
            name="uq_exchange_patient_registration_requests_provider_key",
        ),
        Index(
            "ix_exchange_patient_registration_requests_patient_id",
            "patient_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_patients.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
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

    **DH-IDENTITY-001: ``patient_id`` is the exact source-identity-to-directory-record link every
    reference now carries** -- exactly one ``Patient`` per reference, though one ``Patient`` may
    have many references (``patient_id`` is indexed, deliberately not database-``unique``). The
    composite foreign key below (targeting ``Patient``'s own composite unique key, see its
    docstring) makes "this reference's ``source_organization_id`` equals its Patient's
    ``provider_organization_id``" a database-enforced fact -- a cross-provider link is a foreign-key
    violation, not merely an application bug waiting to happen. No reference-linking/merge API
    exists in this ticket (ADR-051 Decision 12) -- every reference is bound to its ``Patient`` at
    creation time only, by ``exchange/identity.py``'s ``PatientReferenceRepository`` (legacy exact
    binding: a new source identity always creates its own new 1:1 ``Patient``, an existing one
    returns its existing ``Patient`` unchanged) or by ``exchange/patients.py``'s
    ``PatientRepository`` (native registration: the ``Patient`` and its one native reference are
    always created together, atomically)."""

    __tablename__ = "exchange_patient_references"
    __table_args__ = (
        UniqueConstraint(
            "source_organization_id",
            "source_system",
            "source_identity_system",
            "source_identity_value",
            name="uq_exchange_patient_references_source_identity",
        ),
        ForeignKeyConstraint(
            ["patient_id", "source_organization_id"],
            ["exchange_patients.id", "exchange_patients.provider_organization_id"],
            name="fk_exchange_patient_references_patient_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_exchange_patient_references_source_organization_id", "source_organization_id"),
        Index("ix_exchange_patient_references_patient_id", "patient_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
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


class LabResultTemplate(Base):
    """DH-LAB-003/ADR-053: a controlled, immutable, versioned result-structure definition scoped
    to one ``LabOffering`` -- **not** to ``TestCatalogEntry``. `TestCatalogEntry` is the provider-
    facing ordered *service* identity; `LabOffering` is one particular lab's own implementation of
    that service, and two labs offering the identical catalog test may legitimately report a
    different observation structure (different methodology, different local reporting format,
    different number of observations) without that being a second ordered-service identity. See
    this class's own ADR for the full reasoning.

    Immutable once created: no field on this row or its ``observations`` children is ever updated
    after creation (`LabResultTemplateRepository` exposes no edit method at all). A structural
    change is always a new row -- `version` N+1 for the same `lab_offering_id` -- never a mutation
    of version N, because a template version may eventually be referenced by historical Patient
    result entry (LAB-004's own forward concern, not implemented here).

    Exactly one ``active=True`` row may exist per `lab_offering_id` at a time (the partial unique
    index below is the database backstop; `LabResultTemplateRepository.activate_template`'s own
    `FOR UPDATE`-locked compare-and-swap is the actual serializing mechanism, mirroring
    `CatalogRepository.set_price`'s established discover-and-swap discipline). An offering may
    legitimately have **zero** active templates (before initial configuration, or between v1's
    retirement and v2's activation in the same transaction) -- `null` is a valid, honestly
    represented state, never fabricated.

    No reference-range field exists here by design (§14 of this ticket): a static low/high pair
    depends on age/sex/methodology/population/instrument facts this product has no approved
    applicability model for yet. No clinical-interpretation field (normal/abnormal/critical) exists
    either -- this table is result *structure*, never decision support."""

    __tablename__ = "exchange_lab_result_templates"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_exchange_lab_result_templates_version"),
        UniqueConstraint(
            "lab_offering_id", "version", name="uq_exchange_lab_result_templates_offering_version"
        ),
        Index(
            "uq_exchange_lab_result_templates_one_active_per_offering",
            "lab_offering_id",
            unique=True,
            postgresql_where=text("active = true"),
            sqlite_where=text("active = 1"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_offerings.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    observations: Mapped[list[LabResultTemplateObservation]] = relationship(
        back_populates="template", order_by="LabResultTemplateObservation.sort_order"
    )


class LabResultTemplateObservation(Base):
    """One controlled observation definition within a ``LabResultTemplate`` version -- pure
    configuration, never a Patient result value (contrast with ``LabObservation``, which LAB-004
    will populate by referencing one of these definitions, never by letting the browser invent
    ``source_code``/``display_label``/``result_type``/``numeric_unit`` at write time -- see this
    ticket's own ADR for that forward contract).

    ``result_type``/``numeric_unit`` column shapes deliberately match ``LabObservation``'s own
    exactly (``String(16)`` CHECK NUMERIC/TEXT, ``String(32)`` unit) so a template value can never
    be too long to copy into the observation LAB-004 eventually derives from it. Same reasoning for
    ``source_code``/``display_label`` matching ``LabObservation.source_code``/
    ``display_label_snapshot``'s own ``String(64)``/``String(256)`` shapes.

    No enumerated ``allowed_values`` for TEXT and no reference range on this table -- both
    deliberately deferred, per this ticket's own scope boundary (§14/§16)."""

    __tablename__ = "exchange_lab_result_template_observations"
    __table_args__ = (
        CheckConstraint(
            "result_type IN ('NUMERIC', 'TEXT')",
            name="ck_exchange_lab_result_template_observations_result_type",
        ),
        CheckConstraint(
            "result_type = 'NUMERIC' OR numeric_unit IS NULL",
            name="ck_exchange_lab_result_template_observations_text_no_unit",
        ),
        CheckConstraint(
            "sort_order >= 0", name="ck_exchange_lab_result_template_observations_sort"
        ),
        UniqueConstraint(
            "result_template_id",
            "source_code",
            name="uq_exchange_lab_result_template_observations_code",
        ),
        UniqueConstraint(
            "result_template_id",
            "sort_order",
            name="uq_exchange_lab_result_template_observations_sort_order",
        ),
        Index("ix_exchange_lab_result_template_observations_template_id", "result_template_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    result_template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_result_templates.id", ondelete="RESTRICT"), nullable=False
    )
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_label: Mapped[str] = mapped_column(String(256), nullable=False)
    result_type: Mapped[str] = mapped_column(String(16), nullable=False)
    numeric_unit: Mapped[str | None] = mapped_column(String(32))
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[LabResultTemplate] = relationship(back_populates="observations")


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
            # DHEALTH-LAB-BLOCKED-001 appends 'BLOCKED'. Purely additive -- no existing value is
            # removed, renamed or retyped, so every row already in this table still satisfies it
            # and no backfill exists or is needed.
            "status IN ('AWAITING_LAB_SELECTION', 'SELECTED', 'ACCEPTED', 'IN_PROGRESS', "
            "'COMPLETED', 'REJECTED', 'CANCELLED', 'BLOCKED')",
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
    # DHEALTH-CLINIC-VISIT-001: the visit this order was raised during, when it was raised during
    # one. **Nullable permanently, not transitionally.** Two distinct populations legitimately have
    # no visit and must keep working forever: every order that predates this ticket, and every
    # standalone diagnostic order a walk-in or a connector raises without a clinic contact. This
    # column is only ever set from the committing `LabOrderIntent`'s own `visit_id` (never supplied
    # directly to the order-commit call, never inferred from the patient or a timestamp) -- see
    # `exchange/order_intents.py`'s `commit` for why the intent is the only safe place to capture
    # it.
    visit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_visits.id", ondelete="RESTRICT"), nullable=True
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
    # DHEALTH-CLINIC-VISIT-001: the visit context, captured **here** rather than at order commit,
    # because this row is the immutable one. The intent is created once inside the visit and is
    # frozen; commit copies this value onto the LabOrder and re-validates nothing. Capturing it at
    # commit instead would mean a lost-response retry could present a different visit and silently
    # move the order, which `docs/NATIVE-OPERATING-PRODUCT.md` §36's linkage rules forbid. It also
    # joins this intent's replay comparison in `exchange/order_intents.py` (alongside patient,
    # practitioner and the item set), so reusing one idempotency_key under a *different* visit is a
    # loud `IdempotencyKeyConflict` rather than a silent return of the other visit's intent.
    visit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_visits.id", ondelete="RESTRICT"), nullable=True
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
        # DHEALTH-LAB-BLOCKED-001: exists solely so `LabOrderBlock` can carry a *composite*
        # foreign key at `(lab_order_id, lab_organization_id)` and have the database refuse a
        # block row naming any laboratory other than the one this order is actually assigned to.
        # Structurally redundant on its own -- `lab_order_id` is already unique below, so the pair
        # trivially is too -- and that is exactly why adding it is safe: it can never reject a row
        # the existing constraint would have accepted. Same shape, and the same reasoning, as
        # DHEALTH-BILLING-001's `uq_exchange_invoices_id_org` behind `exchange_payments`.
        UniqueConstraint(
            "lab_order_id",
            "lab_organization_id",
            name="uq_exchange_lab_selections_order_lab",
        ),
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


#: DHEALTH-SPECIMEN-001: the only order statuses for which a ``LabSpecimen`` row may be created.
#:
#: Deliberately the **same two values** ``BLOCKABLE_ORDER_STATUSES`` already names, and for the
#: same underlying reason -- "work this laboratory has genuinely taken and has not yet released".
#: It is a separate frozenset rather than an alias of that one because the two answer different
#: questions (*may this be blocked?* versus *may a physical sample be recorded against it?*) and a
#: future change to either must not silently move the other; the equality is asserted by a test
#: rather than expressed by sharing the object.
#:
#: **This is not a widening of the block-entry rule**, and it is not a widening of the
#: DH-EX-011 collection milestone rule either. The *first* milestone still may only be created
#: while ``ACCEPTED`` -- ``_record_specimen_in_session`` keeps that check exactly as DH-EX-011
#: wrote it, so a legacy ``IN_PROGRESS``/``COMPLETED`` order that never had a milestone still
#: cannot retroactively acquire one. ``IN_PROGRESS`` appears here only for an **additional**
#: specimen on an order whose milestone already exists, which is the one case the existing
#: workflow genuinely produces: ``LabOrderBlock.resolve`` restores ``blocked_from_status``, and
#: `docs/NATIVE-OPERATING-PRODUCT.md` §16's own worked example is an order blocked *from*
#: ``IN_PROGRESS`` for an unusable specimen. There is no path from ``IN_PROGRESS`` back to
#: ``ACCEPTED``, so an ``ACCEPTED``-only rule would have made a second physical sample
#: unrecordable for exactly the situation §16 describes -- the software would have had to either
#: overwrite the first specimen or pretend the second one did not exist.
SPECIMEN_RECORDABLE_ORDER_STATUSES: frozenset[str] = frozenset(
    {LabOrderStatus.ACCEPTED.value, LabOrderStatus.IN_PROGRESS.value}
)


class LabSpecimen(Base):
    """DHEALTH-SPECIMEN-001: one **physical sample instance** recorded as having been collected in
    service of exactly one ``LabOrder``.

    This is the entity ``LabSampleCollection``'s own docstring says it deliberately is *not*, and
    the distinction between the two rows is the whole content of this ticket:

    * ``LabSampleCollection`` is an **order-level workflow milestone** -- at most one per order,
      the thing ``ACCEPTED -> IN_PROGRESS`` is gated on. It answers *"has this laboratory started
      the collection step for this order?"*
    * ``LabSpecimen`` is a **physical sample instance** -- any number per order, never rewritten
      once written. It answers *"which physical samples has this laboratory recorded for this
      order, when, and on whose authority?"*

    It is not another ``LabOrder``, not a result, not a status, not a barcode, not inventory.

    ── Why ``lab_order_id`` is NOT unique ──

    ``LabSampleCollection.lab_order_id`` carries ``unique=True``, which is correct for a
    once-per-order milestone and **false** as a statement about physical reality. Laboratory
    medicine is not one-order/one-specimen: a CBC and a urine culture cannot share a tube, and a
    sample that turns out to be unusable is replaced by a second sample rather than by an edit to
    the first. A unique constraint here would have encoded "an order has at most one physical
    sample, forever" into the schema, and every later recollection would then have had to either
    overwrite the earlier row or not be recorded at all. Both are lies, and the second is the one
    that gets chosen under deadline pressure.

    This ticket builds no multi-container workflow and no multi-specimen UI. It only refuses to
    make the schema assert something untrue, so that adding either later is not a destructive
    redesign.

    ── ``recorded_at``, and why it is deliberately not ``collected_at`` ──

    The name is inherited from ``LabSampleCollection`` on purpose, and the reasoning is
    unchanged: this is when DHealth's own server clock observed an authenticated laboratory worker
    record this sample, never a caller-supplied or backdated clinical instant. It states *"a lab
    user recorded a physical sample at this instant"*, never *"physical collection definitively
    occurred at this exact historical moment"*. ADR-018's DH-011J2 addendum is the reason the rule
    exists at all -- no reliable native collection signal existed even on the OpenMRS/OpenELIS
    connector track -- and `models.py`'s ``LabOrderStatus`` docstring generalizes it to this whole
    Exchange: never infer a physical-world event from a database timestamp. Introducing a
    ``collected_at`` column here would have quietly reversed that decision under a new ticket's
    cover.

    ── Provenance, all of it server-derived ──

    ``recorded_by_identity_id`` is ``NOT NULL``. Every caller has a real
    ``exchange_identities`` row to attribute: a human laboratory worker through
    ``AuthenticatedPrincipal.identity_id``, and a Connect agent through
    ``ConnectorServicePrincipal.identity_id``, which exists for precisely this purpose (see
    ``connect/service_identity.py``: *"an attribution handle, not a source of permission"*). No
    caller may supply it, and no request body carries it -- a browser that could name the
    collector could attribute a sample to a colleague who never touched it.

    ``lab_organization_id`` is likewise derived, from the URL's laboratory *after*
    ``LabSelection`` has proved that laboratory owns this order. It is stored rather than left to
    be re-derived through the join, matching both sibling tables
    (``LabSampleCollection``/``LabOrderBlock``) exactly: it is the authority under which this row
    was written, and it cannot drift from the selection because ``LabSelection.lab_order_id`` is
    itself unique, so an order has exactly one owning laboratory for its whole life.

    ── ``idempotency_key``: retry safety without a false 1:1 ──

    The hard part. "One specimen per order" would make retries trivially safe and the domain
    false, so retry safety is carried by an explicit client request key instead -- the same
    mechanism, and the same vocabulary, ``LabOrder.idempotency_key`` already established for order
    submission. Scoped ``(lab_order_id, idempotency_key)``: two laboratories' clients generate
    their own keys with no coordination, and the order already fixes the tenant, so a narrower
    scope is unnecessary and a global one would let two orders' keys collide by accident.

    **Nullable, deliberately.** A ``NULL`` key means "recorded through the original DH-EX-011
    keyless path", which is what ``connect/apply.py``'s source-observed collection event and every
    pre-DHEALTH-SPECIMEN-001 script still use. PostgreSQL permits many ``NULL``s in a unique
    constraint, so those rows are not constrained by it -- they are constrained by the keyless
    path's own rule, which is unchanged from DH-EX-011: a keyless call never creates a second
    specimen and never a second milestone. That is a real, recorded gap rather than a hidden one
    (a source LIS that emits two genuine collection events still gets a replay on the second),
    and closing it needs a Connect contract change this ticket does not make.

    ── No lifecycle, no taxonomy, no accession number ──

    There is **no specimen status column** -- no ``COLLECTED``/``RECEIVED``/``REJECTED``/
    ``DISCARDED``/``RECOLLECTED``. A sample that cannot be worked is already expressible: the
    *order* becomes ``BLOCKED`` with a ``LabOrderBlock`` episode naming ``SPECIMEN_UNUSABLE`` or
    ``SPECIMEN_INSUFFICIENT``, and that is an operational statement a worker made, not a clinical
    adequacy judgement DHealth is entitled to record. A second state machine beside it would have
    had to be kept consistent with the first forever, and its vocabulary would have been invented
    here rather than validated by anyone who works in a laboratory.

    There is **no specimen type, container, tube colour, anticoagulant, volume, anatomical source,
    storage location, quality grade or rejection category**. Not "not yet" -- absent, because no
    governed source for any of them exists in this repository: ``TestCatalogEntry`` carries no
    specimen-type field, so the type cannot be derived either, and inventing one is exactly what
    `AGENTS.md`'s "never invent clinical content" rule forbids. The absence is a real product gap
    and is recorded as one.

    There is **no human-facing accession number**. ``id`` is a server-generated UUID, durable and
    suitable for a later label ticket to reference; it is emphatically not an accession identifier
    and nothing renders it as one.

    ── No explicit recollection link ──

    No ``recollection_of_specimen_id``. The question *"was this sample collected because that
    earlier one had to be replaced?"* cannot be answered from the workflow input that exists: the
    collection action carries no such statement, an order may legitimately require more than one
    sample, and inferring the link from two timestamps would be fabricated provenance. Multiple
    immutable rows plus their recorded order preserve the history truthfully -- *how many samples
    exist, when each was recorded, by whom* -- without claiming to know why. Whether the link is
    needed, and where a laboratory would state it, is recorded as a remaining decision.

    ── Immutability ──

    No code path updates a row of this table after insert, and no ``PATCH`` endpoint exists that
    could. There is no void/correct path either: a mistaken record is history, and inventing
    destructive edit semantics for it is recorded as an open decision rather than decided here --
    the same shape ``LabOrderBlock`` chose (*"a block that turned out to be a mistake is resolved
    with a note saying so, not erased"*).

    ── Historical rows ──

    **Nothing is backfilled.** A pre-DHEALTH-SPECIMEN-001 order with a ``LabSampleCollection``
    row and no specimen means exactly *"the collection milestone was recorded; how many physical
    samples there were, and who collected them, is not known to DHealth"*. Manufacturing one
    specimen per such milestone would assert a cardinality of exactly one and an unknown
    collector -- two facts no migration can know -- so the honest absence is preserved instead."""

    __tablename__ = "exchange_lab_specimens"
    __table_args__ = (
        UniqueConstraint(
            "lab_order_id",
            "idempotency_key",
            name="uq_exchange_lab_specimens_order_idempotency_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: Deliberately **not** ``unique`` -- see the class docstring. ``RESTRICT`` matches every
    #: sibling: an order that has physical-sample history cannot be deleted out from under it.
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False
    )
    lab_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    recorded_by_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    #: The client's own retry key, or ``NULL`` for the DH-EX-011 keyless path. Opaque and never
    #: derived from clinical content, exactly as ``LabOrder.idempotency_key`` requires of its own.
    #: No separate index: the unique constraint above is a btree with ``lab_order_id`` leading, so
    #: it already serves both the per-order history read and the per-key replay lookup.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class LabOrderBlock(Base):
    """DHEALTH-LAB-BLOCKED-001: one *episode* of "this laboratory cannot currently continue this
    order", from the moment a worker said so to the moment somebody resolved it.

    An episode, deliberately, and not a pair of columns on ``LabOrder``. `docs/
    NATIVE-OPERATING-PRODUCT.md` §16's worked example is an order blocked for an unusable
    specimen, resolved, worked further, and blocked again for a second, unrelated reason. A
    ``LabOrder.block_reason`` column answers "why is it blocked *now*" and silently destroys the
    first answer the moment the second is written. The operational question a laboratory actually
    asks -- *has this order been trouble before?* -- is only answerable if every episode survives,
    so every episode is its own row and none is ever updated after it is resolved.

    **What is server-derived and what a caller may supply.** A caller supplies exactly two things:
    the ``reason`` (from the closed catalog) and an optional ``note``. Everything else here --
    ``lab_organization_id``, ``blocked_at``, ``blocked_by_identity_id``, ``blocked_from_status``,
    and every ``resolved_*`` field -- is derived from the authenticated principal, the server
    clock, and the order's own locked state. In particular ``blocked_from_status`` is **not** a
    resume destination a client may choose: it is a copy of what the order's status genuinely was
    at the instant it was blocked, and it is the only thing a resolution is ever allowed to
    restore. A client that could name its own resume state could move an order from
    ``ACCEPTED`` to ``IN_PROGRESS`` without a collection milestone, fabricating workflow that
    never happened.

    **At most one unresolved episode per order** --
    ``uq_exchange_lab_order_blocks_one_active_per_order``, a partial unique index over
    ``lab_order_id WHERE resolved_at IS NULL``. The service layer locks the ``LabOrder`` row
    ``FOR UPDATE`` first, exactly as every other lab-workflow mutation in this package does, and
    that lock is what actually serializes two simultaneous block attempts; the index is the second
    layer behind it, not the first -- `exchange_invoice_number_sequences`' own established
    reasoning, applied here.

    **Resolution never rewrites history.** ``resolve`` sets ``resolved_at``/
    ``resolved_by_identity_id`` and optionally ``resolution_note``, and touches nothing else. The
    reason, the note, the actor, the instant and the state it was blocked from all stay exactly as
    recorded. There is no delete path, no ``unblock`` that removes the row, and no ``CANCELLED``
    or ``DISCARDED`` episode status -- a block that turned out to be a mistake is resolved with a
    note saying so, not erased.

    **No specimen is modelled here.** ``SPECIMEN_UNUSABLE`` names why the laboratory stopped; it
    does not identify a tube, create an accession, record a container, or link a recollection to
    its predecessor. ``LabSampleCollection``'s own docstring draws that boundary for the
    collection milestone and this row inherits it unchanged -- a first-class specimen entity is
    **D-3**, still open, and blocking an order is explicitly not a partial down-payment on it.
    """

    __tablename__ = "exchange_lab_order_blocks"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('SPECIMEN_UNUSABLE', 'SPECIMEN_INSUFFICIENT', "
            "'PATIENT_DID_NOT_ATTEND', 'CLARIFICATION_REQUIRED')",
            name="ck_exchange_lab_order_blocks_reason",
        ),
        CheckConstraint(
            # The same two states `BLOCKABLE_ORDER_STATUSES` names, spelled out because a CHECK
            # cannot read a Python frozenset. A block episode recording that it interrupted, say,
            # a COMPLETED order would be a lie the database can refuse outright.
            "blocked_from_status IN ('ACCEPTED', 'IN_PROGRESS')",
            name="ck_exchange_lab_order_blocks_blocked_from_status",
        ),
        CheckConstraint(
            # A resolution has an actor or it has not happened. Neither half is meaningful alone:
            # a `resolved_at` with nobody attached is an event without an owner, and a resolver
            # with no instant is an owner of nothing.
            "(resolved_at IS NULL AND resolved_by_identity_id IS NULL) "
            "OR (resolved_at IS NOT NULL AND resolved_by_identity_id IS NOT NULL)",
            name="ck_exchange_lab_order_blocks_resolution_consistency",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= blocked_at",
            name="ck_exchange_lab_order_blocks_resolved_after_blocked",
        ),
        CheckConstraint(
            # A resolution note on an unresolved episode would be a note about something that has
            # not happened yet.
            "resolution_note IS NULL OR resolved_at IS NOT NULL",
            # Deliberately not spelled "..._resolution_note_requires_resolution": that name is 64
            # characters and PostgreSQL's identifier limit is 63. SQLAlchemy's PostgreSQL dialect
            # refuses an over-long *explicit* constraint name outright, so migration 0037 would
            # fail on the first real `alembic upgrade` while the SQLite unit suite -- which has no
            # identifier limit -- stayed green. Exactly the class of failure
            # `tests/test_migration_hygiene.py` exists because of, one layer down.
            name="ck_exchange_lab_order_blocks_note_requires_resolution",
        ),
        # The tenant boundary, in the database rather than only in the service layer: the pair
        # must name a real `LabSelection`, so a block row can only ever attribute itself to the
        # laboratory this order is genuinely assigned to. A composite FK, not two independent
        # ones -- two separate FKs would each be satisfiable while the *combination* is a lie.
        ForeignKeyConstraint(
            ["lab_order_id", "lab_organization_id"],
            ["exchange_lab_selections.lab_order_id", "exchange_lab_selections.lab_organization_id"],
            name="fk_exchange_lab_order_blocks_selection",
            ondelete="RESTRICT",
        ),
        Index("ix_exchange_lab_order_blocks_lab_order_id", "lab_order_id"),
        Index("ix_exchange_lab_order_blocks_lab_organization_id", "lab_organization_id"),
        Index(
            "uq_exchange_lab_order_blocks_one_active_per_order",
            "lab_order_id",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
            sqlite_where=text("resolved_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Plain FK *as well as* the composite one above: the composite constraint targets
    # `exchange_lab_selections`, so without this the block would have no direct referential link
    # to the order itself, and `exchange_lab_orders` could be deleted out from under it.
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False
    )
    lab_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Optional operational context in the worker's own words. Never required, never parsed, never
    #: interpreted, and never copied into an audit row, a log line or an exception message -- the
    #: identical discipline `VisitNote.body` is held to (`AGENTS.md`, 2026-09-27). `String(1024)`
    #: is matched exactly by the API schema's own `max_length`, because a value longer than the
    #: column raises `DataError` on real PostgreSQL and reaches the caller as a raw 500 while
    #: SQLite, which does not enforce `VARCHAR` length, shows nothing at all (DH-EX-007A's lesson).
    note: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: The order's real status at the instant it was blocked, and therefore the one and only state
    #: a resolution may restore. Never client-supplied -- see this class's own docstring.
    blocked_from_status: Mapped[str] = mapped_column(String(24), nullable=False)
    blocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    blocked_by_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=True
    )
    #: Optional, and held to exactly the same rules as `note` above.
    resolution_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)


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
    # DH-LAB-004/ADR-053 addendum: the exact ``LabResultTemplate`` version this container was
    # pinned to on its first Native write -- set once, at container-creation time, and never
    # changed afterward even if the offering later activates a newer template version (see
    # ``LabResultRepository``'s first-write pinning procedure). ``NULL`` for every container that
    # predates this ticket and for any container created by a non-Native (Connect/legacy)
    # write path that never resolves a template at all -- both are honest absences, never
    # backfilled. ``ON DELETE RESTRICT`` because a pinned historical entry must never be able to
    # silently lose its own provenance by a template row being deleted out from under it (no code
    # anywhere in this codebase deletes a ``LabResultTemplate`` -- this is defense-in-depth, not a
    # response to a real deletion path).
    result_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_lab_result_templates.id", ondelete="RESTRICT"), nullable=True
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
        # DH-LAB-004: at most one *effective* observation per controlled template definition, per
        # container -- the database backstop for the native-value batch write's own replay/conflict
        # discipline (the `LabOrder FOR UPDATE` lock is the actual serializing mechanism; this
        # index is the defense-in-depth for anything that reaches this table any other way,
        # mirroring `supersedes_observation_id`'s own UNIQUE-constraint-as-backstop precedent).
        # Partial: rows with no template definition (legacy/Connect-style free structure) and
        # superseded rows are both excluded on purpose -- neither participates in this rule.
        Index(
            "uq_exchange_lab_observations_one_effective_per_definition",
            "lab_result_id",
            "template_observation_definition_id",
            unique=True,
            postgresql_where=text(
                "template_observation_definition_id IS NOT NULL AND superseded_at IS NULL"
            ),
            sqlite_where=text(
                "template_observation_definition_id IS NOT NULL AND superseded_at IS NULL"
            ),
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
    # DH-LAB-004/ADR-053 addendum: which controlled definition, within the container's own pinned
    # ``result_template_id`` template, this value answers -- never inferred from `source_code`
    # string matching. ``NULL`` for every pre-DH-LAB-004 observation and for any observation
    # recorded through the raw, free-structure path (``record_observation``/correction) under a
    # container that is not template-bound -- both honest absences, never backfilled. ``ON DELETE
    # RESTRICT`` for the identical defense-in-depth reasoning as ``LabResult.result_template_id``.
    template_observation_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_lab_result_template_observations.id", ondelete="RESTRICT"),
        nullable=True,
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
    """**DEPRECATED — COMPATIBILITY-ONLY as of ADR-064 (DHEALTH-RESULT-DELIVERY-001, 2026-09-27).
    Do not read this as authority, do not advance it, and do not write it from new code.**

    Original intent, preserved: *delivery to the ordering provider organization is its own state,
    independent from ``LabResult`` existing -- DH-EX-001 forbids inferring delivery from result
    completion. Starts ``PENDING`` and only an explicit repository call may move it to
    ``DELIVERED``/``FAILED``; one row per order (unique FK), not per item, matching V1's "doctor
    receives the order's result(s) as one unit" framing.*

    **Why it is superseded, and it is structural rather than a matter of taste.** This model was
    created by ``0004_exchange_core`` when it was the *only* delivery concept DHealth had.
    Three more precise owners have since appeared, and ADR-048 changed the shape of the
    underlying fact: a result
    is now released as an immutable **version**, and an amendment produces another. So "the ordering
    provider's system received the result" is necessarily *version*-scoped -- version 1 can be
    delivered while version 2 is still owed. ``lab_order_id`` here is ``unique``, so this
    table holds exactly one status per order and **cannot express that state at all**. It is
    not merely redundant;
    it is the wrong shape for its own stated meaning.

    Who owns each fact instead (ADR-064):

    - a released clinical result exists ....... ``LabResultReleaseVersion``
    - available to a Native provider ......... derived from the release version; no stored state
    - a clinician reviewed one exact version .. ``DoctorReview`` (version-scoped, ADR-048)
    - a provider's own system received it .... ``ConnectorDeliveryObligation`` /
      ``ConnectorDelivery``
      (``connect/runtime_models.py``, unique per ``release_version_id``)
    - the patient was messaged ............... ``PatientNotification`` (``notify/models.py``)

    **The row is still created** by ``exchange/orders.py`` at order creation, inert, and every
    historical row is preserved -- ADR-064 phase A. That is deliberate: three qualified tests
    use the insert as their proof that order creation commits as one aggregate, one of them
    patching this very class as its failure-injection point. Stopping creation would rewrite
    an atomicity proof to remove
    its injection site, for a row nothing reads. Phases B (stop creating) and C (drop the table) are
    named in ADR-064 and left to a future ticket with its own evidence.

    Pinned by ``tests/test_dhealthresultdelivery001_ownership.py``, which fails if a
    production module
    reaches the repository, if a second writer appears, or if someone tries to rescue this model by
    making it version-scoped instead of using the one that already is."""

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
    """MVP organization roles. Validity against
    organization type (PROVIDER: ORG_ADMIN/CLINICIAN; LAB: ORG_ADMIN/LAB_TECH/LAB_RESULT_RELEASER)
    is enforced by ``exchange/membership.py``'s repository layer, not a DB constraint, for the same
    reason ``LabOffering``'s lab-organization-type check is repository-enforced elsewhere in this
    file: SQL cannot portably CHECK a value against a column on a different, joined-through table.

    ``LAB_RESULT_RELEASER`` (DH-EX-012, ADR-044): the explicit result-release authority, distinct
    from ``LAB_TECH``'s result-*entry* authority. ADR-044 binds this as an additive role, not a
    replacement -- the same human may hold both simultaneously (no forced second-person workflow),
    and a membership holding only one of the two may do exactly that one thing (enter, or release)
    and not the other.

    ``BILLING_CLERK`` (DHEALTH-BILLING-001, ADR-066): the money authority, and **only** the
    money authority -- invoices, invoice lines, payments, balances, receipts, and the
    facility's own service prices. It grants no clinical read or write of any kind: no patient
    clinical note, no visit documentation, no diagnostic ordering, no laboratory processing, no
    result entry, no result release, no clinician review, no staff administration, no platform
    administration.

    It is **additive and never implied.** Holding ``ORG_ADMIN``, ``CLINICIAN``, ``LAB_TECH`` or
    ``LAB_RESULT_RELEASER`` does not make a membership a cashier: the role must be granted
    explicitly, through the same ``Équipe et accès`` lifecycle and the same audit as every
    other role. That is the point rather than a detail -- a facility administrator is not
    automatically trusted with the till, and a clinician is not automatically asked to handle
    money.

    Valid at **both** organization types, unlike every other non-admin role here. A laboratory
    charges for the tests it performed and a clinic for the consultation it performed
    (ADR-066's service-performance rule), so both need the same authority over their own
    money.

    **2026-09-28 dated note:** the first sentence of this docstring said "no billing/reception/
    pharmacy roles" until today and is left corrected above rather than silently rewritten --
    reception and pharmacy roles still do not exist."""

    ORG_ADMIN = "ORG_ADMIN"
    CLINICIAN = "CLINICIAN"
    LAB_TECH = "LAB_TECH"
    LAB_RESULT_RELEASER = "LAB_RESULT_RELEASER"
    BILLING_CLERK = "BILLING_CLERK"


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
            "role IN ('ORG_ADMIN', 'CLINICIAN', 'LAB_TECH', 'LAB_RESULT_RELEASER', "
            "'BILLING_CLERK')",
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


class MembershipCapabilityOverride(Base):
    """DHEALTH-ACCESS-CAPABILITIES-001/ADR-067: one capability granted to, or denied from, one
    membership, on top of whatever its role templates give.

    ── Why this table is the whole of "configurable permissions" ──

    A role is a template (``OrganizationRoleAssignment`` above, re-read as a template assignment).
    This table is the exception list, and it is what makes two people with the same job title have
    different access without inventing a third job title. The failure it replaces is
    `FRONT_DESK_WITH_BILLING` / `FRONT_DESK_NO_BILLING` / `ADMIN_WITH_BILLING` — the combinatorial
    role explosion the ticket forbids by name.

    ``effective = (templates ∪ GRANT) − DENY``, and **a DENY always wins** — over a template grant
    and over a direct grant alike. One precedence rule, no ordering subtlety: an access model whose
    answer depends on which row was written first cannot be reasoned about, and the unique
    constraint below means a membership cannot hold two opinions about one capability anyway.

    ── Relational, not a JSON blob ──

    One row per (membership, capability), never a mutable list column. These rows gate security, so
    they need referential integrity, a unique constraint, and the ability to be counted in SQL: the
    last-administrator invariant (§22) is a single query over *effective* authority, and a JSON
    array would force it into a Python loop over members — which is exactly the shape that races.

    ``capability`` is a string rather than a native enum because the catalog is code-defined
    (`exchange/capabilities.py`): the database stores the key and the application refuses any key
    that is not a ``Capability`` member, so a facility cannot invent a permission identifier. There
    is deliberately **no** CHECK constraint enumerating the catalog — that would make adding a
    capability a migration of every deployment, and the enforcement that matters is at the write
    path, where an unknown key is refused before it is ever stored.

    ``ondelete="RESTRICT"`` like every other membership child: an access decision is a record that
    somebody decided something, and it outlives convenience.
    """

    __tablename__ = "exchange_membership_capability_overrides"
    __table_args__ = (
        UniqueConstraint(
            "membership_id",
            "capability",
            name="uq_exchange_membership_capability_overrides_member_cap",
        ),
        CheckConstraint(
            "effect IN ('GRANT', 'DENY')",
            name="ck_exchange_membership_capability_overrides_effect",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organization_memberships.id", ondelete="RESTRICT"), nullable=False
    )
    #: A `Capability` value, e.g. `billing.operate`. Widest catalog key is well under 64.
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    #: `GRANT` or `DENY`. See the class docstring for why there is no third value.
    effect: Mapped[str] = mapped_column(String(8), nullable=False)
    #: Who decided this, kept for the audit trail's benefit as well as the audit table's.
    created_by_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


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


class ProviderLabRoute(Base):
    """DH-CLINIC-006: an explicit DHealth routing configuration answering only "may this provider
    route orders to this DHealth lab organization?" -- never "how does the order physically enter
    that lab's production system?" (deferred to a future pilot-topology ticket). Deliberately
    orthogonal to both existing marketplace concepts: independent of ``LabDirectoryProfile.listed``
    (marketplace visibility) and ``LabDirectoryProfile.accepting_orders`` (current operational
    capacity) -- a route can be active for a lab that is unlisted, and neither flag is ever
    auto-toggled from the other. Also independent of ``LabOffering``, which remains the sole
    capability-matrix source; no test/catalog identifiers are ever copied onto this table.

    Organization-type validity (``provider_organization_id`` must reference a ``PROVIDER``,
    ``lab_organization_id`` must reference a ``LAB``) is enforced by the repository layer, not a
    cross-table DB constraint -- the same "repository is the enforcement point" rule already used
    for ``LabOffering`` and ``LabDirectoryProfile``.

    Launch-1 route configuration is platform-admin-only (see ``exchange/api.py``'s route-management
    endpoint); no provider self-service network onboarding or bilateral invitation/acceptance flow
    exists yet. Deactivation (``active=False``) never deletes, modifies, or hides any already-made
    ``LabSelection`` -- it only prevents *new* provider assignments to that lab; see
    ``LabSelectionRepository.assign_by_provider``'s own fresh re-check at commit time."""

    __tablename__ = "exchange_provider_lab_routes"
    __table_args__ = (
        UniqueConstraint(
            "provider_organization_id",
            "lab_organization_id",
            name="uq_exchange_provider_lab_routes_provider_lab",
        ),
        Index("ix_exchange_provider_lab_routes_lab_organization_id", "lab_organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    lab_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
            "'lab_observation_amended', 'lab_result_amendment_released', "
            # DH-IDENTITY-001: native Patient directory registration. Deliberately not reused for
            # the legacy `patient_reference_bound` event above -- that event already means "a
            # source identity was bound", and stays unchanged; this one means "a new provider-scoped
            # Patient directory record was created", which the legacy bind path also does
            # internally (see `PatientReference`'s own docstring) but does not separately audit as
            # its own event, matching how e.g. a new `LabResultRelease` row does not get its own
            # audit event distinct from `lab_results_released`.
            "'patient_created', "
            # DH-CLINIC-006: platform-admin configuration of a provider's DHealth lab routing.
            # Separate from `lab_order_assigned` above -- that event records a clinician's
            # order-level lab assignment; these record the platform-level relationship that makes
            # such an assignment possible at all.
            "'provider_lab_route_created', 'provider_lab_route_updated', "
            # DH-LAB-003/ADR-053: controlled, LabOffering-scoped Native Lab result-structure
            # template configuration. See `security_audit.py`'s own `SecurityEventType` docstring
            # for the create/activate distinction.
            "'lab_result_template_created', 'lab_result_template_activated', "
            # DH-MVP-ONBOARD-001: facility enrollment, technical discovery, and the refusal of a
            # production route activation to a facility that cannot take the work.
            "'facility_enrollment_created', 'facility_participation_mode_changed', "
            "'facility_onboarding_status_changed', 'facility_technical_discovery_updated', "
            "'facility_interface_capability_recorded', 'facility_onboarding_blocked', "
            "'facility_onboarding_unblocked', 'provider_lab_route_activation_refused', "
            # DH-CONNECT-CONTRACT-001: connector registration, lifecycle, and qualification
            # evidence. One `connector_qualification_recorded` type for both results -- `outcome`
            # already separates a pass from a failure, and a second type would be a synonym.
            "'connector_registered', 'connector_suspended', 'connector_restored', "
            "'connector_retired', 'connector_configuration_changed', "
            "'connector_qualification_recorded', "
            # DH-CONNECT-AGENT-001: the runtime's *security* events, and deliberately only those.
            # A heartbeat is not here -- §64 is explicit that heartbeat is operational telemetry,
            # not a security mutation, and auditing thousands of them a day would bury the handful
            # of rows that matter. What is here: a service identity being issued or cleared, a
            # runtime instance authenticating for the first time (a new principal appearing), and a
            # message refused at the trust boundary. The last mirrors
            # `provider_lab_route_activation_refused` above: a refusal recorded because "who tried
            # to send us this, for which facility, and when" is the question an operator asks.
            "'connector_service_identity_set', 'connector_runtime_connected', "
            "'connector_message_refused', "
            # DH-NOTIFY-001: the two operator acts worth auditing. Every automatic transition --
            # the release-triggered obligation, the dispatcher hand-over, the provider acceptance,
            # the delivery receipt -- is deliberately absent, for the same reason `connector_
            # heartbeat` is: telemetry in a security table buries the rows that matter. That
            # history lives in `patient_notifications`/`patient_notification_attempts`.
            "'patient_notification_requested', 'patient_notification_retry_requested', "
            # DHEALTH-NATIVE-ACCESS-001: the staff access lifecycle -- `role_assigned`'s and
            # `membership_created`'s missing counterparts, which is precisely why
            # DHEALTH-NATIVE-SEC-001's F1 could exist (authority grantable through the product and
            # withdrawable only by direct SQL). `membership_deactivated` deliberately does not say
            # "revoked" or "deleted": the row survives, its status changes, and every historical
            # clinical attribution hanging off it stays attributable. No *refusal* event is added --
            # a cross-organization attempt or a last-administrator lockout is an authorization
            # refusal, not a mutation, and belongs in `exchange/audit.py`'s best-effort file writer
            # for the reason this class's own docstring gives.
            "'membership_deactivated', 'membership_reactivated', 'role_removed', "
            # DHEALTH-CLINIC-VISIT-001: the clinic visit lifecycle. Every one of these is a real
            # state mutation performed by a named clinician, which is the only test this table
            # applies. `visit_note_appended` records *that* a note revision was written and to which
            # visit -- never the text, which is clinical free content and belongs nowhere near an
            # audit row (see `VisitNote`'s own docstring). No `visit_viewed` event: reading is not a
            # mutation, and this table's docstring is explicit that it is not an access log. No
            # refusal events either -- an attempt to complete someone else's visit is an
            # authorization refusal, recorded by `exchange/audit.py`'s best-effort file writer.
            "'visit_created', 'visit_started', 'visit_completed', 'visit_cancelled', "
            "'visit_note_appended', 'visit_reason_amended', "
            # DHEALTH-APPOINTMENTS-001/ADR-073: the four scheduling mutations. All four reuse
            # `resource_type='appointment'` with the booking's own id.
            #
            # `appointment_checked_in` is recorded *in addition to* `visit_created`, not instead of
            # it: the check-in transaction creates a visit through the existing visit path, which
            # audits its own event, and the scheduling fact ("this booking produced that
            # encounter") is a separate question a reader of this table asks. Both rows commit
            # together or neither does.
            #
            # No patient id and no patient name on any of them -- the patient is reachable from the
            # booking, and naming them here would turn this table into a record of who was expected
            # when, which is the same refusal the `'visit'` resource-type line below already makes.
            # There is deliberately no `appointment_viewed`: reading is not a mutation (D-8).
            "'appointment_created', 'appointment_rescheduled', 'appointment_cancelled', "
            "'appointment_checked_in', "
            # DHEALTH-BILLING-001: the four financial mutations. No amount is recorded here --
            # the authoritative figure lives on the immutable Invoice/Payment row, and a second
            # copy could diverge from it. No void, no reversal (neither policy is decided), and
            # no read event (D-8).
            "'clinic_service_created', 'clinic_service_price_changed', 'invoice_created', "
            "'payment_recorded', "
            # DHEALTH-LAB-BLOCKED-001: the two laboratory workflow mutations. Both reuse
            # `resource_type='lab_order'` with the *order* id, matching every lab event above --
            # audit stays order-centric, never per-episode. The reason key is deliberately **not**
            # copied here: DHEALTH-BILLING-001's own line below records no amount for exactly this
            # reason, and the authoritative value lives on the `exchange_lab_order_blocks` row,
            # where a second copy cannot diverge from it. The free-text note is not here either,
            # and never will be -- see `LabOrderBlock.note`.
            "'lab_order_blocked', 'lab_order_block_resolved', "
            # DHEALTH-ACCESS-CAPABILITIES-001/ADR-067: member-specific capability overrides.
            #
            # Three types rather than one with a payload field, because the *effect* is the part a
            # reader of this table is looking for: "who was granted the till" and "who was denied
            # it" are different questions, and making them the same event with a discriminator
            # column buried inside would mean neither can be answered by a `WHERE event_type`.
            # The `capability` column below says which authority; `membership_id` says whose.
            #
            # Deliberately **no** event for assigning a role template: that is `role_assigned`,
            # which already exists and already means exactly this, now that a role *is* a template.
            "'capability_granted', 'capability_denied', 'capability_override_cleared', "
            # DHEALTH-SPECIMEN-001: one physical sample instance was recorded. Distinct from
            # `lab_sample_collection_recorded` above rather than reusing it, because the two are
            # genuinely different facts and the first collection writes both: the milestone row was
            # created *once* for this order, and a specimen row was created *now*. An additional
            # specimen recorded after a block writes only this one -- reusing the milestone event
            # for it would claim a milestone had been created when none was.
            #
            # `resource_type='lab_order'` with the *order* id, matching every lab event above,
            # including `lab_order_blocked`, whose episode row has its own UUID and its own by-id
            # endpoint and still deliberately did not get a `lab_order_block` resource type. The
            # specimen row itself is the authoritative record of which specimen, when, and by whom
            # -- it carries `recorded_by_identity_id` and `recorded_at` -- exactly as the block
            # episode row is the authoritative record of its own reason and note.
            "'lab_specimen_recorded'"
            ")",
            name="ck_exchange_security_audit_events_event_type",
        ),
        CheckConstraint(
            "outcome IN ('created', 'returned_existing', 'updated', 'unchanged', 'refused')",
            name="ck_exchange_security_audit_events_outcome",
        ),
        CheckConstraint(
            "role IS NULL OR role IN ('ORG_ADMIN', 'CLINICIAN', 'LAB_TECH', "
            "'LAB_RESULT_RELEASER', 'BILLING_CLERK')",
            name="ck_exchange_security_audit_events_role",
        ),
        CheckConstraint(
            "resource_type IS NULL OR resource_type IN "
            "('catalog_entry', 'lab_directory_profile', 'lab_offering', 'lab_price', 'lab_order', "
            "'patient_access_grant', 'patient_reference', 'patient', 'provider_lab_route', "
            "'lab_result_template', 'facility_enrollment', "
            "'facility_interface_capability', 'facility_enrollment_blocker', "
            # DH-CONNECT-CONTRACT-001: qualification hangs off an installation, so every connector
            # event names the installation rather than the qualification or the capability row.
            # DH-CONNECT-AGENT-001 keeps that convention -- a refused message names the
            # installation it claimed to be, not the delivery row, because a refused message has no
            # delivery row: refusing it is precisely the decision not to create one.
            # DH-NOTIFY-001: attempts hang off a notification, so audit stays notification-centric
            # rather than per-attempt -- the same convention as the connector line above.
            # DHEALTH-CLINIC-VISIT-001: note revisions hang off a visit, so audit stays
            # visit-centric rather than per-note-revision -- the same convention as the connector
            # and notification lines above. There is deliberately no 'patient' or 'visit_note'
            # resource type added here: the patient is reachable from the visit, and naming the
            # patient on every visit event would turn this table into a record of who was seen when.
            "'connector_installation', 'patient_notification', 'visit', "
            # DHEALTH-BILLING-001: payments hang off an invoice, so audit stays
            # invoice-centric -- the same convention as every line above.
            "'invoice', 'clinic_service', "
            # DHEALTH-APPOINTMENTS-001: a scheduling event names the booking, never the patient and
            # never the visit it produced -- both are reachable from it, and the `'visit'` line
            # above already states why this table does not record who was seen when.
            "'appointment')",
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
    # DHEALTH-ACCESS-CAPABILITIES-001/ADR-067: *which* authority a capability event was about.
    #
    # A column of its own rather than reuse of `role`, because they are different vocabularies and
    # `role` carries a CHECK constraint naming the five role templates -- storing
    # `billing.operate` there would either break that constraint or force it open, and a column
    # that means two things is a column nobody can query. Nullable: every pre-existing event type
    # leaves it empty, and most always will.
    #
    # No CHECK enumerating the catalog, for the reason `MembershipCapabilityOverride.capability`
    # gives: the catalog is code-defined, adding one would make a new capability a migration for
    # every deployment, and an audit row is a record of what happened rather than a place to
    # re-validate it.
    capability: Mapped[str | None] = mapped_column(String(64), nullable=True)
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


# ── DH-MVP-ONBOARD-001: facility enrollment and integration discovery ────────────────────────
#
# Two questions that this repository previously had no way to answer, and which are deliberately
# kept apart because conflating them is how a network claims a facility is connected when it is
# merely known:
#
#   Organizational enrollment -- who is this facility on the DHealth network?
#   Technical integration     -- how does their existing software talk to DHealth, if at all?
#
# An `Organization` row answers neither. It says a name and a type exist. Everything below exists
# so "Laboratoire Central is enrolled" and "DHealth can route production orders to Laboratoire
# Central" are separately representable, separately audited, and never inferred from each other.


class ParticipationMode(StrEnum):
    """Which software actually runs this facility's diagnostic workflow.

    Chosen explicitly per facility, never inferred -- entering a LIS vendor name does not make a
    facility CONNECT, and ADR-043 Law 2 (no double entry) means these are alternatives rather than
    a spectrum: a facility either works in DHealth or keeps its own system and connects it.
    """

    NATIVE = "NATIVE"
    CONNECT = "CONNECT"


class OnboardingStatus(StrEnum):
    """Where a facility is between "agreed to participate" and "production routing enabled".

    The distinctions this preserves are the entire point; collapsing them into ACTIVE is what the
    ticket forbids and what would let an operator believe a facility is live because a row exists:

        facility exists  !=  facility configured  !=  interface discovered
                         !=  connector exists     !=  connector tested
                         !=  production ready

    ``READY_FOR_ROUTING`` is reachable only by a NATIVE facility in this release, because DHealth
    itself is a Native facility's workflow endpoint and no connector exists yet for anything else.
    That restriction is enforced in ``facility_enrollment.py``, not merely documented here.
    """

    DRAFT = "DRAFT"
    FACILITY_VERIFIED = "FACILITY_VERIFIED"
    ORGANIZATION_READY = "ORGANIZATION_READY"
    CONFIGURATION_IN_PROGRESS = "CONFIGURATION_IN_PROGRESS"
    TECHNICAL_DISCOVERY = "TECHNICAL_DISCOVERY"
    AWAITING_CONNECTOR = "AWAITING_CONNECTOR"
    READY_FOR_QUALIFICATION = "READY_FOR_QUALIFICATION"
    READY_FOR_ROUTING = "READY_FOR_ROUTING"
    BLOCKED = "BLOCKED"


class IntegrationFeasibility(StrEnum):
    """A technical conclusion about a CONNECT facility's existing software. Not a sales label.

    ``CONNECTABLE`` means only: the documented interfaces appear capable of supporting a DHealth
    connector. It does not mean a connector exists, and it never means one has been qualified.
    """

    DISCOVERY_REQUIRED = "DISCOVERY_REQUIRED"
    CONNECTABLE = "CONNECTABLE"
    CONNECTOR_DEVELOPMENT_REQUIRED = "CONNECTOR_DEVELOPMENT_REQUIRED"
    NOT_SAFELY_INTEGRABLE = "NOT_SAFELY_INTEGRABLE"


class HostingModel(StrEnum):
    """Where the facility's existing system runs. ``UNKNOWN`` is a real, accepted answer: a clinic
    manager genuinely may not know, and refusing to record that would push operators into guessing,
    which is worse than an honest gap."""

    ON_PREMISE = "ON_PREMISE"
    CLOUD = "CLOUD"
    HYBRID = "HYBRID"
    UNKNOWN = "UNKNOWN"


class InterfaceType(StrEnum):
    """A kind of interface a facility's existing system may expose.

    ``DATABASE_READ`` is representable because vendors do offer it and pretending otherwise would
    make operators record something false. It is never the preferred route, and nothing in this
    repository writes to a partner database.
    """

    REST_API = "REST_API"
    FHIR = "FHIR"
    HL7_V2 = "HL7_V2"
    WEBHOOK = "WEBHOOK"
    SFTP = "SFTP"
    FILE_IMPORT_EXPORT = "FILE_IMPORT_EXPORT"
    DATABASE_READ = "DATABASE_READ"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class InterfaceVerificationState(StrEnum):
    """How much DHealth actually knows about a reported interface.

    The gap between ``REPORTED`` and ``VERIFIED`` is the most load-bearing distinction in Connect
    onboarding: a partner saying "we have FHIR" is a claim, and treating a claim as a capability is
    how an integration project discovers in week six that the endpoint never existed.

    ``VERIFIED`` here means exactly one thing: DHealth staff confirmed the interface exists as
    documented. It says nothing about whether a DHealth connector exists for it, and nothing about
    whether the full diagnostic contract has passed qualification -- that is DH-CONNECT's word to
    use, not this one.
    """

    REPORTED = "REPORTED"
    DOCUMENTED = "DOCUMENTED"
    VERIFIED = "VERIFIED"
    UNSUPPORTED = "UNSUPPORTED"


class EnrollmentBlockerCode(StrEnum):
    """Why a facility cannot progress, as a closed set rather than free text.

    Structured because an operator needs to filter and act on these, and because a free-text field
    on an onboarding record is a place PHI eventually lands. The accompanying note is bounded and
    operational -- vendor names, ticket references, what DHealth is waiting for.
    """

    VENDOR_DOCUMENTATION_UNAVAILABLE = "VENDOR_DOCUMENTATION_UNAVAILABLE"
    VENDOR_CONTACT_REQUIRED = "VENDOR_CONTACT_REQUIRED"
    REQUIRED_INTERFACE_UNAVAILABLE = "REQUIRED_INTERFACE_UNAVAILABLE"
    MISSING_RESULT_RELEASE_EVENT = "MISSING_RESULT_RELEASE_EVENT"
    UNKNOWN_PATIENT_IDENTIFIER_BEHAVIOR = "UNKNOWN_PATIENT_IDENTIFIER_BEHAVIOR"
    CONNECTOR_NOT_IMPLEMENTED = "CONNECTOR_NOT_IMPLEMENTED"
    FACILITY_CONFIGURATION_INCOMPLETE = "FACILITY_CONFIGURATION_INCOMPLETE"
    CLINICAL_TEMPLATE_SIGNOFF_REQUIRED = "CLINICAL_TEMPLATE_SIGNOFF_REQUIRED"


class FacilityEnrollment(Base):
    """One facility's participation in the DHealth network. Exactly one row per ``Organization``.

    Separate from ``Organization`` rather than columns on it, because the two answer different
    questions and have different lifetimes: an organization is an identity that persists, while an
    enrollment is a process with a state machine, discovery fields that only a CONNECT facility
    has, and its own audit trail.

    The CONNECT discovery fields live here as explicit nullable columns rather than in a JSON blob:
    several of them gate go-live decisions, and a fact that decides whether production orders may
    be routed somewhere should be a validated column a query can filter, not a key in a document
    nobody can constrain.

    No credential, API key, password or connection secret is stored here or anywhere adjacent. The
    fields below describe *what exists*; how DHealth would authenticate to it is DH-CONNECT's
    problem and belongs to a secrets architecture that does not yet exist.
    """

    __tablename__ = "exchange_facility_enrollments"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_exchange_facility_enrollments_organization"),
        CheckConstraint(
            "participation_mode IN ('NATIVE', 'CONNECT')",
            name="ck_exchange_facility_enrollments_participation_mode",
        ),
        CheckConstraint(
            "onboarding_status IN ('DRAFT', 'FACILITY_VERIFIED', 'ORGANIZATION_READY', "
            "'CONFIGURATION_IN_PROGRESS', 'TECHNICAL_DISCOVERY', 'AWAITING_CONNECTOR', "
            "'READY_FOR_QUALIFICATION', 'READY_FOR_ROUTING', 'BLOCKED')",
            name="ck_exchange_facility_enrollments_onboarding_status",
        ),
        CheckConstraint(
            "integration_feasibility IS NULL OR integration_feasibility IN "
            "('DISCOVERY_REQUIRED', 'CONNECTABLE', 'CONNECTOR_DEVELOPMENT_REQUIRED', "
            "'NOT_SAFELY_INTEGRABLE')",
            name="ck_exchange_facility_enrollments_integration_feasibility",
        ),
        CheckConstraint(
            "hosting_model IS NULL OR hosting_model IN "
            "('ON_PREMISE', 'CLOUD', 'HYBRID', 'UNKNOWN')",
            name="ck_exchange_facility_enrollments_hosting_model",
        ),
        Index("ix_exchange_facility_enrollments_status", "onboarding_status"),
        Index("ix_exchange_facility_enrollments_mode", "participation_mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    participation_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    onboarding_status: Mapped[str] = mapped_column(String(32), nullable=False)

    # CONNECT technical discovery. All nullable: a NATIVE facility never fills these in, and a
    # CONNECT facility legitimately starts with every one of them unknown.
    integration_feasibility: Mapped[str | None] = mapped_column(String(32))
    existing_system_name: Mapped[str | None] = mapped_column(String(256))
    existing_system_vendor: Mapped[str | None] = mapped_column(String(256))
    existing_system_version: Mapped[str | None] = mapped_column(String(64))
    hosting_model: Mapped[str | None] = mapped_column(String(16))
    technical_contact: Mapped[str | None] = mapped_column(String(256))
    vendor_contact: Mapped[str | None] = mapped_column(String(256))
    documentation_reference: Mapped[str | None] = mapped_column(String(512))

    # Identifier and timestamp discovery (§30/§31) -- capability, never clinical content. These
    # record whether the source system *has* such a concept, so the future Connect contract knows
    # what it can correlate and which source event times actually exist. A missing source timestamp
    # is never to be filled in from a nearby one, which is exactly why its absence is recorded.
    patient_identifier_namespace: Mapped[str | None] = mapped_column(String(256))
    order_identifier_concept: Mapped[str | None] = mapped_column(String(256))
    report_identifier_concept: Mapped[str | None] = mapped_column(String(256))
    specimen_identifier_concept: Mapped[str | None] = mapped_column(String(256))
    exposes_source_event_timestamps: Mapped[bool | None] = mapped_column(Boolean)
    exposes_professional_release_event: Mapped[bool | None] = mapped_column(Boolean)

    notes: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class FacilityInterfaceCapability(Base):
    """One interface a facility's existing system is said to expose, and how well DHealth knows it.

    A child row rather than a column set, because a facility commonly reports several and each
    carries its own verification state -- "FHIR documented, HL7 v2 reported, REST unknown" is the
    normal shape of a real discovery conversation, and flattening it would lose which claim was
    actually checked.
    """

    __tablename__ = "exchange_facility_interface_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "facility_enrollment_id",
            "interface_type",
            name="uq_exchange_facility_interface_capabilities_enrollment_type",
        ),
        CheckConstraint(
            "interface_type IN ('REST_API', 'FHIR', 'HL7_V2', 'WEBHOOK', 'SFTP', "
            "'FILE_IMPORT_EXPORT', 'DATABASE_READ', 'OTHER', 'UNKNOWN')",
            name="ck_exchange_facility_interface_capabilities_type",
        ),
        CheckConstraint(
            "verification_state IN ('REPORTED', 'DOCUMENTED', 'VERIFIED', 'UNSUPPORTED')",
            name="ck_exchange_facility_interface_capabilities_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    facility_enrollment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_facility_enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    interface_type: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_state: Mapped[str] = mapped_column(String(16), nullable=False)
    # What the partner called it, and where the evidence lives. Never a credential or endpoint
    # secret -- a URL to vendor documentation is a reference, not a way in.
    detail: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class FacilityEnrollmentBlocker(Base):
    """One structured reason a facility cannot progress, open until someone resolves it.

    Resolution is a timestamp rather than a delete: what blocked a facility, and for how long, is
    exactly the history an operator needs when a partner asks why go-live slipped.
    """

    __tablename__ = "exchange_facility_enrollment_blockers"
    __table_args__ = (
        CheckConstraint(
            "code IN ('VENDOR_DOCUMENTATION_UNAVAILABLE', 'VENDOR_CONTACT_REQUIRED', "
            "'REQUIRED_INTERFACE_UNAVAILABLE', 'MISSING_RESULT_RELEASE_EVENT', "
            "'UNKNOWN_PATIENT_IDENTIFIER_BEHAVIOR', 'CONNECTOR_NOT_IMPLEMENTED', "
            "'FACILITY_CONFIGURATION_INCOMPLETE', 'CLINICAL_TEMPLATE_SIGNOFF_REQUIRED')",
            name="ck_exchange_facility_enrollment_blockers_code",
        ),
        Index(
            "ix_exchange_facility_enrollment_blockers_open",
            "facility_enrollment_id",
            postgresql_where=text("resolved_at IS NULL"),
            sqlite_where=text("resolved_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    facility_enrollment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_facility_enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    # Bounded and operational: what DHealth is waiting for, from whom. Never clinical content and
    # never a credential.
    note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- DHEALTH-CLINIC-VISIT-001: the native clinic visit / encounter ---------------------------


class VisitStatus(StrEnum):
    """The clinic visit lifecycle, exactly as ``docs/NATIVE-OPERATING-PRODUCT.md`` §12 records it,
    minus the two states that specification's own first-ticket brief (§36) omits.

    ``PLANNED`` and ``NO_SHOW`` are **deliberately absent, not forgotten.** Both exist only with a
    calendar: ``PLANNED`` means "a booked appointment that has not arrived", and ``NO_SHOW`` exists
    (§12's words) "for ``PLANNED`` to be honest". No appointment table exists in this codebase, so
    nothing could ever put a visit into either -- and §12's own rejected-states list names "a state
    reachable by nothing" as decoration. They are added by the appointments ticket, together with
    the table that makes them reachable, or not at all.

    **There is no ``IN_PROGRESS`` -> ``CANCELLED`` transition.** §12 is explicit: CANCELLED is
    reachable "From ``PLANNED`` or ``CHECKED_IN``. Never from ``IN_PROGRESS`` -- that is a
    completed-with-note." A clinician who has begun seeing a patient and then must stop completes
    the visit and says why in the note; the fact that contact happened is not erasable by a status.

    **Nothing here is ever entered by the system.** Every transition is an explicit clinician act
    (``exchange/visits.py``). In particular ``COMPLETED`` is never reached by a lab order finishing,
    a timer, or a date rolling over -- §12's "The clinician says so; nothing auto-completes."
    """

    CHECKED_IN = "CHECKED_IN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class Visit(Base):
    """DHEALTH-CLINIC-VISIT-001: one outpatient clinic contact between a provider organization's
    clinician and one of that organization's own native ``Patient`` directory records.

    This is the first row in this module that represents *care happening* rather than a transaction
    being exchanged, and it is deliberately the narrowest possible such row: who, when, why in the
    patient's own words, one clinician's free-text note, and whatever diagnostic orders were raised.
    It is **not** an encounter in the HL7/FHIR sense, not a longitudinal chart, and not an EMR --
    see this module's own 2026-09-27 dated docstring note for why that boundary still holds.

    **Scoped to a native ``Patient``, never a ``PatientReference``.** A visit is a thing that
    happens to a person the provider organization itself registered and can see in its own
    directory; ``PatientReference`` is a *provenance binding* for one exchanged transaction
    (possibly created by a connector on behalf of a foreign source system) and one human may hold
    several. Hanging a visit off a reference would mean the same person's two visits could be
    unrelatable, which is precisely the ambiguity ADR-051 forbids resolving by guesswork.

    The composite ``ForeignKeyConstraint((patient_id, provider_organization_id))`` targeting
    ``Patient``'s ``uq_exchange_patients_id_provider`` is the point of this table's tenant safety:
    the **database itself** rejects a visit that names a patient belonging to a different provider
    organization, rather than trusting every present and future call site to have checked. This is
    the same mechanism, and the same reasoning, as ``PatientReference``'s composite link
    (DH-IDENTITY-001 §8).

    ``clinician_identity_id`` is nullable because ``CHECKED_IN`` truthfully has no clinician yet --
    the waiting list is that state (§12), and who will see the patient is not known at check-in.
    It is set, once, when a clinician takes the visit. A visit that was never taken and was
    cancelled keeps a NULL clinician forever, which is the honest record.

    ``idempotency_key`` follows the ``(provider_organization_id, idempotency_key)`` uniqueness shape
    ``LabOrder``/``LabOrderIntent``/``PatientRegistrationRequest`` already established, for the
    identical cross-tenant reason given in those docstrings: a bare global-unique key lets two
    organizations' clients collide by accident and receive each other's row.

    The timestamp CHECK constraints below make the state machine's *consequences* non-forgeable at
    the database level rather than merely conventional: a ``COMPLETED`` row without a
    ``completed_at`` (or the reverse) cannot exist, so no later code path can leave a visit in a
    state whose history contradicts it.
    """

    __tablename__ = "exchange_visits"
    __table_args__ = (
        UniqueConstraint(
            "provider_organization_id",
            "idempotency_key",
            name="uq_exchange_visits_provider_idempotency_key",
        ),
        ForeignKeyConstraint(
            ["patient_id", "provider_organization_id"],
            ["exchange_patients.id", "exchange_patients.provider_organization_id"],
            name="fk_exchange_visits_patient_same_provider",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('CHECKED_IN', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
            name="ck_exchange_visits_status",
        ),
        # A started visit has a start time; an unstarted one does not. `CANCELLED` is reachable only
        # from `CHECKED_IN` (see `VisitStatus`), so it is on the unstarted side of this constraint.
        CheckConstraint(
            "(status IN ('IN_PROGRESS', 'COMPLETED')) = (started_at IS NOT NULL)",
            name="ck_exchange_visits_started_at_matches_status",
        ),
        CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)",
            name="ck_exchange_visits_completed_at_matches_status",
        ),
        CheckConstraint(
            "(status = 'CANCELLED') = (cancelled_at IS NOT NULL)",
            name="ck_exchange_visits_cancelled_at_matches_status",
        ),
        # A clinician is required exactly once the visit has been taken, and a taken visit can
        # never lose one -- `COMPLETED` is reachable only through `IN_PROGRESS`.
        CheckConstraint(
            "status NOT IN ('IN_PROGRESS', 'COMPLETED') OR clinician_identity_id IS NOT NULL",
            name="ck_exchange_visits_taken_visit_has_clinician",
        ),
        # The worklist query: this organization's visits in one state, newest first (§13).
        Index("ix_exchange_visits_provider_status", "provider_organization_id", "status"),
        # The patient-chart query: one patient's visit history.
        Index("ix_exchange_visits_provider_patient", "provider_organization_id", "patient_id"),
        # DHEALTH-FRONT-DESK-001/ADR-069: **at most one active visit per patient per provider.**
        #
        # A *partial* unique index, not a constraint, because the uniqueness holds only over the
        # two live states: a patient who was seen last week and comes back today must be able to,
        # so `COMPLETED` and `CANCELLED` rows are unlimited and excluded from the index entirely.
        #
        # This is the database's half of the invariant and it is the load-bearing half. The service
        # pre-reads for an active visit, but two reception workers checking the same patient in at
        # the same instant both pass that read -- the classic check-then-act race -- and only a
        # unique index can refuse the second write. `exchange/visits.py`'s `start` catches the
        # resulting `IntegrityError` and turns it into a 409, so the loser is told rather than
        # 500'd.
        #
        # Same `postgresql_where`/`sqlite_where` pair 0034's order-visit index already uses, so the
        # unit suite (SQLite, built by `metadata.create_all`) enforces it too rather than
        # qualifying it only against PostgreSQL.
        Index(
            "uq_exchange_visits_one_active_per_patient",
            "provider_organization_id",
            "patient_id",
            unique=True,
            postgresql_where=text("status IN ('CHECKED_IN', 'IN_PROGRESS')"),
            sqlite_where=text("status IN ('CHECKED_IN', 'IN_PROGRESS')"),
        ),
        # DHEALTH-APPOINTMENTS-001: exists solely so `Appointment` can carry a *composite* foreign
        # key at `(visit_id, provider_organization_id)` and have the database refuse a booking that
        # links a visit belonging to any clinic other than the one that owns the booking.
        #
        # Structurally redundant on its own -- `id` is already the primary key, so the pair
        # trivially is unique too -- and that is exactly why adding it is safe: it can never reject
        # a row the primary key would have accepted. Same shape, and the same reasoning, as
        # DHEALTH-LAB-BLOCKED-001's `uq_exchange_lab_selections_order_lab` and
        # DHEALTH-BILLING-001's `uq_exchange_invoices_id_org`.
        UniqueConstraint(
            "id",
            "provider_organization_id",
            name="uq_exchange_visits_id_provider",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # NULL until a clinician takes the visit; never cleared afterwards.
    clinician_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VisitStatus.CHECKED_IN.value
    )
    # The patient's stated reason, as stated. Never a diagnosis, never interpreted, never coded --
    # a code system is D-5 in `docs/NATIVE-OPERATING-PRODUCT.md` §28, not this ticket's decision.
    #
    # **Nullable since DHEALTH-FRONT-DESK-001/ADR-069**, and the nullability is the honest shape
    # rather than a relaxation. Before ADR-069 the only caller was a clinician, who always had a
    # reason to record. Now an arrival may be registered by somebody at the desk who holds
    # `VISIT_CHECK_IN` and no clinical authority at all: that person is not asked for a reason
    # (§15 -- a chief complaint collected at reception is clinical documentation by a different
    # name), and NULL is the truthful record of "nobody stated one here". A non-empty string means
    # somebody actually typed it; an empty string is refused by the service so the two cannot blur.
    reason_for_visit: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    notes: Mapped[list[VisitNote]] = relationship(back_populates="visit")


class VisitNote(Base):
    """One append-only revision of a visit's clinical note.

    **Append-only, and that is the whole concurrency design.** A note is never updated in place and
    never deleted; correcting or extending a note inserts a new revision, and the highest
    ``revision_number`` is the current text. Two clinicians editing the same visit therefore cannot
    silently overwrite each other -- the loser of the race violates
    ``uq_exchange_visit_notes_visit_revision`` and is told, rather than having its text vanish. This
    is the narrowest mechanism that gives that guarantee: no row versioning framework, no
    fingerprint column, no optimistic-concurrency token, because the immutability of a row already
    provides it. It is also the same discipline ADR-045/048 established for corrected and amended
    lab results -- in this codebase a clinical statement's history is never rewritten, and a visit
    note is a clinical statement.

    ``revision_number`` exists rather than ordering by ``created_at`` alone because two appends
    inside the same clock tick would otherwise have no defined order, and "which text is current"
    must never be ambiguous.

    ``author_identity_id`` is NOT NULL: an unattributed clinical note is not a record of anything.
    It is the identity that wrote *this revision*, deliberately not the visit's clinician -- a
    covering clinician may append to a visit, and the note must say who actually wrote it.

    The body is free text and is never sent to an audit row, a log line, an error message, or any
    third-party service (``AGENTS.md``'s absolute rule). ``exchange/visits.py`` audits that a note
    was appended and to which visit -- never what it said.
    """

    __tablename__ = "exchange_visit_notes"
    __table_args__ = (
        UniqueConstraint(
            "visit_id", "revision_number", name="uq_exchange_visit_notes_visit_revision"
        ),
        CheckConstraint("revision_number >= 1", name="ck_exchange_visit_notes_revision_positive"),
        Index("ix_exchange_visit_notes_visit_id", "visit_id", "revision_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    visit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_visits.id", ondelete="RESTRICT"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    author_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    visit: Mapped[Visit] = relationship(back_populates="notes")


# --- DHEALTH-APPOINTMENTS-001: the clinic appointment (rendez-vous) ------------------------------


class AppointmentStatus(StrEnum):
    """Two states, and the shortest honest argument for why there are not more.

    ``SCHEDULED`` means *the patient is expected*. ``CANCELLED`` means *they are no longer
    expected*. Everything else a scheduling product usually carries is either derived from the
    booking's ``visit_id`` or is a policy nobody has written down.

    **Arrival is not a state here.** "Has this patient arrived?" is answered by ``visit_id IS NOT
    NULL`` and, beyond that, by the linked ``Visit``'s own status -- which is the authoritative
    record of attendance and clinical work. An ``ARRIVED`` column would be a second copy of that
    fact, free to disagree with it; ``docs/NATIVE-OPERATING-PRODUCT.md`` §12 already rejects
    ``ARRIVED`` as decoration for the same reason on the visit side.

    **``NO_SHOW`` is deliberately absent, and this is a decision rather than an omission.** A
    no-show is an operational judgement, not a timestamp comparison: how late is late, who may
    record it, whether a telephone call from the patient changes it, whether a reschedule
    supersedes it. None of that policy exists in this product, and ADR-073 records it as an open
    Human Decision. What the system *can* say truthfully -- and all it says -- is "this booking's
    time has passed and no visit is linked". Nothing in this codebase converts that observation
    into a stored state, and ``test_dhealthappointments001_appointments.py`` has a guard asserting
    the enum stays at two values.

    **No ``RESCHEDULED`` either.** Rescheduling moves ``scheduled_at`` on the same row and writes an
    ``appointment_rescheduled`` audit event carrying the old and new instants. A terminal
    ``RESCHEDULED`` state plus a replacement row would make one booking look like two in every
    day view, and the audit trail already answers "when was this moved, by whom, from what".
    """

    SCHEDULED = "SCHEDULED"
    CANCELLED = "CANCELLED"


class Appointment(Base):
    """One booked clinic encounter: *this patient is expected at this clinic at this instant*.

    ── An appointment is not a visit ──

    This is the load-bearing distinction of DHEALTH-APPOINTMENTS-001 and the reason this table
    exists at all rather than a ``PLANNED`` visit status. A booking is an **expectation**; a
    ``Visit`` is an **encounter**. Conflating them would mean a patient who never arrives still has
    a visit row in their clinical history, and that the waiting list and the one-active-visit
    invariant both have to learn to ignore rows for people who are not in the building.

    ``docs/NATIVE-OPERATING-PRODUCT.md`` §12 and two earlier ADRs expected the opposite -- that this
    ticket would add ``PLANNED`` and ``NO_SHOW`` to ``VisitStatus``. **ADR-073 supersedes that**
    deliberately and in writing rather than by silence; see it for the full argument. ``VisitStatus``
    is unchanged at four states, and a structural test asserts it.

    ── What a walk-in looks like ──

    Nothing. A walk-in is a ``Visit`` with no ``Appointment`` row pointing at it, which is why the
    link lives on *this* table and not on ``Visit``: the overwhelmingly common case needs no column
    at all, and ``exchange_visits`` keeps no nullable field that is empty for most of its rows.
    §11's field table proposed ``Visit.appointment_id``; ADR-073 §4 records the inversion and why.

    ── Patient identity is a reference, never a snapshot ──

    There is no copied name, date of birth or telephone number. A day view renders the patient's
    *current* directory truth through ``patient_id``, so a name corrected on Tuesday shows corrected
    on Wednesday's list. This is not a frozen scheduling document and ADR-073 says so explicitly --
    unlike a released laboratory report (ADR-072), which genuinely is one.

    ── No duration, no clinician, no reason, no capacity ──

    Each omission is argued in ADR-073 §6 and each is an open Human Decision rather than a gap:
    duration and clinician because §7 gives clinicians only "LTD -- own schedule" and no workflow
    here needs either; a free-text reason because "who may read it, and is it operational or
    clinical" has no governed answer and front-desk scheduling notes must not silently become
    clinical documentation; capacity because without a duration or a resource model the system
    cannot truthfully say an instant is full. **Two patients may legitimately share an instant and
    no constraint here prevents it.**
    """

    __tablename__ = "exchange_appointments"
    __table_args__ = (
        # Create idempotency, scoped per clinic exactly as `LabOrder` and `Visit` scope theirs: a
        # retry after a lost response replays, and two clinics generating the same key value by
        # accident never collide. Deliberately NOT unique on `patient_id` -- the same patient
        # booking a second appointment on another date is ordinary, and a unique patient key would
        # make the common case impossible while calling it idempotency.
        UniqueConstraint(
            "provider_organization_id",
            "idempotency_key",
            name="uq_exchange_appointments_provider_idempotency_key",
        ),
        # The patient must belong to the clinic that owns the booking, enforced by the database and
        # not merely by the service. Same composite-foreign-key shape as
        # `fk_exchange_visits_patient_same_provider`.
        ForeignKeyConstraint(
            ["patient_id", "provider_organization_id"],
            ["exchange_patients.id", "exchange_patients.provider_organization_id"],
            name="fk_exchange_appointments_patient_same_provider",
            ondelete="RESTRICT",
        ),
        # And the *visit* must belong to that same clinic. This is the half a plain
        # `FOREIGN KEY (visit_id)` cannot express: it would happily accept another clinic's visit.
        # The composite reference is what `uq_exchange_visits_id_provider` was added for.
        ForeignKeyConstraint(
            ["visit_id", "provider_organization_id"],
            ["exchange_visits.id", "exchange_visits.provider_organization_id"],
            name="fk_exchange_appointments_visit_same_provider",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('SCHEDULED', 'CANCELLED')",
            name="ck_exchange_appointments_status",
        ),
        # Cancellation provenance is paired with the state at the database level: a `CANCELLED` row
        # without a cancellation time, or a `SCHEDULED` row carrying one, cannot exist.
        CheckConstraint(
            "(status = 'CANCELLED') = (cancelled_at IS NOT NULL)",
            name="ck_exchange_appointments_cancelled_at_matches_status",
        ),
        CheckConstraint(
            "(cancelled_at IS NULL) = (cancelled_by_identity_id IS NULL)",
            name="ck_exchange_appointments_cancellation_actor_pairing",
        ),
        # **A cancelled booking never owns a visit.** The service refuses both orderings (cancel
        # after arrival, arrival after cancel) under a row lock; this is the database's own
        # statement of the same rule, so no future code path can produce the one state the ticket
        # names as unacceptable: a cancelled appointment holding a checked-in visit.
        CheckConstraint(
            "NOT (status = 'CANCELLED' AND visit_id IS NOT NULL)",
            name="ck_exchange_appointments_cancelled_has_no_visit",
        ),
        # Appointment 0..1 -> Visit. At most one booking may claim a given visit, so a second
        # check-in cannot link a second booking to the same encounter.
        UniqueConstraint("visit_id", name="uq_exchange_appointments_visit_id"),
        # The day query: one clinic's bookings over a half-open instant range, in time order.
        Index(
            "ix_exchange_appointments_provider_scheduled_at",
            "provider_organization_id",
            "scheduled_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    #: Part of the composite foreign keys above rather than its own `ForeignKey` column: the pair
    #: is what carries the same-clinic guarantee.
    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: **The booked instant, stored in UTC like every other instant in this package.** The wall
    #: clock a receptionist typed is interpreted server-side in the deployment timezone
    #: (`config.DEPLOYMENT_TIMEZONE`) before it ever reaches this column -- the browser's own zone
    #: never participates, which is what stops a clinic's schedule shifting with the viewer.
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AppointmentStatus.SCHEDULED.value
    )
    #: Set exactly once, by the check-in transaction, and never cleared. Non-`NULL` is the whole of
    #: "this booking produced an encounter"; the linked `Visit`'s status is the whole of what
    #: happened at it.
    visit_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    created_by_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=True
    )


# --- DHEALTH-BILLING-001: facility billing -- services, invoices, payments -----------------------


class BillableSourceType(StrEnum):
    """What a charge is *for*, as a closed set.

    `docs/NATIVE-OPERATING-PRODUCT.md` §19 rejected a single generic ``Service`` object and said
    why: a laboratory test has a catalogue entry, an offering, a result template, a specimen and a
    release authority; a consultation has none of those. Forcing one object would either strip what
    makes ``LabOffering`` safe or bolt laboratory concepts onto a consultation with no use for them.
    So an invoice line names a *source* instead, and the two source kinds keep their own domains.

    Deliberately only two. Adding a third means a real new thing a facility performs and charges
    for,
    not a convenience for something that already fits."""

    CLINIC_SERVICE = "CLINIC_SERVICE"
    LAB_ORDER_ITEM = "LAB_ORDER_ITEM"


class PaymentMethod(StrEnum):
    """The four ways a small facility in this pilot actually takes money, as
    ``docs/NATIVE-OPERATING-PRODUCT.md`` §18 already enumerated them: cash, mobile money, card,
    transfer. Not invented here -- the specification decided them, and this enum matches it.

    **None of these is an integration.** DHealth *records* that money was taken by one of these
    means; it does not move money, contact a bank, call a mobile-money API, or authorise a card.
    There is no provider, no merchant account and no secret anywhere in this domain."""

    CASH = "CASH"
    MOBILE_MONEY = "MOBILE_MONEY"
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"


class ClinicService(Base):
    """A thing a facility performs and charges for that is **not** a laboratory test.

    The consultation is the motivating case, but this is deliberately not called ``Consultation``: a
    clinic may also charge for a dressing, an injection, or a diagnostic it runs on its own bench.
    What unites them is that they have no catalogue entry, no offering, no result template and no
    release authority -- which is exactly why they cannot be ``LabOffering`` rows (§19).

    **Organization-owned and organization-scoped.** One facility's price list is not another's, and
    there is no shared or platform-level service catalogue here. A LAB may hold these too: a
    laboratory that charges a handling fee has the same need.

    Price lives in ``ClinicServicePrice`` rather than on this row, mirroring
    ``LabOffering``/``LabPrice`` exactly. That is not ceremony: it means changing a price
    *supersedes* rather than mutates, so a facility can answer "what did we charge for this in
    March" without an invoice in hand. Invoice lines still carry their own frozen snapshot -- the
    price table is the source those snapshots are read from, never the thing a historical invoice is
    re-rendered from
    (ADR-066)."""

    __tablename__ = "exchange_clinic_services"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_exchange_clinic_services_id_org"),
        UniqueConstraint("organization_id", "name", name="uq_exchange_clinic_services_org_name"),
        Index("ix_exchange_clinic_services_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    #: What appears on the invoice. French, facility-authored, never a code system.
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    prices: Mapped[list[ClinicServicePrice]] = relationship(
        back_populates="clinic_service", order_by="ClinicServicePrice.effective_at"
    )


class ClinicServicePrice(Base):
    """One price a facility charged for one of its own services, over one interval of time.

    A deliberate mirror of ``LabPrice``, down to the partial unique index: exactly one row per
    service has ``superseded_at IS NULL`` (the current price), and changing a price supersedes the
    old row rather than mutating it, so price history is never destroyed.

    ``Numeric(12, 2)`` and never a float, for the reason every money system eventually learns the
    hard way: binary floating point cannot represent 0.1, so sums drift and a balance that should be
    exactly zero is not. The same type ``LabPrice`` already uses.

    Currency is a plain three-letter code defaulting to DJF, exactly as ``LabPrice`` documents: the
    pilot is Djibouti, the column does not assume it stays that way, and **nothing here implements
    multi-currency behaviour** -- no conversion, no rates, no display logic. An invoice carries one
    currency and refuses lines that disagree with it."""

    __tablename__ = "exchange_clinic_service_prices"
    __table_args__ = (
        CheckConstraint(
            "amount >= 0", name="ck_exchange_clinic_service_prices_amount_non_negative"
        ),
        CheckConstraint(
            "length(currency) = 3", name="ck_exchange_clinic_service_prices_currency_shape"
        ),
        Index(
            "uq_exchange_clinic_service_prices_current",
            "clinic_service_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
            sqlite_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clinic_service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_clinic_services.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="DJF")
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clinic_service: Mapped[ClinicService] = relationship(back_populates="prices")


class InvoiceNumberSequence(Base):
    """One monotonic counter per organization per year, so an invoice number is human-readable and
    unique without ever being ``count(*) + 1``.

    Counting rows to pick the next number is the classic double-issue bug: two cashiers invoicing at
    the same instant both count *n* and both write *n+1*. This row is taken ``FOR UPDATE`` inside
    the invoice-creation transaction, so the second caller waits and reads the first caller's
    committed value. The unique constraint on ``Invoice.(organization_id, number)`` is the second
    layer behind that, not the first.

    Scoped per year as well as per organization so numbers restart annually, which is what a
    facility expects of a paper book. **This is not a legal numbering scheme.** Jurisdictional
    invoice numbering rules (gapless sequences, fiscal registration, mandated prefixes) are a policy
    question nobody has answered for Djibouti here, and inventing one would be worse than this
    honest,
    stable, facility-scoped reference (ADR-066)."""

    __tablename__ = "exchange_invoice_number_sequences"
    __table_args__ = (
        CheckConstraint("next_value >= 1", name="ck_exchange_invoice_number_seq_positive"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), primary_key=True
    )
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Invoice(Base):
    """One facility's bill to one patient, for services **that facility performed**.

    ── Who owns it, and who may be billed ──

    ``organization_id`` owns the invoice, its lines, its payments and its receipts, absolutely. A
    clinic cannot read a laboratory's invoice and a laboratory cannot read a clinic's, even when
    both concern the same patient and the same diagnostic order (§25). There is no cross-facility
    settlement, no accounts payable between organizations, and no pass-through billing.

    ``patient_id`` is the provider-scoped native ``Patient`` (ADR-051) -- never a second customer
    directory, never a fuzzy match. **There is deliberately no composite tenant foreign key here**,
    unlike ``Visit``, and the reason is structural rather than an oversight: ``Patient`` is owned by
    the *provider* that registered them, so a laboratory's invoice legitimately names a patient row
    another organization owns. The laboratory's right to bill that person is not ownership of their
    directory record -- it is the fact that the laboratory performed the work, which is proven by
    the invoice *line's* source. See ``InvoiceLine``. (Whether a laboratory may hold patient records
    of its own is **D-6**, still open.)

    ── ADR-066's service-performance rule ──

    An organization may invoice only for services it actually performed. A clinic bills its
    consultation and its own in-house services; a laboratory bills the tests it ran. If Clinic A
    routes an order to Lab B, **Clinic A may not put Lab B's work on an invoice** -- knowing an
    order id is not authority to bill for it. Enforced server-side in ``exchange/billing.py``, not
    by convention. D-2a (the wider commercial question of who ultimately pays whom for routed work)
    stays open; this is the narrow MVP rule that does not pre-empt it.

    ── No stored status, and no stored balance ──

    Neither exists as a column, and that is deliberate. §18's Law 8: *balance is derived -- invoice
    total minus payments -- never stored.* A stored balance is a second source of truth that drifts;
    a stored status is the same mistake one level up, because ``OPEN``/``PARTIALLY_PAID``/``PAID``
    is a pure function of the total and the payments. The API computes and returns them; the
    database holds only facts.

    Nothing here is ``VOID`` either: voiding is a policy question §26 of the specification left
    undecided (may an invoice with payments be voided? what happens to its receipts?), and this
    ticket refuses to invent it. There is therefore **no delete path and no void path** -- an
    invoice, once issued, is a permanent record.

    ── What this row must never do ──

    No field here, and nothing that reads it, may influence clinical state. A result release, a
    visit transition, an order's progress and a doctor's review are all structurally unable to see
    this
    table (ADR-066's release-independence rule, and §27's hard rule)."""

    __tablename__ = "exchange_invoices"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_exchange_invoices_org_number"),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_exchange_invoices_org_idempotency_key",
        ),
        UniqueConstraint("id", "organization_id", name="uq_exchange_invoices_id_org"),
        UniqueConstraint("id", "currency", name="uq_exchange_invoices_id_currency"),
        CheckConstraint("length(currency) = 3", name="ck_exchange_invoices_currency_shape"),
        Index("ix_exchange_invoices_organization_id", "organization_id"),
        Index("ix_exchange_invoices_org_patient", "organization_id", "patient_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    #: A provider-scoped `Patient`. See this class's docstring for why there is no composite key.
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_patients.id", ondelete="RESTRICT"), nullable=False
    )
    #: Human-readable, facility-scoped, printed on the receipt. Never a bare UUID (§15).
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    #: One currency per invoice. Lines may not disagree -- enforced by a composite foreign key from
    #: `InvoiceLine`, so the database refuses a mixed-currency invoice rather than trusting code.
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="DJF")
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    created_by_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    lines: Mapped[list[InvoiceLine]] = relationship(back_populates="invoice")
    payments: Mapped[list[Payment]] = relationship(back_populates="invoice")


class InvoiceLine(Base):
    """One charge on one invoice, with its own frozen financial facts.

    ── The snapshot is the whole point ──

    ``description_snapshot``, ``quantity``, ``unit_amount`` and ``currency`` are captured when the
    invoice is created and are never read back from the price tables afterwards. A facility that
    raises its consultation fee in March does not thereby change what a patient was charged in
    February. Rendering a historical invoice by joining to today's price would be the defect ADR-066
    names explicitly -- and NC-BILL-3 exists to prove the snapshot is genuinely load-bearing.

    ``description_snapshot`` is frozen for the same reason: a service renamed from "Consultation" to
    "Consultation générale" must not retroactively rewrite what an old invoice says was sold.

    ── The source, with real referential integrity ──

    §8 asked for a safer pattern than an untyped polymorphic key, and this is it: **two nullable
    typed foreign keys and a CHECK that exactly one matches ``source_type``.** The database
    therefore knows a ``CLINIC_SERVICE`` line points at a real ``exchange_clinic_services`` row and
    a ``LAB_ORDER_ITEM`` line at a real ``exchange_lab_order_items`` row -- not a dangling UUID
    whose meaning depends on a string column nobody constrained.

    The source exists to *explain the charge*, never to import clinical content. A line says
    "Numération formule sanguine, 1, 3 500 DJF" because that is the thing being sold. It does not,
    and must not, carry the result value, the reference interval, the clinician's note or a
    diagnosis (§18's billing-privacy rule).

    ── Currency cannot drift from the invoice ──

    The composite foreign key ``(invoice_id, currency)`` against ``Invoice``'s own
    ``uq_exchange_invoices_id_currency`` makes a mixed-currency invoice unrepresentable. No
    conversion, no rates, no foreign-exchange engine -- an invoice is in one currency and the
    database enforces it.

    ── No negative money ──

    Quantity is positive and unit amount is non-negative, by CHECK constraint. Negative lines are
    how systems without a refund model fake refunds, and this ticket has no refund or credit model;
    a
    negative line would be a silent invention of one (§14)."""

    __tablename__ = "exchange_invoice_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["invoice_id", "currency"],
            ["exchange_invoices.id", "exchange_invoices.currency"],
            name="fk_exchange_invoice_lines_currency_matches_invoice",
            ondelete="RESTRICT",
        ),
        CheckConstraint("quantity > 0", name="ck_exchange_invoice_lines_quantity_positive"),
        CheckConstraint(
            "unit_amount >= 0", name="ck_exchange_invoice_lines_unit_amount_non_negative"
        ),
        CheckConstraint(
            "source_type IN ('CLINIC_SERVICE', 'LAB_ORDER_ITEM')",
            name="ck_exchange_invoice_lines_source_type",
        ),
        # Exactly one typed source, and it must be the one `source_type` names. This pair is what
        # makes the polymorphism safe rather than a convention.
        CheckConstraint(
            "(source_type = 'CLINIC_SERVICE') = (clinic_service_id IS NOT NULL)",
            name="ck_exchange_invoice_lines_clinic_source_matches",
        ),
        CheckConstraint(
            "(source_type = 'LAB_ORDER_ITEM') = (lab_order_item_id IS NOT NULL)",
            name="ck_exchange_invoice_lines_lab_source_matches",
        ),
        Index("ix_exchange_invoice_lines_invoice_id", "invoice_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    clinic_service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_clinic_services.id", ondelete="RESTRICT"), nullable=True
    )
    lab_order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_lab_order_items.id", ondelete="RESTRICT"), nullable=True
    )
    #: What the patient sees. Frozen at invoicing; never re-read from the service or catalogue.
    description_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class Payment(Base):
    """One recorded fact that money was received against one invoice. Append-only, permanently.

    ── Immutable, and the interface says so ──

    There is no update path and no delete path for a payment, by design. Financial history that can
    be silently rewritten is not history. Correction and reversal semantics (a reversal row? a
    credit? who may authorise one?) are a policy question §26 of the operating-product specification
    left undecided, and ADR-066 refuses to invent one inside this ticket -- so a mis-keyed payment
    is currently a **named gap**, not a quietly-supported edit. The UI does not pretend otherwise.

    ── Overpayment is refused, under concurrency ──

    ``amount`` is positive by CHECK. The invariant that payments may not exceed the invoice total is
    not expressible as a row constraint, so it is enforced by locking the invoice row ``FOR UPDATE``
    and summing committed payments *inside* that lock (``exchange/billing.py``). Two cashiers taking
    the last 3 500 DJF simultaneously is the case that matters: without the lock both read the same
    remaining balance and both succeed. Qualified against real PostgreSQL, because the lock is a
    no-op on SQLite.

    ``sequence_number`` is the payment's ordinal within its invoice, computed under that same lock
    and unique by constraint. It gives every receipt a stable human-readable reference derived from
    the invoice number -- ``FAC-2026-000123-R2`` -- without a second counter table.

    ── No provider, no secret ──

    ``method`` records how money arrived, not a payment integration. Nothing here talks to a bank, a
    card network or a mobile-money API. ``reference`` is a short operator-typed note -- a
    mobile-money confirmation code or a cheque number -- and is deliberately bounded and optional.
    It must never
    hold a card number, and nothing in DHealth ever asks for one."""

    __tablename__ = "exchange_payments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["invoice_id", "organization_id"],
            ["exchange_invoices.id", "exchange_invoices.organization_id"],
            name="fk_exchange_payments_invoice_same_org",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "invoice_id", "sequence_number", name="uq_exchange_payments_invoice_sequence"
        ),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_exchange_payments_org_idempotency_key",
        ),
        CheckConstraint("amount > 0", name="ck_exchange_payments_amount_positive"),
        CheckConstraint("sequence_number >= 1", name="ck_exchange_payments_sequence_positive"),
        CheckConstraint(
            "method IN ('CASH', 'MOBILE_MONEY', 'CARD', 'BANK_TRANSFER')",
            name="ck_exchange_payments_method",
        ),
        Index("ix_exchange_payments_invoice_id", "invoice_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: Denormalised from the invoice **solely** so the composite foreign key above can exist: it is
    #: the database's own proof that a payment can never be attached to another organization's
    #: invoice, rather than a fact this column independently owns.
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Optional, short, operator-typed. Never a card number, never a credential.
    reference: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    recorded_by_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    invoice: Mapped[Invoice] = relationship(back_populates="payments")
