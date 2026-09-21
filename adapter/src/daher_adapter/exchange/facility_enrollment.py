"""DH-MVP-ONBOARD-001: facility enrollment, technical discovery, and readiness.

This module answers two questions that an ``Organization`` row cannot, and keeps them apart:

    Organizational enrollment -- who is this facility on the DHealth network?
    Technical integration     -- how does their existing software talk to DHealth, if at all?

The separation is the point. A network that conflates them tells an operator a facility is
connected when all it really knows is that somebody typed a name into a form.

**What this module refuses to do.** It will not let a CONNECT facility reach ``READY_FOR_ROUTING``.
No connector exists yet -- that is DH-CONNECT-CONTRACT-001's subject -- so a CONNECT facility
declared routable would mean DHealth sending a production order into nothing. The restriction is
enforced here, in the repository, rather than at the API layer, because an invariant that only one
caller checks is an invariant one refactor away from being gone.

**Where the route-activation gate is not.** ``ProviderLabRouteRepository.upsert`` stays a
primitive: it writes the row and enforces organization types, exactly as it did before this ticket.
Readiness is *policy*, evaluated by ``evaluate_route_activation`` and called by the platform-admin
endpoint that actually activates routing. That split matters for a concrete reason -- ``demo_seed``
and the DH-CLINIC-006 concurrency tests construct known-good fixtures by calling the repository
directly, and a gate buried in the primitive would have made building a fixture require satisfying
a policy that the fixture exists to test. The actor a gate protects against is a platform
administrator clicking "activate" in a browser, and that path goes through the evaluator.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from daher_adapter.exchange.models import (
    EnrollmentBlockerCode,
    FacilityEnrollment,
    FacilityEnrollmentBlocker,
    FacilityInterfaceCapability,
    HostingModel,
    IntegrationFeasibility,
    InterfaceType,
    InterfaceVerificationState,
    LabDirectoryProfile,
    LabOffering,
    OnboardingStatus,
    Organization,
    OrganizationMembership,
    OrganizationMembershipStatus,
    OrganizationRoleAssignment,
    OrganizationStatus,
    OrganizationType,
    ParticipationMode,
)
from daher_adapter.models import utc_now

SessionFactory = Callable[[], AbstractContextManager[Session]]


class FacilityEnrollmentError(Exception):
    """Base for every refusal in this module."""


class FacilityEnrollmentNotFound(FacilityEnrollmentError):
    pass


class OrganizationNotFound(FacilityEnrollmentError):
    pass


class InvalidOnboardingTransition(FacilityEnrollmentError):
    """A status the facility cannot legitimately be in, given its participation mode.

    Raised rather than silently coerced: an operator who asked for something impossible needs to be
    told, not quietly given a different answer.
    """


class ParticipationModeChangeRefused(FacilityEnrollmentError):
    """§34: mode is consequential, so a change fails closed once real work depends on it."""


@dataclass(frozen=True)
class InterfaceCapabilityRecord:
    interface_type: str
    verification_state: str
    detail: str | None


@dataclass(frozen=True)
class BlockerRecord:
    id: uuid.UUID
    code: str
    note: str | None
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True)
class FacilityEnrollmentRecord:
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    organization_type: str
    organization_status: str
    participation_mode: str
    onboarding_status: str
    integration_feasibility: str | None
    existing_system_name: str | None
    existing_system_vendor: str | None
    existing_system_version: str | None
    hosting_model: str | None
    technical_contact: str | None
    vendor_contact: str | None
    documentation_reference: str | None
    patient_identifier_namespace: str | None
    order_identifier_concept: str | None
    report_identifier_concept: str | None
    specimen_identifier_concept: str | None
    exposes_source_event_timestamps: bool | None
    exposes_professional_release_event: bool | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    interfaces: tuple[InterfaceCapabilityRecord, ...] = ()
    open_blockers: tuple[BlockerRecord, ...] = ()


# A CONNECT facility cannot be declared routable while no connector exists. Everything else in the
# state model is reachable by either mode.
CONNECT_FORBIDDEN_STATUSES = frozenset({OnboardingStatus.READY_FOR_ROUTING.value})

# Discovery states belong to CONNECT: a Native facility has no external system to discover, and
# putting one in TECHNICAL_DISCOVERY would state something untrue about what DHealth is waiting on.
NATIVE_FORBIDDEN_STATUSES = frozenset(
    {
        OnboardingStatus.TECHNICAL_DISCOVERY.value,
        OnboardingStatus.AWAITING_CONNECTOR.value,
        OnboardingStatus.READY_FOR_QUALIFICATION.value,
    }
)


def _require_status_allowed(participation_mode: str, onboarding_status: str) -> None:
    if (
        participation_mode == ParticipationMode.CONNECT.value
        and onboarding_status in CONNECT_FORBIDDEN_STATUSES
    ):
        raise InvalidOnboardingTransition(
            f"a CONNECT facility cannot be {onboarding_status}: no DHealth connector exists yet, "
            "so production routing to it would deliver nowhere (DH-CONNECT-CONTRACT-001)"
        )
    if (
        participation_mode == ParticipationMode.NATIVE.value
        and onboarding_status in NATIVE_FORBIDDEN_STATUSES
    ):
        raise InvalidOnboardingTransition(
            f"a NATIVE facility cannot be {onboarding_status}: it has no external system to "
            "discover, document or qualify"
        )


class FacilityEnrollmentRepository:
    """Durable enrollment state. One row per organization, enforced by a unique constraint and by
    an in-transaction re-read, so two operators submitting the same facility twice produce one
    enrollment rather than two competing ones."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    # ── reads ────────────────────────────────────────────────────────────────────────────────

    def get_for_organization(self, organization_id: uuid.UUID) -> FacilityEnrollmentRecord:
        with self._sessions() as session:
            enrollment = self._load(session, organization_id)
            return self._to_record(session, enrollment)

    def find_for_organization(self, organization_id: uuid.UUID) -> FacilityEnrollmentRecord | None:
        with self._sessions() as session:
            enrollment = session.scalar(
                select(FacilityEnrollment).where(
                    FacilityEnrollment.organization_id == organization_id
                )
            )
            if enrollment is None:
                return None
            return self._to_record(session, enrollment)

    def list_all(self) -> list[FacilityEnrollmentRecord]:
        """Every enrolled facility, for the platform operator's network overview."""
        with self._sessions() as session:
            enrollments = session.scalars(select(FacilityEnrollment)).all()
            records = [self._to_record(session, e) for e in enrollments]
        records.sort(key=lambda r: r.organization_name.lower())
        return records

    # ── writes ───────────────────────────────────────────────────────────────────────────────

    def create(
        self,
        *,
        organization_id: uuid.UUID,
        participation_mode: ParticipationMode | str,
        onboarding_status: OnboardingStatus | str = OnboardingStatus.DRAFT,
        session: Session | None = None,
    ) -> tuple[FacilityEnrollmentRecord, bool]:
        """Returns ``(record, created)``. Idempotent by organization: a replay returns the existing
        enrollment rather than a second one, and never silently rewrites its mode -- changing how a
        facility participates is ``set_participation_mode``'s deliberate, audited job."""
        mode = _value(participation_mode)
        status = _value(onboarding_status)
        _require_status_allowed(mode, status)

        if session is not None:
            return self._create_in_session(
                session,
                organization_id=organization_id,
                participation_mode=mode,
                onboarding_status=status,
            )
        with self._sessions() as owned:
            record, created = self._create_in_session(
                owned,
                organization_id=organization_id,
                participation_mode=mode,
                onboarding_status=status,
            )
            owned.commit()
            return record, created

    def _create_in_session(
        self,
        session: Session,
        *,
        organization_id: uuid.UUID,
        participation_mode: str,
        onboarding_status: str,
    ) -> tuple[FacilityEnrollmentRecord, bool]:
        organization = session.get(Organization, organization_id)
        if organization is None:
            raise OrganizationNotFound(f"organization {organization_id} does not exist")

        existing = session.scalar(
            select(FacilityEnrollment)
            .where(FacilityEnrollment.organization_id == organization_id)
            .with_for_update()
        )
        if existing is not None:
            return self._to_record(session, existing), False

        now = utc_now()
        enrollment = FacilityEnrollment(
            organization_id=organization_id,
            participation_mode=participation_mode,
            onboarding_status=onboarding_status,
            created_at=now,
            updated_at=now,
        )
        try:
            with session.begin_nested():
                session.add(enrollment)
                session.flush()
        except IntegrityError:
            # Lost the create race. The unique constraint held; re-read and treat the winner as
            # authoritative rather than surfacing a duplicate-key error to an operator who did
            # nothing wrong.
            existing = session.scalar(
                select(FacilityEnrollment)
                .where(FacilityEnrollment.organization_id == organization_id)
                .with_for_update()
            )
            if existing is None:
                raise
            return self._to_record(session, existing), False
        return self._to_record(session, enrollment), True

    def set_participation_mode(
        self,
        *,
        organization_id: uuid.UUID,
        participation_mode: ParticipationMode | str,
        has_dependent_workflow: bool,
    ) -> tuple[FacilityEnrollmentRecord, bool]:
        """§34. Returns ``(record, changed)``.

        ``has_dependent_workflow`` is supplied by the caller from real state (orders, active
        routes). When work already depends on the current mode, the change fails closed: migrating
        a live facility between Native and Connect is a deliberate operation with data
        consequences, and this ticket does not implement it. Refusing is the honest outcome; doing
        it silently would strand whatever already exists.
        """
        mode = _value(participation_mode)
        with self._sessions() as session:
            enrollment = self._load(session, organization_id, for_update=True)
            if enrollment.participation_mode == mode:
                return self._to_record(session, enrollment), False
            if has_dependent_workflow:
                raise ParticipationModeChangeRefused(
                    "this facility already has dependent workflow state (orders or an active "
                    "route), so its participation mode cannot be changed here -- migrating a live "
                    "facility is a deliberate operation this release does not implement"
                )
            # The new mode may forbid the status the facility currently sits in.
            status = enrollment.onboarding_status
            if (
                mode == ParticipationMode.CONNECT.value and status in CONNECT_FORBIDDEN_STATUSES
            ) or (mode == ParticipationMode.NATIVE.value and status in NATIVE_FORBIDDEN_STATUSES):
                status = OnboardingStatus.CONFIGURATION_IN_PROGRESS.value
            enrollment.participation_mode = mode
            enrollment.onboarding_status = status
            enrollment.updated_at = utc_now()
            session.flush()
            record = self._to_record(session, enrollment)
            session.commit()
            return record, True

    def set_onboarding_status(
        self, *, organization_id: uuid.UUID, onboarding_status: OnboardingStatus | str
    ) -> tuple[FacilityEnrollmentRecord, bool]:
        status = _value(onboarding_status)
        with self._sessions() as session:
            enrollment = self._load(session, organization_id, for_update=True)
            _require_status_allowed(enrollment.participation_mode, status)
            changed = enrollment.onboarding_status != status
            if changed:
                enrollment.onboarding_status = status
                enrollment.updated_at = utc_now()
                session.flush()
            record = self._to_record(session, enrollment)
            session.commit()
            return record, changed

    def update_discovery(
        self,
        *,
        organization_id: uuid.UUID,
        fields: dict[str, object],
    ) -> FacilityEnrollmentRecord:
        """Technical discovery for a CONNECT facility. Only the named columns are writable; a key
        this method does not recognise is rejected rather than ignored, so a typo in a caller
        cannot silently drop a fact an operator entered."""
        with self._sessions() as session:
            enrollment = self._load(session, organization_id, for_update=True)
            if enrollment.participation_mode != ParticipationMode.CONNECT.value:
                raise InvalidOnboardingTransition(
                    "technical discovery applies to a CONNECT facility: a Native facility has no "
                    "external system to describe"
                )
            unknown = set(fields) - DISCOVERY_FIELDS
            if unknown:
                raise FacilityEnrollmentError(f"unknown discovery field(s): {sorted(unknown)}")
            if "hosting_model" in fields and fields["hosting_model"] is not None:
                _require_member(HostingModel, str(fields["hosting_model"]), "hosting_model")
            if (
                "integration_feasibility" in fields
                and fields["integration_feasibility"] is not None
            ):
                _require_member(
                    IntegrationFeasibility,
                    str(fields["integration_feasibility"]),
                    "integration_feasibility",
                )
            for key, value in fields.items():
                setattr(enrollment, key, value)
            enrollment.updated_at = utc_now()
            session.flush()
            record = self._to_record(session, enrollment)
            session.commit()
            return record

    def set_interface_capability(
        self,
        *,
        organization_id: uuid.UUID,
        interface_type: InterfaceType | str,
        verification_state: InterfaceVerificationState | str,
        detail: str | None = None,
    ) -> tuple[InterfaceCapabilityRecord, bool]:
        """Record or update what DHealth knows about one interface. Idempotent per interface type.

        Returns ``(record, created)``. Note what this does *not* do: recording an interface as
        ``VERIFIED`` changes no onboarding status and unlocks no routing. Verification means the
        interface exists as documented, which is a long way from a qualified connector.
        """
        itype = _value(interface_type)
        state = _value(verification_state)
        _require_member(InterfaceType, itype, "interface_type")
        _require_member(InterfaceVerificationState, state, "verification_state")
        with self._sessions() as session:
            enrollment = self._load(session, organization_id, for_update=True)
            existing = session.scalar(
                select(FacilityInterfaceCapability)
                .where(
                    FacilityInterfaceCapability.facility_enrollment_id == enrollment.id,
                    FacilityInterfaceCapability.interface_type == itype,
                )
                .with_for_update()
            )
            now = utc_now()
            created = existing is None
            if existing is None:
                existing = FacilityInterfaceCapability(
                    facility_enrollment_id=enrollment.id,
                    interface_type=itype,
                    verification_state=state,
                    detail=detail,
                    created_at=now,
                    updated_at=now,
                )
                session.add(existing)
            else:
                existing.verification_state = state
                existing.detail = detail
                existing.updated_at = now
            session.flush()
            record = InterfaceCapabilityRecord(
                interface_type=existing.interface_type,
                verification_state=existing.verification_state,
                detail=existing.detail,
            )
            session.commit()
            return record, created

    def add_blocker(
        self,
        *,
        organization_id: uuid.UUID,
        code: EnrollmentBlockerCode | str,
        note: str | None = None,
    ) -> BlockerRecord:
        blocker_code = _value(code)
        _require_member(EnrollmentBlockerCode, blocker_code, "code")
        with self._sessions() as session:
            enrollment = self._load(session, organization_id, for_update=True)
            existing = session.scalar(
                select(FacilityEnrollmentBlocker).where(
                    FacilityEnrollmentBlocker.facility_enrollment_id == enrollment.id,
                    FacilityEnrollmentBlocker.code == blocker_code,
                    FacilityEnrollmentBlocker.resolved_at.is_(None),
                )
            )
            if existing is not None:
                return _blocker_record(existing)
            blocker = FacilityEnrollmentBlocker(
                facility_enrollment_id=enrollment.id,
                code=blocker_code,
                note=note,
                created_at=utc_now(),
            )
            session.add(blocker)
            session.flush()
            record = _blocker_record(blocker)
            session.commit()
            return record

    def resolve_blocker(self, *, blocker_id: uuid.UUID) -> BlockerRecord:
        with self._sessions() as session:
            blocker = session.get(FacilityEnrollmentBlocker, blocker_id)
            if blocker is None:
                raise FacilityEnrollmentNotFound(f"blocker {blocker_id} does not exist")
            if blocker.resolved_at is None:
                blocker.resolved_at = utc_now()
                session.flush()
            record = _blocker_record(blocker)
            session.commit()
            return record

    # ── internals ────────────────────────────────────────────────────────────────────────────

    def _load(
        self, session: Session, organization_id: uuid.UUID, *, for_update: bool = False
    ) -> FacilityEnrollment:
        statement = select(FacilityEnrollment).where(
            FacilityEnrollment.organization_id == organization_id
        )
        if for_update:
            statement = statement.with_for_update()
        enrollment = session.scalar(statement)
        if enrollment is None:
            raise FacilityEnrollmentNotFound(
                f"organization {organization_id} has no facility enrollment"
            )
        return enrollment

    def _to_record(
        self, session: Session, enrollment: FacilityEnrollment
    ) -> FacilityEnrollmentRecord:
        organization = session.get(Organization, enrollment.organization_id)
        interfaces = session.scalars(
            select(FacilityInterfaceCapability)
            .where(FacilityInterfaceCapability.facility_enrollment_id == enrollment.id)
            .order_by(FacilityInterfaceCapability.interface_type)
        ).all()
        blockers = session.scalars(
            select(FacilityEnrollmentBlocker)
            .where(
                FacilityEnrollmentBlocker.facility_enrollment_id == enrollment.id,
                FacilityEnrollmentBlocker.resolved_at.is_(None),
            )
            .order_by(FacilityEnrollmentBlocker.created_at)
        ).all()
        return FacilityEnrollmentRecord(
            id=enrollment.id,
            organization_id=enrollment.organization_id,
            organization_name=organization.name if organization else "",
            organization_type=organization.org_type if organization else "",
            organization_status=organization.status if organization else "",
            participation_mode=enrollment.participation_mode,
            onboarding_status=enrollment.onboarding_status,
            integration_feasibility=enrollment.integration_feasibility,
            existing_system_name=enrollment.existing_system_name,
            existing_system_vendor=enrollment.existing_system_vendor,
            existing_system_version=enrollment.existing_system_version,
            hosting_model=enrollment.hosting_model,
            technical_contact=enrollment.technical_contact,
            vendor_contact=enrollment.vendor_contact,
            documentation_reference=enrollment.documentation_reference,
            patient_identifier_namespace=enrollment.patient_identifier_namespace,
            order_identifier_concept=enrollment.order_identifier_concept,
            report_identifier_concept=enrollment.report_identifier_concept,
            specimen_identifier_concept=enrollment.specimen_identifier_concept,
            exposes_source_event_timestamps=enrollment.exposes_source_event_timestamps,
            exposes_professional_release_event=enrollment.exposes_professional_release_event,
            notes=enrollment.notes,
            created_at=enrollment.created_at,
            updated_at=enrollment.updated_at,
            interfaces=tuple(
                InterfaceCapabilityRecord(
                    interface_type=i.interface_type,
                    verification_state=i.verification_state,
                    detail=i.detail,
                )
                for i in interfaces
            ),
            open_blockers=tuple(_blocker_record(b) for b in blockers),
        )


