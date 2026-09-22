"""DH-CONNECT-CONTRACT-001 — durable connector registration and qualification evidence.

Three tables, and deliberately not more. Enterprise integration products grow fifteen; every one of
them is a place for two facts to disagree. What actually has to be durable here is: which connector
is installed where, what each qualification attempt tested and concluded, and which capabilities it
covered.

The load-bearing omission: **there is no ``QUALIFIED`` column anywhere.** Whether a facility's
connector is qualified is a question with a correct answer derivable from evidence, and storing it
as well would create a second answer that can drift from the first. It is computed by
``qualification.applicable_qualification`` and nowhere else. The installation's own ``status``
records only what a human decided -- registered, suspended, retired -- which is a different kind of
fact and cannot be derived from anything.

Shares ``Base`` with ``daher_adapter.models``, like ``exchange.models`` does, so one Alembic chain
and one ``create_all`` cover everything.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from daher_adapter.models import Base, utc_now


class ConnectorInstallationStatus(StrEnum):
    """What a human has decided about this installation. Never whether it passed anything.

    ``SUSPENDED`` and ``RETIRED`` are both "may not carry production traffic", and they are
    separate because they mean different things to the person reading the screen: a suspended
    connector is expected back, a retired one is not. Neither deletes anything -- not the
    qualification history, not the facility, not the configured route. That mirrors the staffing
    rule the Exchange already follows: losing the ability to do the work does not erase the
    decision that the relationship exists.
    """

    REGISTERED = "REGISTERED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class QualificationResult(StrEnum):
    """Two states, because a completed run either satisfied the contract or did not.

    No ``RUNNING``: a durable in-progress row would be a state this build has no use for, and an
    attempt that dies halfway is indistinguishable from one that never started. A run is recorded
    when it completes.
    """

    PASSED = "PASSED"
    FAILED = "FAILED"


class ConnectorInstallation(Base):
    """One connector implementation, installed for exactly one facility.

    **The organization binding is explicit and structural**, a foreign key on this row. It is never
    inferred from a patient identifier, a hostname, an email address, a display name or an external
    facility name -- every one of those is attacker- or accident-controlled, and a connector that
    could pick its own facility by sending a different patient id would be a cross-tenant data path.
    Qualification for organization A therefore cannot say anything about organization B, because
    qualification hangs off an installation and an installation hangs off one organization.

    ``configuration_fingerprint`` and ``mapping_fingerprint`` are what make qualification go stale
    by itself. They are SHA-256 over canonical JSON (``contract.fingerprint``), never Python's
    ``hash()``, which is per-process randomized and would make a pinned qualification expire on
    restart. When the partner upgrades their LIS, or somebody edits a test mapping, the fingerprint
    changes and the previous qualification simply stops applying -- nobody has to remember to
    invalidate it, which is the only version of that rule that survives contact with real
    operations.

    **Participation mode is enforced in the repository, not here.** Only a CONNECT facility may have
    a connector; a NATIVE facility works in DHealth directly and a connector installed for one
    would be a contradiction. Expressing that as a database constraint needs a join to
    `exchange_facility_enrollments` that no CHECK can make without a trigger, so
    `ConnectorInstallationRepository.register` refuses it -- the same application-layer discipline
    `LabResultReleaseManifestEntry` documents for its own cross-item integrity check, and for the
    same reason.

    **No secrets.** Not an API key, not a password, not a client secret, not a private key, not an
    SFTP credential, not a database password. There is no column here that could hold one, and
    there is deliberately not a generic JSON column that could be used to smuggle one in. If a
    future runtime needs a credential it stores a non-secret handle elsewhere and resolves it
    outside the Exchange database.
    """

    __tablename__ = "connector_installations"
    __table_args__ = (
        # **At most one live installation per facility** -- a genuine partial unique index, the
        # identical shape `uq_exchange_amendments_one_preparing_per_order` already established,
        # applied to a different status predicate.
        #
        # Not `UNIQUE(organization_id, connector_key)`. That weaker key would permit two different
        # non-retired connectors at one facility and leave "which one is authoritative" to be
        # settled by whichever was created most recently -- recency deciding authority is exactly
        # the race `applicable_qualification` refuses to rely on, and it would be a strange place
        # to reintroduce it. It would also refuse the ordinary replacement story: retire
        # `openelis-fhir` 1.2, register `openelis-fhir` 2.0, same key.
        #
        # So the replacement sequence is explicit: **retire the old installation, then register its
        # successor.** Retired rows are excluded from the index precisely so that history
        # accumulates without ever blocking the next registration.
        Index(
            "uq_connector_installations_one_live_per_org",
            "organization_id",
            unique=True,
            postgresql_where=text("status <> 'RETIRED'"),
            sqlite_where=text("status <> 'RETIRED'"),
        ),
        CheckConstraint(
            "status IN ('REGISTERED', 'SUSPENDED', 'RETIRED')",
            name="ck_connector_installations_status",
        ),
        Index("ix_connector_installations_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_organizations.id", ondelete="RESTRICT"), nullable=False
    )
    #: The implementation family, e.g. ``openelis-fhir``. Not a vendor's product name and not a
    #: hostname -- it identifies the DHealth-side adapter whose behaviour qualification tested.
    connector_key: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The exact build. ``1.2`` passing proves nothing about ``2.0``; pinning it is what makes that
    #: statement enforceable rather than aspirational.
    connector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system_version: Mapped[str | None] = mapped_column(String(64))
    interface_type: Mapped[str] = mapped_column(String(32), nullable=False)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ConnectorInstallationStatus.REGISTERED.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ConnectorQualification(Base):
    """One completed qualification attempt, and what it was actually run against.

    **Immutable evidence.** A failed run never becomes a passed one by UPDATE; a second attempt is
    a second row. That is not bookkeeping fastidiousness -- the question "was this connector ever
    qualified, and against what" is the question somebody asks after a patient got the wrong
    result, and an answer that can be edited is not an answer. Nothing in this codebase updates
    ``result``.

    The version and fingerprint columns are **copied from the installation at run time**, not read
    through the foreign key. That is the whole mechanism of §54-§57: when the installation later
    changes its connector version or its mapping, this row still says what was tested, the two no
    longer match, and the qualification stops applying. Reading through the FK would make every old
    qualification silently describe the newest build.

    ``evidence_summary`` is a short human sentence, not a log dump. Internal test output belongs in
    the harness's own artefacts; what a platform operator needs on screen is which capabilities
    passed and what failed, and those are structured rows.
    """

    __tablename__ = "connector_qualifications"
    __table_args__ = (
        CheckConstraint(
            "result IN ('PASSED', 'FAILED')", name="ck_connector_qualifications_result"
        ),
        CheckConstraint(
            "completed_at >= started_at", name="ck_connector_qualifications_completed_after_start"
        ),
        Index("ix_connector_qualifications_installation_id", "connector_installation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    connector_installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_installations.id", ondelete="RESTRICT"), nullable=False
    )
    # -- pinned at run time, never re-read from the installation -----------------------------
    contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    connector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Which revision of the qualification scenarios ran. A suite that gains a scenario is a new
    #: suite version, so an old PASS is not quietly credited with proving something never tested.
    suite_version: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ConnectorQualificationCapability(Base):
    """Which capability a qualification attempt exercised, and whether it held.

    A separate row per capability rather than a JSON blob on the qualification, because "did this
    laboratory connector prove it can publish an amendment" is a question that gates production
    clinical routing, and a fact that gates routing should be something a query can filter and a
    constraint can protect.
    """

    __tablename__ = "connector_qualification_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "qualification_id",
            "capability",
            name="uq_connector_qualification_capabilities_unique",
        ),
        CheckConstraint(
            "capability IN ("
            "'ORDER_RECEIPT', 'BUSINESS_ACCEPTANCE', 'RELEASED_RESULT_PUBLICATION', "
            "'AMENDMENT_PUBLICATION', 'COLLECTION_EVENT', 'PROCESSING_EVENT', "
            "'ORDER_SUBMISSION', 'RELEASED_RESULT_RECEIPT', 'AMENDMENT_RECEIPT', "
            "'REVIEW_REPORTING')",
            name="ck_connector_qualification_capabilities_capability",
        ),
        Index(
            "ix_connector_qualification_capabilities_qualification_id",
            "qualification_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    qualification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_qualifications.id", ondelete="RESTRICT"), nullable=False
    )
    capability: Mapped[str] = mapped_column(String(32), nullable=False)
    passed: Mapped[bool] = mapped_column(nullable=False)
    #: Why it failed, in one short sentence, for the operator screen. Never a stack trace.
    detail: Mapped[str | None] = mapped_column(String(512))
