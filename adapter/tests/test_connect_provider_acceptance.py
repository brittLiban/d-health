"""DH-CONNECT-AGENT-001 correction §20/§25 — the whole journey, through the real agent.

Every other test in this correction holds one piece still. This one holds nothing still:

    installation v1 submits ORDER-ABC
      -> DHealth creates the order and binds the correlation
      -> v1 is retired, v2 is registered for the same source system
      -> the laboratory releases while v2 is suspended
      -> the release commits, and the obligation persists
      -> no wrong-system delivery happens in the meantime
      -> v2 is restored
      -> the provider's own system receives the released result, exactly once
      -> and then the amendment, the same way

Driven through ``AgentRunner`` against the real runtime API over ``TestClient``. Nothing here calls
``handle_result`` or ``handle_amendment``: §41's rule is that an adapter called directly proves
nothing, and a journey assembled out of direct calls would prove even less. The laboratory half is
native, through the Exchange's own repositories, for the reason the qualification suite gives -- it
is not what is under test, and a second connector in the middle would give every failure two homes.

The negative half is the same journey with one fact changed: the successor registered for a
*different* source system. Epic's ``ORDER-ABC`` is not MedSys's ``ORDER-ABC``, so the released
report must not reach it -- and the obligation must stay owed rather than be quietly discharged by
whatever connector the facility happens to have.

**What this still does not prove:** the far end is ``SyntheticProviderAdapter``. No partner system,
no vendor format, no real EMR was contacted.
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
from exchange_support import make_lab, make_offering, make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import (
    OrderSubmission,
    ScopedExternalId,
    SourcePractitionerRef,
    fingerprint,
)
from daher_adapter.connect.correlation import CorrelationNamespace, CorrelationRepository
from daher_adapter.connect.provider_qualification import drive_order_submission
from daher_adapter.connect.qualification import qualification_verdict_in_session
from daher_adapter.connect.reconciliation import unresolved_obligations
from daher_adapter.connect.repository import ConnectorInstallationRepository
from daher_adapter.connect.runtime_api import build_connect_runtime_router
from daher_adapter.connect.runtime_models import (
    ConnectorDelivery,
    ConnectorDeliveryObligation,
    DeliveryState,
    ObligationState,
)
from daher_adapter.connect_agent.adapter import AdapterEvent, SyntheticProviderAdapter
from daher_adapter.connect_agent.client import ConnectRuntimeClient, TokenSource
from daher_adapter.connect_agent.runner import AgentRunner
from daher_adapter.connect_agent.store import AgentStore
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    LabOrderItem,
    LabResultType,
    OrganizationType,
    ParticipationMode,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    LabResultAmendmentRepository,
    LabResultReleaseRepository,
    LabResultRepository,
    _effective_observation_rows,
)
from daher_adapter.exchange.selection import LabSelectionRepository

RUNTIME_AUDIENCE = "dhealth-connect-runtime"
MEDSYS = "medsys"
EPIC = "epic"
EXTERNAL_ORDER = "ORDER-ABC"
SOURCE_EVENT_ID = "medsys-order-abc-created"


@pytest.fixture
def world(sessions: sessionmaker[Session]):
    """A CONNECT provider with no connector yet, and a native laboratory able to finish work.

    No installation is registered here on purpose: this file's subject is what happens *across*
    installations, so each test registers the ones its own journey needs.
    """
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)

    provider = make_provider(organizations, "Clinique de la Paix", operational=False)
    FacilityEnrollmentRepository(sessions).create(
        organization_id=provider.id, participation_mode=ParticipationMode.CONNECT
    )
    lab = make_lab(organizations, "Laboratoire Central", operational=True)
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

    private_key, public_key = generate_keypair()
    app = FastAPI()
    app.include_router(
        build_connect_runtime_router(
            session_factory=sessions,
            runtime_token_validator=build_validator(public_key, audience=RUNTIME_AUDIENCE),
            platform_admin_role=PLATFORM_ADMIN_ROLE,
        )
    )
    return {
        "sessions": sessions,
        "organizations": organizations,
        "installations": ConnectorInstallationRepository(sessions),
        "provider_id": provider.id,
        "lab_id": lab.id,
        "identity_id": identity.id,
        "price_id": price.id,
        "app": app,
        "private_key": private_key,
    }


def _register(world, *, version: str, source_system: str, client_id: str):
    """Register one installation and mint the credential its agent will present.

    Each installation gets its **own** service client id. Not a detail: ``service_client_id`` is
    globally unique across retired rows too, so a successor reusing its predecessor's credential is
    impossible by construction -- which is the database saying that a replacement is a new principal
    with its own qualification to earn, not a continuation of the old one (§5).
    """
    installations = world["installations"]
    record = installations.register(
        organization_id=world["provider_id"],
        connector_key="synthetic-emr",
        connector_version=version,
        source_system=source_system,
        interface_type="FHIR",
        configuration_fingerprint=fingerprint({"endpoint": f"https://{source_system}.invalid"}),
        mapping_fingerprint=fingerprint({"mapping_revision": [[source_system, "GLU"]]}),
        actor_identity_id=world["identity_id"],
    )
    installations.set_service_identity(
        installation_id=record.id,
        service_client_id=client_id,
        actor_identity_id=world["identity_id"],
    )
    token = make_token(
        world["private_key"],
        subject=f"service-account-{client_id}",
        audience=RUNTIME_AUDIENCE,
        roles=[],
        extra_claims={"azp": client_id},
    )
    return record, token


def _agent(world, tmp_path, *, adapter, installation_id, token, source_system, name):
    store = AgentStore(tmp_path / f"{name}.sqlite")
    client = ConnectRuntimeClient(
        base_url="http://connect.invalid",
        tokens=TokenSource(token),
        http_client=TestClient(world["app"]),
    )
    runner = AgentRunner(
        store=store,
        client=client,
        adapter=adapter,
        organization_id=world["provider_id"],
        installation_id=installation_id,
        source_system=source_system,
    )
    return store, runner


def _queue_submission(adapter: SyntheticProviderAdapter, organization_id: uuid.UUID) -> None:
    """What the partner's EMR did: a clinician placed an order in it."""
    adapter.queued_events.append(
        AdapterEvent(
            source_event_id=SOURCE_EVENT_ID,
            occurred_at=datetime.now(UTC) - timedelta(minutes=5),
            payload=OrderSubmission(
                patient=ScopedExternalId(
                    source_organization_id=organization_id,
                    source_system=MEDSYS,
                    source_identity_system="urn:medsys:mrn",
                    source_identity_value="MRN-42",
                ),
                practitioner=SourcePractitionerRef(
                    source_organization_id=organization_id,
                    source_system=MEDSYS,
                    source_identity_system="urn:medsys:staff",
                    source_identity_value="DR-7",
                ),
                external_order_id=EXTERNAL_ORDER,
                source_test_codes=("GLU",),
            ),
        )
    )