DISCOVERY_FIELDS = frozenset(
    {
        "integration_feasibility",
        "existing_system_name",
        "existing_system_vendor",
        "existing_system_version",
        "hosting_model",
        "technical_contact",
        "vendor_contact",
        "documentation_reference",
        "patient_identifier_namespace",
        "order_identifier_concept",
        "report_identifier_concept",
        "specimen_identifier_concept",
        "exposes_source_event_timestamps",
        "exposes_professional_release_event",
        "notes",
    }
)


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _require_member(enum_cls: type, value: str, field_name: str) -> None:
    if value not in {member.value for member in enum_cls}:
        raise FacilityEnrollmentError(f"{field_name}={value!r} is not a supported value")


def _blocker_record(blocker: FacilityEnrollmentBlocker) -> BlockerRecord:
    return BlockerRecord(
        id=blocker.id,
        code=blocker.code,
        note=blocker.note,
        created_at=blocker.created_at,
        resolved_at=blocker.resolved_at,
    )


# ── readiness ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReadinessFinding:
    """One thing that is or is not true about a facility, derived from real rows.

    ``blocking`` separates the two questions §37 and §11 ask differently: whether production
    routing may be switched on *right now*, and whether the facility is fully configured for
    people to work in. A lab with offerings and open doors but no technician yet is routable --
    orders will arrive and wait in DHealth, which is where they belong -- and is not finished.
    Reporting both, and marking which is which, is more useful than one boolean.
    """

    code: str
    satisfied: bool
    blocking: bool
    detail: str | None = None


