"""DH-CONNECT-CONTRACT-001 — what "qualified" means, computed rather than stored.

One function decides it, and everything else -- readiness, the platform API, the operator screen --
reads that one function. There is no ``qualified`` column to disagree with it and no operator
gesture that can set one.

    qualified  = there is a PASSED qualification that still applies to this exact installation
    operational= a live runtime is reachable and working right now
    routable   = both, plus everything else the facility's readiness already requires

Those are three different questions, and this module answers only the first. The second has no
answer in this build because no runtime exists, which is why ``connector_operational`` is
unconditionally false and says so in plain words rather than being quietly omitted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    Capability,
    HealthState,
    contract_major,
    required_capabilities,
)
from daher_adapter.connect.models import (
    ConnectorInstallation,
    ConnectorInstallationStatus,
    ConnectorQualification,
    ConnectorQualificationCapability,
    QualificationResult,
)

#: The revision of the qualification scenario set. Bump when a scenario is added or changed, so an
#: older PASS is not credited with having proved something that did not exist when it ran.
QUALIFICATION_SUITE_VERSION = "1"


class QualificationGap(StrEnum):
    """Why a facility's connector is not qualified, precisely enough to act on.

    Not one flattened "not qualified". "You never registered a connector", "the one you registered
    has never been through qualification", "it passed, but against a mapping you have since
    changed" and "it passed everything except amendments" are four different pieces of work for
    four different people, and collapsing them into a single refusal makes the operator guess.

    These double as the API's error vocabulary and as the ``detail`` on the readiness finding, so
    the same distinction reaches the screen without being re-derived anywhere.
    """

    NO_INSTALLATION = "NO_INSTALLATION"
    NEVER_QUALIFIED = "NEVER_QUALIFIED"
    LAST_ATTEMPT_FAILED = "LAST_ATTEMPT_FAILED"
    QUALIFICATION_STALE = "QUALIFICATION_STALE"
    CONTRACT_MAJOR_MISMATCH = "CONTRACT_MAJOR_MISMATCH"
    REQUIRED_CAPABILITY_MISSING = "REQUIRED_CAPABILITY_MISSING"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


@dataclass(frozen=True)
class QualificationVerdict:
    """The whole answer: whether it is qualified, and if not, exactly why not."""

    qualified: bool
    gap: QualificationGap | None
    qualification_id: uuid.UUID | None = None
    completed_at: datetime | None = None
    #: Required capabilities that did not pass, when that is the reason.
    missing_capabilities: tuple[Capability, ...] = ()
    #: What the installation declares it implements, when that is what disqualifies it.
    installed_contract_version: str | None = None

    @property
    def detail(self) -> str:
        """One French sentence for the readiness finding and the operator screen.

        Written here rather than in React because the reason is derived from the same rows as the
        verdict and splitting them would let the two drift -- the same argument that keeps every
        readiness rule server-side.
        """
        if self.qualified:
            return "connecteur qualifié pour cette configuration"
        match self.gap:
            case QualificationGap.NO_INSTALLATION:
                return "aucun connecteur n'est enregistré pour cet établissement"
            case QualificationGap.NEVER_QUALIFIED:
                return "le connecteur enregistré n'a pas encore été qualifié"
            case QualificationGap.LAST_ATTEMPT_FAILED:
                return "la dernière qualification a échoué"
            case QualificationGap.QUALIFICATION_STALE:
                return (
                    "la configuration ou le connecteur ont changé depuis la dernière "
                    "qualification réussie ; une nouvelle qualification est requise"
                )
            case QualificationGap.CONTRACT_MAJOR_MISMATCH:
                return (
                    "le connecteur implémente le contrat "
                    f"{self.installed_contract_version or '?'} ; DHealth applique "
                    f"{CONTRACT_VERSION} et une nouvelle qualification est requise"
                )
            case QualificationGap.REQUIRED_CAPABILITY_MISSING:
                names = ", ".join(c.value for c in self.missing_capabilities)
                return f"capacités requises non démontrées : {names}"
            case QualificationGap.SUSPENDED:
                return "le connecteur est suspendu"
            case QualificationGap.RETIRED:
                return "le connecteur a été retiré du service"
            case _:
                return "connecteur non qualifié"


def active_installation_in_session(
    session: Session, organization_id: uuid.UUID
) -> ConnectorInstallation | None:
    """The installation that would carry this facility's traffic, retired ones excluded.

    A retired installation is history, not a candidate: it is kept so the evidence survives, and it
    can never qualify the facility again. A *suspended* one is still returned, because the operator
    needs to see it and be told it is suspended -- silently reporting "no connector registered" for
    a connector somebody deliberately suspended would hide the very decision they made.
    """
    return session.scalar(
        select(ConnectorInstallation)
        .where(
            ConnectorInstallation.organization_id == organization_id,
            ConnectorInstallation.status != ConnectorInstallationStatus.RETIRED.value,
        )
        .order_by(ConnectorInstallation.created_at.desc(), ConnectorInstallation.id)
        .limit(1)
    )


def _passed_capabilities(session: Session, qualification_id: uuid.UUID) -> set[Capability]:
    rows = session.scalars(
        select(ConnectorQualificationCapability.capability).where(
            ConnectorQualificationCapability.qualification_id == qualification_id,
            ConnectorQualificationCapability.passed.is_(True),
        )
    ).all()
    return {Capability(row) for row in rows}


def applicable_qualification(
    session: Session, installation: ConnectorInstallation
) -> ConnectorQualification | None:
    """The PASSED qualification that still describes this exact installation, if there is one.

    Applicability is **exact equality on what was tested**, never recency:

        contract version, connector version, configuration fingerprint, mapping fingerprint

    That is deliberately not "the most recent PASS". Consider two qualification runs completing
    concurrently, one for the build currently installed and one for a build that has since been
    replaced: ordering by completion time could let the stale run qualify the new installation,
    because last-write-wins is a race, not a decision. Matching on fingerprints makes the answer
    independent of timing -- a run either tested this thing or it did not.

    Among runs that do match, the tie-break is ``completed_at`` then ``id``, so the answer is
    deterministic rather than whatever the database happens to return first. All matching runs
    describe the same tested artefact, so which one is returned changes nothing about the verdict.
    """
    return session.scalar(
        select(ConnectorQualification)
        .where(
            ConnectorQualification.connector_installation_id == installation.id,
            ConnectorQualification.result == QualificationResult.PASSED.value,
            ConnectorQualification.contract_version == installation.contract_version,
            ConnectorQualification.connector_version == installation.connector_version,
            ConnectorQualification.configuration_fingerprint
            == installation.configuration_fingerprint,
            ConnectorQualification.mapping_fingerprint == installation.mapping_fingerprint,
        )
        .order_by(ConnectorQualification.completed_at.desc(), ConnectorQualification.id)
        .limit(1)
    )


def qualification_verdict_in_session(
    session: Session, organization_id: uuid.UUID, organization_type: str
) -> QualificationVerdict:
    """Is this facility's connector qualified, and if not, what exactly is missing.

    Fails closed at every branch. An organization type this contract has no bundle for raises
    rather than qualifying trivially; an installation whose contract version this build no longer
    implements is stale rather than grandfathered.
    """
    installation = active_installation_in_session(session, organization_id)
    if installation is None:
        # Either nothing was ever registered, or everything registered has been retired. Both mean
        # no connector can carry this facility's work; the screen distinguishes them by listing
        # retired installations separately.
        retired = session.scalar(
            select(ConnectorInstallation.id).where(
                ConnectorInstallation.organization_id == organization_id,
                ConnectorInstallation.status == ConnectorInstallationStatus.RETIRED.value,
            )
        )
        return QualificationVerdict(
            qualified=False,
            gap=QualificationGap.RETIRED if retired else QualificationGap.NO_INSTALLATION,
        )

    if installation.status == ConnectorInstallationStatus.SUSPENDED.value:
        # Deliberately checked before qualification: a suspended connector may well hold a perfectly
        # valid PASS, and saying "qualified" about something somebody switched off would be true
        # and useless. Suspension does not delete the evidence; it just is not the answer today.
        return QualificationVerdict(qualified=False, gap=QualificationGap.SUSPENDED)

    if not contract_version_current(installation):
        # A connector built against a different major version of this contract is not "probably
        # fine". A major bump is by definition a change a connector cannot be assumed to survive,
        # which is why §57 requires requalification rather than a compatibility guess. Checked
        # before the qualification lookup so a PASS row that happens to match every fingerprint
        # still cannot carry an obsolete contract forward.
        return QualificationVerdict(
            qualified=False,
            gap=QualificationGap.CONTRACT_MAJOR_MISMATCH,
            installed_contract_version=installation.contract_version,
        )

    qualification = applicable_qualification(session, installation)
    if qualification is None:
        any_attempt = session.scalar(
            select(ConnectorQualification.result)
            .where(ConnectorQualification.connector_installation_id == installation.id)
            .order_by(ConnectorQualification.completed_at.desc())
            .limit(1)
        )
        if any_attempt is None:
            return QualificationVerdict(qualified=False, gap=QualificationGap.NEVER_QUALIFIED)
        # There is history, but none of it describes what is installed now. Distinguishing "you
        # tried and failed" from "you passed, then changed something" matters: the first needs a
        # connector fix, the second needs a rerun.
        passed_before = session.scalar(
            select(ConnectorQualification.id).where(
                ConnectorQualification.connector_installation_id == installation.id,
                ConnectorQualification.result == QualificationResult.PASSED.value,
            )
        )
        return QualificationVerdict(
            qualified=False,
            gap=(
                QualificationGap.QUALIFICATION_STALE
                if passed_before
                else QualificationGap.LAST_ATTEMPT_FAILED
            ),
        )

    required = required_capabilities(organization_type)
    passed = _passed_capabilities(session, qualification.id)
    missing = tuple(sorted(required - passed, key=lambda c: c.value))
    if missing:
        # A run can complete and be recorded PASSED for the scenarios it covered while still not
        # covering the production bundle -- a laboratory connector that receives orders and cannot
        # publish results has passed something, and it has not passed enough to take clinical work.
        return QualificationVerdict(
            qualified=False,
            gap=QualificationGap.REQUIRED_CAPABILITY_MISSING,
            qualification_id=qualification.id,
            completed_at=qualification.completed_at,
            missing_capabilities=missing,
        )

    return QualificationVerdict(
        qualified=True,
        gap=None,
        qualification_id=qualification.id,
        completed_at=qualification.completed_at,
    )


def health_state_in_session(
    session: Session,  # noqa: ARG001 -- signature is the contract; no runtime exists to query yet
    organization_id: uuid.UUID,  # noqa: ARG001
) -> HealthState:
    """How the connector is doing right now.

    ``UNKNOWN``, always, in this build -- and that is the honest answer rather than a stub. There
    is no connector runtime, no heartbeat, no health endpoint and no agent, so DHealth has no
    evidence about reachability and says so. DH-CONNECT-AGENT-001 replaces this body with a real
    query; until it does, nothing in the system may report a connector as ``HEALTHY``, because
    nothing in the system has observed one.

    The signature exists now so the readiness rule that consumes it is written against the real
    shape, and so turning it on later is a change to one function rather than to every caller.
    """
    return HealthState.UNKNOWN


def connector_operational_in_session(session: Session, organization_id: uuid.UUID) -> bool:
    """Whether a live runtime is currently carrying this facility's traffic.

    False while health is anything but ``HEALTHY``. Since health is unconditionally ``UNKNOWN``
    today, this is false for every CONNECT facility in this build -- which is exactly why a CONNECT
    facility remains unroutable no matter how much qualification evidence exists. Qualification
    proves a connector *could* work; only a runtime proves one *is* working, and this ticket builds
    the first without pretending to the second.
    """
    return health_state_in_session(session, organization_id) == HealthState.HEALTHY


def contract_version_current(installation: ConnectorInstallation) -> bool:
    """Whether this installation implements the contract major version this build speaks.

    An unparseable version is **not** current. Failing closed here matters: the alternative would
    let a malformed or unknown contract string read as compatible, which is precisely the silent
    carry-forward this rule exists to prevent.
    """
    try:
        return contract_major(installation.contract_version) == contract_major(CONTRACT_VERSION)
    except ValueError:
        return False
