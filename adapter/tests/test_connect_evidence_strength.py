"""DH-CONNECT-CONTRACT-001 correction — DECLARED ≠ EXERCISED ≠ QUALIFIED.

The original ticket recorded ``ORDER_RECEIPT`` as ``passed=True`` with the words "declaration only"
in a prose ``detail``, and the verdict counted every passing row toward the required production
bundle. Nothing was routable, so nothing was visibly wrong -- and DH-CONNECT-AGENT-001 is exactly
the ticket that supplies the missing runtime. On the day it lands, a connector that had only
*claimed* it could receive orders would have become production-routable without DHealth ever having
watched it receive one.

These tests are the guard. Every one of them is about the gap between what a connector says and
what DHealth has seen, and the most important pair is ES3/ES6: the synthetic laboratory harness --
the best evidence this build can produce -- does **not** reach production qualification, and the
only way to get there in a test is to hand-build evidence no run produced.

No external system is contacted. No transport exists. Every value below is synthetic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from exchange_support import make_lab, make_provider
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import Capability, fingerprint, optional_capabilities
from daher_adapter.connect.models import EvidenceLevel, QualificationResult
from daher_adapter.connect.qualification import (
    QUALIFICATION_SUITE_VERSION,
    QualificationGap,
    connector_operational_in_session,
    qualification_verdict_in_session,
)
from daher_adapter.connect.repository import (
    CapabilityOutcome,
    ConnectorInstallationRepository,
)
from daher_adapter.exchange.facility_enrollment import (
    FacilityEnrollmentRepository,
    is_operationally_ready_in_session,
    operational_findings_in_session,
)
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    OnboardingStatus,
    OrganizationType,
    ParticipationMode,
)
from daher_adapter.exchange.organizations import OrganizationRepository

CONFIG_FP = fingerprint({"endpoint": "https://lis.invalid/fhir", "timeout_s": 30})
MAPPING_FP = fingerprint({"mapping_revision": [["synthetic-lis", "GLU", "cat-1", "GLU"]]})

LAB_REQUIRED = (
    Capability.ORDER_RECEIPT,
    Capability.BUSINESS_ACCEPTANCE,
    Capability.COLLECTION_EVENT,
    Capability.RELEASED_RESULT_PUBLICATION,
    Capability.AMENDMENT_PUBLICATION,
)
PROVIDER_REQUIRED = (
    Capability.ORDER_SUBMISSION,
    Capability.RELEASED_RESULT_RECEIPT,
    Capability.AMENDMENT_RECEIPT,
)


@pytest.fixture
def repos(sessions: sessionmaker[Session]):
    organizations = OrganizationRepository(sessions)
    return {
        "sessions": sessions,
        "organizations": organizations,
        "enrollments": FacilityEnrollmentRepository(sessions),
        "identities": ExchangeIdentityRepository(sessions),
        "connectors": ConnectorInstallationRepository(sessions),
    }


def _actor(repos) -> uuid.UUID:
    identity, _ = repos["identities"].get_or_create(
        issuer="https://issuer.example",
        subject="platform-admin",
        display_name="Opérateur",
        email=None,
    )
    return identity.id


def _connect_facility(repos, *, provider: bool = False, name="Laboratoire Central"):
    org = (
        make_provider(repos["organizations"], name, operational=False)
        if provider
        else make_lab(repos["organizations"], name, operational=False)
    )
    repos["enrollments"].create(
        organization_id=org.id,
        participation_mode=ParticipationMode.CONNECT,
        onboarding_status=OnboardingStatus.AWAITING_CONNECTOR,
    )
    return org


def _register(repos, organization_id, **overrides):
    payload = {
        "organization_id": organization_id,
        "connector_key": "openelis-fhir",
        "connector_version": "0.1",
        "source_system": "OpenELIS",
        "interface_type": "FHIR",
        "configuration_fingerprint": CONFIG_FP,
        "mapping_fingerprint": MAPPING_FP,
        "actor_identity_id": _actor(repos),
    }
    payload.update(overrides)
    return repos["connectors"].register(**payload)


def _record(repos, installation_id, outcomes, *, suite_version=None):
    """Write a qualification from explicit ``CapabilityOutcome``s.

    No helper here fills in an evidence level. Every test states, per capability, whether DHealth
    drove it or the connector merely claimed it -- which is the distinction under test, and a
    helper that guessed would be testing the helper.
    """
    started = datetime.now(UTC) - timedelta(minutes=5)
    return repos["connectors"].record_qualification(
        installation_id=installation_id,
        suite_version=suite_version or QUALIFICATION_SUITE_VERSION,
        started_at=started,
        completed_at=started + timedelta(minutes=4),
        result=QualificationResult.PASSED,
        capabilities=outcomes,
        actor_identity_id=_actor(repos),
        evidence_summary="fixture",
    )


def _bundle(capabilities, level, *, passed=True):
    return [
        CapabilityOutcome(capability=c, passed=passed, evidence_level=level) for c in capabilities
    ]


def _verdict(repos, organization_id, org_type=OrganizationType.LAB):
    with repos["sessions"]() as session:
        return qualification_verdict_in_session(session, organization_id, org_type)


# ── ES1/ES2: the rule itself ─────────────────────────────────────────────────────────────────


def test_ES1_a_declared_required_capability_does_not_qualify(repos) -> None:
    """The whole correction in one assertion.

    Every required capability passed. One of them passed because the connector said so. That is
    not a qualification, and before this correction it was.
    """
    lab = _connect_facility(repos)
    installation = _register(repos, lab.id)
    _record(
        repos,
        installation.id,
        _bundle([Capability.ORDER_RECEIPT], EvidenceLevel.DECLARED)
        + _bundle(
            [c for c in LAB_REQUIRED if c is not Capability.ORDER_RECEIPT],
            EvidenceLevel.EXERCISED,
        ),
    )

    verdict = _verdict(repos, lab.id)
    assert verdict.qualified is False
    assert verdict.gap is QualificationGap.REQUIRED_CAPABILITY_UNEXERCISED
    assert verdict.unexercised_capabilities == (Capability.ORDER_RECEIPT,)


def test_ES2_the_same_capability_exercised_does_qualify(repos) -> None:
    """The control for ES1. The only difference between the two is one word in one column.

    Hand-built evidence. No run in this build produces an exercised ORDER_RECEIPT, and this test
    is not a claim that one does -- it exists so ES1's failure is attributable to the evidence
    level rather than to something else about the fixture.
    """
    lab = _connect_facility(repos)
    installation = _register(repos, lab.id)
    _record(repos, installation.id, _bundle(LAB_REQUIRED, EvidenceLevel.EXERCISED))

    verdict = _verdict(repos, lab.id)
    assert verdict.qualified is True
    assert verdict.gap is None
    assert verdict.unexercised_capabilities == ()


# ── ES3/ES4: what the real synthetic harness actually achieves ───────────────────────────────


def test_ES3_the_synthetic_lab_harness_does_not_reach_production_qualification(repos) -> None:
    """§5/§18: the best evidence this build can produce is not enough, and says so.

    Reproduced here from capability outcomes rather than by running the harness -- the harness's
    own end-to-end result is pinned by ``test_AC_the_whole_connect_contract_journey``. What this
    adds is the statement in isolation: four exercised, one declared, not qualified.
    """
    lab = _connect_facility(repos)
    installation = _register(repos, lab.id)
    _record(
        repos,
        installation.id,
        [
            CapabilityOutcome(
                capability=Capability.ORDER_RECEIPT,
                passed=True,
                evidence_level=EvidenceLevel.DECLARED,
                detail="déclaration seule : aucun transport n'existe pour livrer une commande",
            ),
            *_bundle(
                [c for c in LAB_REQUIRED if c is not Capability.ORDER_RECEIPT],
                EvidenceLevel.EXERCISED,
            ),
        ],
    )

    verdict = _verdict(repos, lab.id)
    assert verdict.qualified is False
    with repos["sessions"]() as session:
        findings = {
            f.code: (f.satisfied, f.blocking)
            for f in operational_findings_in_session(session, lab.id)
        }
    assert findings["connector_qualified"] == (False, True)


def test_ES4_the_gap_names_the_unexercised_capability_and_not_a_missing_one(repos) -> None:
    """§4: "you never built it" and "we cannot yet prove what you built" are different bug reports.

    Flattening them into REQUIRED_CAPABILITY_MISSING would send an operator to tell a partner to
    implement something the partner already implemented, when the actual gap is DHealth's own
    missing transport.
    """
    lab = _connect_facility(repos)
    installation = _register(repos, lab.id)
    _record(
        repos,
        installation.id,
        _bundle([Capability.ORDER_RECEIPT], EvidenceLevel.DECLARED)
        + _bundle(
            [c for c in LAB_REQUIRED if c is not Capability.ORDER_RECEIPT],
            EvidenceLevel.EXERCISED,
        ),
    )

    verdict = _verdict(repos, lab.id)
    assert verdict.unexercised_capabilities == (Capability.ORDER_RECEIPT,)
    assert verdict.missing_capabilities == ()
    assert verdict.gap is not QualificationGap.REQUIRED_CAPABILITY_MISSING
    # The operator sentence counts rather than naming the enum; the panel enumerates with its own
    # French labels. A raw contract constant is not a thing to show somebody.
    assert "non encore exercée" in verdict.detail
    assert "ORDER_RECEIPT" not in verdict.detail


# ── ES5: provider connectors ─────────────────────────────────────────────────────────────────


def test_ES5_a_declaration_only_provider_connector_does_not_qualify(repos) -> None:
    """§6: there is no provider harness, so every provider capability row is a declaration.

    The declarations are kept -- they are how DHealth records what a partner says it implements,
    which is what the next conversation with that partner is about. They are simply not proof.
    """
    clinic = _connect_facility(repos, provider=True, name="Clinique Al Rahma")
    installation = _register(repos, clinic.id, connector_key="vendor-emr")
    _record(repos, installation.id, _bundle(PROVIDER_REQUIRED, EvidenceLevel.DECLARED))

    verdict = _verdict(repos, clinic.id, OrganizationType.PROVIDER)
    assert verdict.qualified is False
    assert verdict.gap is QualificationGap.REQUIRED_CAPABILITY_UNEXERCISED
    assert set(verdict.unexercised_capabilities) == set(PROVIDER_REQUIRED)


# ── ES6: §18's second half, in a fixture and nowhere else ────────────────────────────────────


def test_ES6_flipping_order_receipt_to_exercised_completes_qualification(repos) -> None:
    """§18: what DH-CONNECT-AGENT-001 will legitimately make true, proved here as arithmetic.

    **This is a test fixture. No runtime exists, no transport was built, and nothing in this
    repository can produce an exercised ORDER_RECEIPT.** The point is to show that the only thing
    standing between the synthetic laboratory result and production qualification is that one
    capability's evidence -- so when the agent ticket supplies a real delivery path, it satisfies
    the existing contract rather than reinterpreting it.

    Note what does *not* change: operational and routable stay false. Qualification was never the
    only gate.
    """
    lab = _connect_facility(repos)
    installation = _register(repos, lab.id)
    _record(
        repos,
        installation.id,
        _bundle([Capability.ORDER_RECEIPT], EvidenceLevel.DECLARED)
        + _bundle(
            [c for c in LAB_REQUIRED if c is not Capability.ORDER_RECEIPT],
            EvidenceLevel.EXERCISED,
        ),
    )
    assert _verdict(repos, lab.id).qualified is False

    # A second attempt, as a real requalification would be -- not an edit of the first. The
    # original evidence stays exactly as recorded.
    _record(repos, installation.id, _bundle(LAB_REQUIRED, EvidenceLevel.EXERCISED))

    verdict = _verdict(repos, lab.id)
    assert verdict.qualified is True
    assert verdict.gap is None

    with repos["sessions"]() as session:
        assert connector_operational_in_session(session, lab.id) is False
        assert is_operationally_ready_in_session(session, lab.id) is False

    history = repos["connectors"].qualification_history(installation.id)
    assert len(history) == 2, "the weaker attempt was rewritten instead of superseded"


# ── ES7/ES8: the boundaries of the rule ──────────────────────────────────────────────────────


def test_ES7_an_optional_capability_may_be_declared_without_blocking(repos) -> None:
    """Only *required* capabilities must be exercised.

    Demanding proof of optional ones would block partners on capabilities the contract itself calls
    optional -- PROCESSING_EVENT is nice to have, and a laboratory that cannot report "now in the
    analyser" is still perfectly able to take clinical work.
    """
    lab = _connect_facility(repos)
    installation = _register(repos, lab.id)
    optional = optional_capabilities(OrganizationType.LAB)
    assert Capability.PROCESSING_EVENT in optional, "fixture assumes the optional lab bundle"

    _record(
        repos,
        installation.id,
        _bundle(LAB_REQUIRED, EvidenceLevel.EXERCISED)
        + _bundle(list(optional), EvidenceLevel.DECLARED),
    )

    verdict = _verdict(repos, lab.id)
    assert verdict.qualified is True
    assert verdict.unexercised_capabilities == ()


def test_ES8_an_exercised_failure_still_fails(repos) -> None:
    """Evidence strength narrows what counts as a pass; it never rescues a failure.

    A capability DHealth genuinely drove and watched misbehave is the strongest possible evidence
    that the connector does not have it.
    """
    lab = _connect_facility(repos)
    installation = _register(repos, lab.id)
    _record(
        repos,
        installation.id,
        _bundle(
            [c for c in LAB_REQUIRED if c is not Capability.AMENDMENT_PUBLICATION],
            EvidenceLevel.EXERCISED,
        )
        + _bundle([Capability.AMENDMENT_PUBLICATION], EvidenceLevel.EXERCISED, passed=False),
    )

    verdict = _verdict(repos, lab.id)
    assert verdict.qualified is False
    assert verdict.gap is QualificationGap.REQUIRED_CAPABILITY_MISSING
    assert verdict.missing_capabilities == (Capability.AMENDMENT_PUBLICATION,)


# ── ES9: the semantic bump ───────────────────────────────────────────────────────────────────


def test_ES9_a_qualification_from_the_previous_suite_no_longer_applies(repos) -> None:
    """§11: suite 1 and suite 2 answer different questions about the same connector.

    Under suite 1, a declared ORDER_RECEIPT satisfied the required bundle. Letting a suite-1 PASS
    carry forward would silently re-credit it with a claim suite 2 refuses to make -- the exact
    reinterpretation this correction exists to prevent. Fail closed: requalify.

    And the reason given must be the true one. Nothing about the installation changed, so
    QUALIFICATION_STALE ("the configuration or connector have changed") would be a falsehood in a
    readiness finding.
    """
    lab = _connect_facility(repos)
    installation = _register(repos, lab.id)
    _record(
        repos,
        installation.id,
        _bundle(LAB_REQUIRED, EvidenceLevel.EXERCISED),
        suite_version="1",
    )

    verdict = _verdict(repos, lab.id)
    assert verdict.qualified is False
    assert verdict.gap is QualificationGap.SUITE_VERSION_OUTDATED
    assert "suite" in verdict.detail

    # Nothing was deleted: the old attempt is still readable history.
    assert len(repos["connectors"].qualification_history(installation.id)) == 1


# ── ES10/ES11/ES12: what this correction must not have changed ───────────────────────────────


def test_ES10_native_facilities_are_untouched(repos) -> None:
    """A NATIVE facility has no connector concept and gains no connector finding."""
    lab = make_lab(repos["organizations"], "Laboratoire Horizon", operational=False)
    repos["enrollments"].create(
        organization_id=lab.id,
        participation_mode=ParticipationMode.NATIVE,
        onboarding_status=OnboardingStatus.ORGANIZATION_READY,
    )
    with repos["sessions"]() as session:
        codes = {f.code for f in operational_findings_in_session(session, lab.id)}
    assert "connector_qualified" not in codes
    assert "connector_operational" not in codes


def test_ES11_operational_stays_false_at_every_evidence_level(repos) -> None:
    """§9: this correction changed what qualification means, not what DHealth can observe.

    A runtime is a thing that reports in. None does, at any evidence level, for any connector.
    """
    lab = _connect_facility(repos)
    installation = _register(repos, lab.id)
    for level in (EvidenceLevel.DECLARED, EvidenceLevel.EXERCISED):
        with repos["sessions"]() as session:
            assert connector_operational_in_session(session, lab.id) is False, level
    _record(repos, installation.id, _bundle(LAB_REQUIRED, EvidenceLevel.EXERCISED))
    with repos["sessions"]() as session:
        assert connector_operational_in_session(session, lab.id) is False


def test_ES12_production_routing_stays_refused_even_when_fully_qualified(repos) -> None:
    """The chain that must not collapse, restated after the correction.

    qualified ≠ operational ≠ routable. Hand-built exercised evidence reaches the first and moves
    neither of the others, because `connector_operational` is a separate blocking finding.
    """
    lab = _connect_facility(repos)
    installation = _register(repos, lab.id)
    _record(repos, installation.id, _bundle(LAB_REQUIRED, EvidenceLevel.EXERCISED))

    assert _verdict(repos, lab.id).qualified is True
    with repos["sessions"]() as session:
        findings = {
            f.code: (f.satisfied, f.blocking)
            for f in operational_findings_in_session(session, lab.id)
        }
        assert is_operationally_ready_in_session(session, lab.id) is False
    assert findings["connector_qualified"] == (True, True)
    assert findings["connector_operational"] == (False, True)