@dataclass(frozen=True)
class RouteActivationDecision:
    allowed: bool
    findings: tuple[ReadinessFinding, ...]

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return tuple(f.code for f in self.findings if f.blocking and not f.satisfied)


# ── operational readiness, shared by every production path ───────────────────────────────────
#
# Three different code paths need the same answer to "can this facility actually do the work?":
# the platform endpoint that activates a route, the read that lists eligible destinations for an
# order, and the assignment mutation itself. Expressing it three times -- once in Python, twice in
# SQL -- is how the three quietly drift apart until a lab is eligible but unassignable.
#
# So it lives here once, as a function taking a caller's own `Session`. That shape matters: the
# assignment path evaluates this while already holding a `LabOrder` row lock, and a helper that
# opened its own session there would be a deadlock waiting for load. Callers that have no session
# use `FacilityReadinessEvaluator`, which opens one and delegates to exactly this function.

#: Roles a NATIVE facility must actually have, through an ACTIVE membership, before production
#: work may be routed to it. A lab needs both: entry and release are separate authorities
#: (ADR-044), so a lab with technicians and no releaser can receive orders it is structurally
#: incapable of ever completing -- results would be entered and could never be released. One
#: person holding both roles satisfies both requirements; the check is on roles present at the
#: organization, never on a count of humans.
NATIVE_REQUIRED_ROLES: dict[str, tuple[str, ...]] = {
    OrganizationType.PROVIDER.value: ("CLINICIAN",),
    OrganizationType.LAB.value: ("LAB_TECH", "LAB_RESULT_RELEASER"),
}

