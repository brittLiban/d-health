"""DH-MVP-ONBOARD-001: operational readiness, and the three facts it keeps apart.

    configured route      a platform admin said this pair may exchange work
    operationally ready   the facility can actually do that work today
    eligible destination  both of the above, for this order, at this moment

The tests below exist mostly to pin the second and third apart from the first. A lab whose last
result releaser leaves is still *configured* -- nothing rewrites that row, because a human decided
that relationship deliberately -- and it stops being somewhere new work may be sent.

Why staffing gates production routing at all: entry and release are separate authorities
(ADR-044). A Native lab with technicians and no releaser can receive an order, enter results
against it, and then be structurally incapable of releasing them. That is work arriving somewhere
it can never leave, which is the condition §37 exists to prevent.
"""

from __future__ import annotations

import uuid

import pytest
from exchange_support import activate_result_template, make_lab, make_offering, make_provider
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.facility_enrollment import (
    FacilityEnrollmentRepository,
    FacilityReadinessEvaluator,
    is_operationally_ready_in_session,
    operational_findings_in_session,
    untemplated_native_entries_in_session,
)
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    EnrollmentBlockerCode,
    OnboardingStatus,
    OrganizationRole,
    ParticipationMode,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.provider_lab_routes import ProviderLabRouteRepository


@pytest.fixture
def repos(sessions: sessionmaker[Session]):
    organizations = OrganizationRepository(sessions)
    return {
        "sessions": sessions,
        "organizations": organizations,
        "catalog": CatalogRepository(sessions, organizations),
        "directory": LabDirectoryProfileRepository(sessions, organizations),
        "identities": ExchangeIdentityRepository(sessions),
        "memberships": OrganizationMembershipRepository(sessions),
        "roles": OrganizationRoleAssignmentRepository(sessions),
        "routes": ProviderLabRouteRepository(sessions, organizations),
        "enrollments": FacilityEnrollmentRepository(sessions),
    }


def _evaluator(repos) -> FacilityReadinessEvaluator:
    return FacilityReadinessEvaluator(
        sessions=repos["sessions"],
        enrollments=repos["enrollments"],
    )


def _grant(repos, organization_id: uuid.UUID, roles: list[OrganizationRole], subject: str) -> None:
    """One identity, one membership, the given roles. A single membership may hold several roles --
    which is exactly the case the lab dual-role test below depends on."""
    identity, _ = repos["identities"].get_or_create(
        issuer="https://issuer.example", subject=subject, display_name=subject, email=None
    )
    membership, _ = repos["memberships"].create(
        organization_id=organization_id, identity_id=identity.id
    )
    for role in roles:
        repos["roles"].assign(membership_id=membership.id, role=role)


def _enrol(repos, organization_id: uuid.UUID, mode=ParticipationMode.NATIVE):
    return repos["enrollments"].create(
        organization_id=organization_id,
        participation_mode=mode,
        onboarding_status=OnboardingStatus.ORGANIZATION_READY,
    )


