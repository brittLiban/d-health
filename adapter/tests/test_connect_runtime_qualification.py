"""DH-CONNECT-AGENT-001 §40–§41, §104 — the synthetic laboratory finally reaching qualification.

This file is the one that closes the hole DH-CONNECT-CONTRACT-001's correction opened deliberately.
That correction recorded ``ORDER_RECEIPT`` as ``DECLARED`` because nothing could deliver an order,
and it said in as many words that the ticket which supplies the runtime is the one that must earn
``EXERCISED``. So the assertions below are not "the flag flipped" -- they are "an order travelled
the durable path, three independent places agree it arrived, and only then did the flag flip".

§41's invalid version is worth restating, because it is what every test here is shaped to avoid:

    Invalid qualification: call ``synthetic_adapter.handle_order(order)`` directly and mark
    ORDER_RECEIPT exercised. That bypasses the runtime this ticket is supposed to prove.

Nothing here calls ``handle_order``. The only way the adapter ends up holding an order is if DHealth
queued it, the runtime API handed it over, and the agent delivered it.

**What this still does not prove**, stated here rather than in a footnote: the far end is
``SyntheticLabAdapter``. No partner system, no vendor format, no real EMR or LIS. §107's wording is
the honest summary -- the runtime exists, and no real external system has been qualified.
"""

from __future__ import annotations

import uuid

import pytest
from exchange_auth_support import (
    ISSUER,
    PLATFORM_ADMIN_ROLE,
    build_validator,
    generate_keypair,
    make_token,
)
from exchange_support import make_patient_reference, make_practitioner_reference, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import (
    Capability,
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
from daher_adapter.connect.runtime_api import build_connect_runtime_router
from daher_adapter.connect.runtime_models import ConnectorDelivery, DeliveryState
from daher_adapter.connect.runtime_qualification import (
    drive_order_receipt,
    order_receipt_scenario,
    qualification_event_id,
)
from daher_adapter.connect_agent.adapter import SyntheticLabAdapter
from daher_adapter.connect_agent.client import ConnectRuntimeClient, TokenSource
from daher_adapter.connect_agent.runner import AgentRunner
from daher_adapter.connect_agent.store import AgentStore, InboxState
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    LabOrderStatus,
    LabSelection,
    Organization,
    OrganizationStatus,
    OrganizationType,
    ParticipationMode,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.models import utc_now

RUNTIME_AUDIENCE = "dhealth-connect-runtime"
AGENT_CLIENT = "connect-agent-lab-central"
CONFIG_FP = fingerprint({"endpoint": "https://lis.invalid/fhir"})
MAPPINGS = (
    TestMapping(
        source_system="synthetic-lis",
        source_code="GLU",
        catalog_entry_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        catalog_code="GLU",
    ),
)


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def world(sessions: sessionmaker[Session], keypair):
    """A CONNECT laboratory, a registered connector with a service identity, and a running agent.

    Assembled here rather than in ``src/`` because building the agent needs ``TestClient`` -- a test
    dependency. ``runtime_qualification`` takes the finished runner as an argument for exactly that
    reason.
    """
    with sessions.begin() as session:
        org = Organization(
            name="Laboratoire Central",
            org_type=OrganizationType.LAB.value,
            status=OrganizationStatus.ACTIVE.value,
        )
        session.add(org)
        session.flush()
        session.expunge(org)
    FacilityEnrollmentRepository(sessions).create(
        organization_id=org.id, participation_mode=ParticipationMode.CONNECT
    )
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer=ISSUER, subject="platform-admin", display_name="Platform", email=None
    )
    repository = ConnectorInstallationRepository(sessions)
    record = repository.register(
        organization_id=org.id,
        connector_key="synthetic-lis",
        connector_version="0.1",
        source_system="synthetic-lis",
        interface_type="FHIR",
        configuration_fingerprint=CONFIG_FP,
        mapping_fingerprint=mapping_fingerprint(MAPPINGS),
        actor_identity_id=identity.id,
    )
    repository.set_service_identity(
        installation_id=record.id, service_client_id=AGENT_CLIENT, actor_identity_id=identity.id
    )

    private_key, public_key = keypair
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
        "organization_id": org.id,
        "installation_id": record.id,
        "identity_id": identity.id,
        "app": app,
        "token": token,
        "repository": repository,
    }


def _agent(world, sessions, tmp_path, adapter: SyntheticLabAdapter):
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
        organization_id=world["organization_id"],
        installation_id=world["installation_id"],
        source_system="synthetic-lis",
    )
    return store, runner


def _drive(world, sessions, tmp_path, adapter, lab_order_id):
    store, runner = _agent(world, sessions, tmp_path, adapter)
    try:
        return drive_order_receipt(
            sessions,
            installation_id=world["installation_id"],
            organization_id=world["organization_id"],
            lab_order_id=lab_order_id,
            run_cycle=runner.run_once,
            adapter_has_order=adapter.has_order,
            locally_acknowledged=lambda: bool(
                store._connection.execute(  # noqa: SLF001
                    "SELECT 1 FROM inbox WHERE state = ?", (InboxState.ACKNOWLEDGED,)
                ).fetchone()
            ),
        )
    finally:
        store.close()