#: Which finding codes, when unsatisfied, mean "do not send production work here".
STAFFING_FINDING_CODES = {
    "CLINICIAN": "clinician_present",
    "LAB_TECH": "lab_technician_present",
    "LAB_RESULT_RELEASER": "lab_releaser_present",
}


def active_roles_in_session(session: Session, organization_id: uuid.UUID) -> frozenset[str]:
    """Roles held through an ACTIVE membership at this organization.

    Inactive memberships are excluded deliberately: an inactivated membership fails authorization
    everywhere else in this system, so counting it here would let readiness claim a capability the
    authorization layer would refuse the moment someone tried to use it.
    """
    rows = session.execute(
        select(OrganizationRoleAssignment.role)
        .join(
            OrganizationMembership,
            OrganizationMembership.id == OrganizationRoleAssignment.membership_id,
        )
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.status == OrganizationMembershipStatus.ACTIVE.value,
        )
    ).all()
    return frozenset(row[0] for row in rows)


def operational_findings_in_session(
    session: Session, organization_id: uuid.UUID
) -> list[ReadinessFinding]:
    """Everything that decides whether production work may be routed to this facility right now.

    Derived entirely from live rows (§74). Nothing here reads a stored verdict, which is what makes
    a facility that loses its last releaser stop being an eligible destination without anybody
    having to remember to update a flag -- and without this code touching the configured route,
    which stays exactly as the platform admin left it.
    """
    findings: list[ReadinessFinding] = []
    organization = session.get(Organization, organization_id)
    if organization is None:
        return [
            ReadinessFinding(
                code="organization_exists", satisfied=False, blocking=True, detail=None
            )
        ]

    enrollment = session.scalar(
        select(FacilityEnrollment).where(FacilityEnrollment.organization_id == organization_id)
    )
    if enrollment is None:
        # Fail closed. An organization nobody enrolled is not a destination, and treating a missing
        # row as a pass would be a permanent hole for anything created outside the onboarding flow.
        return [
            ReadinessFinding(
                code="facility_enrolled",
                satisfied=False,
                blocking=True,
                detail="cet établissement n'a pas de dossier d'adhésion",
            )
        ]

    findings.append(
        ReadinessFinding(
            code="organization_active",
            satisfied=organization.status == OrganizationStatus.ACTIVE.value,
            blocking=True,
        )
    )

    open_blockers = session.scalars(
        select(FacilityEnrollmentBlocker.code).where(
            FacilityEnrollmentBlocker.facility_enrollment_id == enrollment.id,
            FacilityEnrollmentBlocker.resolved_at.is_(None),
        )
    ).all()
    findings.append(
        ReadinessFinding(
            code="enrollment_not_blocked",
            satisfied=(
                enrollment.onboarding_status != OnboardingStatus.BLOCKED.value and not open_blockers
            ),
            blocking=True,
            detail="; ".join(open_blockers) if open_blockers else None,
        )
    )

    roles = active_roles_in_session(session, organization_id)
    findings.append(
        ReadinessFinding(
            code="administrator_present", satisfied="ORG_ADMIN" in roles, blocking=False
        )
    )

    is_native = enrollment.participation_mode == ParticipationMode.NATIVE.value
    for role in NATIVE_REQUIRED_ROLES.get(organization.org_type, ()):
        findings.append(
            ReadinessFinding(
                code=STAFFING_FINDING_CODES[role],
                satisfied=role in roles,
                # Staffing gates production routing for a NATIVE facility, where DHealth itself is
                # the workflow: an order routed to a lab with no releaser would arrive and could
                # never be completed. For a CONNECT facility the work happens in the partner's own
                # system, so DHealth-side staffing is not what decides deliverability -- and such a
                # facility is blocked anyway by the absent connector below.
                blocking=is_native,
            )
        )

    if organization.org_type == OrganizationType.LAB.value:
        profile = session.scalar(
            select(LabDirectoryProfile).where(
                LabDirectoryProfile.lab_organization_id == organization_id
            )
        )
        findings.append(
            ReadinessFinding(
                code="lab_accepting_orders",
                satisfied=bool(profile and profile.accepting_orders),
                blocking=True,
            )
        )
        active_offerings = session.scalar(
            select(func.count())
            .select_from(LabOffering)
            .where(
                LabOffering.lab_organization_id == organization_id,
                LabOffering.active.is_(True),
            )
        )
        findings.append(
            ReadinessFinding(
                code="lab_has_active_offering",
                satisfied=bool(active_offerings),
                blocking=True,
                detail=f"{active_offerings or 0} analyse(s) active(s)",
            )
        )

    if not is_native:
        findings.append(
            ReadinessFinding(
                code="connector_qualified",
                satisfied=False,
                blocking=True,
                detail="aucun connecteur DHealth n'existe encore pour cet établissement",
            )
        )

    return findings


