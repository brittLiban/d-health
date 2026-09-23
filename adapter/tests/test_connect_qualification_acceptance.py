"""DH-CONNECT-CONTRACT-001 §84/§85 — the acceptance journey, end to end.

The scenario the ticket asks for, executed rather than described:

    Laboratoire Central, CONNECT, FHIR interface VERIFIED
        -> register a synthetic connector, openelis-fhir 0.1, contract 1.0
        -> run the qualification suite
        -> order receipt, business acceptance, released result, amendment, idempotency all PASS
        -> connector_qualified = TRUE
        -> no live runtime exists
        -> connector_operational = FALSE
        -> production_routing_allowed = FALSE

Then the staleness half: change the mapping fingerprint, watch the qualification stop applying,
and confirm the evidence is still there.

**The harness prepares its own order.** A CONNECT laboratory is not a routable destination -- that
is the whole point of the ticket -- so production routing would never create this order. The
scenarios therefore construct one directly, which is exactly right: qualification asks "would this
connector handle the work correctly", and production routing separately asks "may this facility be
sent work today". Conflating them is what would let a passing suite unlock routing.

No external system is contacted. No FHIR, HL7, SFTP, OpenELIS or OpenMRS code runs. No credential
exists. Every patient, practitioner and value below is synthetic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from exchange_support import (
    make_lab,
    make_patient_reference,
    make_practitioner_reference,
    make_provider,
)
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.apply import ConnectApplyError, ConnectBridge
from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    RETRYABLE,
    BusinessAcceptance,
    Capability,
    FailureCategory,
    InMemoryReceiptStore,
    ResultObservation,
    TestMapping,
    fingerprint,
    mapping_fingerprint,
)
from daher_adapter.connect.harness import LabQualificationHarness, SyntheticConnectorSubject
from daher_adapter.connect.models import EvidenceLevel, QualificationResult
from daher_adapter.connect.qualification import (
    QUALIFICATION_SUITE_VERSION,
    QualificationGap,
    qualification_verdict_in_session,
)
from daher_adapter.connect.repository import ConnectorInstallationRepository
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.facility_enrollment import (
    FacilityEnrollmentRepository,
    is_operationally_ready_in_session,
    operational_findings_in_session,
)
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    LabOrderStatus,
    LabSelection,
    OnboardingStatus,
    OrganizationRole,
    ParticipationMode,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    LabResultAmendmentRepository,
    LabResultReleaseRepository,
    LabResultRepository,
)
from daher_adapter.models import utc_now


@pytest.fixture
def world(sessions: sessionmaker[Session]):
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    return {
        "sessions": sessions,
        "organizations": organizations,
        "catalog": catalog,
        "enrollments": FacilityEnrollmentRepository(sessions),
        "identities": ExchangeIdentityRepository(sessions),
        "memberships": OrganizationMembershipRepository(sessions),
        "roles": OrganizationRoleAssignmentRepository(sessions),
        "patients": PatientReferenceRepository(sessions, organizations),
        "practitioners": PractitionerReferenceRepository(sessions),
        "orders": LabOrderRepository(sessions, organizations, catalog),
        "workflow": LabWorkflowRepository(sessions, organizations),
        "results": LabResultRepository(sessions),
        "releases": LabResultReleaseRepository(sessions),
        "amendments": LabResultAmendmentRepository(sessions),
        "connectors": ConnectorInstallationRepository(sessions),
    }


def _identity(world, subject: str) -> uuid.UUID:
    identity, _ = world["identities"].get_or_create(
        issuer="https://issuer.example", subject=subject, display_name=subject, email=None
    )
    return identity.id


def _service_identity(world) -> uuid.UUID:
    """The attribution placeholder every Exchange row this bridge writes carries.

    A stand-in for the service identity DH-CONNECT-AGENT-001 will issue to a connector runtime
    (§58). Exchange columns like ``released_by_identity_id`` are non-nullable by design -- "somebody
    released this" with no somebody is not a fact -- so the honest move is a named synthetic
    identity, not relaxing the column.
    """
    return _identity(world, "connect-runtime-synthetic")


def _connect_lab(world, name="Laboratoire Central"):
    lab = make_lab(world["organizations"], name, operational=False)
    world["enrollments"].create(
        organization_id=lab.id,
        participation_mode=ParticipationMode.CONNECT,
        onboarding_status=OnboardingStatus.AWAITING_CONNECTOR,
    )
    world["enrollments"].set_interface_capability(
        organization_id=lab.id,
        interface_type="FHIR",
        verification_state="VERIFIED",
        detail="FHIR R4 confirmé par DHealth",
    )
    return lab


def _prepared_order(world, lab) -> tuple[uuid.UUID, uuid.UUID]:
    """An order sitting at the laboratory, SELECTED, ready for the connector to act on.

    Constructed rather than routed -- see the module docstring. Returns ``(order_id, entry_id)``.
    """
    provider = make_provider(world["organizations"], "Clinique Al Rahma", operational=True)
    patient = make_patient_reference(world["patients"], provider_org_id=provider.id)
    practitioner = make_practitioner_reference(world["practitioners"], provider_org_id=provider.id)
    entry, _ = world["catalog"].create_entry(code="GLU", display_name="Glycémie à jeun")
    created = world["orders"].create(
        idempotency_key=f"acceptance-{uuid.uuid4()}",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    )
    order_id = created.order.id
    with world["sessions"].begin() as session:
        from daher_adapter.exchange.models import LabOrder

        session.add(
            LabSelection(
                lab_order_id=order_id,
                lab_organization_id=lab.id,
                selected_at=utc_now(),
                selected_via_patient_access_grant_id=None,
            )
        )
        session.get(LabOrder, order_id).status = LabOrderStatus.SELECTED.value
    return order_id, entry.id


MAPPINGS = (
    TestMapping(
        source_system="synthetic-lis",
        source_code="GLU",
        catalog_entry_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        catalog_code="GLU",
    ),
)
CONFIG_FP = fingerprint({"endpoint": "https://lis.invalid/fhir", "timeout_s": 30})


def _register(world, lab, **overrides):
    payload = {
        "organization_id": lab.id,
        "connector_key": "openelis-fhir",
        "connector_version": "0.1",
        "source_system": "OpenELIS",
        "source_system_version": "2.7",
        "interface_type": "FHIR",
        "configuration_fingerprint": CONFIG_FP,
        "mapping_fingerprint": mapping_fingerprint(MAPPINGS),
        "actor_identity_id": _identity(world, "platform-admin"),
    }
    payload.update(overrides)
    return world["connectors"].register(**payload)


def _run_suite(world, lab, installation, order_id, *, subject=None):
    """Drive the harness, then record whatever it concluded as durable evidence."""
    subject = subject or SyntheticConnectorSubject(connector_key="openelis-fhir")
    bridge = ConnectBridge(
        workflow=world["workflow"],
        results=world["results"],
        releases=world["releases"],
        amendments=world["amendments"],
        receipts=InMemoryReceiptStore(),
        installation_organization_id=lab.id,
        service_identity_id=_service_identity(world),
    )
    harness = LabQualificationHarness(
        bridge=bridge,
        subject=subject,
        organization_id=lab.id,
        installation_id=installation.id,
    )
    with world["sessions"].begin() as session:
        run = harness.run(
            session,
            order_id=order_id,
            observations=(
                ResultObservation(
                    source_code="GLU",
                    display_label="Glycémie à jeun",
                    result_type="NUMERIC",
                    numeric_value=Decimal("5.4"),
                    numeric_unit="mmol/L",
                    reference_range_low=Decimal("3.9"),
                    reference_range_high=Decimal("5.6"),
                ),
            ),
        )
    world["connectors"].record_qualification(
        installation_id=installation.id,
        suite_version=QUALIFICATION_SUITE_VERSION,
        started_at=run.started_at,
        completed_at=run.completed_at,
        result=QualificationResult.PASSED if run.all_passed else QualificationResult.FAILED,
        capabilities=run.capability_outcomes,
        actor_identity_id=_identity(world, "platform-admin"),
        evidence_summary=run.evidence_summary,
    )
    return run


def _findings(world, organization_id) -> dict[str, bool]:
    with world["sessions"]() as session:
        return {
            f.code: f.satisfied for f in operational_findings_in_session(session, organization_id)
        }


def test_AC_the_whole_connect_contract_journey(world):
    """§84, in one test, because the point is that these facts hold together.

    The load-bearing assertion is the last group: everything the contract asks for was
    demonstrated, and production routing is still unavailable. A version of this ticket that made
    qualification unlock routing would have passed every other test in the suite.
    """
    lab = _connect_lab(world)
    order_id, _ = _prepared_order(world, lab)

    # -- before anything: no connector, nothing qualified, nothing routable ------------------
    assert _findings(world, lab.id)["connector_qualified"] is False
    with world["sessions"]() as session:
        assert qualification_verdict_in_session(session, lab.id, "LAB").gap is (
            QualificationGap.NO_INSTALLATION
        )

    installation = _register(world, lab)
    assert installation.contract_version == CONTRACT_VERSION
    assert _findings(world, lab.id)["connector_qualified"] is False, (
        "registration alone must never qualify anything"
    )

    run = _run_suite(world, lab, installation, order_id)
    assert run.all_passed, run.evidence_summary

    # Every required laboratory capability was demonstrated by a scenario that actually wrote
    # Exchange rows -- an acceptance that reached ACCEPTED, a release that created a
    # LabResultRelease, an amendment that created version 2.
    passed = {c.capability for c in run.capability_outcomes if c.passed}
    assert {
        Capability.ORDER_RECEIPT,
        Capability.BUSINESS_ACCEPTANCE,
        Capability.COLLECTION_EVENT,
        Capability.RELEASED_RESULT_PUBLICATION,
        Capability.AMENDMENT_PUBLICATION,
    } <= passed

    # -- ADR-056 decisions 21 and 23: a PASS says how far it goes ------------------------------
    #
    # Four of the five required capabilities were driven end to end through the real Exchange
    # repositories. ORDER_RECEIPT was not and could not be: it is the one DHealth -> connector
    # capability, it needs something to push a message over, and no transport exists. So it is
    # recorded DECLARED, which the verdict reads.
    outcomes = {c.capability: c for c in run.capability_outcomes}
    for capability in (
        Capability.BUSINESS_ACCEPTANCE,
        Capability.COLLECTION_EVENT,
        Capability.RELEASED_RESULT_PUBLICATION,
        Capability.AMENDMENT_PUBLICATION,
    ):
        assert outcomes[capability].evidence_level is EvidenceLevel.EXERCISED, capability

    receipt = outcomes[Capability.ORDER_RECEIPT]
    assert receipt.passed is True
    assert receipt.evidence_level is EvidenceLevel.DECLARED
    assert receipt.detail is not None and "déclaration seule" in receipt.detail
    # The summary names the scenario that proved less than the others. It says "réserve(s)" rather
    # than "déclaration seule" since DH-CONNECT-AGENT-001: the same field now also carries a
    # scenario that genuinely delivered an order against a synthetic adapter, and calling that a
    # declaration would understate it. The per-capability  above still says exactly what
    # this run's limitation was.
    assert "réserve(s)" in run.evidence_summary
    assert "LQ1-order-receipt-declared" in run.evidence_summary

    stored = world["connectors"].qualification_history(installation.id)[0]
    stored_receipt = next(
        c for c in stored.capabilities if c.capability is Capability.ORDER_RECEIPT
    )
    assert stored_receipt.evidence_level is EvidenceLevel.DECLARED, (
        "the evidence level did not survive into the durable row"
    )
    assert stored_receipt.detail is not None and "déclaration seule" in stored_receipt.detail

    # -- §18, first half: the suite passed and production qualification is incomplete ----------
    #
    # This is the assertion the correction to this ticket exists to produce, and it reads as a
    # contradiction only if "passed" and "qualified" are the same word. They are not. The run is
    # recorded PASSED and stays PASSED forever: every scenario this suite could execute behaved
    # correctly, which is true and worth keeping. Production qualification is a different question
    # -- may clinical work be routed here -- and the answer is no, because one required capability
    # rests on the connector's own say-so.
    #
    # Before the correction this block asserted `connector_qualified is True`. That was the bug:
    # nothing was routable then, so it looked harmless, and DH-CONNECT-AGENT-001 is precisely the
    # ticket that would have made it dangerous.
    assert stored.result == QualificationResult.PASSED.value

    findings = _findings(world, lab.id)
    assert findings["connector_qualified"] is False
    assert findings["connector_operational"] is False

    with world["sessions"]() as session:
        verdict = qualification_verdict_in_session(session, lab.id, "LAB")
        assert not is_operationally_ready_in_session(session, lab.id)
    assert verdict.qualified is False
    assert verdict.gap is QualificationGap.REQUIRED_CAPABILITY_UNEXERCISED
    assert verdict.unexercised_capabilities == (Capability.ORDER_RECEIPT,)
    assert verdict.missing_capabilities == (), (
        "ORDER_RECEIPT is declared, not absent -- the two are different work for different people"
    )
    assert "non encore exercée" in verdict.detail

    # -- staleness: change the mapping, lose the qualification, keep the evidence ------------
    world["connectors"].update_fingerprints(
        installation_id=installation.id,
        mapping_fingerprint=mapping_fingerprint(
            (
                MAPPINGS[0].model_copy(
                    update={"catalog_entry_id": uuid.UUID("44444444-4444-4444-4444-444444444444")}
                ),
            )
        ),
        actor_identity_id=_identity(world, "platform-admin"),
    )
    assert _findings(world, lab.id)["connector_qualified"] is False
    with world["sessions"]() as session:
        verdict = qualification_verdict_in_session(session, lab.id, "LAB")
    assert verdict.gap is QualificationGap.QUALIFICATION_STALE

    history = world["connectors"].qualification_history(installation.id)
    assert len(history) == 1
    assert history[0].result == QualificationResult.PASSED.value, (
        "a stale qualification is still a qualification that happened; nothing rewrites it"
    )


def test_AC2_the_released_result_and_its_amendment_are_real_exchange_rows(world):
    """LQ7/LQ10/LQ11 at the database level, not at the harness's word for it."""
    from daher_adapter.exchange.models import (
        LabResultRelease,
        LabResultReleaseVersion,
    )

    lab = _connect_lab(world)
    order_id, _ = _prepared_order(world, lab)
    installation = _register(world, lab)
    run = _run_suite(world, lab, installation, order_id)
    assert run.all_passed, run.evidence_summary

    with world["sessions"]() as session:
        releases = (
            session.query(LabResultRelease).filter(LabResultRelease.lab_order_id == order_id).all()
        )
        versions = (
            session.query(LabResultReleaseVersion)
            .filter(LabResultReleaseVersion.lab_order_id == order_id)
            .order_by(LabResultReleaseVersion.version_number)
            .all()
        )

    # Exactly one release, despite the suite publishing the same report twice.
    assert len(releases) == 1
    # Two versions: the original and the correction. The original still binds to the release that
    # authorized it, which is what makes it reconstructable.
    assert [v.version_number for v in versions] == [1, 2]
    assert versions[0].lab_result_release_id == releases[0].id
    assert versions[0].amendment_id is None
    assert versions[1].lab_result_release_id is None
    assert versions[1].amendment_id is not None