def _submit_through_v1(world, tmp_path) -> tuple[uuid.UUID, object]:
    """v1's whole life: register, submit ORDER-ABC through the agent, bind the correlation.

    The agent is closed afterwards and never used again -- which is the point. Everything the rest
    of the journey relies on has to survive without it.
    """
    record, token = _register(world, version="1.0", source_system=MEDSYS, client_id="agent-v1")
    adapter = SyntheticProviderAdapter()
    store, runner = _agent(
        world,
        tmp_path,
        adapter=adapter,
        installation_id=record.id,
        token=token,
        source_system=MEDSYS,
        name="v1",
    )
    try:
        submission = drive_order_submission(
            world["sessions"],
            installation_id=record.id,
            namespace=CorrelationNamespace(
                organization_id=world["provider_id"], source_system=MEDSYS
            ),
            external_order_id=EXTERNAL_ORDER,
            emit_submission=lambda: _queue_submission(adapter, world["provider_id"]),
            run_cycle=runner.run_once,
        )
    finally:
        store.close()
    assert submission.lab_order_id is not None, submission.detail
    return submission.lab_order_id, record


def _release(world, lab_order_id: uuid.UUID) -> None:
    """The laboratory's own work, native, through the Exchange's own repositories."""
    sessions = world["sessions"]
    organizations = world["organizations"]
    workflow = LabWorkflowRepository(sessions, organizations)
    lab_id = world["lab_id"]
    actor = world["identity_id"]

    LabSelectionRepository(sessions, organizations).select(
        lab_order_id=lab_order_id,
        lab_organization_id=lab_id,
        expected_price_ids=frozenset({world["price_id"]}),
    )
    workflow.accept(order_id=lab_order_id, lab_organization_id=lab_id)
    workflow.record_collection(order_id=lab_order_id, lab_organization_id=lab_id)
    workflow.start_processing(order_id=lab_order_id, lab_organization_id=lab_id)

    with sessions() as session:
        item_id = session.scalar(
            select(LabOrderItem.id).where(LabOrderItem.lab_order_id == lab_order_id)
        )
    results = LabResultRepository(sessions)
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
    LabResultReleaseRepository(sessions).release(
        order_id=lab_order_id, lab_organization_id=lab_id, released_by_identity_id=actor
    )