def is_operationally_ready_in_session(session: Session, organization_id: uuid.UUID) -> bool:
    """The one-line form, for a caller that only needs the verdict.

    "Operationally ready" is the middle of three distinct facts this system keeps apart:

        configured route      a platform admin has said this pair may exchange work
        operationally ready   the facility can actually do that work today
        eligible destination  both of the above, for this specific order, right now

    A facility that loses its last releaser stops being operationally ready. Its configured route
    is untouched -- somebody decided that relationship deliberately and it remains their decision
    -- but it stops being an eligible destination until the staffing comes back.
    """
    return all(
        f.satisfied for f in operational_findings_in_session(session, organization_id) if f.blocking
    )


class FacilityReadinessEvaluator:
    """Derives readiness from real rows, never from anything an operator ticked.

    §74: every fact is a query. "Does this lab have a releaser?" is answered by reading memberships
    and role assignments, not by a checkbox somebody set during onboarding and nobody revisited
    when that person left.

    This class is a session-owning wrapper. The logic lives in
    ``operational_findings_in_session`` so that the endpoint activating a route, the read listing
    eligible destinations, and the assignment mutation itself all consult one implementation
    instead of three that drift.

    Two classes of finding:

    *Blocking* findings mean production work must not be routed here -- §37's conditions, the
    NATIVE staffing requirements, and the rule that a CONNECT destination cannot be routed at all
    while no connector exists.

    *Non-blocking* findings are configuration completeness that does not make work undeliverable:
    an administrator, and the controlled result templates a Native lab needs for structured entry.

    Staffing is blocking, and that placement is deliberate. Entry and release are separate
    authorities (ADR-044), so a Native lab holding LAB_TECH but no LAB_RESULT_RELEASER can receive
    an order, enter results against it, and then be structurally unable to release them -- work
    that arrives somewhere it can never leave. One person holding both roles satisfies both
    requirements; the requirement is on authority present at the organization, not on headcount.
    """

    def __init__(
        self,
        *,
        sessions: SessionFactory,
        enrollments: FacilityEnrollmentRepository,
        catalog=None,
        templates=None,
    ) -> None:
        self._sessions = sessions
        self._enrollments = enrollments
        self._catalog = catalog
        self._templates = templates

    def roles_present(self, organization_id: uuid.UUID) -> frozenset[str]:
        with self._sessions() as session:
            return active_roles_in_session(session, organization_id)

    def evaluate(
        self, organization_id: uuid.UUID
    ) -> tuple[FacilityEnrollmentRecord, list[ReadinessFinding]]:
        enrollment = self._enrollments.get_for_organization(organization_id)
        with self._sessions() as session:
            findings = operational_findings_in_session(session, organization_id)
        findings.extend(self._template_findings(enrollment))
        return enrollment, findings

    def is_operationally_ready(self, organization_id: uuid.UUID) -> bool:
        with self._sessions() as session:
            return is_operationally_ready_in_session(session, organization_id)

    def _template_findings(self, enrollment: FacilityEnrollmentRecord) -> list[ReadinessFinding]:
        """Native structured result entry runs against controlled templates.

        Non-blocking: a lab missing one can still receive orders and record results by the
        pre-template path, so this is configuration completeness rather than deliverability. Never
        evaluated for a CONNECT lab, which does not use Native entry at all.
        """
        if (
            self._templates is None
            or self._catalog is None
            or enrollment.organization_type != OrganizationType.LAB.value
            or enrollment.participation_mode != ParticipationMode.NATIVE.value
        ):
            return []
        offerings = [
            o for o in self._catalog.list_offerings_for_lab(enrollment.organization_id) if o.active
        ]
        offering_ids = [o.id for o in offerings]
        active_templates = (
            self._templates.get_active_templates_for_offerings(offering_ids) if offering_ids else {}
        )
        return [
            ReadinessFinding(
                code="lab_result_templates_active",
                satisfied=bool(offering_ids) and len(active_templates) == len(offering_ids),
                blocking=False,
                detail=f"{len(active_templates)}/{len(offering_ids)} analyse(s) structurée(s)",
            )
        ]

    def evaluate_route_activation(
        self, *, provider_organization_id: uuid.UUID, lab_organization_id: uuid.UUID
    ) -> RouteActivationDecision:
        """§37: may production routing from this provider to this lab be switched on?

        Both ends are evaluated, because routing is a claim about a pair: a provider with no
        clinician has nobody to order, and a lab with no releaser has nobody to finish. A blocked
        provider must not be able to send work even to a perfectly staffed lab.

        A missing enrollment is a refusal rather than a pass -- fail-open by omission is how a
        facility nobody enrolled ends up receiving production orders.
        """
        findings: list[ReadinessFinding] = []
        with self._sessions() as session:
            for side, organization_id in (
                ("provider", provider_organization_id),
                ("lab", lab_organization_id),
            ):
                findings.extend(
                    ReadinessFinding(
                        code=f"{side}_{f.code}",
                        satisfied=f.satisfied,
                        blocking=f.blocking,
                        detail=f.detail,
                    )
                    for f in operational_findings_in_session(session, organization_id)
                )
        allowed = all(f.satisfied for f in findings if f.blocking)
        return RouteActivationDecision(allowed=allowed, findings=tuple(findings))


