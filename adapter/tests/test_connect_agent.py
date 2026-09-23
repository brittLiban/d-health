"""DH-CONNECT-AGENT-001 §90–§93 — the agent, driven against the real server in one process.

Starlette's ``TestClient`` -- an ``httpx.Client`` over a synchronous ASGI transport -- is what
makes this honest. The real ``ConnectRuntimeClient`` talks to the real
``build_connect_runtime_router`` over the real request/response cycle, with no port, no stub and no
mock of either side. A failing test here means one of the two is genuinely wrong, which is exactly
what a stubbed client cannot tell you: a stub drifts from the thing it stands in for and nobody
notices until production.

    R   round trip. An order queued centrally reaches the adapter, and the delivery is confirmed.
    C   crash windows. Every point the process can die mid-sequence, and what is owed afterwards.
    S   store and CLI. Durability, schema advance, and the commands that deliberately do not exist.

Nothing here touches a partner system, a network, FHIR, HL7 or a vendor format. The adapter under
test is ``SyntheticLabAdapter``, and §82's rule holds throughout: it is a test double for the
delivery path, never a claim that any real integration exists.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from exchange_auth_support import (
    ISSUER,
    PLATFORM_ADMIN_ROLE,
    build_validator,
    generate_keypair,
    make_token,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    BusinessAcceptance,
    Direction,
    Envelope,
    FailureCategory,
    HealthState,
    OrderNotification,
    ScopedExternalId,
    SourcePractitionerRef,
    fingerprint,
)
from daher_adapter.connect.delivery import DeliveryRepository
from daher_adapter.connect.models import ConnectorInstallation
from daher_adapter.connect.repository import ConnectorInstallationRepository
from daher_adapter.connect.runtime_api import build_connect_runtime_router
from daher_adapter.connect.runtime_health import runtime_health_in_session
from daher_adapter.connect.runtime_models import ConnectorDelivery, DeliveryState
from daher_adapter.connect_agent.adapter import (
    AdapterEvent,
    AdapterOrder,
    AdapterRefused,
    AdapterUnavailable,
    SyntheticLabAdapter,
)
from daher_adapter.connect_agent.cli import COMMANDS, build_parser
from daher_adapter.connect_agent.client import ConnectRuntimeClient, TokenSource
from daher_adapter.connect_agent.config import AgentConfigurationError, AgentSettings
from daher_adapter.connect_agent.runner import AgentRunner
from daher_adapter.connect_agent.store import AgentStore, AgentStoreTooNew, InboxState
from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    Organization,
    OrganizationStatus,
    OrganizationType,
    ParticipationMode,
)

RUNTIME_AUDIENCE = "dhealth-connect-runtime"
AGENT_CLIENT = "connect-agent-lab-central"
CONFIG_FP = fingerprint({"endpoint": "https://lis.invalid/fhir"})
MAPPING_FP = fingerprint({"mapping_revision": [["synthetic-lis", "GLU", "cat-1", "GLU"]]})


# ── world ────────────────────────────────────────────────────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        # Real instant, not a fixed date -- see the identical note in test_connect_runtime.py.
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> datetime:
        self.now = self.now + delta
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def keypair():
    return generate_keypair()


@pytest.fixture
def installation(sessions: sessionmaker[Session]) -> ConnectorInstallation:
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
        mapping_fingerprint=MAPPING_FP,
        actor_identity_id=identity.id,
    )
    repository.set_service_identity(
        installation_id=record.id, service_client_id=AGENT_CLIENT, actor_identity_id=identity.id
    )
    with sessions() as session:
        row = session.get(ConnectorInstallation, record.id)
        session.expunge(row)
    return row


@pytest.fixture
def server(sessions, keypair, clock) -> FastAPI:
    _, public_key = keypair
    app = FastAPI()
    app.include_router(
        build_connect_runtime_router(
            session_factory=sessions,
            runtime_token_validator=build_validator(public_key, audience=RUNTIME_AUDIENCE),
            platform_admin_role=PLATFORM_ADMIN_ROLE,
            now=clock,
        )
    )
    return app


@pytest.fixture
def adapter() -> SyntheticLabAdapter:
    return SyntheticLabAdapter()


def _client(server: FastAPI, keypair) -> ConnectRuntimeClient:
    """The real client, over the real app, in this process. No port, no stub.

    ``TestClient`` is an ``httpx.Client`` carrying a *synchronous* ASGI transport, which is what the
    agent needs: ``httpx.ASGITransport`` implements only ``handle_async_request``, so a sync client
    cannot drive it at all.
    """
    private_key, _ = keypair
    token = make_token(
        private_key,
        subject=f"service-account-{AGENT_CLIENT}",
        audience=RUNTIME_AUDIENCE,
        roles=[],
        extra_claims={"azp": AGENT_CLIENT},
    )
    return ConnectRuntimeClient(
        base_url="http://connect.invalid",
        tokens=TokenSource(token),
        http_client=TestClient(server),
    )


@pytest.fixture
def store(tmp_path) -> AgentStore:
    made = AgentStore(tmp_path / "agent.sqlite")
    yield made
    made.close()


def _runner(store, server, keypair, adapter, installation) -> AgentRunner:
    return AgentRunner(
        store=store,
        client=_client(server, keypair),
        adapter=adapter,
        organization_id=installation.organization_id,
        installation_id=installation.id,
        source_system="synthetic-lis",
    )


def _queue_order(sessions, installation, *, event="order-1", when=None) -> uuid.UUID:
    """Put one order notification in the central outbox, the way the Exchange hook will."""
    moment = when or datetime(2026, 9, 23, 7, 55, tzinfo=UTC)
    lab_order_id = uuid.uuid4()
    envelope = Envelope(
        contract_version=CONTRACT_VERSION,
        message_id=uuid.uuid4(),
        connector_installation_id=installation.id,
        organization_id=installation.organization_id,
        source_system="dhealth",
        source_event_id=event,
        source_occurred_at=moment,
        sent_at=moment,
        lab_order_id=lab_order_id,
        payload=OrderNotification(
            patient=ScopedExternalId(
                source_organization_id=installation.organization_id,
                source_system="emr",
                source_identity_system="urn:emr:mrn",
                source_identity_value="P-1",
            ),
            practitioner=SourcePractitionerRef(
                source_organization_id=installation.organization_id,
                source_system="emr",
                source_identity_system="urn:emr:staff",
                source_identity_value="D-1",
            ),
            catalog_codes=("GLU",),
        ),
    )
    with sessions.begin() as session:
        DeliveryRepository().enqueue(
            session,
            installation_id=installation.id,
            envelope=envelope,
            expected_direction=Direction.DHEALTH_TO_CONNECTOR,
            now=moment,
        )
    return lab_order_id


# ── R: the round trip ────────────────────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_R1_an_order_queued_centrally_reaches_the_adapter(
        self, sessions, server, keypair, adapter, installation, store
    ):
        """§41's whole point: the order travels the **real** delivery path.

        Queue centrally, run one cycle, and the synthetic laboratory has the order. Nothing here
        calls ``adapter.handle_order`` directly -- the assertion is that the runtime did.
        """
        lab_order_id = _queue_order(sessions, installation)
        report = _runner(store, server, keypair, adapter, installation).run_once()

        assert report.claimed == 1
        assert report.handled == 1
        assert adapter.has_order(lab_order_id)
        with sessions() as session:
            delivery = session.scalar(select(ConnectorDelivery))
            assert delivery.state == DeliveryState.DELIVERED.value

    def test_R2_the_local_inbox_is_written_before_the_acknowledgement(
        self, sessions, server, keypair, adapter, installation, store
    ):
        """The agent's own durable record survives the cycle and says the work is finished."""
        _queue_order(sessions, installation)
        _runner(store, server, keypair, adapter, installation).run_once()
        rows = store._connection.execute("SELECT state FROM inbox").fetchall()  # noqa: SLF001
        assert [row["state"] for row in rows] == [InboxState.ACKNOWLEDGED]

    def test_R3_a_redelivered_order_does_not_reach_the_adapter_twice(
        self, sessions, server, keypair, adapter, installation, store, clock
    ):
        """At-least-once plus a lost ACK means redelivery is normal traffic.

        For a laboratory a second delivery means a second specimen request for a patient already
        drawn, so the local receipt is what stands between an ordinary retry and a real clinical
        error. Simulated by returning the delivery to the queue and running again.

        **Asserted on the call count, not on the list of accepted orders.** ``SyntheticLabAdapter``
        is itself idempotent, so ``received_orders`` stays at one whether or not the agent's replay
        check works -- this test passed with that check deleted until NC-RUNTIME-4 said so. A real
        partner adapter may not be idempotent, and the runtime must not depend on it being so.
        """
        lab_order_id = _queue_order(sessions, installation)
        runner = _runner(store, server, keypair, adapter, installation)
        runner.run_once()
        assert adapter.handle_order_calls == 1

        # DHealth forgets the confirmation: the row goes back to PENDING, exactly as an expired
        # lease would leave it.
        with sessions.begin() as session:
            delivery = session.scalar(select(ConnectorDelivery))
            delivery.state = DeliveryState.PENDING.value
            delivery.delivered_at = None
            delivery.claimed_by_runtime_instance_id = None
            delivery.claim_expires_at = None

        clock.advance(timedelta(seconds=1))
        report = runner.run_once()
        assert report.claimed == 1
        assert adapter.handle_order_calls == 1, "the adapter must not be called a second time"
        assert len(adapter.received_orders) == 1
        assert adapter.has_order(lab_order_id)

    def test_R4_an_adapter_outage_leaves_the_message_retryable(
        self, sessions, server, keypair, adapter, installation, store, clock
    ):
        """An unreachable LIS is not a reason to discard a clinical order.

        Reported as REMOTE_UNAVAILABLE, so the server backs off and tries again rather than
        dead-lettering on the first blip.
        """
        _queue_order(sessions, installation)
        adapter.unavailable = True
        runner = _runner(store, server, keypair, adapter, installation)
        report = runner.run_once()

        assert report.failed == 1
        with sessions() as session:
            delivery = session.scalar(select(ConnectorDelivery))
        assert delivery.state == DeliveryState.PENDING.value
        assert delivery.last_failure_category == FailureCategory.REMOTE_UNAVAILABLE.value

    def test_R5_a_refused_order_dead_letters_rather_than_retrying_forever(
        self, sessions, server, keypair, adapter, installation, store
    ):
        """A mapping error will be the same mapping error in fifteen seconds. §56: the row is
        unresolved and visible, not deleted and not cancelled."""
        _queue_order(sessions, installation)
        adapter.fail_next = FailureCategory.MAPPING.value
        _runner(store, server, keypair, adapter, installation).run_once()

        with sessions() as session:
            delivery = session.scalar(select(ConnectorDelivery))
        assert delivery.state == DeliveryState.DEAD_LETTER.value
        assert delivery.dead_lettered_at is not None
        assert delivery.last_failure_category == FailureCategory.MAPPING.value

    def test_R6_an_adapter_event_travels_back_to_dhealth(
        self, sessions, server, keypair, adapter, installation, store
    ):
        """The other direction. The adapter reports an acceptance; DHealth ends up holding it."""
        lab_order_id = _queue_order(sessions, installation)
        adapter.queued_events.append(
            AdapterEvent(
                source_event_id="lis-evt-1",
                payload=BusinessAcceptance(external_order_id="ACC-1"),
                occurred_at=datetime(2026, 9, 23, 8, 1, tzinfo=UTC),
                lab_order_id=lab_order_id,
            )
        )
        report = _runner(store, server, keypair, adapter, installation).run_once()

        assert report.submitted == 1
        with sessions() as session:
            inbound = session.scalars(
                select(ConnectorDelivery).where(
                    ConnectorDelivery.direction == Direction.CONNECTOR_TO_DHEALTH.value
                )
            ).all()
        assert len(inbound) == 1
        assert inbound[0].source_event_id == "lis-evt-1"

    def test_R7_the_same_adapter_event_twice_is_submitted_once(
        self, sessions, server, keypair, adapter, installation, store
    ):
        """A poller re-reading its window reports the same event again. That must be free."""
        lab_order_id = _queue_order(sessions, installation)
        event = AdapterEvent(
            source_event_id="lis-evt-1",
            payload=BusinessAcceptance(external_order_id="ACC-1"),
            occurred_at=datetime(2026, 9, 23, 8, 1, tzinfo=UTC),
            lab_order_id=lab_order_id,
        )
        runner = _runner(store, server, keypair, adapter, installation)
        adapter.queued_events.append(event)
        runner.run_once()
        adapter.queued_events.append(event)
        runner.run_once()

        with sessions() as session:
            inbound = session.scalars(
                select(ConnectorDelivery).where(
                    ConnectorDelivery.direction == Direction.CONNECTOR_TO_DHEALTH.value
                )
            ).all()
        assert len(inbound) == 1

    def test_R8_a_heartbeat_makes_the_facility_operationally_healthy(
        self, sessions, server, keypair, adapter, installation, store, clock
    ):
        """The agent's own cycle is what turns ``connector_operational`` true -- no gesture, no
        column, no button.

        ``now=clock.now`` because the server stamped ``last_seen_at`` from the same frozen clock;
        reading health against the wall clock would measure the age of the test run rather than the
        age of the heartbeat.
        """
        _runner(store, server, keypair, adapter, installation).run_once()
        with sessions() as session:
            health = runtime_health_in_session(session, installation, now=clock.now)
        assert health.state is HealthState.HEALTHY
        assert health.is_operational

    def test_R9_an_unreachable_adapter_degrades_the_facility(
        self, sessions, server, keypair, adapter, installation, store, clock
    ):
        """§63. The agent is alive and says it cannot reach its LIS; that is not routable.

        The distinction this pins is DEGRADED rather than UNAVAILABLE: the agent *is* answering, so
        reporting silence would point an operator at the wrong machine.
        """
        adapter.unavailable = True
        _runner(store, server, keypair, adapter, installation).run_once()
        with sessions() as session:
            health = runtime_health_in_session(session, installation, now=clock.now)
        assert health.state is HealthState.DEGRADED
        assert not health.is_operational


