"""DH-CONNECT-AGENT-001 (provider side) — the synthetic provider reaching qualification.

The mirror of ``test_connect_runtime_qualification.py``, and it closes the mirror-image hole. That
file made ``ORDER_RECEIPT`` exercisable; every *provider* capability was still permanently
``DECLARED``, so a provider connector could not qualify at all — not because it failed, but because
nothing in the codebase could try.

§41's rule is what shapes every test here:

    Invalid qualification: call the adapter directly and mark the capability exercised.

Nothing below calls ``handle_result`` or ``handle_amendment``. The only way the synthetic EMR ends
up holding a report is: a laboratory released it, the release hook queued a notification in the same
transaction, the runtime API handed it over, and the agent delivered it. Likewise the only way
DHealth ends up holding an order is that the adapter emitted one and the agent submitted it.

The laboratory half is deliberately **native**, driven straight through the Exchange's own
repositories. It is not what is under test, and putting a second connector in the middle of it would
mean a failure anywhere in the chain had two possible homes.

**What this still does not prove:** the far end is ``SyntheticProviderAdapter``. No partner system,
no vendor format, no real EMR. §107's wording stands unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from exchange_auth_support import (
    ISSUER,
    PLATFORM_ADMIN_ROLE,
    build_validator,
    generate_keypair,
    make_token,
)
from exchange_support import lab_staff, make_lab, make_offering, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import (
    Capability,
    OrderSubmission,
    ScopedExternalId,
    SourcePractitionerRef,
    fingerprint,
)
from daher_adapter.connect.correlation import CorrelationNamespace, CorrelationRepository
from daher_adapter.connect.models import EvidenceLevel, QualificationResult
from daher_adapter.connect.provider_qualification import (
    ProviderQualificationHarness,
    drive_order_submission,
    drive_release_notification,
)
from daher_adapter.connect.qualification import (
    QUALIFICATION_SUITE_VERSION,
    QualificationGap,
    qualification_verdict_in_session,
)
from daher_adapter.connect.reconciliation import unresolved_obligations
from daher_adapter.connect.repository import ConnectorInstallationRepository
from daher_adapter.connect.runtime_api import build_connect_runtime_router
from daher_adapter.connect.runtime_models import (
    ConnectorCorrelation,
    ConnectorDelivery,
    DeliveryState,
    ObligationState,
)
from daher_adapter.connect_agent.adapter import AdapterEvent, SyntheticProviderAdapter
from daher_adapter.connect_agent.client import ConnectRuntimeClient, TokenSource
from daher_adapter.connect_agent.runner import AgentRunner
from daher_adapter.connect_agent.store import AgentStore, InboxState
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    LabOrder,
    LabOrderItem,
    LabResultReleaseVersion,
    LabResultType,
    OrganizationType,
    ParticipationMode,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    LabResultAmendmentRepository,
    LabResultReleaseRepository,
    LabResultRepository,
)
from daher_adapter.exchange.selection import LabSelectionRepository

RUNTIME_AUDIENCE = "dhealth-connect-runtime"
AGENT_CLIENT = "connect-agent-clinique"
SOURCE_SYSTEM = "emr"
EXTERNAL_ORDER_ID = "EMR-ORDER-77"
SOURCE_EVENT_ID = "emr-order-77-created"
PROVIDER_CAPABILITIES = frozenset(
    {
        Capability.ORDER_SUBMISSION,
        Capability.RELEASED_RESULT_RECEIPT,
        Capability.AMENDMENT_RECEIPT,
    }
)


@pytest.fixture
def world(sessions: sessionmaker[Session]):
    """A CONNECT provider with a registered connector, and a NATIVE laboratory able to finish work.

    Assembled here rather than in ``src/`` because building the agent needs ``TestClient`` -- a test
    dependency ``src/`` has no business importing. ``provider_qualification`` takes the finished
    runner as an argument for exactly that reason.
    """
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)

    # ``operational=False``: the default enrols NATIVE, and a facility cannot be both. It also
    # leaves the provider with no CLINICIAN, which is the honest shape of a CONNECT facility --
    # people order in their own EMR and have no DHealth account (§7).
    provider = make_provider(organizations, "Clinique de la Paix", operational=False)
    FacilityEnrollmentRepository(sessions).create(
        organization_id=provider.id, participation_mode=ParticipationMode.CONNECT
    )
    lab = make_lab(organizations, "Laboratoire Central", operational=True)
    # Listed and accepting, because ``LabSelectionRepository.select`` refuses a laboratory that is
    # neither -- a real rule, satisfied by the fixture rather than worked around.
    LabDirectoryProfileRepository(sessions, organizations).upsert(
        lab_organization_id=lab.id,
        public_address="Boulevard de la République",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )

    glucose, _ = catalog.create_entry(code="GLU", display_name="Glycémie à jeun")
    offering, _ = make_offering(catalog, lab_organization_id=lab.id, catalog_entry_id=glucose.id)
    price, _ = catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("500.00"),
        currency="DJF",
        expected_current_price_id=None,
    )

    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer=ISSUER, subject="platform-admin", display_name="Platform", email=None
    )
    installations = ConnectorInstallationRepository(sessions)
    record = installations.register(
        organization_id=provider.id,
        connector_key="synthetic-emr",
        connector_version="0.1",
        source_system="emr",
        interface_type="FHIR",
        configuration_fingerprint=fingerprint({"endpoint": "https://emr.invalid/fhir"}),
        mapping_fingerprint=fingerprint({"mapping_revision": [["emr", "GLUC", "cat-1", "GLU"]]}),
        actor_identity_id=identity.id,
    )
    installations.set_service_identity(
        installation_id=record.id, service_client_id=AGENT_CLIENT, actor_identity_id=identity.id
    )

    private_key, public_key = generate_keypair()
    app = FastAPI()
    app.include_router(
        build_connect_runtime_router(
            session_factory=sessions,
            runtime_token_validator=build_validator(public_key, audience=RUNTIME_AUDIENCE),
            platform_admin_role=PLATFORM_ADMIN_ROLE,
        )
    )
    token = make_token(
        private_key,
        subject=f"service-account-{AGENT_CLIENT}",
        audience=RUNTIME_AUDIENCE,
        roles=[],
        extra_claims={"azp": AGENT_CLIENT},
    )
    return {
        "sessions": sessions,
        "provider_id": provider.id,
        "lab_id": lab.id,
        "installation_id": record.id,
        "identity_id": identity.id,
        "price_id": price.id,
        "app": app,
        "token": token,
    }


def _agent(world, tmp_path, adapter: SyntheticProviderAdapter):
    store = AgentStore(tmp_path / "agent.sqlite")
    client = ConnectRuntimeClient(
        base_url="http://connect.invalid",
        tokens=TokenSource(world["token"]),
        http_client=TestClient(world["app"]),
    )
    runner = AgentRunner(
        store=store,
        client=client,
        adapter=adapter,
        organization_id=world["provider_id"],
        installation_id=world["installation_id"],
        source_system="emr",
    )
    return store, runner


def _locally_acknowledged(store: AgentStore):
    def check(source_event_id: str) -> bool:
        return bool(
            store._connection.execute(  # noqa: SLF001
                "SELECT 1 FROM inbox WHERE source_event_id = ? AND state = ?",
                (source_event_id, InboxState.ACKNOWLEDGED),
            ).fetchone()
        )

    return check


def _queue_submission(adapter: SyntheticProviderAdapter, organization_id: uuid.UUID) -> None:
    """What the partner's EMR "did": a clinician placed an order in it.

    The adapter is handed an event, exactly as a real one would produce from its own system. Nothing
    here talks to DHealth -- the agent's poll is what turns this into a submission, which is the
    whole point of driving it this way rather than posting the envelope.
    """
    adapter.queued_events.append(
        AdapterEvent(
            source_event_id=SOURCE_EVENT_ID,
            occurred_at=datetime.now(UTC) - timedelta(minutes=5),
            payload=OrderSubmission(
                patient=ScopedExternalId(
                    source_organization_id=organization_id,
                    source_system="emr",
                    source_identity_system="urn:emr:mrn",
                    source_identity_value="MRN-77",
                ),
                practitioner=SourcePractitionerRef(
                    source_organization_id=organization_id,
                    source_system="emr",
                    source_identity_system="urn:emr:staff",
                    source_identity_value="DR-3",
                ),
                external_order_id=EXTERNAL_ORDER_ID,
                source_test_codes=("GLU",),
            ),
        )
    )


def _run_native_laboratory_to_release(world, lab_order_id: uuid.UUID) -> None:
    """The laboratory's own work, through DHealth's own repositories. Native, not a connector.

    Accept, collect, start, record the value, complete the entry, release. Every call is the one the
    Exchange's own UI makes; nothing here is a Connect concern except the hook that fires at the end
    of ``release`` -- which is what the drive is measuring.
    """
    sessions = world["sessions"]
    organizations = OrganizationRepository(sessions)
    workflow = LabWorkflowRepository(sessions, organizations)
    results = LabResultRepository(sessions)
    releases = LabResultReleaseRepository(sessions)
    lab_id = world["lab_id"]
    actor = world["identity_id"]

    LabSelectionRepository(sessions, organizations).select(
        lab_order_id=lab_order_id,
        lab_organization_id=lab_id,
        expected_price_ids=frozenset({world["price_id"]}),
    )
    workflow.accept(order_id=lab_order_id, lab_organization_id=lab_id)
    workflow.record_collection(
        order_id=lab_order_id,
        lab_organization_id=lab_id,
        recorded_by_identity_id=lab_staff(organizations, lab_id),
    )
    workflow.start_processing(order_id=lab_order_id, lab_organization_id=lab_id)

    with sessions() as session:
        item = session.scalar(select(LabOrderItem).where(LabOrderItem.lab_order_id == lab_order_id))
        item_id = item.id

    results.record_observation(
        order_id=lab_order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot="Glycémie à jeun",
        recorded_by_identity_id=actor,
        numeric_value=Decimal("5.4"),
        numeric_unit="mmol/L",
    )
    results.complete_entry(
        order_id=lab_order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        completed_by_identity_id=actor,
    )
    releases.release(
        order_id=lab_order_id, lab_organization_id=lab_id, released_by_identity_id=actor
    )


def _run_native_amendment(
    world, lab_order_id: uuid.UUID, *, label: str = "Glycémie à jeun"
) -> None:
    """A correction after release, again through the Exchange's own amendment machinery.

    ``label`` is a parameter because the amendment writes its **own** observation: blanking the
    rows released earlier does not reach it, so a test that needs an inexpressible correction has to
    say so here. (Learned by writing that test the other way first.)
    """
    sessions = world["sessions"]
    amendments = LabResultAmendmentRepository(sessions)
    lab_id = world["lab_id"]
    actor = world["identity_id"]

    amendment = amendments.start_amendment(
        order_id=lab_order_id, lab_organization_id=lab_id, started_by_identity_id=actor
    )
    with sessions() as session:
        item = session.scalar(select(LabOrderItem).where(LabOrderItem.lab_order_id == lab_order_id))
        item_id = item.id
        from daher_adapter.exchange.results import _effective_observation_rows

        effective = _effective_observation_rows(session, lab_order_id)
        predecessor_id = effective[0][1]

    amendments.correct_observation(
        amendment_id=amendment.id,
        order_id=lab_order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        observation_id=predecessor_id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot=label,
        corrected_by_identity_id=actor,
        numeric_value=Decimal("7.1"),
        numeric_unit="mmol/L",
    )
    amendments.release_amendment(
        amendment_id=amendment.id,
        order_id=lab_order_id,
        lab_organization_id=lab_id,
        released_by_identity_id=actor,
    )


@pytest.fixture
def driven(world, sessions, tmp_path):
    """The whole provider journey, driven once and shared by the assertions below.

    One journey rather than one per test because each of the three drives depends on the previous
    one having really happened: there is no result to notify about until an order exists, and no
    amendment until a release does. Re-driving per test would either duplicate the whole chain or
    fake its earlier links, and faking them is what §41 forbids.
    """
    adapter = SyntheticProviderAdapter()
    store, runner = _agent(world, tmp_path, adapter)
    acknowledged = _locally_acknowledged(store)
    try:
        submission = drive_order_submission(
            sessions,
            installation_id=world["installation_id"],
            namespace=CorrelationNamespace(
                organization_id=world["provider_id"], source_system=SOURCE_SYSTEM
            ),
            external_order_id=EXTERNAL_ORDER_ID,
            emit_submission=lambda: _queue_submission(adapter, world["provider_id"]),
            run_cycle=runner.run_once,
        )
        assert submission.lab_order_id is not None, submission.detail
        lab_order_id = submission.lab_order_id

        released = drive_release_notification(
            sessions,
            installation_id=world["installation_id"],
            lab_order_id=lab_order_id,
            external_order_id=EXTERNAL_ORDER_ID,
            release_version=1,
            perform_release=lambda: _run_native_laboratory_to_release(world, lab_order_id),
            run_cycle=runner.run_once,
            partner_has_report=lambda ext, ver: adapter.report_for(ext, ver) is not None,
            partner_call_count=lambda: adapter.handle_result_calls,
            locally_acknowledged=acknowledged,
        )
        amended = drive_release_notification(
            sessions,
            installation_id=world["installation_id"],
            lab_order_id=lab_order_id,
            external_order_id=EXTERNAL_ORDER_ID,
            release_version=2,
            perform_release=lambda: _run_native_amendment(world, lab_order_id),
            run_cycle=runner.run_once,
            partner_has_report=lambda ext, ver: adapter.report_for(ext, ver) is not None,
            partner_call_count=lambda: adapter.handle_amendment_calls,
            locally_acknowledged=acknowledged,
        )
        yield {
            "adapter": adapter,
            "lab_order_id": lab_order_id,
            "submission": submission,
            "released": released,
            "amended": amended,
        }
    finally:
        store.close()


class TestProviderCapabilitiesAreExercised:
    def test_PQ1_a_submitted_order_becomes_a_dhealth_order_with_its_correlation(
        self, driven, sessions, world
    ):
        """The EMR asked, and DHealth has the order **and** knows what the EMR calls it.

        The correlation half is not bookkeeping: without it the two later drives cannot happen at
        all, because a released result would have no provider-side identifier to be filed against.
        """
        submission = driven["submission"]
        assert submission.passed, submission.detail
        assert submission.central_state == DeliveryState.DELIVERED.value

        with sessions() as session:
            order = session.get(LabOrder, driven["lab_order_id"])
        assert order is not None
        assert order.provider_organization_id == world["provider_id"]

    def test_PQ2_a_released_result_reaches_the_providers_own_system(self, driven):
        """§41's assertion, provider side: this test never called ``handle_result``, and it was.

        Exactly once, and the count is the assertion rather than the presence of the report --
        the synthetic adapter is idempotent, so a redelivery that should never have happened would
        leave the report looking perfectly filed.
        """
        released = driven["released"]
        assert released.passed, released.detail
        assert released.adapter_calls == 1
        assert released.central_state == DeliveryState.DELIVERED.value

        report = driven["adapter"].report_for(EXTERNAL_ORDER_ID, 1)
        assert report is not None
        assert report.external_order_id == EXTERNAL_ORDER_ID
        assert [obs.source_code for obs in report.observations] == ["GLU"]
        assert not report.is_amendment

    def test_PQ3_an_amendment_arrives_as_a_correction_not_a_second_result(self, driven):
        """The clinical distinction the whole provider side exists to preserve.

        A correction filed as an independent second result leaves a clinician reading both values
        with nothing saying which superseded the other. So the amendment must arrive as version 2,
        naming version 1, through ``handle_amendment`` rather than ``handle_result`` -- and the
        original must still be there.
        """
        amended = driven["amended"]
        assert amended.passed, amended.detail
        assert amended.adapter_calls == 1

        adapter = driven["adapter"]
        correction = adapter.report_for(EXTERNAL_ORDER_ID, 2)
        assert correction is not None
        assert correction.is_amendment
        assert correction.corrects_release_version == 1
        assert adapter.handle_result_calls == 1, "an amendment is not a second released result"
        assert adapter.report_for(EXTERNAL_ORDER_ID, 1) is not None, (
            "the superseded version must remain readable in the receiving system"
        )

    def test_PQ4_the_observation_values_are_relayed_exactly_as_released(self, driven):
        """DHealth records and routes. It does not normalize a unit or reinterpret a value."""
        original = driven["adapter"].report_for(EXTERNAL_ORDER_ID, 1)
        correction = driven["adapter"].report_for(EXTERNAL_ORDER_ID, 2)
        assert original.observations[0].numeric_value == Decimal("5.4")
        assert original.observations[0].numeric_unit == "mmol/L"
        assert correction.observations[0].numeric_value == Decimal("7.1")

    def test_PQ5_the_notification_carries_the_version_that_was_released(self, driven, sessions):
        """Each notification is about one release version, and the versions really are distinct.

        Read from DHealth's own release versions rather than from the adapter, so the assertion is
        that the two sides agree rather than that the adapter is self-consistent.
        """
        with sessions() as session:
            versions = sorted(
                v.version_number
                for v in session.scalars(
                    select(LabResultReleaseVersion).where(
                        LabResultReleaseVersion.lab_order_id == driven["lab_order_id"]
                    )
                ).all()
            )
        assert versions == [1, 2]


class TestProviderQualificationRun:
    def test_PQ6_three_exercised_scenarios_make_a_passing_run(self, driven):
        """The run the repository would record: every provider capability EXERCISED and passing."""
        started = datetime.now(UTC)
        run = ProviderQualificationHarness(declared_capabilities=PROVIDER_CAPABILITIES).run(
            started_at=started,
            completed_at=started,
            submission=driven["submission"],
            released_result=driven["released"],
            amendment=driven["amended"],
        )
        outcomes = {outcome.capability: outcome for outcome in run.capability_outcomes}
        assert set(outcomes) == PROVIDER_CAPABILITIES
        for capability in PROVIDER_CAPABILITIES:
            assert outcomes[capability].passed, capability
            assert outcomes[capability].evidence_level is EvidenceLevel.EXERCISED

    def test_PQ7_the_synthetic_limitation_survives_every_pass(self, driven):
        """§82/§86. A pass against a synthetic adapter must never read as a real integration."""
        started = datetime.now(UTC)
        run = ProviderQualificationHarness(declared_capabilities=PROVIDER_CAPABILITIES).run(
            started_at=started,
            completed_at=started,
            submission=driven["submission"],
            released_result=driven["released"],
            amendment=driven["amended"],
        )
        for scenario in run.scenarios:
            assert scenario.passed
            assert scenario.limitation is not None, scenario.scenario
            assert "synthétique" in scenario.limitation

    def test_PQ8_a_connector_with_no_runtime_stays_declared_and_fails(self):
        """Nothing driven, nothing proved. The declaration-only answer, and it is not a pass.

        This is the state every provider connector was in before this module existed, and the run
        must still be able to express it -- a harness that could only report success would have
        made "we did not test it" indistinguishable from "it works".
        """
        started = datetime.now(UTC)
        run = ProviderQualificationHarness(declared_capabilities=PROVIDER_CAPABILITIES).run(
            started_at=started, completed_at=started
        )
        assert not run.all_passed
        for outcome in run.capability_outcomes:
            assert not outcome.passed
            assert outcome.evidence_level is EvidenceLevel.DECLARED

    def test_PQ9_an_undeclared_capability_fails_rather_than_being_skipped(self, driven):
        """A provider that never declared amendment receipt does not get to qualify without it.

        The required provider bundle is the set whose absence breaks the round trip; a correction
        that cannot reach the clinician who acted on the original is the one failure with direct
        patient consequences, which is why it is required rather than optional.
        """
        started = datetime.now(UTC)
        run = ProviderQualificationHarness(
            declared_capabilities=frozenset(
                {Capability.ORDER_SUBMISSION, Capability.RELEASED_RESULT_RECEIPT}
            )
        ).run(
            started_at=started,
            completed_at=started,
            submission=driven["submission"],
            released_result=driven["released"],
            amendment=driven["amended"],
        )
        outcomes = {outcome.capability: outcome for outcome in run.capability_outcomes}
        assert not outcomes[Capability.AMENDMENT_RECEIPT].passed
        assert not run.all_passed


class TestWhatStillDoesNotHappen:
    def test_PQ11_a_provider_connector_can_now_reach_production_qualification(
        self, driven, world, sessions
    ):
        """The headline change: a provider facility that could never qualify, qualifying.

        Before this, ``qualification_verdict_in_session`` for a PROVIDER always ended at
        ``REQUIRED_CAPABILITY_UNEXERCISED`` naming all three capabilities -- not because any of them
        failed, but because nothing in the codebase could try them. The verdict is read from the
        durable rows rather than from the run object, because the rows are what production consults.
        """
        started = datetime.now(UTC)
        run = ProviderQualificationHarness(declared_capabilities=PROVIDER_CAPABILITIES).run(
            started_at=started,
            completed_at=started,
            submission=driven["submission"],
            released_result=driven["released"],
            amendment=driven["amended"],
        )
        ConnectorInstallationRepository(sessions).record_qualification(
            installation_id=world["installation_id"],
            suite_version=QUALIFICATION_SUITE_VERSION,
            started_at=run.started_at,
            completed_at=run.completed_at,
            result=QualificationResult.PASSED if run.all_passed else QualificationResult.FAILED,
            capabilities=list(run.capability_outcomes),
            actor_identity_id=world["identity_id"],
            evidence_summary=run.evidence_summary,
        )

        with sessions() as session:
            verdict = qualification_verdict_in_session(
                session, world["provider_id"], OrganizationType.PROVIDER.value
            )
        assert verdict.qualified, verdict.detail
        assert verdict.unexercised_capabilities == ()

    def test_PQ12_qualified_is_still_not_production_routable(self, driven, world, sessions):
        """§6, §86, §107 — the rule a working provider side must not quietly relax.

        The far end is a synthetic adapter. A qualified provider connector with a healthy agent is
        still refused ``READY_FOR_ROUTING``, exactly as the laboratory one is, and this is pinned as
        a test so a later ticket cannot lift the block by accident while editing something nearby.
        """
        from daher_adapter.exchange.facility_enrollment import InvalidOnboardingTransition

        started = datetime.now(UTC)
        run = ProviderQualificationHarness(declared_capabilities=PROVIDER_CAPABILITIES).run(
            started_at=started,
            completed_at=started,
            submission=driven["submission"],
            released_result=driven["released"],
            amendment=driven["amended"],
        )
        ConnectorInstallationRepository(sessions).record_qualification(
            installation_id=world["installation_id"],
            suite_version=QUALIFICATION_SUITE_VERSION,
            started_at=run.started_at,
            completed_at=run.completed_at,
            result=QualificationResult.PASSED,
            capabilities=list(run.capability_outcomes),
            actor_identity_id=world["identity_id"],
            evidence_summary=run.evidence_summary,
        )

        with pytest.raises(InvalidOnboardingTransition) as error:
            FacilityEnrollmentRepository(sessions).set_onboarding_status(
                organization_id=world["provider_id"],
                onboarding_status="READY_FOR_ROUTING",
            )
        assert "vendor adapter" in str(error.value)

    def test_PQ10_a_natively_placed_order_notifies_nobody(self, world, sessions, tmp_path):
        """No correlation, no notification -- and that is the correct outcome, not a bug.

        An order placed inside DHealth by a facility that also happens to run a connector was never
        asked for by that EMR and has no identifier there. Sending it a DHealth id it has never seen
        would be a message it cannot file: worse than silence, because it looks delivered.
        """
        from daher_adapter.exchange.identity import (
            PatientReferenceRepository,
            PractitionerReferenceRepository,
        )
        from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput

        sessions_factory = world["sessions"]
        organizations = OrganizationRepository(sessions_factory)
        catalog = CatalogRepository(sessions_factory, organizations)
        patients = PatientReferenceRepository(sessions_factory, organizations)
        practitioners = PractitionerReferenceRepository(sessions_factory)

        patient = patients.get_or_create(
            source_organization_id=world["provider_id"],
            source_system="dhealth",
            source_identity_system="urn:dhealth:native",
            source_identity_value="NATIVE-1",
        )
        practitioner = practitioners.get_or_create(
            source_organization_id=world["provider_id"],
            source_system="dhealth",
            source_identity_system="urn:dhealth:native",
            source_identity_value="NATIVE-DR",
        )
        with sessions() as session:
            entry_id = session.scalar(
                select(LabOrderItem.catalog_entry_id).limit(1)
            ) or _glucose_id(session)

        created = LabOrderRepository(sessions_factory, organizations, catalog).create(
            idempotency_key="native-1",
            provider_organization_id=world["provider_id"],
            patient_reference_id=patient.id,
            practitioner_reference_id=practitioner.id,
            items=[OrderItemInput(catalog_entry_id=entry_id)],
        )
        _run_native_laboratory_to_release(world, created.order.id)

        with sessions() as session:
            deliveries = session.scalars(
                select(ConnectorDelivery).where(
                    ConnectorDelivery.connector_installation_id == world["installation_id"]
                )
            ).all()
        assert deliveries == [], "an order the EMR never submitted must not produce a notification"

    def test_PQ13_a_replacement_installation_starts_unqualified(
        self, driven, world, sessions, tmp_path
    ):
        """§5, at the qualification layer. The namespace carries over; the qualification does not.

        This is the half that would be dangerous in the other direction. v2 inherits what the source
        system's identifiers *mean* -- a fact about the partner's world, unchanged by DHealth
        upgrading its adapter. It inherits nothing about whether v2's own build can carry clinical
        traffic, which is a fact about the connector and has to be earned again.

        So immediately after a replacement the facility is not production-qualified, and the
        correlation still resolves. Both at once is the point: a system that had to choose between
        them would either lose the identifier or credit an untested build.
        """
        started = datetime.now(UTC)
        run = ProviderQualificationHarness(declared_capabilities=PROVIDER_CAPABILITIES).run(
            started_at=started,
            completed_at=started,
            submission=driven["submission"],
            released_result=driven["released"],
            amendment=driven["amended"],
        )
        installations = ConnectorInstallationRepository(sessions)
        installations.record_qualification(
            installation_id=world["installation_id"],
            suite_version=QUALIFICATION_SUITE_VERSION,
            started_at=run.started_at,
            completed_at=run.completed_at,
            result=QualificationResult.PASSED,
            capabilities=list(run.capability_outcomes),
            actor_identity_id=world["identity_id"],
            evidence_summary=run.evidence_summary,
        )
        with sessions() as session:
            assert qualification_verdict_in_session(
                session, world["provider_id"], OrganizationType.PROVIDER.value
            ).qualified

        installations.retire(
            installation_id=world["installation_id"], actor_identity_id=world["identity_id"]
        )
        successor = installations.register(
            organization_id=world["provider_id"],
            connector_key="synthetic-emr",
            connector_version="0.2",
            source_system=SOURCE_SYSTEM,
            interface_type="FHIR",
            configuration_fingerprint=fingerprint({"endpoint": "https://emr.invalid/fhir/v2"}),
            mapping_fingerprint=fingerprint(
                {"mapping_revision": [["emr", "GLUC", "cat-1", "GLU"]]}
            ),
            actor_identity_id=world["identity_id"],
        )

        with sessions() as session:
            verdict = qualification_verdict_in_session(
                session, world["provider_id"], OrganizationType.PROVIDER.value
            )
            # ...and the identifier v1 reported still resolves, under the namespace rather than
            # under either installation.
            resolved = CorrelationRepository().resolve_order(
                session,
                namespace=CorrelationNamespace(
                    organization_id=world["provider_id"], source_system=SOURCE_SYSTEM
                ),
                external_order_id=EXTERNAL_ORDER_ID,
            )
        assert not verdict.qualified, (
            "a new installation has its own qualification to earn, whatever its predecessor proved"
        )
        assert resolved == driven["lab_order_id"]
        assert successor.id != world["installation_id"]

    def test_PQ14_suspension_withholds_qualification_without_erasing_it(
        self, driven, world, sessions
    ):
        """Suspension is a pause, and the qualification record is not what is paused.

        A suspended connector is not production-qualified -- that is the operator's instruction and
        it must hold. But the run that proved this build could carry clinical traffic is still true
        while it is suspended, so restoring must not require requalifying: an operator who suspends
        a connector for an afternoon has not invalidated last week's evidence, and a system that
        behaved as though they had would make suspension expensive enough to avoid using.
        """
        started = datetime.now(UTC)
        run = ProviderQualificationHarness(declared_capabilities=PROVIDER_CAPABILITIES).run(
            started_at=started,
            completed_at=started,
            submission=driven["submission"],
            released_result=driven["released"],
            amendment=driven["amended"],
        )
        installations = ConnectorInstallationRepository(sessions)
        installations.record_qualification(
            installation_id=world["installation_id"],
            suite_version=QUALIFICATION_SUITE_VERSION,
            started_at=run.started_at,
            completed_at=run.completed_at,
            result=QualificationResult.PASSED,
            capabilities=list(run.capability_outcomes),
            actor_identity_id=world["identity_id"],
            evidence_summary=run.evidence_summary,
        )

        installations.suspend(
            installation_id=world["installation_id"], actor_identity_id=world["identity_id"]
        )
        with sessions() as session:
            suspended = qualification_verdict_in_session(
                session, world["provider_id"], OrganizationType.PROVIDER.value
            )
        assert not suspended.qualified
        assert suspended.gap == QualificationGap.SUSPENDED

        installations.restore(
            installation_id=world["installation_id"], actor_identity_id=world["identity_id"]
        )
        with sessions() as session:
            restored = qualification_verdict_in_session(
                session, world["provider_id"], OrganizationType.PROVIDER.value
            )
        assert restored.qualified, "restoring must not require requalifying"

    def test_PQ15_a_qualified_connector_can_still_owe_an_undelivered_result(
        self, driven, world, sessions
    ):
        """QUALIFIED is a statement about a connector, not a guarantee about every message.

        The two reads are independent and an operator needs both. A connector that has proved it can
        receive released results can still have a specific report that could not be built and
        therefore could not be queued -- and the honest answer to "is everything the laboratory
        released actually on its way" comes from the obligation table, never from the verdict.

        The failure is induced the way BF1 induces it: a label that the contract refuses, reaching
        the boundary through a release that really happened. The release commits regardless.
        """
        started = datetime.now(UTC)
        run = ProviderQualificationHarness(declared_capabilities=PROVIDER_CAPABILITIES).run(
            started_at=started,
            completed_at=started,
            submission=driven["submission"],
            released_result=driven["released"],
            amendment=driven["amended"],
        )
        ConnectorInstallationRepository(sessions).record_qualification(
            installation_id=world["installation_id"],
            suite_version=QUALIFICATION_SUITE_VERSION,
            started_at=run.started_at,
            completed_at=run.completed_at,
            result=QualificationResult.PASSED,
            capabilities=list(run.capability_outcomes),
            actor_identity_id=world["identity_id"],
            evidence_summary=run.evidence_summary,
        )

        # A second correction, whose own observation carries a label the contract refuses.
        _run_native_amendment(world, driven["lab_order_id"], label="")

        with sessions() as session:
            verdict = qualification_verdict_in_session(
                session, world["provider_id"], OrganizationType.PROVIDER.value
            )
            owed = unresolved_obligations(session)
        assert verdict.qualified, "one undeliverable message does not un-qualify a connector"
        assert len(owed) == 1
        assert owed[0].state == ObligationState.FAILED.value
        assert owed[0].failure_category == "VALIDATION"
        assert owed[0].resolved_at is None

    def test_PQ16_an_agent_may_not_submit_into_a_source_system_it_did_not_register(
        self, world, tmp_path, sessions
    ):
        """§6, enforced at the boundary rather than trusted from the envelope.

        The namespace an identifier lands in decides which external system's ``ORDER-77`` this is,
        so an agent that could name it in the message would be choosing its own scope -- the same
        class of mistake as accepting ``organization_id`` from a request body. The installation's
        registered source system is the authority, and a disagreement is refused rather than
        reconciled.

        Driven through the real agent and the real API, because that is the path where it matters:
        this is the check that would go unnoticed if it only existed in a unit test of the applier.
        """
        adapter = SyntheticProviderAdapter()
        store = AgentStore(tmp_path / "wrong-namespace.sqlite")
        client = ConnectRuntimeClient(
            base_url="http://connect.invalid",
            tokens=TokenSource(world["token"]),
            http_client=TestClient(world["app"]),
        )
        runner = AgentRunner(
            store=store,
            client=client,
            adapter=adapter,
            organization_id=world["provider_id"],
            installation_id=world["installation_id"],
            # Registered as "emr". This agent claims to be speaking for a different system.
            source_system="lis",
        )
        try:
            _queue_submission(adapter, world["provider_id"])
            for _ in range(3):
                runner.run_once()
        finally:
            store.close()

        with sessions() as session:
            delivery = session.scalar(
                select(ConnectorDelivery).where(
                    ConnectorDelivery.message_type == "ORDER_SUBMISSION"
                )
            )
            correlations = session.scalars(select(ConnectorCorrelation)).all()
        assert delivery is not None, "the message reached DHealth -- it is the applier that refuses"
        assert delivery.state == DeliveryState.DEAD_LETTER.value, (
            "a namespace disagreement is not retryable: retrying changes nothing about it"
        )
        assert delivery.last_failure_category == "VALIDATION"
        assert correlations == [], (
            "no identifier may be written into a namespace the installation did not register"
        )


def _glucose_id(session: Session) -> uuid.UUID:
    from daher_adapter.exchange.models import TestCatalogEntry

    return session.scalar(select(TestCatalogEntry.id).where(TestCatalogEntry.code == "GLU"))