def _ready_lab(
    repos,
    *,
    name="Laboratoire Central",
    roles=None,
    participation_mode=ParticipationMode.NATIVE,
    templated_offering=True,
):
    """A Native lab that satisfies every production condition, so each test can remove exactly one
    thing and see the effect of that one thing.

    ``templated_offering=False`` leaves its single offering without an active result template --
    the state in which a Native lab can complete nothing it advertises.
    """
    lab = make_lab(repos["organizations"], name, operational=False)
    _enrol(repos, lab.id, participation_mode)
    repos["directory"].upsert(
        lab_organization_id=lab.id,
        public_address="Avenue 13, Djibouti",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    entry, _ = repos["catalog"].create_entry(code=f"GLU-{name[:4]}", display_name="Glycémie")
    make_offering(
        repos["catalog"],
        lab_organization_id=lab.id,
        catalog_entry_id=entry.id,
        templated=templated_offering,
    )
    _grant(
        repos,
        lab.id,
        roles
        if roles is not None
        else [OrganizationRole.LAB_TECH, OrganizationRole.LAB_RESULT_RELEASER],
        f"lab-staff-{name}",
    )
    return lab


def _ready_provider(repos, *, name="Clinique de la Paix", roles=None):
    provider = make_provider(repos["organizations"], name, operational=False)
    _enrol(repos, provider.id)
    _grant(
        repos,
        provider.id,
        roles if roles is not None else [OrganizationRole.CLINICIAN],
        f"prov-staff-{name}",
    )
    return provider


def _blocking(findings) -> set[str]:
    return {f.code for f in findings if f.blocking and not f.satisfied}


class TestNativeStaffingGatesRouting:
    def test_RS1_fully_staffed_native_pair_is_routable(self, repos):
        provider = _ready_provider(repos)
        lab = _ready_lab(repos)
        decision = _evaluator(repos).evaluate_route_activation(
            provider_organization_id=provider.id, lab_organization_id=lab.id
        )
        assert decision.allowed, decision.blocking_reasons

    def test_RS2_native_provider_without_a_clinician_cannot_be_routed(self, repos):
        # Nobody at this clinic may order. Routing from it is a claim about work that cannot start.
        provider = _ready_provider(repos, roles=[OrganizationRole.ORG_ADMIN])
        lab = _ready_lab(repos)
        decision = _evaluator(repos).evaluate_route_activation(
            provider_organization_id=provider.id, lab_organization_id=lab.id
        )
        assert not decision.allowed
        assert "provider_clinician_present" in decision.blocking_reasons

    def test_RS3_native_lab_without_a_technician_cannot_be_routed(self, repos):
        lab = _ready_lab(repos, roles=[OrganizationRole.LAB_RESULT_RELEASER])
        decision = _evaluator(repos).evaluate_route_activation(
            provider_organization_id=_ready_provider(repos).id, lab_organization_id=lab.id
        )
        assert not decision.allowed
        assert "lab_lab_technician_present" in decision.blocking_reasons

    def test_RS4_native_lab_without_a_releaser_cannot_be_routed(self, repos):
        # The sharpest case. This lab can accept an order and enter every value, and can never
        # release the result -- release is a separate authority (ADR-044). Work would arrive
        # somewhere it can never leave.
        lab = _ready_lab(repos, roles=[OrganizationRole.LAB_TECH])
        decision = _evaluator(repos).evaluate_route_activation(
            provider_organization_id=_ready_provider(repos).id, lab_organization_id=lab.id
        )
        assert not decision.allowed
        assert "lab_lab_releaser_present" in decision.blocking_reasons

    def test_RS5_one_person_holding_both_lab_roles_satisfies_both(self, repos):
        # The requirement is on authority present at the organization, never on headcount. A small
        # laboratory where one person enters and releases is a real, permitted configuration --
        # ADR-044 explicitly declines to force a second human.
        lab = _ready_lab(
            repos, roles=[OrganizationRole.LAB_TECH, OrganizationRole.LAB_RESULT_RELEASER]
        )
        _, findings = _evaluator(repos).evaluate(lab.id)
        assert _blocking(findings) == set()

        with repos["sessions"]() as session:
            assert is_operationally_ready_in_session(session, lab.id)

    def test_RS6_an_inactive_membership_does_not_count_as_staffing(self, repos):
        # An inactivated membership fails authorization everywhere else in this system. Counting it
        # here would make readiness promise a capability the authorization layer would refuse.
        lab = _ready_lab(repos)
        memberships = repos["memberships"].list_for_organization(lab.id)
        for membership in memberships:
            repos["memberships"].set_inactive(membership.id)

        _, findings = _evaluator(repos).evaluate(lab.id)
        assert "lab_technician_present" in _blocking(findings)
        assert "lab_releaser_present" in _blocking(findings)

    def test_RS7_connect_facility_is_refused_whatever_its_staffing(self, repos):
        lab = make_lab(repos["organizations"], "Laboratoire Connecté", operational=False)
        _enrol(repos, lab.id, mode=ParticipationMode.CONNECT)
        repos["directory"].upsert(
            lab_organization_id=lab.id,
            public_address="Avenue 14, Djibouti",
            latitude=None,
            longitude=None,
            public_phone=None,
            listed=False,
            accepting_orders=True,
        )
        entry, _ = repos["catalog"].create_entry(code="GLU-CX", display_name="Glycémie")
        make_offering(repos["catalog"], lab_organization_id=lab.id, catalog_entry_id=entry.id)
        _grant(
            repos,
            lab.id,
            [OrganizationRole.LAB_TECH, OrganizationRole.LAB_RESULT_RELEASER],
            "connect-staff",
        )
        decision = _evaluator(repos).evaluate_route_activation(
            provider_organization_id=_ready_provider(repos).id, lab_organization_id=lab.id
        )
        assert not decision.allowed
        assert "lab_connector_qualified" in decision.blocking_reasons

    def test_RS8_an_open_blocker_makes_the_facility_unroutable(self, repos):
        lab = _ready_lab(repos)
        repos["enrollments"].add_blocker(
            organization_id=lab.id,
            code=EnrollmentBlockerCode.FACILITY_CONFIGURATION_INCOMPLETE,
            note="en attente de la configuration du laboratoire",
        )
        decision = _evaluator(repos).evaluate_route_activation(
            provider_organization_id=_ready_provider(repos).id, lab_organization_id=lab.id
        )
        assert not decision.allowed
        assert "lab_enrollment_not_blocked" in decision.blocking_reasons

    def test_RS9_an_unenrolled_organization_fails_closed(self, repos):
        # Fail-open by omission is how a facility nobody enrolled ends up receiving production work.
        lab = make_lab(repos["organizations"], "Laboratoire Sans Dossier", operational=False)
        decision = _evaluator(repos).evaluate_route_activation(
            provider_organization_id=_ready_provider(repos).id, lab_organization_id=lab.id
        )
        assert not decision.allowed
        assert "lab_facility_enrolled" in decision.blocking_reasons


class TestStaffingLossDoesNotRewriteTheRoute:
    """The distinction the ticket is most concerned with: losing staff must not silently mutate a
    configured relationship, and must still stop new work from being sent."""

    def test_RS10_losing_the_last_releaser_leaves_the_route_row_untouched(self, repos):
        provider = _ready_provider(repos)
        lab = _ready_lab(repos)
        route, _, _ = repos["routes"].upsert(
            provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
        )
        assert route.active

        for membership in repos["memberships"].list_for_organization(lab.id):
            repos["memberships"].set_inactive(membership.id)

        # Configured: unchanged. Nobody decided to end this relationship, so nothing ends it.
        still_configured = repos["routes"].list_for_provider(provider.id, active_only=False)
        assert [r.active for r in still_configured] == [True]

        # Operationally ready: no.
        with repos["sessions"]() as session:
            assert not is_operationally_ready_in_session(session, lab.id)

    def test_RS11_an_unready_lab_is_not_an_eligible_destination(self, repos):
        provider = _ready_provider(repos)
        lab = _ready_lab(repos)
        repos["routes"].upsert(
            provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
        )
        entry_ids = [o.catalog_entry_id for o in repos["catalog"].list_offerings_for_lab(lab.id)]

        eligible = repos["routes"].find_whole_order_eligible_labs(provider.id, entry_ids)
        assert [e.lab_organization_id for e in eligible] == [lab.id]

        for membership in repos["memberships"].list_for_organization(lab.id):
            repos["memberships"].set_inactive(membership.id)

        # Same active route, same offerings, same open doors -- and no longer a destination.
        assert repos["routes"].find_whole_order_eligible_labs(provider.id, entry_ids) == []

    def test_RS12_two_memberships_splitting_the_lab_roles_are_ready(self, repos):
        """Two people, one entering and one releasing, is the other real laboratory shape.

        The requirement is that both authorities are present at the organization -- not that one
        person holds both, and not that two different humans exist. Both configurations satisfy it.
        """
        lab = make_lab(repos["organizations"], "Laboratoire Deux Personnes", operational=False)
        _enrol(repos, lab.id)
        repos["directory"].upsert(
            lab_organization_id=lab.id,
            public_address="Avenue 15, Djibouti",
            latitude=None,
            longitude=None,
            public_phone=None,
            listed=False,
            accepting_orders=True,
        )
        entry, _ = repos["catalog"].create_entry(code="GLU-2P", display_name="Glycémie")
        make_offering(repos["catalog"], lab_organization_id=lab.id, catalog_entry_id=entry.id)
        _grant(repos, lab.id, [OrganizationRole.LAB_TECH], "tech-only-person")
        _grant(repos, lab.id, [OrganizationRole.LAB_RESULT_RELEASER], "releaser-only-person")

        _, findings = _evaluator(repos).evaluate(lab.id)
        assert _blocking(findings) == set()

    def test_RS13_org_admin_alone_satisfies_no_operational_role(self, repos):
        """ORG_ADMIN administers a facility; it does not enter results and does not release them.

        ADR-044 keeps those authorities separate deliberately, and administration implying either
        would quietly hand a manager clinical authority nobody granted.
        """
        lab = _ready_lab(repos, name="Laboratoire Admin Seul", roles=[OrganizationRole.ORG_ADMIN])
        _, lab_findings = _evaluator(repos).evaluate(lab.id)
        assert "lab_technician_present" in _blocking(lab_findings)
        assert "lab_releaser_present" in _blocking(lab_findings)

        provider = _ready_provider(
            repos, name="Clinique Admin Seul", roles=[OrganizationRole.ORG_ADMIN]
        )
        _, provider_findings = _evaluator(repos).evaluate(provider.id)
        assert "clinician_present" in _blocking(provider_findings)

    def test_RS14_restoring_staffing_makes_the_same_route_usable_again(self, repos):
        """No route recreation. The relationship never went anywhere; only the facility's ability
        to act on it did, and that is re-derived on every read."""
        provider = _ready_provider(repos, name="Clinique Retour")
        lab = _ready_lab(repos, name="Laboratoire Retour")
        route, _, _ = repos["routes"].upsert(
            provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
        )
        entry_ids = [o.catalog_entry_id for o in repos["catalog"].list_offerings_for_lab(lab.id)]
        assert repos["routes"].find_whole_order_eligible_labs(provider.id, entry_ids)

        for membership in repos["memberships"].list_for_organization(lab.id):
            repos["memberships"].set_inactive(membership.id)
        assert repos["routes"].find_whole_order_eligible_labs(provider.id, entry_ids) == []

        # A new person arrives holding both authorities. The original route row is untouched
        # throughout -- same id, still active.
        _grant(
            repos,
            lab.id,
            [OrganizationRole.LAB_TECH, OrganizationRole.LAB_RESULT_RELEASER],
            "replacement-staff",
        )
        eligible_again = repos["routes"].find_whole_order_eligible_labs(provider.id, entry_ids)
        assert [e.lab_organization_id for e in eligible_again] == [lab.id]

        surviving = repos["routes"].list_for_provider(provider.id, active_only=False)
        assert [(r.id, r.active) for r in surviving] == [(route.id, True)]

    def test_RS15_a_pair_finding_carries_its_side_as_a_field_not_as_a_code_prefix(self, repos):
        # The side must be recoverable without string surgery. A consumer that strips "lab_" off
        # the front of a code works by accident today -- "lab_facility_enrolled" and
        # "lab_lab_technician_present" do not strip alike -- and breaks the day a code is added
        # that follows neither shape.
        lab = _ready_lab(repos, roles=[OrganizationRole.LAB_TECH])
        decision = _evaluator(repos).evaluate_route_activation(
            provider_organization_id=_ready_provider(repos).id, lab_organization_id=lab.id
        )
        assert not decision.allowed
        blocking = decision.blocking_findings
        assert [(f.side, f.code) for f in blocking] == [("lab", "lab_releaser_present")]
        # Every finding, satisfied or not, knows which end of the pair it is about, and no code
        # carries the side inside it.
        assert {f.side for f in decision.findings} == {"provider", "lab"}
        assert not any(f.code.startswith(("provider_", "lab_lab_")) for f in decision.findings)

    def test_RS16_a_single_facility_evaluation_has_no_side(self, repos):
        # Readiness for one facility answers a question with only one end to it.
        lab = _ready_lab(repos, roles=[OrganizationRole.LAB_TECH])
        with repos["sessions"]() as session:
            findings = operational_findings_in_session(session, lab.id)
        assert findings
        assert {f.side for f in findings} == {None}


class TestNativeResultTemplates:
    """DH-MVP-ONBOARD-001 correction: a Native lab must be able to *finish* what it advertises.

    DHealth's own human result entry runs against a controlled active template --
    ``NativeResultEntryForm`` renders UNSTARTED_NO_TEMPLATE as "native result entry unavailable",
    by design. So an active offering with no active template is a service that could be ordered
    and never completed, and no order requiring it may be routed to that laboratory.

    The rule is deliberately *order-scoped* rather than facility-wide, except in the one case
    where the two coincide: a lab that can complete nothing at all.
    """

    def test_TP1_native_lab_with_offering_and_template_is_eligible(self, repos):
        provider = _ready_provider(repos)
        lab = _ready_lab(repos)
        entry, _ = repos["catalog"].create_entry(code="TP1", display_name="TP1 Test")
        make_offering(repos["catalog"], lab_organization_id=lab.id, catalog_entry_id=entry.id)
        repos["routes"].upsert(
            provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
        )
        eligible = repos["routes"].find_whole_order_eligible_labs(provider.id, [entry.id])
        assert [e.lab_organization_id for e in eligible] == [lab.id]

    def test_TP2_native_lab_offering_without_a_template_is_not_eligible(self, repos):
        # The whole point. The lab is staffed, open, enrolled and offers the test -- and DHealth
        # could not enter a single value against the order if it arrived there.
        provider = _ready_provider(repos)
        lab = _ready_lab(repos)
        entry, _ = repos["catalog"].create_entry(code="TP2", display_name="TP2 Test")
        make_offering(
            repos["catalog"],
            lab_organization_id=lab.id,
            catalog_entry_id=entry.id,
            templated=False,
        )
        repos["routes"].upsert(
            provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
        )
        assert repos["routes"].find_whole_order_eligible_labs(provider.id, [entry.id]) == []

    def test_TP3_only_the_untemplated_service_is_refused(self, repos):
        """Partial coverage does not cost the laboratory its other work.

        A lab that can complete nine of its ten offerings is a real destination for the nine.
        Blocking the whole facility would refuse work it can genuinely do, and would push an
        operator toward deactivating the tenth offering to clear a flag.
        """
        provider = _ready_provider(repos)
        lab = _ready_lab(repos)
        good, _ = repos["catalog"].create_entry(code="TP3-OK", display_name="Templated")
        bad, _ = repos["catalog"].create_entry(code="TP3-NO", display_name="Untemplated")
        make_offering(repos["catalog"], lab_organization_id=lab.id, catalog_entry_id=good.id)
        make_offering(
            repos["catalog"],
            lab_organization_id=lab.id,
            catalog_entry_id=bad.id,
            templated=False,
        )
        repos["routes"].upsert(
            provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
        )
        assert [
            e.lab_organization_id
            for e in repos["routes"].find_whole_order_eligible_labs(provider.id, [good.id])
        ] == [lab.id]
        assert repos["routes"].find_whole_order_eligible_labs(provider.id, [bad.id]) == []
        # And an order requiring both is refused, because eligibility is whole-order only.
        assert repos["routes"].find_whole_order_eligible_labs(provider.id, [good.id, bad.id]) == []

    def test_TP4_partial_coverage_does_not_block_route_activation(self, repos):
        # The facility-wide verdict and the order-scoped one answer different questions. This
        # pins that they are allowed to disagree, in exactly this direction and no other.
        provider = _ready_provider(repos)
        # The fixture lab already has one templated offering; this adds a second, untemplated.
        lab = _ready_lab(repos)
        bad, _ = repos["catalog"].create_entry(code="TP4-NO", display_name="Untemplated")
        make_offering(
            repos["catalog"],
            lab_organization_id=lab.id,
            catalog_entry_id=bad.id,
            templated=False,
        )
        decision = _evaluator(repos).evaluate_route_activation(
            provider_organization_id=provider.id, lab_organization_id=lab.id
        )
        assert decision.allowed, decision.blocking_reasons
        template_finding = next(
            f for f in decision.findings if f.code == "lab_result_templates_active"
        )
        assert not template_finding.satisfied
        assert not template_finding.blocking
        assert template_finding.detail is not None
        assert template_finding.detail.startswith("1/2 ")

    def test_TP5_a_lab_that_can_complete_nothing_blocks_route_activation(self, repos):
        # Zero coverage is not partial coverage. A lab that can finish none of what it offers is
        # not a destination at all -- the same fact lab_has_active_offering already blocks on.
        provider = _ready_provider(repos)
        # Its one and only offering has no active template: nothing here can be completed.
        lab = _ready_lab(repos, templated_offering=False)
        decision = _evaluator(repos).evaluate_route_activation(
            provider_organization_id=provider.id, lab_organization_id=lab.id
        )
        assert not decision.allowed
        assert "lab_lab_result_templates_active" in decision.blocking_reasons

    def test_TP6_activating_a_template_makes_the_same_route_eligible(self, repos):
        """No route recreation. The configured relationship never changed.

        This is the staffing-loss property restated for configuration: what an operator fixes is
        the missing template, not the route, and the route row is identical afterwards.
        """
        provider = _ready_provider(repos)
        lab = _ready_lab(repos)
        entry, _ = repos["catalog"].create_entry(code="TP6", display_name="TP6 Test")
        offering, _ = make_offering(
            repos["catalog"],
            lab_organization_id=lab.id,
            catalog_entry_id=entry.id,
            templated=False,
        )
        route, _created, _changed = repos["routes"].upsert(
            provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
        )
        assert repos["routes"].find_whole_order_eligible_labs(provider.id, [entry.id]) == []

        activate_result_template(repos["catalog"], offering_id=offering.id)

        eligible = repos["routes"].find_whole_order_eligible_labs(provider.id, [entry.id])
        assert [e.lab_organization_id for e in eligible] == [lab.id]
        surviving = repos["routes"].list_for_provider(provider.id, active_only=False)
        assert [(r.id, r.active) for r in surviving] == [(route.id, True)]

    def test_TP7_connect_is_blocked_by_the_connector_never_by_templates(self, repos):
        """CONNECT stays independent of Native template requirements.

        Asserted as an absence rather than inferred from the presence of the connector finding. A
        CONNECT laboratory's results are produced in the partner's own system, whose structure
        DHealth neither owns nor requires; letting a Native template rule reach it would couple a
        future connector to a workflow it does not use.
        """
        provider = _ready_provider(repos)
        lab = _ready_lab(
            repos, participation_mode=ParticipationMode.CONNECT, templated_offering=False
        )
        decision = _evaluator(repos).evaluate_route_activation(
            provider_organization_id=provider.id, lab_organization_id=lab.id
        )
        assert not decision.allowed
        assert "lab_connector_qualified" in decision.blocking_reasons
        assert not any(f.code == "lab_result_templates_active" for f in decision.findings)

    def test_TP8_the_order_scoped_rule_never_touches_a_connect_lab(self, repos):
        # The helper itself, directly: the Connect exemption lives in the code path every caller
        # shares, not repeated at each call site where it could be forgotten.
        lab = _ready_lab(
            repos, participation_mode=ParticipationMode.CONNECT, templated_offering=False
        )
        entry, _ = repos["catalog"].create_entry(code="TP8", display_name="TP8 Test")
        make_offering(
            repos["catalog"],
            lab_organization_id=lab.id,
            catalog_entry_id=entry.id,
            templated=False,
        )
        with repos["sessions"]() as session:
            assert untemplated_native_entries_in_session(session, lab.id, [entry.id]) == []