# ── C: crash windows ─────────────────────────────────────────────────────────────────────────


class TestCrashWindows:
    """§74–§76. Every point the process can die, and what is owed when it comes back."""

    def test_C1_a_crash_between_the_adapter_and_the_acknowledgement_is_recovered(
        self, sessions, server, keypair, adapter, installation, store
    ):
        """The window §75 is about, driven rather than reasoned about.

        The adapter has the order; DHealth has not been told. On restart the agent owes an
        acknowledgement, sends it, and does **not** call the adapter again.
        """
        lab_order_id = _queue_order(sessions, installation)

        from daher_adapter.connect_agent.client import RuntimeUnreachable

        runner = _runner(store, server, keypair, adapter, installation)
        # Only the acknowledgement is replaced, so the claim, the persist and the adapter call all
        # happen for real against the real server. Injecting the fault anywhere coarser would prove
        # something about the fixture rather than about the recovery.
        original = runner._client.acknowledge  # noqa: SLF001

        def _explode(**_kwargs):
            raise RuntimeUnreachable("simulated crash before the acknowledgement")

        runner._client.acknowledge = _explode  # noqa: SLF001
        runner.run_once()

        assert adapter.has_order(lab_order_id)
        owed = store.unacknowledged()
        assert len(owed) == 1, "the agent must know it still owes an acknowledgement"

        # Restart: the acknowledgement works again.
        runner._client.acknowledge = original  # noqa: SLF001
        report = runner.run_once()
        assert report.acknowledged_backlog == 1
        assert store.unacknowledged() == []
        assert adapter.handle_order_calls == 1, "the adapter was not called a second time"
        with sessions() as session:
            assert session.scalar(select(ConnectorDelivery)).state == DeliveryState.DELIVERED.value

    def test_C2_a_crash_before_the_adapter_leaves_the_message_pending_locally(
        self, sessions, server, keypair, adapter, installation, store
    ):
        """Persisted, not yet handled. The next cycle retries the adapter and nothing is lost."""
        _queue_order(sessions, installation)
        runner = _runner(store, server, keypair, adapter, installation)
        adapter.unavailable = True
        runner.run_once()
        assert adapter.received_orders == []

        adapter.unavailable = False
        # The failed row is FAILED locally and PENDING centrally; the central redelivery is what
        # brings it back, which is the design: DHealth owns the retry schedule, not the agent.
        with sessions.begin() as session:
            delivery = session.scalar(select(ConnectorDelivery))
            delivery.next_attempt_at = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)
        runner.run_once()
        assert len(adapter.received_orders) == 1

    def test_C3_a_restart_keeps_the_same_runtime_instance_id(self, tmp_path):
        """§74: an agent restarting is the same runtime, not a new deployment.

        Minted into the store rather than configured, so two agents cannot be handed the same value
        and start stealing each other's leases.
        """
        first = AgentStore(tmp_path / "agent.sqlite")
        minted = first.runtime_instance_id()
        first.close()
        second = AgentStore(tmp_path / "agent.sqlite")
        assert second.runtime_instance_id() == minted
        second.close()

    def test_C4_undelivered_outbound_work_survives_a_restart(
        self, sessions, server, keypair, installation, tmp_path
    ):
        """§51. Work the agent holds and has not yet handed over is still there after a restart.

        Driven through the outbound path, because that is where the agent genuinely *owns* a message
        nobody else has a copy of: the partner's system produced it, DHealth has not received
        it, and if this store lost it there would be no second copy anywhere. The submission fails
        the way a network outage fails -- retryably -- so the row stays ``PENDING`` rather than
        being stopped.
        """
        from daher_adapter.connect_agent.client import RuntimeUnreachable

        lab_order_id = _queue_order(sessions, installation)
        adapter = SyntheticLabAdapter()
        adapter.queued_events.append(
            AdapterEvent(
                source_event_id="lis-evt-1",
                payload=BusinessAcceptance(external_order_id="ACC-1"),
                occurred_at=datetime.now(UTC),
                lab_order_id=lab_order_id,
            )
        )
        store = AgentStore(tmp_path / "agent.sqlite")
        runner = _runner(store, server, keypair, adapter, installation)

        def _unreachable(_envelope):
            raise RuntimeUnreachable("simulated outage while submitting")

        runner._client.submit = _unreachable  # noqa: SLF001
        runner.run_once()

        assert len(store.pending_outbound()) == 1
        assert store.queue_depth() >= 1
        store.close()

        reopened = AgentStore(tmp_path / "agent.sqlite")
        surviving = reopened.pending_outbound()
        assert len(surviving) == 1
        assert surviving[0].source_event_id == "lis-evt-1"
        # Still PENDING, not FAILED: an outage is retryable, and stopping the resubmission would
        # strand a clinical event the partner already produced.
        assert surviving[0].state == "PENDING"
        reopened.close()