class RouteActivationNotReady(FacilityEnrollmentError):
    """A production route activation refused because the pair is not operationally ready.

    Carries the whole decision rather than a message: the caller needs to tell an operator exactly
    which conditions failed, and a string would force the UI to parse prose.
    """

    def __init__(self, decision: RouteActivationDecision) -> None:
        super().__init__("route activation refused: " + ", ".join(decision.blocking_reasons))
        self.decision = decision


def facility_has_dependent_workflow_in_session(
    session: Session, organization_id: uuid.UUID
) -> bool:
    """Does real work already depend on how this facility participates? (§34)

    Derived rather than asked of the caller, because a UI that had to answer this would be deciding
    a safety question from stale state. "Dependent" means either an active route touching this
    organization or any order it has created or been assigned -- switching participation mode with
    either in place would strand whatever exists, and migrating a live facility between Native and
    Connect is a deliberate operation this release does not implement.
    """
    from daher_adapter.exchange.models import LabOrder, LabSelection, ProviderLabRoute

    active_route = session.scalar(
        select(func.count())
        .select_from(ProviderLabRoute)
        .where(
            ProviderLabRoute.active.is_(True),
            or_(
                ProviderLabRoute.provider_organization_id == organization_id,
                ProviderLabRoute.lab_organization_id == organization_id,
            ),
        )
    )
    if active_route:
        return True
    ordered = session.scalar(
        select(func.count())
        .select_from(LabOrder)
        .where(LabOrder.provider_organization_id == organization_id)
    )
    if ordered:
        return True
    assigned = session.scalar(
        select(func.count())
        .select_from(LabSelection)
        .where(LabSelection.lab_organization_id == organization_id)
    )
    return bool(assigned)