def _amend(world, lab_order_id: uuid.UUID) -> None:
    sessions = world["sessions"]
    amendments = LabResultAmendmentRepository(sessions)
    lab_id = world["lab_id"]
    actor = world["identity_id"]

    amendment = amendments.start_amendment(
        order_id=lab_order_id, lab_organization_id=lab_id, started_by_identity_id=actor
    )
    with sessions() as session:
        item_id = session.scalar(
            select(LabOrderItem.id).where(LabOrderItem.lab_order_id == lab_order_id)
        )
        predecessor_id = _effective_observation_rows(session, lab_order_id)[0][1]
    amendments.correct_observation(
        amendment_id=amendment.id,
        order_id=lab_order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        observation_id=predecessor_id,
        result_type=LabResultType.NUMERIC,
        source_code="GLU",
        display_label_snapshot="Glycémie à jeun",
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


def _notifications(sessions) -> list[ConnectorDelivery]:
    with sessions() as session:
        return list(
            session.scalars(
                select(ConnectorDelivery).where(
                    ConnectorDelivery.message_type.in_(
                        ("RESULT_NOTIFICATION", "AMENDMENT_NOTIFICATION")
                    )
                )
            )
        )


def _obligations(sessions) -> list[ConnectorDeliveryObligation]:
    with sessions() as session:
        return list(
            session.scalars(
                select(ConnectorDeliveryObligation).order_by(ConnectorDeliveryObligation.created_at)
            )
        )


def test_AC1_a_result_ordered_by_a_replaced_connector_still_reaches_the_provider(
    world, sessions, tmp_path
):
    """§25, end to end. The connector that asked is gone and the report arrives anyway.

    Read the sequence as an operator would live it: an EMR integration is upgraded, the old
    installation is retired, the new one is registered and then suspended for an afternoon while
    somebody works on it -- and in the middle of that afternoon the laboratory releases a result for
    an order placed weeks earlier. Before this correction that result was lost twice over: the
    correlation could no longer be found, and the suspension meant nothing was queued.
    """
    lab_order_id, v1 = _submit_through_v1(world, tmp_path)

    # The upgrade. v1 is retired; v2 is registered for the same source system, with its own
    # credential and its own qualification to earn.
    world["installations"].retire(installation_id=v1.id, actor_identity_id=world["identity_id"])
    v2, v2_token = _register(world, version="2.0", source_system=MEDSYS, client_id="agent-v2")
    with sessions() as session:
        verdict = qualification_verdict_in_session(
            session, world["provider_id"], OrganizationType.PROVIDER.value
        )
    assert not verdict.qualified, (
        "a successor inherits the namespace's meaning, never its predecessor's qualification"
    )

    # ...and is suspended before the laboratory releases.
    world["installations"].suspend(installation_id=v2.id, actor_identity_id=world["identity_id"])

    adapter = SyntheticProviderAdapter()
    store, runner = _agent(
        world,
        tmp_path,
        adapter=adapter,
        installation_id=v2.id,
        token=v2_token,
        source_system=MEDSYS,
        name="v2",
    )
    try:
        _release(world, lab_order_id)

        # The release committed, and the obligation exists -- against a connector that cannot
        # currently carry it. The external identifier is still the one v1 reported.
        obligations = _obligations(sessions)
        assert len(obligations) == 1
        assert obligations[0].state == ObligationState.QUEUED.value
        assert obligations[0].source_system == MEDSYS
        notifications = _notifications(sessions)
        assert len(notifications) == 1
        assert notifications[0].connector_installation_id == v2.id
        assert notifications[0].state == DeliveryState.PENDING.value

        # A suspended agent may run: it simply cannot do anything. The cycle records the refusal
        # rather than raising, and the partner's system stays empty.
        suspended_cycle = runner.run_once()
        assert suspended_cycle.errors, "a suspended installation must be refused, visibly"
        assert suspended_cycle.handled == 0
        assert adapter.handle_result_calls == 0, "nothing reaches a partner during a suspension"

        # Restored. No reconciliation needed and none run: the delivery was already queued, and the
        # restoration is the only event required -- nothing had to *notice* it.
        world["installations"].restore(
            installation_id=v2.id, actor_identity_id=world["identity_id"]
        )
        runner.run_once()
        runner.run_once()

        assert adapter.handle_result_calls == 1, "exactly once -- not zero, and not twice"
        report = adapter.report_for(EXTERNAL_ORDER, 1)
        assert report is not None
        assert report.external_order_id == EXTERNAL_ORDER, (
            "the identifier v1 reported is the one v2 delivers against"
        )
        assert [obs.source_code for obs in report.observations] == ["GLU"]
        assert report.observations[0].numeric_value == Decimal("5.4")
        assert not report.is_amendment

        # And the correction, which is the message whose loss matters most.
        _amend(world, lab_order_id)
        runner.run_once()
        runner.run_once()

        assert adapter.handle_amendment_calls == 1
        assert adapter.handle_result_calls == 1, "an amendment is not a second released result"
        correction = adapter.report_for(EXTERNAL_ORDER, 2)
        assert correction is not None
        assert correction.is_amendment
        assert correction.corrects_release_version == 1
        assert correction.observations[0].numeric_value == Decimal("7.1")
        assert adapter.report_for(EXTERNAL_ORDER, 1) is not None, (
            "the superseded version stays readable in the receiving system"
        )
    finally:
        store.close()

    with sessions() as session:
        assert unresolved_obligations(session) == []
    states = {obligation.state for obligation in _obligations(sessions)}
    assert states == {ObligationState.QUEUED.value}
    assert len(_notifications(sessions)) == 2


def test_AC2_a_successor_for_a_different_source_system_receives_nothing(world, sessions, tmp_path):
    """§20's tail and §25's: EMR A's correlation does not migrate to EMR B.

    Same journey, one fact changed -- the successor is registered for Epic rather than MedSys. Epic
    has never heard of ``ORDER-ABC``; the string it would file the report against belongs to a
    system that no longer exists here. The facility has exactly one live connector and it is still
    the wrong one, which is precisely the case a "the facility only has one, so it must be right"
    shortcut would get wrong.

    So the report goes nowhere and says so. An unresolved obligation an operator has to look at is
    the correct outcome; a report filed into Epic against another system's identifier is the
    incorrect one, and it is the kind that is discovered by a clinician.
    """
    lab_order_id, v1 = _submit_through_v1(world, tmp_path)
    world["installations"].retire(installation_id=v1.id, actor_identity_id=world["identity_id"])
    successor, token = _register(world, version="1.0", source_system=EPIC, client_id="agent-epic")

    adapter = SyntheticProviderAdapter()
    store, runner = _agent(
        world,
        tmp_path,
        adapter=adapter,
        installation_id=successor.id,
        token=token,
        source_system=EPIC,
        name="epic",
    )
    try:
        _release(world, lab_order_id)

        obligations = _obligations(sessions)
        assert len(obligations) == 1
        assert obligations[0].state == ObligationState.PENDING.value
        assert obligations[0].failure_category == "REMOTE_UNAVAILABLE"
        assert obligations[0].source_system == MEDSYS, (
            "the obligation is owed to the system that ordered, not to the one now connected"
        )
        assert obligations[0].resolved_at is None
        assert _notifications(sessions) == []

        # The Epic agent is healthy, registered and running. It still receives nothing.
        for _ in range(3):
            runner.run_once()
        assert adapter.handle_result_calls == 0
        assert adapter.handle_amendment_calls == 0
        assert adapter.report_for(EXTERNAL_ORDER, 1) is None
    finally:
        store.close()

    with sessions() as session:
        assert len(unresolved_obligations(session)) == 1
        # And the correlation itself is untouched: it still resolves under MedSys, and not at all
        # under Epic. A migration would have had to rewrite it, and nothing does.
        correlations = CorrelationRepository()
        assert (
            correlations.resolve_order(
                session,
                namespace=CorrelationNamespace(
                    organization_id=world["provider_id"], source_system=MEDSYS
                ),
                external_order_id=EXTERNAL_ORDER,
            )
            == lab_order_id
        )


def test_AC3_a_successors_first_heartbeat_discharges_what_its_predecessor_left_owed(
    world, sessions, tmp_path
):
    """§9's preferred outcome, with nothing to run and nobody to remember to run it.

    The release happens when the facility has no connector at all: v1 is retired and v2 does not
    exist yet, so there is nothing to queue against and the obligation is simply owed. What changes
    that is an eligible agent appearing -- and that is an event DHealth already hears, because the
    agent announces itself. So the recovery rides on the heartbeat rather than on a scheduler, a
    worker or an operator remembering a command: §14, and the reason
    ``reconcile_delivery_obligations`` is a method rather than a project.

    No reconciliation is called anywhere in this test. The agent simply runs.
    """
    lab_order_id, v1 = _submit_through_v1(world, tmp_path)
    world["installations"].retire(installation_id=v1.id, actor_identity_id=world["identity_id"])
    _release(world, lab_order_id)

    owed = _obligations(sessions)
    assert len(owed) == 1
    assert owed[0].state == ObligationState.PENDING.value
    assert _notifications(sessions) == [], "nothing could carry it, so nothing was queued"

    successor, token = _register(world, version="2.0", source_system=MEDSYS, client_id="agent-v2")
    adapter = SyntheticProviderAdapter()
    store, runner = _agent(
        world,
        tmp_path,
        adapter=adapter,
        installation_id=successor.id,
        token=token,
        source_system=MEDSYS,
        name="v2-heartbeat",
    )
    try:
        runner.run_once()
    finally:
        store.close()

    assert adapter.handle_result_calls == 1
    report = adapter.report_for(EXTERNAL_ORDER, 1)
    assert report is not None
    assert report.external_order_id == EXTERNAL_ORDER
    discharged = _obligations(sessions)[0]
    assert discharged.state == ObligationState.QUEUED.value
    assert discharged.resolved_at is not None
    deliveries = _notifications(sessions)
    assert len(deliveries) == 1
    assert deliveries[0].connector_installation_id == successor.id