# ── S: store, configuration, CLI ─────────────────────────────────────────────────────────────


class TestStoreAndCli:
    def test_S1_the_schema_advances_to_the_current_migration_count(self, tmp_path):
        from daher_adapter.connect_agent.store import MIGRATIONS

        store = AgentStore(tmp_path / "agent.sqlite")
        assert store.schema_version == len(MIGRATIONS)
        store.close()

    def test_S2_reopening_does_not_reapply_migrations(self, tmp_path):
        """Idempotent startup. An agent is upgraded by replacing a binary; this runs every boot."""
        AgentStore(tmp_path / "agent.sqlite").close()
        store = AgentStore(tmp_path / "agent.sqlite")
        from daher_adapter.connect_agent.store import MIGRATIONS

        assert store.schema_version == len(MIGRATIONS)
        store.close()

    def test_S3_a_store_from_a_newer_agent_is_refused(self, tmp_path):
        """§51: refuse rather than rewrite. The rows are undelivered clinical messages, and an
        older build silently fitting them to an older shape is how one gets lost."""
        store = AgentStore(tmp_path / "agent.sqlite")
        store._connection.execute("UPDATE schema_version SET version = 99")  # noqa: SLF001
        store.close()
        with pytest.raises(AgentStoreTooNew):
            AgentStore(tmp_path / "agent.sqlite")

    def test_S4_the_cli_registers_no_destructive_command(self):
        """Inspects the parser, not the docstring. The store holds clinical messages that exist
        nowhere else on the partner's premises, and a ``purge`` would be reached for at exactly the
        moment a stuck queue makes somebody want to try anything."""
        parser = build_parser()
        registered = set()
        for action in parser._subparsers._group_actions:  # noqa: SLF001
            registered |= set(action.choices)
        assert registered == set(COMMANDS)
        assert registered & {"purge", "drop", "reset", "skip", "delete", "force-deliver"} == set()

    def test_S5_the_guard_would_catch_a_fake_destructive_command(self):
        """Control for the test above: prove it inspects the registry rather than trivially
        passing."""
        import argparse

        parser = build_parser()
        action = next(iter(parser._subparsers._group_actions))  # noqa: SLF001
        action.choices["purge"] = argparse.ArgumentParser()
        registered = set()
        for registered_action in parser._subparsers._group_actions:  # noqa: SLF001
            registered |= set(registered_action.choices)
        assert registered != set(COMMANDS)

    def test_S6_configuration_refuses_to_start_when_incomplete(self):
        """§73. An agent that starts half-configured does nothing while looking deployed."""
        with pytest.raises(AgentConfigurationError) as error:
            AgentSettings.from_env({"DHEALTH_CONNECT_SERVER_URL": "https://dhealth.invalid"})
        assert "DHEALTH_CONNECT_CLIENT_ID" in str(error.value)

    def test_S7_the_token_never_appears_in_reportable_configuration(self):
        """§9/§72. Absent rather than masked -- a mask still trains everyone that secrets belong in
        this output."""
        settings = AgentSettings.from_env(
            {
                "DHEALTH_CONNECT_SERVER_URL": "https://dhealth.invalid",
                "DHEALTH_CONNECT_CLIENT_ID": AGENT_CLIENT,
                "DHEALTH_CONNECT_TOKEN": "super-secret-value",
                "DHEALTH_CONNECT_ORGANIZATION_ID": str(uuid.uuid4()),
                "DHEALTH_CONNECT_INSTALLATION_ID": str(uuid.uuid4()),
                "DHEALTH_CONNECT_SOURCE_SYSTEM": "synthetic-lis",
            }
        )
        printed = settings.redacted()
        assert "super-secret-value" not in str(printed)
        assert "token" not in printed

    def test_S8_the_synthetic_adapter_is_idempotent_on_the_order_id(self):
        """The protocol requires it of every implementation, so the shipped one must honour it."""
        adapter = SyntheticLabAdapter()
        order = AdapterOrder(
            lab_order_id=uuid.uuid4(),
            patient_source_system="emr",
            patient_identity_system="urn:emr:mrn",
            patient_identity_value="P-1",
            practitioner_identity_value="D-1",
            catalog_codes=("GLU",),
            ordered_at=datetime(2026, 9, 23, 8, 0, tzinfo=UTC),
        )
        adapter.handle_order(order)
        adapter.handle_order(order)
        assert len(adapter.received_orders) == 1

    def test_S9_the_synthetic_adapter_can_be_made_to_fail_both_ways(self):
        """A test that cannot make the adapter fail cannot prove what a real failure does."""
        adapter = SyntheticLabAdapter()
        order = AdapterOrder(
            lab_order_id=uuid.uuid4(),
            patient_source_system="emr",
            patient_identity_system="urn:emr:mrn",
            patient_identity_value="P-1",
            practitioner_identity_value="D-1",
            catalog_codes=("GLU",),
            ordered_at=datetime(2026, 9, 23, 8, 0, tzinfo=UTC),
        )
        adapter.fail_next = FailureCategory.MAPPING.value
        with pytest.raises(AdapterRefused):
            adapter.handle_order(order)
        adapter.unavailable = True
        with pytest.raises(AdapterUnavailable):
            adapter.handle_order(order)
        assert adapter.probe() is False
