"""DH-CONNECT-CONTRACT-001 — registering connectors and recording qualification evidence.

The only way a ``ConnectorQualification`` row is ever written is ``record_qualification``, and the
only caller that should reach it is the qualification harness (or, later, real build tooling running
the same suite). There is deliberately no path -- no endpoint, no admin gesture, no repository
method -- that accepts ``{"qualified": true}`` and believes it. Qualification is evidence produced
by running scenarios, not an opinion an operator can hold.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import CONTRACT_VERSION, Capability, contract_major
from daher_adapter.connect.models import (
    ConnectorInstallation,
    ConnectorInstallationStatus,
    ConnectorQualification,
    ConnectorQualificationCapability,
    QualificationResult,
)
from daher_adapter.exchange.models import (
    FacilityEnrollment,
    Organization,
    ParticipationMode,
)
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
from daher_adapter.models import utc_now


class ConnectorInstallationNotFound(RuntimeError):
    """No such installation, or it belongs to a different facility than the caller claimed."""


class FacilityNotConnect(RuntimeError):
    """A connector was offered to a facility that does not use one.

    A NATIVE facility's staff work in DHealth directly -- that is what NATIVE means. Registering a
    connector for one would create a facility that is simultaneously its own workflow system and a
    partner integration, and every downstream rule would have to pick which. Refused at the one
    place installations are created rather than papered over later.
    """


class ConnectorAlreadyInstalled(RuntimeError):
    """This facility already has a live connector.

    Retire the existing one first. Two live installations would make "which connector is this
    facility's" a question answered by creation order, and an authority decided by recency is the
    race the qualification model is built to avoid.
    """


class InstallationRetired(RuntimeError):
    """A retired installation cannot be changed, qualified, or brought back.

    Retirement means "this is no longer the intended production integration". Evidence is kept
    forever; the row is simply never a candidate again. Bringing one back would make the retirement
    decision reversible-by-accident, so the supported path is registering a successor.
    """


@dataclass(frozen=True)
class CapabilityOutcome:
    """One capability a qualification run exercised, and what happened."""

    capability: Capability
    passed: bool
    detail: str | None = None


@dataclass(frozen=True)
class ConnectorInstallationRecord:
    id: uuid.UUID
    organization_id: uuid.UUID
    connector_key: str
    connector_version: str
    contract_version: str
    source_system: str
    source_system_version: str | None
    interface_type: str
    configuration_fingerprint: str
    mapping_fingerprint: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConnectorQualificationRecord:
    id: uuid.UUID
    connector_installation_id: uuid.UUID
    contract_version: str
    connector_version: str
    configuration_fingerprint: str
    mapping_fingerprint: str
    suite_version: str
    started_at: datetime
    completed_at: datetime
    result: str
    evidence_summary: str | None
    capabilities: tuple[CapabilityOutcome, ...]


def _to_installation_record(row: ConnectorInstallation) -> ConnectorInstallationRecord:
    return ConnectorInstallationRecord(
        id=row.id,
        organization_id=row.organization_id,
        connector_key=row.connector_key,
        connector_version=row.connector_version,
        contract_version=row.contract_version,
        source_system=row.source_system,
        source_system_version=row.source_system_version,
        interface_type=row.interface_type,
        configuration_fingerprint=row.configuration_fingerprint,
        mapping_fingerprint=row.mapping_fingerprint,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class ConnectorInstallationRepository:
    """Registration, lifecycle, and the one authorized path to a qualification row."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        security_audit: ExchangeSecurityAuditRepository | None = None,
    ) -> None:
        self._sessions = sessions
        self._audit = security_audit or ExchangeSecurityAuditRepository()

    # -- registration ------------------------------------------------------------------------

    def register(
        self,
        *,
        organization_id: uuid.UUID,
        connector_key: str,
        connector_version: str,
        source_system: str,
        interface_type: str,
        configuration_fingerprint: str,
        mapping_fingerprint: str,
        actor_identity_id: uuid.UUID,
        source_system_version: str | None = None,
        contract_version: str = CONTRACT_VERSION,
        session: Session | None = None,
    ) -> ConnectorInstallationRecord:
        if session is not None:
            return self._register_in_session(
                session,
                organization_id=organization_id,
                connector_key=connector_key,
                connector_version=connector_version,
                source_system=source_system,
                source_system_version=source_system_version,
                interface_type=interface_type,
                configuration_fingerprint=configuration_fingerprint,
                mapping_fingerprint=mapping_fingerprint,
                contract_version=contract_version,
                actor_identity_id=actor_identity_id,
            )
        with self._sessions.begin() as owned:
            return self._register_in_session(
                owned,
                organization_id=organization_id,
                connector_key=connector_key,
                connector_version=connector_version,
                source_system=source_system,
                source_system_version=source_system_version,
                interface_type=interface_type,
                configuration_fingerprint=configuration_fingerprint,
                mapping_fingerprint=mapping_fingerprint,
                contract_version=contract_version,
                actor_identity_id=actor_identity_id,
            )

    def _register_in_session(
        self,
        session: Session,
        *,
        organization_id: uuid.UUID,
        connector_key: str,
        connector_version: str,
        source_system: str,
        source_system_version: str | None,
        interface_type: str,
        configuration_fingerprint: str,
        mapping_fingerprint: str,
        contract_version: str,
        actor_identity_id: uuid.UUID,
    ) -> ConnectorInstallationRecord:
        organization = session.get(Organization, organization_id)
        if organization is None:
            raise ConnectorInstallationNotFound(f"organization {organization_id} does not exist")

        enrollment = session.scalar(
            select(FacilityEnrollment).where(FacilityEnrollment.organization_id == organization_id)
        )
        # Fail closed on a missing enrollment too: an organization nobody enrolled has not declared
        # a participation mode, and defaulting an undeclared facility into CONNECT would be exactly
        # the kind of inference the enrollment model exists to prevent.
        if enrollment is None or enrollment.participation_mode != ParticipationMode.CONNECT.value:
            raise FacilityNotConnect(
                f"organization {organization_id} does not participate through DHealth Connect"
            )

        if contract_major(contract_version) != contract_major(CONTRACT_VERSION):
            raise ValueError(
                f"contract version {contract_version!r} is not this build's major version "
                f"({CONTRACT_VERSION!r})"
            )

        existing = session.scalar(
            select(ConnectorInstallation).where(
                ConnectorInstallation.organization_id == organization_id,
                ConnectorInstallation.status != ConnectorInstallationStatus.RETIRED.value,
            )
        )
        if existing is not None:
            raise ConnectorAlreadyInstalled(
                f"organization {organization_id} already has a live connector installation "
                f"({existing.connector_key} {existing.connector_version}); retire it first"
            )

        row = ConnectorInstallation(
            organization_id=organization_id,
            connector_key=connector_key,
            connector_version=connector_version,
            contract_version=contract_version,
            source_system=source_system,
            source_system_version=source_system_version,
            interface_type=interface_type,
            configuration_fingerprint=configuration_fingerprint,
            mapping_fingerprint=mapping_fingerprint,
            status=ConnectorInstallationStatus.REGISTERED.value,
        )
        session.add(row)
        session.flush()
        self._audit.record(
            session,
            event_type="connector_registered",
            actor_identity_id=actor_identity_id,
            organization_id=organization_id,
            outcome="created",
            resource_type="connector_installation",
            resource_id=row.id,
        )
        return _to_installation_record(row)

    # -- lifecycle ---------------------------------------------------------------------------

    def _set_status(
        self,
        session: Session,
        *,
        installation_id: uuid.UUID,
        status: ConnectorInstallationStatus,
        event_type: str,
        actor_identity_id: uuid.UUID,
    ) -> ConnectorInstallationRecord:
        row = session.get(ConnectorInstallation, installation_id, with_for_update=True)
        if row is None:
            raise ConnectorInstallationNotFound(f"connector installation {installation_id}")
        if row.status == ConnectorInstallationStatus.RETIRED.value:
            raise InstallationRetired(
                f"connector installation {installation_id} is retired and cannot be changed"
            )
        if row.status == status.value:
            # Idempotent replay: already in the requested state, so no mutation and no audit row.
            # Same discipline as every other repository here -- a second call reports the existing
            # fact rather than writing a second event describing a change that did not happen.
            return _to_installation_record(row)
        row.status = status.value
        row.updated_at = utc_now()
        session.flush()
        self._audit.record(
            session,
            event_type=event_type,  # type: ignore[arg-type]
            actor_identity_id=actor_identity_id,
            organization_id=row.organization_id,
            outcome="updated",
            resource_type="connector_installation",
            resource_id=row.id,
        )
        return _to_installation_record(row)

    def suspend(
        self,
        *,
        installation_id: uuid.UUID,
        actor_identity_id: uuid.UUID,
        session: Session | None = None,
    ) -> ConnectorInstallationRecord:
        """Stop this connector carrying production traffic, without erasing anything.

        Qualification history stays, the facility stays, the configured provider-lab route stays.
        Restoring it may make the route eligible again if everything else still passes -- exactly
        the philosophy the Exchange already applies when a laboratory loses its last releaser.
        """
        if session is not None:
            return self._set_status(
                session,
                installation_id=installation_id,
                status=ConnectorInstallationStatus.SUSPENDED,
                event_type="connector_suspended",
                actor_identity_id=actor_identity_id,
            )
        with self._sessions.begin() as owned:
            return self._set_status(
                owned,
                installation_id=installation_id,
                status=ConnectorInstallationStatus.SUSPENDED,
                event_type="connector_suspended",
                actor_identity_id=actor_identity_id,
            )

    def restore(
        self,
        *,
        installation_id: uuid.UUID,
        actor_identity_id: uuid.UUID,
        session: Session | None = None,
    ) -> ConnectorInstallationRecord:
        """Lift a suspension. Grants nothing on its own -- qualification is still evaluated."""
        if session is not None:
            return self._set_status(
                session,
                installation_id=installation_id,
                status=ConnectorInstallationStatus.REGISTERED,
                event_type="connector_restored",
                actor_identity_id=actor_identity_id,
            )
        with self._sessions.begin() as owned:
            return self._set_status(
                owned,
                installation_id=installation_id,
                status=ConnectorInstallationStatus.REGISTERED,
                event_type="connector_restored",
                actor_identity_id=actor_identity_id,
            )

    def retire(
        self,
        *,
        installation_id: uuid.UUID,
        actor_identity_id: uuid.UUID,
        session: Session | None = None,
    ) -> ConnectorInstallationRecord:
        """Out of service permanently. Evidence is kept; this row never qualifies anything again."""
        if session is not None:
            return self._set_status(
                session,
                installation_id=installation_id,
                status=ConnectorInstallationStatus.RETIRED,
                event_type="connector_retired",
                actor_identity_id=actor_identity_id,
            )
        with self._sessions.begin() as owned:
            return self._set_status(
                owned,
                installation_id=installation_id,
                status=ConnectorInstallationStatus.RETIRED,
                event_type="connector_retired",
                actor_identity_id=actor_identity_id,
            )

    # -- configuration drift -----------------------------------------------------------------

    def update_fingerprints(
        self,
        *,
        installation_id: uuid.UUID,
        actor_identity_id: uuid.UUID,
        configuration_fingerprint: str | None = None,
        mapping_fingerprint: str | None = None,
        connector_version: str | None = None,
        source_system_version: str | None = None,
        session: Session | None = None,
    ) -> ConnectorInstallationRecord:
        """Record that the installed thing changed.

        This is how qualification goes stale, and it goes stale *by itself*: the previous
        qualification row keeps the fingerprints it actually tested, this row now carries different
        ones, and ``applicable_qualification`` stops matching them. Nothing sets a flag, and there
        is no code path that could forget to.
        """
        if session is not None:
            return self._update_fingerprints_in_session(
                session,
                installation_id=installation_id,
                actor_identity_id=actor_identity_id,
                configuration_fingerprint=configuration_fingerprint,
                mapping_fingerprint=mapping_fingerprint,
                connector_version=connector_version,
                source_system_version=source_system_version,
            )
        with self._sessions.begin() as owned:
            return self._update_fingerprints_in_session(
                owned,
                installation_id=installation_id,
                actor_identity_id=actor_identity_id,
                configuration_fingerprint=configuration_fingerprint,
                mapping_fingerprint=mapping_fingerprint,
                connector_version=connector_version,
                source_system_version=source_system_version,
            )

    def _update_fingerprints_in_session(
        self,
        session: Session,
        *,
        installation_id: uuid.UUID,
        actor_identity_id: uuid.UUID,
        configuration_fingerprint: str | None,
        mapping_fingerprint: str | None,
        connector_version: str | None,
        source_system_version: str | None,
    ) -> ConnectorInstallationRecord:
        row = session.get(ConnectorInstallation, installation_id, with_for_update=True)
        if row is None:
            raise ConnectorInstallationNotFound(f"connector installation {installation_id}")
        if row.status == ConnectorInstallationStatus.RETIRED.value:
            raise InstallationRetired(
                f"connector installation {installation_id} is retired and cannot be changed"
            )
        if configuration_fingerprint is not None:
            row.configuration_fingerprint = configuration_fingerprint
        if mapping_fingerprint is not None:
            row.mapping_fingerprint = mapping_fingerprint
        if connector_version is not None:
            row.connector_version = connector_version
        if source_system_version is not None:
            row.source_system_version = source_system_version
        row.updated_at = utc_now()
        session.flush()
        self._audit.record(
            session,
            event_type="connector_configuration_changed",  # type: ignore[arg-type]
            actor_identity_id=actor_identity_id,
            organization_id=row.organization_id,
            outcome="updated",
            resource_type="connector_installation",
            resource_id=row.id,
        )
        return _to_installation_record(row)

    # -- qualification evidence --------------------------------------------------------------

    def record_qualification(
        self,
        *,
        installation_id: uuid.UUID,
        suite_version: str,
        started_at: datetime,
        completed_at: datetime,
        result: QualificationResult,
        capabilities: Sequence[CapabilityOutcome],
        actor_identity_id: uuid.UUID,
        evidence_summary: str | None = None,
        session: Session | None = None,
    ) -> ConnectorQualificationRecord:
        """Write one completed qualification attempt. Append-only, always.

        The version and fingerprint columns are copied from the installation **inside this call**,
        under a row lock, so the evidence names exactly what was in place when the run completed.
        A later configuration change edits the installation and leaves this row alone -- which is
        what makes the row evidence rather than a cache.

        There is no update path. A failed attempt stays failed forever and a later success is a new
        row beside it, so "has this connector ever passed, and against what" is always answerable.
        """
        if session is not None:
            return self._record_qualification_in_session(
                session,
                installation_id=installation_id,
                suite_version=suite_version,
                started_at=started_at,
                completed_at=completed_at,
                result=result,
                capabilities=capabilities,
                actor_identity_id=actor_identity_id,
                evidence_summary=evidence_summary,
            )
        with self._sessions.begin() as owned:
            return self._record_qualification_in_session(
                owned,
                installation_id=installation_id,
                suite_version=suite_version,
                started_at=started_at,
                completed_at=completed_at,
                result=result,
                capabilities=capabilities,
                actor_identity_id=actor_identity_id,
                evidence_summary=evidence_summary,
            )

    def _record_qualification_in_session(
        self,
        session: Session,
        *,
        installation_id: uuid.UUID,
        suite_version: str,
        started_at: datetime,
        completed_at: datetime,
        result: QualificationResult,
        capabilities: Sequence[CapabilityOutcome],
        actor_identity_id: uuid.UUID,
        evidence_summary: str | None,
    ) -> ConnectorQualificationRecord:
        installation = session.get(ConnectorInstallation, installation_id, with_for_update=True)
        if installation is None:
            raise ConnectorInstallationNotFound(f"connector installation {installation_id}")
        if installation.status == ConnectorInstallationStatus.RETIRED.value:
            raise InstallationRetired(
                f"connector installation {installation_id} is retired; qualifying it would produce "
                "evidence about something nobody intends to run"
            )

        row = ConnectorQualification(
            connector_installation_id=installation.id,
            contract_version=installation.contract_version,
            connector_version=installation.connector_version,
            configuration_fingerprint=installation.configuration_fingerprint,
            mapping_fingerprint=installation.mapping_fingerprint,
            suite_version=suite_version,
            started_at=started_at,
            completed_at=completed_at,
            result=result.value,
            evidence_summary=evidence_summary,
        )
        session.add(row)
        session.flush()

        seen: set[Capability] = set()
        for outcome in capabilities:
            if outcome.capability in seen:
                raise ValueError(
                    f"capability {outcome.capability.value} reported twice in one qualification"
                )
            seen.add(outcome.capability)
            session.add(
                ConnectorQualificationCapability(
                    qualification_id=row.id,
                    capability=outcome.capability.value,
                    passed=outcome.passed,
                    detail=outcome.detail,
                )
            )
        session.flush()

        # One event type; the outcome distinguishes a pass from a failure. A second type would be a
        # synonym, and synonyms in this vocabulary cost a migration to say the same thing twice.
        self._audit.record(
            session,
            event_type="connector_qualification_recorded",  # type: ignore[arg-type]
            actor_identity_id=actor_identity_id,
            organization_id=installation.organization_id,
            outcome="created" if result is QualificationResult.PASSED else "refused",
            resource_type="connector_installation",
            resource_id=installation.id,
        )
        return ConnectorQualificationRecord(
            id=row.id,
            connector_installation_id=row.connector_installation_id,
            contract_version=row.contract_version,
            connector_version=row.connector_version,
            configuration_fingerprint=row.configuration_fingerprint,
            mapping_fingerprint=row.mapping_fingerprint,
            suite_version=row.suite_version,
            started_at=row.started_at,
            completed_at=row.completed_at,
            result=row.result,
            evidence_summary=row.evidence_summary,
            capabilities=tuple(capabilities),
        )

    # -- reads -------------------------------------------------------------------------------

    def list_for_organization(
        self, organization_id: uuid.UUID, *, session: Session | None = None
    ) -> list[ConnectorInstallationRecord]:
        def _read(s: Session) -> list[ConnectorInstallationRecord]:
            rows = s.scalars(
                select(ConnectorInstallation)
                .where(ConnectorInstallation.organization_id == organization_id)
                .order_by(ConnectorInstallation.created_at.desc())
            ).all()
            return [_to_installation_record(row) for row in rows]

        if session is not None:
            return _read(session)
        with self._sessions() as owned:
            return _read(owned)

    def qualification_history(
        self, installation_id: uuid.UUID, *, session: Session | None = None
    ) -> list[ConnectorQualificationRecord]:
        """Every attempt, newest first. Nothing is ever removed from this list."""

        def _read(s: Session) -> list[ConnectorQualificationRecord]:
            rows = s.scalars(
                select(ConnectorQualification)
                .where(ConnectorQualification.connector_installation_id == installation_id)
                .order_by(ConnectorQualification.completed_at.desc())
            ).all()
            out: list[ConnectorQualificationRecord] = []
            for row in rows:
                caps = s.scalars(
                    select(ConnectorQualificationCapability)
                    .where(ConnectorQualificationCapability.qualification_id == row.id)
                    .order_by(ConnectorQualificationCapability.capability)
                ).all()
                out.append(
                    ConnectorQualificationRecord(
                        id=row.id,
                        connector_installation_id=row.connector_installation_id,
                        contract_version=row.contract_version,
                        connector_version=row.connector_version,
                        configuration_fingerprint=row.configuration_fingerprint,
                        mapping_fingerprint=row.mapping_fingerprint,
                        suite_version=row.suite_version,
                        started_at=row.started_at,
                        completed_at=row.completed_at,
                        result=row.result,
                        evidence_summary=row.evidence_summary,
                        capabilities=tuple(
                            CapabilityOutcome(
                                capability=Capability(c.capability),
                                passed=c.passed,
                                detail=c.detail,
                            )
                            for c in caps
                        ),
                    )
                )
            return out

        if session is not None:
            return _read(session)
        with self._sessions() as owned:
            return _read(owned)


#: Convenience for tests and the harness: capability outcomes keyed by capability.
def outcomes_from(mapping: Mapping[Capability, bool]) -> tuple[CapabilityOutcome, ...]:
    return tuple(
        CapabilityOutcome(capability=cap, passed=ok)
        for cap, ok in sorted(mapping.items(), key=lambda kv: kv[0].value)
    )
