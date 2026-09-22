"""DH-CONNECT-CONTRACT-001 §65 — what a CONNECT facility's readiness actually derives.

Before this ticket `connector_qualified` was a hardcoded `False`. It is now computed from real
evidence, which creates a new and more dangerous failure mode: a facility could become routable
because somebody produced qualification rows. These tests exist mostly to prove it cannot.

The property that matters most is CR8. A connector can satisfy the whole contract in qualification
and still not be running anywhere -- qualification proves a build *could* work, only a runtime
proves one *is* working -- so a fully qualified CONNECT facility stays unroutable. If CR8 ever goes
green while `connector_operational` reports true without a runtime behind it, DHealth has started
telling operators something it cannot know.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from exchange_support import make_lab, make_provider
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import Capability, fingerprint
from daher_adapter.connect.models import QualificationResult
from daher_adapter.connect.qualification import (
    QUALIFICATION_SUITE_VERSION,
    QualificationGap,
    qualification_verdict_in_session,
)
from daher_adapter.connect.repository import (
    CapabilityOutcome,
    ConnectorInstallationRepository,
    FacilityNotConnect,
)
from daher_adapter.exchange.facility_enrollment import (
    FacilityEnrollmentRepository,
    is_operationally_ready_in_session,
    operational_findings_in_session,
)
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    OnboardingStatus,
    OrganizationRole,
    ParticipationMode,
)
from daher_adapter.exchange.organizations import OrganizationRepository

CONFIG_FP = fingerprint({"endpoint": "https://lis.invalid/fhir", "timeout_s": 30})
MAPPING_FP = fingerprint({"mapping_revision": [["synthetic-lis", "GLU", "cat-1", "GLU"]]})

LAB_BUNDLE = {
    Capability.ORDER_RECEIPT: True,
    Capability.BUSINESS_ACCEPTANCE: True,
    # Required, not optional: without a collection milestone the Exchange refuses
    # ACCEPTED -> IN_PROGRESS, so no result can be entered at all. See `required_capabilities`.
    Capability.COLLECTION_EVENT: True,
    Capability.RELEASED_RESULT_PUBLICATION: True,
    Capability.AMENDMENT_PUBLICATION: True,
}


@pytest.fixture
def repos(sessions: sessionmaker[Session]):
    organizations = OrganizationRepository(sessions)
    return {
        "sessions": sessions,
        "organizations": organizations,
        "enrollments": FacilityEnrollmentRepository(sessions),
        "identities": ExchangeIdentityRepository(sessions),
        "memberships": OrganizationMembershipRepository(sessions),
        "roles": OrganizationRoleAssignmentRepository(sessions),
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


def _connect_lab(repos, name="Laboratoire Central"):
    lab = make_lab(repos["organizations"], name, operational=False)
    repos["enrollments"].create(
        organization_id=lab.id,
        participation_mode=ParticipationMode.CONNECT,
        onboarding_status=OnboardingStatus.AWAITING_CONNECTOR,
    )
    return lab


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


def _qualify(repos, installation_id, *, capabilities=None, result=QualificationResult.PASSED):
    started = datetime.now(UTC) - timedelta(minutes=5)
    return repos["connectors"].record_qualification(
        installation_id=installation_id,
        suite_version=QUALIFICATION_SUITE_VERSION,
        started_at=started,
        completed_at=started + timedelta(minutes=4),
        result=result,
        capabilities=[
            CapabilityOutcome(capability=cap, passed=ok)
            for cap, ok in (capabilities or LAB_BUNDLE).items()
        ],
        actor_identity_id=_actor(repos),
        evidence_summary="suite synthétique",
    )


def _findings(repos, organization_id) -> dict[str, tuple[bool, bool]]:
    """``{code: (satisfied, blocking)}`` for one facility."""
    with repos["sessions"]() as session:
        return {
            f.code: (f.satisfied, f.blocking)
            for f in operational_findings_in_session(session, organization_id)
        }


def _routable(repos, organization_id) -> bool:
    with repos["sessions"]() as session:
        return is_operationally_ready_in_session(session, organization_id)


def _verdict(repos, organization_id, org_type="LAB"):
    with repos["sessions"]() as session:
        return qualification_verdict_in_session(session, organization_id, org_type)


class TestConnectReadinessDerivesFromEvidence:
    def test_CR1_connect_facility_with_no_connector_is_not_qualified_and_not_routable(self, repos):
        lab = _connect_lab(repos)
        findings = _findings(repos, lab.id)
        assert findings["connector_qualified"] == (False, True)
        assert findings["connector_operational"] == (False, True)
        assert not _routable(repos, lab.id)
        assert _verdict(repos, lab.id).gap is QualificationGap.NO_INSTALLATION

    def test_CR2_a_verified_interface_is_not_a_qualified_connector(self, repos):
        """The distinction §2 refuses to collapse, asserted rather than assumed.

        A partner's interface being verified means DHealth checked the interface exists as
        documented. It says nothing about whether a connector was ever built against it, let alone
        whether that connector satisfies this contract.
        """
        lab = _connect_lab(repos)
        repos["enrollments"].set_interface_capability(
            organization_id=lab.id,
            interface_type="FHIR",
            verification_state="VERIFIED",
            detail=None,
        )
        findings = _findings(repos, lab.id)
        assert findings["connector_qualified"] == (False, True)
        assert not _routable(repos, lab.id)

    def test_CR3_registered_but_never_qualified_is_not_qualified(self, repos):
        lab = _connect_lab(repos)
        _register(repos, lab.id)
        assert _verdict(repos, lab.id).gap is QualificationGap.NEVER_QUALIFIED
        assert _findings(repos, lab.id)["connector_qualified"] == (False, True)

    def test_CR4_qualification_for_one_facility_says_nothing_about_another(self, repos):
        """§9/Q3: tenant isolation, structural rather than checked.

        Qualification hangs off an installation and an installation hangs off exactly one
        organization, so there is no query that could carry a PASS across facilities. This test
        exists so that remains true if somebody later "simplifies" the lookup.
        """
        qualified = _connect_lab(repos, "Laboratoire A")
        other = _connect_lab(repos, "Laboratoire B")
        installation = _register(repos, qualified.id)
        _qualify(repos, installation.id)

        assert _verdict(repos, qualified.id).qualified is True
        assert _verdict(repos, other.id).qualified is False
        assert _verdict(repos, other.id).gap is QualificationGap.NO_INSTALLATION
        assert _findings(repos, other.id)["connector_qualified"] == (False, True)

    def test_CR5_a_stale_connector_version_does_not_carry_the_qualification_forward(self, repos):
        lab = _connect_lab(repos)
        installation = _register(repos, lab.id)
        _qualify(repos, installation.id)
        assert _verdict(repos, lab.id).qualified is True

        # The partner ships a new build. Nothing invalidates anything by hand; the pinned version
        # on the evidence simply stops matching the installation.
        repos["connectors"].update_fingerprints(
            installation_id=installation.id,
            connector_version="0.2",
            actor_identity_id=_actor(repos),
        )
        verdict = _verdict(repos, lab.id)
        assert verdict.qualified is False
        assert verdict.gap is QualificationGap.QUALIFICATION_STALE

    def test_CR5b_a_different_contract_major_version_requires_requalification(self, repos):
        """§57: no automatic carry-forward across a contract major version."""
        lab = _connect_lab(repos)
        installation = _register(repos, lab.id)
        _qualify(repos, installation.id)

        with repos["sessions"].begin() as session:
            from daher_adapter.connect.models import ConnectorInstallation

            row = session.get(ConnectorInstallation, installation.id)
            row.contract_version = "2.0"

        verdict = _verdict(repos, lab.id)
        assert verdict.qualified is False
        assert verdict.gap is QualificationGap.CONTRACT_MAJOR_MISMATCH
        assert "2.0" in verdict.detail

    def test_CR6_a_changed_mapping_fingerprint_invalidates_the_qualification(self, repos):
        """§56: a mapping change can alter which clinical test a patient actually receives."""
        lab = _connect_lab(repos)
        installation = _register(repos, lab.id)
        _qualify(repos, installation.id)
        assert _verdict(repos, lab.id).qualified is True

        repos["connectors"].update_fingerprints(
            installation_id=installation.id,
            mapping_fingerprint=fingerprint(
                {"mapping_revision": [["synthetic-lis", "GLU", "cat-2", "GLU"]]}
            ),
            actor_identity_id=_actor(repos),
        )
        assert _verdict(repos, lab.id).gap is QualificationGap.QUALIFICATION_STALE

    def test_CR7_a_pass_missing_a_required_capability_does_not_qualify(self, repos):
        """§39: a laboratory that receives orders and cannot return results is not qualified.

        The run genuinely PASSED for what it covered. That is not the same as covering the
        production bundle, and flattening the two is how a partner ends up taking clinical work it
        cannot return.
        """
        lab = _connect_lab(repos)
        installation = _register(repos, lab.id)
        _qualify(
            repos,
            installation.id,
            capabilities={
                Capability.ORDER_RECEIPT: True,
                Capability.BUSINESS_ACCEPTANCE: True,
                Capability.COLLECTION_EVENT: True,
                Capability.RELEASED_RESULT_PUBLICATION: True,
                # The correction path is broken. Everything else works.
                Capability.AMENDMENT_PUBLICATION: False,
            },
        )
        verdict = _verdict(repos, lab.id)
        assert verdict.qualified is False
        assert verdict.gap is QualificationGap.REQUIRED_CAPABILITY_MISSING
        assert verdict.missing_capabilities == (Capability.AMENDMENT_PUBLICATION,)
        assert "AMENDMENT_PUBLICATION" in verdict.detail

    def test_CR8_a_fully_qualified_connector_with_no_runtime_is_still_unroutable(self, repos):
        """The ticket's central claim, and the one a future change is most likely to break.

        Everything the contract asks for has been demonstrated. No runtime exists, so DHealth has
        observed nothing about whether the connector is reachable, and production routing stays
        unavailable. `connector_qualified` true beside `connector_operational` false is not an
        inconsistency -- it is the two facts this ticket exists to keep apart.
        """
        lab = _connect_lab(repos)
        installation = _register(repos, lab.id)
        _qualify(repos, installation.id)

        findings = _findings(repos, lab.id)
        assert findings["connector_qualified"] == (True, True)
        assert findings["connector_operational"] == (False, True)
        assert not _routable(repos, lab.id)

    def test_CR9_a_suspended_connector_is_not_qualified_and_history_survives(self, repos):
        lab = _connect_lab(repos)
        installation = _register(repos, lab.id)
        _qualify(repos, installation.id)

        repos["connectors"].suspend(
            installation_id=installation.id, actor_identity_id=_actor(repos)
        )
        verdict = _verdict(repos, lab.id)
        assert verdict.qualified is False
        assert verdict.gap is QualificationGap.SUSPENDED
        assert not _routable(repos, lab.id)

        # §32: suspension deletes nothing. The evidence is still there, and restoring returns the
        # facility to exactly where it was -- the same philosophy as a lab that loses and regains
        # its last releaser.
        assert len(repos["connectors"].qualification_history(installation.id)) == 1
        repos["connectors"].restore(
            installation_id=installation.id, actor_identity_id=_actor(repos)
        )
        assert _verdict(repos, lab.id).qualified is True

    def test_CR10_a_retired_connector_never_qualifies_again(self, repos):
        lab = _connect_lab(repos)
        installation = _register(repos, lab.id)
        _qualify(repos, installation.id)
        repos["connectors"].retire(installation_id=installation.id, actor_identity_id=_actor(repos))

        verdict = _verdict(repos, lab.id)
        assert verdict.qualified is False
        assert verdict.gap is QualificationGap.RETIRED
        assert not _routable(repos, lab.id)
        # Evidence outlives the installation's usefulness, deliberately.
        assert len(repos["connectors"].qualification_history(installation.id)) == 1

    def test_CR11_native_readiness_is_untouched_by_any_of_this(self, repos):
        """§52: a NATIVE facility has no connector concept, and gains none from this ticket."""
        provider = make_provider(repos["organizations"], "Clinique Al Rahma", operational=False)
        repos["enrollments"].create(
            organization_id=provider.id,
            participation_mode=ParticipationMode.NATIVE,
            onboarding_status=OnboardingStatus.ORGANIZATION_READY,
        )
        identity, _ = repos["identities"].get_or_create(
            issuer="https://issuer.example", subject="doc", display_name="Doc", email=None
        )
        membership, _ = repos["memberships"].create(
            organization_id=provider.id, identity_id=identity.id
        )
        repos["roles"].assign(membership_id=membership.id, role=OrganizationRole.CLINICIAN)

        findings = _findings(repos, provider.id)
        assert "connector_qualified" not in findings
        assert "connector_operational" not in findings
        assert _routable(repos, provider.id)

    def test_CR12_a_native_facility_cannot_register_a_connector_at_all(self, repos):
        """The refusal that keeps CR11 true no matter what an operator does."""
        native = make_lab(repos["organizations"], "Laboratoire Nour", operational=False)
        repos["enrollments"].create(
            organization_id=native.id,
            participation_mode=ParticipationMode.NATIVE,
            onboarding_status=OnboardingStatus.ORGANIZATION_READY,
        )
        with pytest.raises(FacilityNotConnect):
            _register(repos, native.id)