def test_AC3_a_connector_requiring_double_entry_does_not_qualify(world):
    """§53: ADR-043 Law 2 as a qualification requirement, not a slogan.

    A "connector" that leaves a technician entering the result in the LIS and again in DHealth has
    automated nothing and has doubled the transcription-error surface on a clinical value.
    """
    lab = _connect_lab(world)
    order_id, _ = _prepared_order(world, lab)
    installation = _register(world, lab)
    run = _run_suite(
        world,
        lab,
        installation,
        order_id,
        subject=SyntheticConnectorSubject(
            connector_key="manual-double-entry", requires_manual_duplicate_entry=True
        ),
    )
    assert not run.all_passed
    assert any(s.scenario == "NDE1-no-double-entry" and not s.passed for s in run.scenarios)
    assert _findings(world, lab.id)["connector_qualified"] is False


def test_AC4_a_connector_that_cannot_amend_does_not_qualify(world):
    """§39/§66: passing part of the bundle is not passing the bundle.

    A laboratory connector that receives orders, accepts them and publishes results but cannot
    publish a correction is a real and dangerous shape: the clinician who acted on the original
    never learns it changed.
    """
    lab = _connect_lab(world)
    order_id, _ = _prepared_order(world, lab)
    installation = _register(world, lab)
    partial = SyntheticConnectorSubject(
        connector_key="no-amendments",
        declared_capabilities=frozenset(
            {
                Capability.ORDER_RECEIPT,
                Capability.BUSINESS_ACCEPTANCE,
                Capability.COLLECTION_EVENT,
                Capability.RELEASED_RESULT_PUBLICATION,
            }
        ),
    )
    run = _run_suite(world, lab, installation, order_id, subject=partial)
    assert not run.all_passed

    # The harness is stricter than the minimum, deliberately: a capability the subject does not
    # declare produces a *failing scenario*, so the whole run is recorded FAILED rather than as a
    # pass that merely covers less. The facility is therefore unqualified for the plainer reason
    # -- the last attempt failed -- and the capability row still names what was missing, so an
    # operator sees which requirement to fix rather than only that something went wrong.
    #
    # The other route to the same refusal, a run recorded PASSED whose capability rows do not
    # cover the bundle, is what CR7 pins; both must refuse.
    with world["sessions"]() as session:
        verdict = qualification_verdict_in_session(session, lab.id, "LAB")
    assert verdict.qualified is False
    assert verdict.gap is QualificationGap.LAST_ATTEMPT_FAILED

    history = world["connectors"].qualification_history(installation.id)
    amendment_row = next(
        c for c in history[0].capabilities if c.capability is Capability.AMENDMENT_PUBLICATION
    )
    assert amendment_row.passed is False
    assert "capacité non déclarée" in (amendment_row.detail or "")


