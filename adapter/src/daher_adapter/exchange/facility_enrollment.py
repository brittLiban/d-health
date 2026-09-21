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

from sqlalchemy import select
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
    OnboardingStatus,
    Organization,
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


class FacilityReadinessEvaluator:
    """Derives readiness from real rows, never from anything an operator ticked.

    §74: every fact below is a query. "Does this lab have a technician?" is answered by reading
    memberships and role assignments, not by a checkbox somebody set during onboarding and nobody
    revisited when that technician left.

    Two classes of finding, deliberately distinguished:

    *Blocking* findings gate production route activation -- §37's list plus the rule that a CONNECT
    destination cannot be routed at all while no connector exists. These are the conditions under
    which activating routing would send a transaction nowhere.

    *Non-blocking* findings are configuration completeness -- §11's staffing and template facts.
    A Native lab with offerings, open doors and no technician yet is genuinely routable: orders
    arrive and wait inside DHealth, which is exactly where they belong, and the lab will staff up.
    Blocking activation on it would hold up a Native deployment for a condition that resolves
    itself, which is the failure §38 warns against.
    """

    def __init__(
        self,
        *,
        enrollments: FacilityEnrollmentRepository,
        memberships,
        roles,
        directory,
        catalog,
        templates=None,
    ) -> None:
        self._enrollments = enrollments
        self._memberships = memberships
        self._roles = roles
        self._directory = directory
        self._catalog = catalog
        self._templates = templates

    def roles_present(self, organization_id: uuid.UUID) -> frozenset[str]:
        """Roles actually held through an ACTIVE membership at this organization.

        Inactive memberships are excluded on purpose: an inactivated membership must fail
        authorization everywhere else in this system, so counting it as staffing here would make
        readiness claim a capability the authorization layer would refuse.
        """
        present: set[str] = set()
        for membership in self._memberships.list_for_organization(organization_id):
            if membership.status != "ACTIVE":
                continue
            present.update(self._roles.list_for_membership(membership.id))
        return frozenset(present)

    def evaluate(
        self, organization_id: uuid.UUID
    ) -> tuple[FacilityEnrollmentRecord, list[ReadinessFinding]]:
        enrollment = self._enrollments.get_for_organization(organization_id)
        findings: list[ReadinessFinding] = []

        findings.append(
            ReadinessFinding(
                code="organization_active",
                satisfied=enrollment.organization_status == "ACTIVE",
                blocking=True,
            )
        )
        findings.append(
            ReadinessFinding(
                code="enrollment_not_blocked",
                satisfied=enrollment.onboarding_status != OnboardingStatus.BLOCKED.value,
                blocking=True,
                detail=(
                    "; ".join(b.code for b in enrollment.open_blockers)
                    if enrollment.open_blockers
                    else None
                ),
            )
        )

        roles = self.roles_present(organization_id)
        findings.append(
            ReadinessFinding(
                code="administrator_present",
                satisfied="ORG_ADMIN" in roles,
                blocking=False,
            )
        )

        if enrollment.organization_type == OrganizationType.LAB.value:
            findings.extend(self._lab_findings(enrollment, roles))
        else:
            findings.extend(self._provider_findings(roles))

        # The one rule that overrides every other consideration: DHealth has no connector, so a
        # CONNECT facility is not a destination production work can be sent to, however complete
        # its discovery is. Verified interfaces do not change this; only a qualified connector
        # would, and that is DH-CONNECT-CONTRACT-001's to deliver.
        if enrollment.participation_mode == ParticipationMode.CONNECT.value:
            findings.append(
                ReadinessFinding(
                    code="connector_qualified",
                    satisfied=False,
                    blocking=True,
                    detail="aucun connecteur DHealth n'existe encore pour cet établissement",
                )
            )

        return enrollment, findings

    def _lab_findings(
        self, enrollment: FacilityEnrollmentRecord, roles: frozenset[str]
    ) -> list[ReadinessFinding]:
        profile = self._directory.get_for_lab(enrollment.organization_id)
        offerings = self._catalog.list_offerings_for_lab(enrollment.organization_id)
        active_offerings = [o for o in offerings if o.active]

        findings = [
            ReadinessFinding(
                code="lab_accepting_orders",
                satisfied=bool(profile and profile.accepting_orders),
                blocking=True,
            ),
            ReadinessFinding(
                code="lab_has_active_offering",
                satisfied=bool(active_offerings),
                blocking=True,
                detail=f"{len(active_offerings)} analyse(s) active(s)",
            ),
            ReadinessFinding(
                code="lab_technician_present",
                satisfied="LAB_TECH" in roles,
                blocking=False,
            ),
            ReadinessFinding(
                code="lab_releaser_present",
                satisfied="LAB_RESULT_RELEASER" in roles,
                blocking=False,
            ),
        ]

        # Native result entry runs against controlled templates; a Native lab without one cannot
        # record a structured result even though orders would still reach it. Configuration, not a
        # routing gate -- and never evaluated for a CONNECT lab, which does not use Native entry.
        if (
            self._templates is not None
            and enrollment.participation_mode == ParticipationMode.NATIVE.value
        ):
            offering_ids = [o.id for o in active_offerings]
            active_templates = (
                self._templates.get_active_templates_for_offerings(offering_ids)
                if offering_ids
                else {}
            )
            findings.append(
                ReadinessFinding(
                    code="lab_result_templates_active",
                    satisfied=bool(offering_ids) and len(active_templates) == len(offering_ids),
                    blocking=False,
                    detail=f"{len(active_templates)}/{len(offering_ids)} analyse(s) structurée(s)",
                )
            )

        return findings

    def _provider_findings(self, roles: frozenset[str]) -> list[ReadinessFinding]:
        return [
            ReadinessFinding(
                code="clinician_present",
                satisfied="CLINICIAN" in roles,
                blocking=False,
            )
        ]

    def evaluate_route_activation(
        self, *, provider_organization_id: uuid.UUID, lab_organization_id: uuid.UUID
    ) -> RouteActivationDecision:
        """§37: may production routing from this provider to this lab be switched on?

        Both ends are evaluated, because routing is a claim about a pair. A blocked provider must
        not be able to send work either, even to a perfectly configured lab.

        A missing enrollment is a refusal rather than a pass. Fail-open by omission is how a
        facility nobody enrolled ends up receiving production orders.
        """
        findings: list[ReadinessFinding] = []
        for side, organization_id in (
            ("provider", provider_organization_id),
            ("lab", lab_organization_id),
        ):
            try:
                _, side_findings = self.evaluate(organization_id)
            except FacilityEnrollmentNotFound:
                findings.append(
                    ReadinessFinding(
                        code=f"{side}_enrolled",
                        satisfied=False,
                        blocking=True,
                        detail="cet établissement n'a pas de dossier d'adhésion",
                    )
                )
                continue
            findings.extend(
                ReadinessFinding(
                    code=f"{side}_{f.code}",
                    satisfied=f.satisfied,
                    blocking=f.blocking,
                    detail=f.detail,
                )
                for f in side_findings
            )

        allowed = all(f.satisfied for f in findings if f.blocking)
        return RouteActivationDecision(allowed=allowed, findings=tuple(findings))