class TestOrderReceiptIsExercised:
    def test_RQ1_an_order_reaches_the_adapter_through_the_durable_path(
        self, world, sessions, tmp_path
    ):
        """§41. Queued centrally, claimed over HTTP, delivered by the agent, acknowledged back.

        The assertion nobody should be able to satisfy by shortcut: the adapter was never called by
        this test, and it has the order.
        """
        adapter = SyntheticLabAdapter()
        lab_order_id = uuid.uuid4()
        assert adapter.received_orders == []

        evidence = _drive(world, sessions, tmp_path, adapter, lab_order_id)

        assert evidence.passed
        assert evidence.delivered_to_adapter
        assert evidence.central_state == DeliveryState.DELIVERED.value
        assert evidence.locally_acknowledged
        assert adapter.has_order(lab_order_id)

    def test_RQ2_the_scenario_records_exercised_evidence(self, world, sessions, tmp_path):
        """And the scenario the harness stores says ``EXERCISED``, with the limitation kept.

        The limitation survives the pass on purpose -- it is the part of the claim that was *not*
        established, and DH-CONNECT-CONTRACT-001's correction exists because a limitation nobody
        records is a limitation nobody reads.
        """
        adapter = SyntheticLabAdapter()
        evidence = _drive(world, sessions, tmp_path, adapter, uuid.uuid4())
        scenario = order_receipt_scenario(evidence)

        assert scenario.scenario == "LQ1-order-receipt-delivered"
        assert scenario.capability is Capability.ORDER_RECEIPT
        assert scenario.evidence_level is EvidenceLevel.EXERCISED
        assert scenario.passed
        assert "synthétique" in (scenario.limitation or "")

    def test_RQ3_a_refusing_adapter_is_still_exercised_and_fails(self, world, sessions, tmp_path):
        """Evidence level says *how* it was tested, not whether it worked.

        A connector DHealth drove and which refused the order has been exercised and has failed.
        Recording that as ``DECLARED`` would lose the fact that DHealth actually tried -- and would
        make a refusing connector indistinguishable from an untested one.
        """
        adapter = SyntheticLabAdapter()
        adapter.fail_next = "MAPPING"
        evidence = _drive(world, sessions, tmp_path, adapter, uuid.uuid4())
        scenario = order_receipt_scenario(evidence)

        assert not evidence.passed
        assert not scenario.passed
        assert scenario.evidence_level is EvidenceLevel.EXERCISED
        assert scenario.detail

    def test_RQ4_a_severed_delivery_path_fails_closed(self, world, sessions, tmp_path):
        """The control §41 asks for: if the runtime cannot hand the message over, nothing passes.

        The agent is never run at all, which is the strongest possible form of "the delivery path is
        broken". A driver that reported success here would be reporting on something other than the
        delivery path.
        """
        adapter = SyntheticLabAdapter()
        lab_order_id = uuid.uuid4()
        evidence = drive_order_receipt(
            sessions,
            installation_id=world["installation_id"],
            organization_id=world["organization_id"],
            lab_order_id=lab_order_id,
            run_cycle=lambda: None,
            adapter_has_order=adapter.has_order,
            locally_acknowledged=lambda: False,
        )
        assert not evidence.passed
        assert evidence.central_state == DeliveryState.PENDING.value

    def test_RQ5_the_order_notification_is_queued_the_way_the_hook_queues_it(
        self, world, sessions, tmp_path
    ):
        """One row, keyed on the order, idempotent -- not a hand-built fixture row.

        A qualification that inserted its own delivery would prove the agent can drain a queue, not
        that DHealth can fill one.
        """
        adapter = SyntheticLabAdapter()
        lab_order_id = uuid.uuid4()
        _drive(world, sessions, tmp_path, adapter, lab_order_id)
        with sessions() as session:
            rows = session.scalars(
                select(ConnectorDelivery).where(
                    ConnectorDelivery.source_event_id == qualification_event_id(lab_order_id)
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].message_type == "ORDER_NOTIFICATION"


class TestProductionQualification:
    """§104. The whole suite, and what it does and does not make true."""

    def test_RQ6_the_full_suite_now_reaches_production_qualification(
        self, world, sessions, tmp_path
    ):
        """The headline change, and the first time any connector in this codebase gets here.

        Before this ticket the synthetic laboratory ended at ``REQUIRED_CAPABILITY_UNEXERCISED``
        naming ``ORDER_RECEIPT``, because nothing could deliver an order. It now delivers one, and
        that is the *only* difference -- every other scenario behaves exactly as it did.
        """
        order_id = _prepared_order(world, sessions)
        adapter = SyntheticLabAdapter()
        receipt = order_receipt_scenario(_drive(world, sessions, tmp_path, adapter, order_id))
        assert receipt.passed

        harness = LabQualificationHarness(
            bridge=_bridge(world, sessions),
            subject=SyntheticConnectorSubject(connector_key="synthetic-lis"),
            organization_id=world["organization_id"],
            installation_id=world["installation_id"],
        )
        with sessions.begin() as session:
            run = harness.run(session, order_id=order_id, order_receipt=receipt)

        world["repository"].record_qualification(
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
                session, world["organization_id"], OrganizationType.LAB.value
            )
        assert verdict.qualified, verdict.detail
        assert verdict.gap is None
        assert verdict.unexercised_capabilities == ()

    def test_RQ7_without_a_runtime_the_same_suite_still_does_not_qualify(self, world, sessions):
        """The other half of the same claim, and the one that keeps it honest.

        Run the identical suite with no ``order_receipt`` -- no agent, no delivery -- and the result
        is the pre-ticket outcome: ``ORDER_RECEIPT`` recorded ``DECLARED`` and production
        qualification incomplete. So what changed the verdict was the delivery, not the bump.
        """
        order_id = _prepared_order(world, sessions)
        harness = LabQualificationHarness(
            bridge=_bridge(world, sessions),
            subject=SyntheticConnectorSubject(connector_key="synthetic-lis"),
            organization_id=world["organization_id"],
            installation_id=world["installation_id"],
        )
        with sessions.begin() as session:
            run = harness.run(session, order_id=order_id)

        world["repository"].record_qualification(
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
                session, world["organization_id"], OrganizationType.LAB.value
            )
        assert not verdict.qualified
        assert verdict.gap is QualificationGap.REQUIRED_CAPABILITY_UNEXERCISED
        assert Capability.ORDER_RECEIPT in verdict.unexercised_capabilities

    def test_RQ8_qualified_is_still_not_production_routable(self, world, sessions, tmp_path):
        """§6, §86, §107. The rule this ticket must not quietly relax.

        A qualified connector with a healthy agent is still refused ``READY_FOR_ROUTING``, because
        the thing on the far end is a synthetic adapter. Pinned as a test so a later ticket
        cannot lift the block by accident while editing something nearby.
        """
        from daher_adapter.exchange.facility_enrollment import InvalidOnboardingTransition

        order_id = _prepared_order(world, sessions)
        _drive(world, sessions, tmp_path, SyntheticLabAdapter(), order_id)

        with pytest.raises(InvalidOnboardingTransition) as error:
            FacilityEnrollmentRepository(sessions).set_onboarding_status(
                organization_id=world["organization_id"],
                onboarding_status="READY_FOR_ROUTING",
            )
        assert "vendor adapter" in str(error.value)


# ── helpers ──────────────────────────────────────────────────────────────────────────────────


def _bridge(world, sessions):
    from daher_adapter.connect.apply import ConnectBridge
    from daher_adapter.connect.contract import InMemoryReceiptStore
    from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
    from daher_adapter.exchange.results import (
        LabResultAmendmentRepository,
        LabResultReleaseRepository,
        LabResultRepository,
    )

    organizations = OrganizationRepository(sessions)
    return ConnectBridge(
        workflow=LabWorkflowRepository(sessions, organizations),
        results=LabResultRepository(sessions),
        releases=LabResultReleaseRepository(sessions),
        amendments=LabResultAmendmentRepository(sessions),
        receipts=InMemoryReceiptStore(),
        installation_organization_id=world["organization_id"],
        service_identity_id=world["identity_id"],
    )


def _prepared_order(world, sessions) -> uuid.UUID:
    """A real, SELECTED order at this laboratory.

    Real rather than a fabricated UUID because ``ConnectorDelivery.lab_order_id`` is a foreign key
    with ``ON DELETE RESTRICT``: a made-up id is a row PostgreSQL refuses outright, and SQLite
    accepts silently. That asymmetry is exactly what made 0028's length bug invisible, so fixtures
    here construct the real row.
    """
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    provider = make_provider(organizations, f"Clinique {uuid.uuid4().hex[:6]}", operational=True)
    patient = make_patient_reference(
        PatientReferenceRepository(sessions, organizations), provider_org_id=provider.id
    )
    practitioner = make_practitioner_reference(
        PractitionerReferenceRepository(sessions), provider_org_id=provider.id
    )
    # The code must be exactly "GLU": the harness's default observation reports that source code,
    # and a released result whose code matches no ordered item never completes the order -- which
    # then fails the amendment scenarios for a reason that has nothing to do with the connector.
    entry, _ = catalog.create_entry(code="GLU", display_name="Glycémie à jeun")
    created = LabOrderRepository(sessions, organizations, catalog).create(
        idempotency_key=f"runtime-qual-{uuid.uuid4()}",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    )
    order_id = created.order.id
    with sessions.begin() as session:
        from daher_adapter.exchange.models import LabOrder

        session.add(
            LabSelection(
                lab_order_id=order_id,
                lab_organization_id=world["organization_id"],
                selected_at=utc_now(),
                selected_via_patient_access_grant_id=None,
            )
        )
        session.get(LabOrder, order_id).status = LabOrderStatus.SELECTED.value
    return order_id