def test_AC5_native_facilities_are_completely_unaffected(world):
    """§85: the control. Connect exists beside Native, never underneath it."""
    native_provider = make_provider(world["organizations"], "Clinique Nour", operational=False)
    native_lab = make_lab(world["organizations"], "Laboratoire Nour", operational=False)
    for org, roles in (
        (native_provider, [OrganizationRole.CLINICIAN]),
        (native_lab, [OrganizationRole.LAB_TECH, OrganizationRole.LAB_RESULT_RELEASER]),
    ):
        world["enrollments"].create(
            organization_id=org.id,
            participation_mode=ParticipationMode.NATIVE,
            onboarding_status=OnboardingStatus.ORGANIZATION_READY,
        )
        identity_id = _identity(world, f"staff-{org.id}")
        membership, _ = world["memberships"].create(organization_id=org.id, identity_id=identity_id)
        for role in roles:
            world["roles"].assign(membership_id=membership.id, role=role)

    provider_findings = _findings(world, native_provider.id)
    assert "connector_qualified" not in provider_findings
    assert "connector_operational" not in provider_findings
    with world["sessions"]() as session:
        assert is_operationally_ready_in_session(session, native_provider.id)


def test_AC6_qualification_evidence_is_never_rewritten(world):
    """§46: a failed attempt stays failed; a later success sits beside it.

    "Was this connector ever qualified, and against what" is the question somebody asks after a
    patient received the wrong result. An answer that can be edited is not an answer.
    """
    lab = _connect_lab(world)
    order_id, _ = _prepared_order(world, lab)
    installation = _register(world, lab)

    failing = SyntheticConnectorSubject(
        connector_key="openelis-fhir", requires_manual_duplicate_entry=True
    )
    _run_suite(world, lab, installation, order_id, subject=failing)

    # A second order, because the first has already been released and amended by the failed run's
    # scenarios -- a real requalification runs against fresh work too.
    second_order, _ = _prepared_order(world, lab)
    _run_suite(world, lab, installation, second_order)

    history = world["connectors"].qualification_history(installation.id)
    assert len(history) == 2
    assert {h.result for h in history} == {
        QualificationResult.FAILED.value,
        QualificationResult.PASSED.value,
    }
    # The later run is the applicable one and it PASSED -- the earlier failure does not linger as
    # the answer. Production qualification is nonetheless false, because the passing run's
    # ORDER_RECEIPT is a declaration; that is this ticket's correction, and AC pins it. What AC6
    # is about is that neither row was rewritten, so assert on the gap rather than on `False`,
    # which would also be satisfied if the failure had somehow become the applicable verdict.
    with world["sessions"]() as session:
        verdict = qualification_verdict_in_session(session, lab.id, "LAB")
    assert verdict.gap is QualificationGap.REQUIRED_CAPABILITY_UNEXERCISED
    assert verdict.qualification_id == history[0].id, (
        "the applicable verdict must come from the later, passing run"
    )
    assert _findings(world, lab.id)["connector_qualified"] is False


def test_AC7_the_source_event_time_survives_into_nothing_it_should_not(world):
    """Q5/§12: DHealth records its own receive time; the source's time is not overwritten by it.

    Asserted on the envelope rather than on a stored column, because this build stores no connector
    message -- the durable message log is DH-CONNECT-AGENT-001's. What matters now is that the
    contract carries both and a connector cannot conflate them.
    """
    lab = _connect_lab(world)
    installation = _register(world, lab)
    subject = SyntheticConnectorSubject()
    occurred = datetime(2026, 9, 15, 6, 30, tzinfo=UTC)
    envelope = subject.envelope_for(
        scenario="backlog",
        organization_id=lab.id,
        installation_id=installation.id,
        lab_order_id=uuid.uuid4(),
        payload=BusinessAcceptance(external_order_id="EXT-1"),
        source_event_id="evt-backlog",
        source_occurred_at=occurred,
    )
    assert envelope.source_occurred_at == occurred
    assert envelope.sent_at > occurred, "a drained backlog transmits long after the event"


def test_AC8_a_connector_cannot_act_for_a_facility_it_is_not_installed_at(world):
    """S9/S4: the *traffic* half of tenant isolation, not the evidence half.

    AC4 and CR4 pin that qualifying laboratory A does not qualify laboratory B. That is about
    stored verdicts. This is about a live message: a bridge bound to laboratory A receives an
    envelope whose ``organization_id`` names laboratory B, which is what a mis-provisioned
    installation or a deliberate cross-tenant attempt actually looks like on the wire.

    Two things must hold. The message is refused as VALIDATION -- not TEMPORARY, because retrying
    a cross-tenant write must never be encouraged -- and laboratory B's order does not move.
    """
    lab_a = _connect_lab(world, name="Laboratoire A")
    lab_b = _connect_lab(world, name="Laboratoire B")
    order_id, _ = _prepared_order(world, lab_b)

    bridge = ConnectBridge(
        workflow=world["workflow"],
        results=world["results"],
        releases=world["releases"],
        amendments=world["amendments"],
        receipts=InMemoryReceiptStore(),
        installation_organization_id=lab_a.id,
        service_identity_id=_service_identity(world),
    )
    subject = SyntheticConnectorSubject()
    envelope = subject.envelope_for(
        scenario="cross-tenant",
        organization_id=lab_b.id,
        installation_id=uuid.uuid4(),
        lab_order_id=order_id,
        payload=BusinessAcceptance(external_order_id="EXT-CROSS"),
        source_event_id="evt-cross-tenant",
        source_occurred_at=utc_now(),
    )

    with pytest.raises(ConnectApplyError) as caught:
        with world["sessions"].begin() as session:
            bridge.apply(session, envelope)
    assert caught.value.category is FailureCategory.VALIDATION
    assert caught.value.category not in RETRYABLE, "a cross-tenant write must not be retried"

    from daher_adapter.exchange.models import LabOrder

    with world["sessions"].begin() as session:
        assert session.get(LabOrder, order_id).status == LabOrderStatus.SELECTED.value, (
            "laboratory B's order moved on a message the bridge should never have applied"
        )
