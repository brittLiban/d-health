"""DH-CONNECT-AGENT-001 §87–§89 — the server side of the connector runtime.

Three groups, and each one is about a different way the runtime could quietly be wrong:

    A   service identity. Who is allowed to call these endpoints, and what decides which facility
        they act for. Every A-test is really the same question -- §8's "the installation comes from
        the credential, never from the request" -- asked from a different angle.
    H   health. That it is *derived*, that the agent's clock does not decide liveness, and that
        every state boundary is where the policy says it is rather than where a constant happens
        to land.
    D   durable delivery. Claim leases, retry, dead letters, and the two properties nothing else
        protects: a message is never lost, and an applied message is never applied twice.

The agent package is deliberately not involved. When it exists it will drive this same router over
``ASGITransport``, and a failure there should mean the agent is wrong -- which is only true if the
server is already known to be right.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from exchange_auth_support import (
    AUDIENCE,
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

from daher_adapter.connect.apply import ApplyOutcome, ConnectApplyError
from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    BusinessAcceptance,
    CollectionEvent,
    Direction,
    Envelope,
    FailureCategory,
    HealthState,
    OrderNotification,
    ScopedExternalId,
    SourcePractitionerRef,
    fingerprint,
)
from daher_adapter.connect.delivery import (
    DEFAULT_RETRY_POLICY,
    DeliveryConflict,
    DeliveryDirectionMismatch,
    DeliveryRepository,
    DurableReceiptStore,
)
from daher_adapter.connect.inbound import EXCHANGE_APPLIER_INSTANCE_ID
from daher_adapter.connect.models import ConnectorInstallation
from daher_adapter.connect.qualification import health_state_in_session
from daher_adapter.connect.repository import (
    ConnectorInstallationRepository,
    ServiceClientAlreadyBound,
)
from daher_adapter.connect.runtime_api import build_connect_runtime_router
from daher_adapter.connect.runtime_health import (
    DEFAULT_HEALTH_POLICY,
    RuntimeHealthPolicy,
    aware_utc,
    derive_health,
    runtime_health_in_session,
)
from daher_adapter.connect.runtime_models import (
    ConnectorDelivery,
    ConnectorRuntimeInstance,
    DeliveryState,
)
from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    Organization,
    OrganizationStatus,
    OrganizationType,
    ParticipationMode,
)

RUNTIME_AUDIENCE = "dhealth-connect-runtime"
AGENT_CLIENT = "connect-agent-lab-central"
OTHER_CLIENT = "connect-agent-other"

CONFIG_FP = fingerprint({"endpoint": "https://lis.invalid/fhir"})
MAPPING_FP = fingerprint({"mapping_revision": [["synthetic-lis", "GLU", "cat-1", "GLU"]]})


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def keypair():
    return generate_keypair()


class _Clock:
    """A settable clock, because every interesting case here is about the passage of time.

    Tests that need an agent to have gone quiet for two minutes must not take two minutes, and
    tests that assert a lease expired must not depend on how long the suite happened to run.
    """

    def __init__(self, start: datetime | None = None) -> None:
        # Starts at the *real* instant rather than a fixed date, and that is deliberate. Several
        # assertions read health through a caller that takes no clock -- readiness findings,
        # because a readiness rule with an injectable clock is a readiness rule somebody can freeze.
        # Those comparisons happen against the wall clock, so a heartbeat stamped in 2026 while the
        # reader says "now" would be 90 seconds stale the moment the suite ran on any other day.
        # Starting here keeps the clock fully deterministic *relative to itself* -- `advance` is
        # still exact -- while leaving "no time has passed" true for readers that cannot be told.
        self.now = start or datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> datetime:
        self.now = self.now + delta
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _connect_lab(sessions: sessionmaker[Session], name: str) -> Organization:
    """A CONNECT-enrolled laboratory.

    The enrollment is not decoration: ``ConnectorInstallationRepository.register`` refuses an
    organization that has not declared CONNECT participation, deliberately, because defaulting an
    undeclared facility into CONNECT is exactly the inference the enrollment model exists to
    prevent.
    """
    with sessions.begin() as session:
        org = Organization(
            name=name,
            org_type=OrganizationType.LAB.value,
            status=OrganizationStatus.ACTIVE.value,
        )
        session.add(org)
        session.flush()
        session.expunge(org)
    FacilityEnrollmentRepository(sessions).create(
        organization_id=org.id, participation_mode=ParticipationMode.CONNECT
    )
    return org


@pytest.fixture
def lab(sessions: sessionmaker[Session]) -> Organization:
    return _connect_lab(sessions, "Laboratoire Central")


@pytest.fixture
def actor(sessions: sessionmaker[Session]) -> uuid.UUID:
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer=ISSUER, subject="platform-admin", display_name="Platform", email=None
    )
    return identity.id


@pytest.fixture
def installation(sessions, lab, actor) -> ConnectorInstallation:
    """A registered connector, bound to a service client. The ordinary starting state."""
    repository = ConnectorInstallationRepository(sessions)
    record = repository.register(
        organization_id=lab.id,
        connector_key="synthetic-lis",
        connector_version="0.1",
        source_system="synthetic-lis",
        interface_type="FHIR",
        configuration_fingerprint=CONFIG_FP,
        mapping_fingerprint=MAPPING_FP,
        actor_identity_id=actor,
    )
    repository.set_service_identity(
        installation_id=record.id, service_client_id=AGENT_CLIENT, actor_identity_id=actor
    )
    with sessions() as session:
        row = session.get(ConnectorInstallation, record.id)
        session.expunge(row)
    return row


@pytest.fixture
def runtime_client(sessions, keypair, clock, installation) -> TestClient:  # noqa: ARG001
    _, public_key = keypair
    router = build_connect_runtime_router(
        session_factory=sessions,
        runtime_token_validator=build_validator(public_key, audience=RUNTIME_AUDIENCE),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
        now=clock,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _agent_token(private_key, *, client_id: str = AGENT_CLIENT, audience=RUNTIME_AUDIENCE, **kw):
    """A connector agent's client-credentials token: the service account subject plus ``azp``."""
    return make_token(
        private_key,
        subject=f"service-account-{client_id}",
        audience=audience,
        roles=kw.pop("roles", []),
        extra_claims={"azp": client_id, **kw.pop("extra_claims", {})},
        **kw,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _beat(client, token, *, instance="agent-1", queue=0, dead=0, reachable=True, reported=None):
    return client.post(
        "/exchange/v1/connect/runtime/heartbeat",
        headers=_auth(token),
        json={
            "runtime_instance_id": instance,
            "agent_version": "0.1.0",
            "contract_version": CONTRACT_VERSION,
            "reported_at": (reported or datetime(2026, 9, 23, 8, 0, tzinfo=UTC)).isoformat(),
            "local_queue_depth": queue,
            "local_dead_letters": dead,
            "adapter_reachable": reachable,
        },
    )


def _order_notification(organization_id: uuid.UUID) -> OrderNotification:
    return OrderNotification(
        patient=ScopedExternalId(
            source_organization_id=organization_id,
            source_system="emr",
            source_identity_system="urn:emr:mrn",
            source_identity_value="P-1",
        ),
        practitioner=SourcePractitionerRef(
            source_organization_id=organization_id,
            source_system="emr",
            source_identity_system="urn:emr:staff",
            source_identity_value="D-1",
        ),
        catalog_codes=("GLU",),
    )


def _envelope(installation, *, payload=None, event="evt-1", when=None, **overrides) -> Envelope:
    moment = when or datetime(2026, 9, 23, 7, 55, tzinfo=UTC)
    fields = {
        "contract_version": CONTRACT_VERSION,
        "message_id": uuid.uuid4(),
        "connector_installation_id": installation.id,
        "organization_id": installation.organization_id,
        "source_system": "synthetic-lis",
        "source_event_id": event,
        "source_occurred_at": moment,
        "sent_at": moment + timedelta(seconds=1),
        "lab_order_id": uuid.uuid4(),
        "payload": payload or BusinessAcceptance(external_order_id="EXT-1"),
    }
    fields.update(overrides)
    return Envelope(**fields)


# ── A: service identity ──────────────────────────────────────────────────────────────────────


class TestServiceIdentity:
    """§7–§11. The installation comes from the credential, and from nothing else."""

    def test_A1_a_valid_agent_token_resolves_to_its_own_installation(
        self, runtime_client, keypair, installation
    ):
        """The happy path, and the only path: ``azp`` -> ``service_client_id`` -> installation.

        Nothing in the request body names a facility. The heartbeat succeeds, which means the server
        found one -- and the only place it could have come from is the token.
        """
        private_key, _ = keypair
        response = _beat(runtime_client, _agent_token(private_key))
        assert response.status_code == 200, response.text
        assert response.json()["health_state"] == HealthState.HEALTHY.value
        assert installation.service_client_id == AGENT_CLIENT

    def test_A2_a_human_token_is_not_an_agent_credential(self, runtime_client, keypair):
        """§7: an agent is a different principal class, and the audience is what enforces it.

        A perfectly valid clinician or platform-admin token -- correct issuer, correct signature,
        correct expiry -- is minted for the *Exchange* audience and does not validate here. One
        credential must not work in both worlds.
        """
        private_key, _ = keypair
        human = make_token(private_key, subject="clinician-1", audience=AUDIENCE, roles=[])
        assert _beat(runtime_client, human).status_code == 401

    def test_A3_a_platform_admin_service_client_is_refused(self, runtime_client, keypair):
        """§7 again, from the other side: a *misconfigured* client must work nowhere.

        A client carrying the platform-admin realm role would otherwise get a service principal here
        and administrator authority on the clinical API -- one credential, two authority classes.
        Refusing is right even though the mistake is the deployment's: the safe direction is that
        such a token is useless rather than universal.
        """
        private_key, _ = keypair
        token = _agent_token(private_key, roles=[PLATFORM_ADMIN_ROLE])
        assert _beat(runtime_client, token).status_code == 401

    def test_A4_an_unknown_client_is_indistinguishable_from_a_bad_token(
        self, runtime_client, keypair
    ):
        """No enumeration. "That client is not a connector" and "your token is bad" look identical.

        Otherwise a caller with any valid runtime-audience token could discover which OIDC clients
        are registered connectors by watching the status code change.
        """
        private_key, _ = keypair
        unknown = _agent_token(private_key, client_id="not-registered")
        garbage = make_token(private_key, subject="x", audience=RUNTIME_AUDIENCE)
        first = _beat(runtime_client, unknown)
        second = _beat(runtime_client, garbage)
        assert first.status_code == second.status_code == 401
        assert first.json()["detail"] == second.json()["detail"]

    def test_A5_a_token_with_no_authorized_party_is_refused(self, runtime_client, keypair):
        """``azp`` is the whole of the identification. Missing it is not "default to something"."""
        private_key, _ = keypair
        token = make_token(private_key, subject="service-account-x", audience=RUNTIME_AUDIENCE)
        assert _beat(runtime_client, token).status_code == 401

    def test_A6_a_suspended_installation_is_told_so_in_plain_words(
        self, runtime_client, keypair, sessions, installation, actor
    ):
        """409, not 401, and it says "suspended".

        Nothing leaks: the caller already proved it holds this installation's credential. And an
        agent that learns its installation was suspended can stop, where one told 401 retries
        forever while an operator wonders why the queue is not draining.
        """
        private_key, _ = keypair
        ConnectorInstallationRepository(sessions).suspend(
            installation_id=installation.id, actor_identity_id=actor
        )
        response = _beat(runtime_client, _agent_token(private_key))
        assert response.status_code == 409
        assert "suspended" in response.json()["detail"].lower()

    def test_A13_a_retired_installations_credential_is_refused(
        self, runtime_client, keypair, sessions, installation, actor
    ):
        """Retirement is one-way, and it must end the credential with it.

        Distinct from suspension, which A6 covers, because retirement is precisely what frees the
        facility to register a *replacement* connector. A retired installation whose credential
        still resolved would be an agent acting for a facility that has moved on to a different
        one -- which is why `service_client_id` is unique globally rather than among live rows, and
        why the status check happens after the lookup rather than inside it.
        """
        private_key, _ = keypair
        ConnectorInstallationRepository(sessions).retire(
            installation_id=installation.id, actor_identity_id=actor
        )
        response = _beat(runtime_client, _agent_token(private_key))
        assert response.status_code == 409
        assert "retired" in response.json()["detail"].lower()

    def test_A14_health_ignores_a_retired_installations_agent(
        self, runtime_client, keypair, sessions, installation, actor, lab
    ):
        """And the heartbeat it managed to send before retirement decides nothing afterwards.

        A stale-but-valid runtime row must not be readable by health derivation, or a retired
        connector would keep reporting HEALTHY off a row nobody can add to.
        """
        private_key, _ = keypair
        assert _beat(runtime_client, _agent_token(private_key)).status_code == 200
        ConnectorInstallationRepository(sessions).retire(
            installation_id=installation.id, actor_identity_id=actor
        )
        with sessions() as session:
            # `active_installation_in_session` excludes retired rows entirely, so the facility reads
            # as having no installation at all -- which is the truthful answer once it is free to
            # register a replacement.
            assert health_state_in_session(session, lab.id) is HealthState.UNKNOWN

    def test_A7_the_envelope_cannot_choose_a_different_organization(
        self, runtime_client, keypair, installation, sessions
    ):
        """§47. A caller-supplied organization is a claim, not authority -- refused and audited.

        The critical half of the assertion is the second one: **no delivery row exists**. A refusal
        that still queued the message would have accepted a cross-tenant write and merely
        complained.
        """
        private_key, _ = keypair
        stranger = _envelope(installation, organization_id=uuid.uuid4())
        response = runtime_client.post(
            "/exchange/v1/connect/runtime/submit",
            headers=_auth(_agent_token(private_key)),
            json={"envelope": stranger.model_dump(mode="json")},
        )
        assert response.status_code == 409
        with sessions() as session:
            assert session.scalars(select(ConnectorDelivery)).all() == []
            refusals = session.scalars(
                select(ExchangeSecurityAuditEvent).where(
                    ExchangeSecurityAuditEvent.event_type == "connector_message_refused"
                )
            ).all()
            assert len(refusals) == 1
            assert refusals[0].outcome == "refused"
            assert refusals[0].resource_id == installation.id

    def test_A8_a_contract_major_mismatch_fails_closed(self, runtime_client, keypair, installation):
        """§49: refuse before processing, not process and warn afterwards.

        A contract-2 envelope cannot even be constructed by this build, so the check is exercised by
        posting the JSON directly -- which is also how a real mismatched agent would reach it.
        """
        private_key, _ = keypair
        body = _envelope(installation).model_dump(mode="json")
        body["contract_version"] = "2.0"
        response = runtime_client.post(
            "/exchange/v1/connect/runtime/submit",
            headers=_auth(_agent_token(private_key)),
            json={"envelope": body},
        )
        # 422 from the envelope's own validator or 409 from the router's -- both are refusals before
        # any clinical processing, which is what §49 requires. What must never happen is 200.
        assert response.status_code in (409, 422)

    def test_A9_an_agent_may_not_claim_the_appliers_reserved_instance_id(
        self, runtime_client, keypair
    ):
        """DHealth's own applier holds leases under a reserved id; an agent using it could
        acknowledge DHealth's inbound work, marking clinical messages delivered that nothing
        applied."""
        private_key, _ = keypair
        response = _beat(
            runtime_client, _agent_token(private_key), instance=EXCHANGE_APPLIER_INSTANCE_ID
        )
        assert response.status_code == 422

    def test_A10_one_client_cannot_be_bound_to_two_installations(
        self, sessions, installation, actor
    ):
        """§8's structural half. A credential that can be re-pointed is a cross-tenant path.

        Global uniqueness rather than "unique among live installations": were it the latter,
        retiring a connector would free its client id and a still-valid token would resolve
        somewhere new.
        """
        repository = ConnectorInstallationRepository(sessions)
        other_id = _connect_lab(sessions, "Autre Laboratoire").id
        second = repository.register(
            organization_id=other_id,
            connector_key="synthetic-lis",
            connector_version="0.1",
            source_system="synthetic-lis",
            interface_type="FHIR",
            configuration_fingerprint=CONFIG_FP,
            mapping_fingerprint=MAPPING_FP,
            actor_identity_id=actor,
        )
        with pytest.raises(ServiceClientAlreadyBound):
            repository.set_service_identity(
                installation_id=second.id,
                service_client_id=AGENT_CLIENT,
                actor_identity_id=actor,
            )

    def test_A11_clearing_the_binding_stops_the_agent_working(
        self, runtime_client, keypair, sessions, installation, actor
    ):
        """The revocation path, and the reason the ``None`` case exists.

        An operator can stop an agent without depending on somebody also deleting the Keycloak
        client. The token stays valid and resolves to nothing, which is refused -- fail closed.
        """
        private_key, _ = keypair
        assert _beat(runtime_client, _agent_token(private_key)).status_code == 200
        ConnectorInstallationRepository(sessions).set_service_identity(
            installation_id=installation.id, service_client_id=None, actor_identity_id=actor
        )
        assert _beat(runtime_client, _agent_token(private_key)).status_code == 401

    def test_A12_binding_a_service_identity_is_audited_once(self, sessions, installation, actor):
        """``connector_service_identity_set``, and not a second time for an unchanged rebind.

        An audit event describing a change that did not happen is noise in the one table an
        investigation reads -- the same discipline every other lifecycle method here follows.
        """
        repository = ConnectorInstallationRepository(sessions)
        repository.set_service_identity(
            installation_id=installation.id,
            service_client_id=AGENT_CLIENT,
            actor_identity_id=actor,
        )
        with sessions() as session:
            events = session.scalars(
                select(ExchangeSecurityAuditEvent).where(
                    ExchangeSecurityAuditEvent.event_type == "connector_service_identity_set"
                )
            ).all()
        assert len(events) == 1


# ── H: health, derived ───────────────────────────────────────────────────────────────────────


class TestRuntimeHealth:
    """§12–§16, §60–§63. Health is a function of observations, and has no write path."""

    def test_H1_never_heard_from_is_unknown_not_unavailable(self, sessions, installation):
        """ "You have not installed an agent" and "your agent stopped answering" have different
        owners, so they are different states and different sentences."""
        with sessions() as session:
            health = runtime_health_in_session(session, installation)
        assert health.state is HealthState.UNKNOWN
        assert not health.is_operational
        assert "jamais" in health.reasons[0]

    def test_H2_a_fresh_heartbeat_is_healthy(self, runtime_client, keypair, sessions, installation):
        """Reported in, adapter reachable, no backlog. The only combination that is operational."""
        private_key, _ = keypair
        assert _beat(runtime_client, _agent_token(private_key)).status_code == 200
        with sessions() as session:
            health = runtime_health_in_session(session, installation)
        assert health.state is HealthState.HEALTHY
        assert health.is_operational
        assert health.agent_version == "0.1.0"

    def test_H3_silence_past_the_threshold_is_unavailable(
        self, runtime_client, keypair, sessions, installation, clock
    ):
        """Measured against DHealth's own receive stamp, and exercised through a real heartbeat.

        Driving it by writing a ``ConnectorRuntimeInstance`` by hand would prove only that the pure
        function works. Going through the endpoint is what proves the stored value round-trips as
        something comparable -- which on SQLite it does not, without ``aware_utc``.
        """
        private_key, _ = keypair
        assert _beat(runtime_client, _agent_token(private_key)).status_code == 200
        clock.advance(DEFAULT_HEALTH_POLICY.heartbeat_stale_after + timedelta(seconds=1))
        with sessions() as session:
            health = runtime_health_in_session(session, installation, now=clock.now)
        assert health.state is HealthState.UNAVAILABLE
        assert not health.is_operational

    def test_H4_exactly_at_the_threshold_is_still_healthy(
        self, runtime_client, keypair, sessions, installation, clock
    ):
        """The boundary, pinned. A threshold tested only well past itself is a threshold whose
        comparison operator nobody has checked."""
        private_key, _ = keypair
        assert _beat(runtime_client, _agent_token(private_key)).status_code == 200
        clock.advance(DEFAULT_HEALTH_POLICY.heartbeat_stale_after)
        with sessions() as session:
            health = runtime_health_in_session(session, installation, now=clock.now)
        assert health.state is HealthState.HEALTHY

    def test_H5_an_unreachable_source_system_is_degraded_and_not_operational(
        self, runtime_client, keypair, sessions, installation
    ):
        """§63. An agent that is alive and cannot reach its LIS accepts orders and strands them,
        which is worse than refusing them -- the clinician believes the order arrived."""
        private_key, _ = keypair
        assert _beat(runtime_client, _agent_token(private_key), reachable=False).status_code == 200
        with sessions() as session:
            health = runtime_health_in_session(session, installation)
        assert health.state is HealthState.DEGRADED
        assert not health.is_operational
        assert any("système source" in reason for reason in health.reasons)

    def test_H6_the_agents_own_clock_cannot_make_it_look_alive(
        self, runtime_client, keypair, sessions, installation, clock
    ):
        """§14. An agent reporting a timestamp an hour in the future goes stale on schedule anyway.

        ``reported_at`` is stored so an operator can see skew; it is never what liveness is measured
        against. Without this, a clock-skewed agent could claim to have been heard from tomorrow.
        """
        private_key, _ = keypair
        future = clock.now + timedelta(hours=1)
        assert _beat(runtime_client, _agent_token(private_key), reported=future).status_code == 200
        clock.advance(DEFAULT_HEALTH_POLICY.heartbeat_stale_after + timedelta(seconds=1))
        with sessions() as session:
            health = runtime_health_in_session(session, installation, now=clock.now)
            stored = session.scalar(select(ConnectorRuntimeInstance))
        assert health.state is HealthState.UNAVAILABLE
        assert stored.reported_at is not None

    def test_H7_a_naive_reported_at_is_refused(self, runtime_client, keypair):
        """§59: no naive datetimes. An instant with no offset is not a fact about when."""
        private_key, _ = keypair
        response = runtime_client.post(
            "/exchange/v1/connect/runtime/heartbeat",
            headers=_auth(_agent_token(private_key)),
            json={
                "runtime_instance_id": "agent-1",
                "agent_version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
                "reported_at": "2026-09-23T08:00:00",
                "local_queue_depth": 0,
                "local_dead_letters": 0,
                "adapter_reachable": True,
            },
        )
        assert response.status_code == 422

    def test_H8_a_restart_keeps_one_runtime_row_and_counts_the_beats(
        self, runtime_client, keypair, sessions
    ):
        """§74: an agent restarting is not a new connector. Same instance id, same row."""
        private_key, _ = keypair
        for _ in range(3):
            assert _beat(runtime_client, _agent_token(private_key)).status_code == 200
        with sessions() as session:
            rows = session.scalars(select(ConnectorRuntimeInstance)).all()
        assert len(rows) == 1
        assert rows[0].heartbeat_count == 3

    def test_H9_a_redeploy_creates_a_second_instance_and_the_newest_decides(
        self, runtime_client, keypair, sessions, installation, clock
    ):
        """A new instance id is a new deployment. The old row stays history and stops deciding."""
        private_key, _ = keypair
        first = _beat(runtime_client, _agent_token(private_key), instance="agent-1")
        assert first.status_code == 200
        clock.advance(timedelta(seconds=10))
        second = _beat(runtime_client, _agent_token(private_key), instance="agent-2")
        assert second.status_code == 200
        with sessions() as session:
            rows = session.scalars(select(ConnectorRuntimeInstance)).all()
            health = runtime_health_in_session(session, installation, now=clock.now)
        assert len(rows) == 2
        assert health.runtime_instance_id == "agent-2"

    def test_H10_heartbeat_is_not_written_to_the_security_audit(
        self, runtime_client, keypair, sessions
    ):
        """§64. One audit row for the first sighting of a runtime instance -- a new principal
        appearing -- and none for the beats after it. A table with a row per poll buries the
        handful of events that matter under its own noise."""
        private_key, _ = keypair
        for _ in range(5):
            _beat(runtime_client, _agent_token(private_key))
        with sessions() as session:
            events = session.scalars(
                select(ExchangeSecurityAuditEvent).where(
                    ExchangeSecurityAuditEvent.event_type == "connector_runtime_connected"
                )
            ).all()
        assert len(events) == 1

    def test_H11_a_suspended_connector_says_suspended_not_never_connected(
        self, runtime_client, keypair, sessions, installation, actor
    ):
        """A finding that lies about *why* is worse than one that says nothing.

        The agent here is beating perfectly. Reporting "no agent has ever reported in" would send an
        operator hunting an installation problem they created themselves.
        """
        private_key, _ = keypair
        assert _beat(runtime_client, _agent_token(private_key)).status_code == 200
        ConnectorInstallationRepository(sessions).suspend(
            installation_id=installation.id, actor_identity_id=actor
        )
        with sessions() as session:
            fresh = session.get(ConnectorInstallation, installation.id)
            health = runtime_health_in_session(session, fresh)
        assert health.state is HealthState.UNKNOWN
        assert "suspendu" in health.reasons[0]
        assert "jamais" not in health.reasons[0]

    def test_H12_health_is_not_qualification(self, runtime_client, keypair, sessions, lab):
        """§16, in both directions. This installation has never been qualified and its agent is
        beating: HEALTHY, and still not routable. Nothing here reads a qualification row."""
        private_key, _ = keypair
        assert _beat(runtime_client, _agent_token(private_key)).status_code == 200
        with sessions() as session:
            assert health_state_in_session(session, lab.id) is HealthState.HEALTHY

    def test_H13_every_threshold_lives_in_one_policy_object(self):
        """§15. Not scattered constants -- a heartbeat interval and a staleness window that drift
        apart declare an on-schedule agent intermittently dead."""
        policy = RuntimeHealthPolicy()
        assert policy.heartbeat_stale_after > policy.heartbeat_interval
        assert policy.degraded_dead_letters >= 1
        tight = RuntimeHealthPolicy(heartbeat_stale_after=timedelta(seconds=1))
        instance = ConnectorRuntimeInstance(
            connector_installation_id=uuid.uuid4(),
            runtime_instance_id="agent-1",
            agent_version="0.1",
            contract_version=CONTRACT_VERSION,
            first_seen_at=datetime(2026, 9, 23, 8, 0, tzinfo=UTC),
            last_seen_at=datetime(2026, 9, 23, 8, 0, tzinfo=UTC),
            reported_at=datetime(2026, 9, 23, 8, 0, tzinfo=UTC),
            heartbeat_count=1,
            local_queue_depth=0,
            local_dead_letters=0,
            adapter_reachable=True,
        )
        later = datetime(2026, 9, 23, 8, 0, 5, tzinfo=UTC)
        assert (
            derive_health(
                instance=instance,
                pending_deliveries=0,
                oldest_pending_age=None,
                dead_letters=0,
                now=later,
                policy=tight,
            ).state
            is HealthState.UNAVAILABLE
        )

    def test_H14_a_dead_letter_degrades(self):
        """One undelivered clinical message is not an acceptable number of undelivered clinical
        messages, so the threshold is one."""
        instance = ConnectorRuntimeInstance(
            connector_installation_id=uuid.uuid4(),
            runtime_instance_id="agent-1",
            agent_version="0.1",
            contract_version=CONTRACT_VERSION,
            first_seen_at=datetime(2026, 9, 23, 8, 0, tzinfo=UTC),
            last_seen_at=datetime(2026, 9, 23, 8, 0, tzinfo=UTC),
            reported_at=datetime(2026, 9, 23, 8, 0, tzinfo=UTC),
            heartbeat_count=1,
            local_queue_depth=0,
            local_dead_letters=0,
            adapter_reachable=True,
        )
        health = derive_health(
            instance=instance,
            pending_deliveries=0,
            oldest_pending_age=None,
            dead_letters=1,
            now=datetime(2026, 9, 23, 8, 0, 1, tzinfo=UTC),
        )
        assert health.state is HealthState.DEGRADED
        assert not health.is_operational


# ── D: durable delivery ──────────────────────────────────────────────────────────────────────


class TestDurableDelivery:
    """§20–§37, §52–§56. Never lost, never applied twice, never silently discarded."""

    def test_D1_an_outbound_message_is_queued_and_claimable(self, sessions, installation):
        deliveries = DeliveryRepository()
        with sessions.begin() as session:
            notification = _order_notification(installation.organization_id)
            envelope = _envelope(installation, payload=notification)
            delivery, created = deliveries.enqueue(
                session,
                installation_id=installation.id,
                envelope=envelope,
                expected_direction=Direction.DHEALTH_TO_CONNECTOR,
            )
            assert created
            assert delivery.state == DeliveryState.PENDING.value

        with sessions.begin() as session:
            claimed = deliveries.claim(
                session,
                installation_id=installation.id,
                direction=Direction.DHEALTH_TO_CONNECTOR,
                runtime_instance_id="agent-1",
            )
            assert len(claimed) == 1
            assert claimed[0].state == DeliveryState.CLAIMED.value
            assert claimed[0].attempt_count == 1

    def test_D2_re_enqueueing_the_same_event_is_one_row(self, sessions, installation):
        """The caller can be a hook with no memory. Firing twice produces one delivery."""
        deliveries = DeliveryRepository()
        notification = _order_notification(installation.organization_id)
        envelope = _envelope(installation, payload=notification)
        with sessions.begin() as session:
            first, created_first = deliveries.enqueue(
                session,
                installation_id=installation.id,
                envelope=envelope,
                expected_direction=Direction.DHEALTH_TO_CONNECTOR,
            )
            second, created_second = deliveries.enqueue(
                session,
                installation_id=installation.id,
                envelope=envelope,
                expected_direction=Direction.DHEALTH_TO_CONNECTOR,
            )
            assert created_first and not created_second
            assert first.id == second.id

    def test_D3_the_same_event_with_different_content_is_refused(self, sessions, installation):
        """Never an overwrite. The second version of a clinical event does not quietly win."""
        deliveries = DeliveryRepository()
        with sessions.begin() as session:
            deliveries.enqueue(
                session,
                installation_id=installation.id,
                envelope=_envelope(installation, payload=BusinessAcceptance(external_order_id="A")),
                expected_direction=Direction.CONNECTOR_TO_DHEALTH,
            )
            with pytest.raises(DeliveryConflict):
                deliveries.enqueue(
                    session,
                    installation_id=installation.id,
                    envelope=_envelope(
                        installation, payload=BusinessAcceptance(external_order_id="B")
                    ),
                    expected_direction=Direction.CONNECTOR_TO_DHEALTH,
                )

    def test_D4_a_message_cannot_be_queued_in_the_wrong_direction(self, sessions, installation):
        """§45's second boundary. The queue itself refuses, whoever asked."""
        deliveries = DeliveryRepository()
        with sessions.begin() as session:
            with pytest.raises(DeliveryDirectionMismatch):
                deliveries.enqueue(
                    session,
                    installation_id=installation.id,
                    envelope=_envelope(
                        installation, payload=_order_notification(installation.organization_id)
                    ),
                    expected_direction=Direction.CONNECTOR_TO_DHEALTH,
                )

    def test_D5_an_expired_lease_is_reclaimable_by_another_instance(
        self, sessions, installation, clock
    ):
        """The point of a lease. An agent that dies holding work blocks it for one lease, not
        forever, and no operator has to know that unsticking it is a thing."""
        deliveries = DeliveryRepository()
        with sessions.begin() as session:
            deliveries.enqueue(
                session,
                installation_id=installation.id,
                envelope=_envelope(
                    installation, payload=_order_notification(installation.organization_id)
                ),
                expected_direction=Direction.DHEALTH_TO_CONNECTOR,
                now=clock.now,
            )
        with sessions.begin() as session:
            first = deliveries.claim(
                session,
                installation_id=installation.id,
                direction=Direction.DHEALTH_TO_CONNECTOR,
                runtime_instance_id="agent-dead",
                lease=timedelta(minutes=2),
                now=clock.now,
            )
            assert len(first) == 1

        # Still held: a second agent gets nothing while the lease stands.
        with sessions.begin() as session:
            assert (
                deliveries.claim(
                    session,
                    installation_id=installation.id,
                    direction=Direction.DHEALTH_TO_CONNECTOR,
                    runtime_instance_id="agent-new",
                    now=clock.now + timedelta(minutes=1),
                )
                == []
            )

        clock.advance(timedelta(minutes=3))
        with sessions.begin() as session:
            reclaimed = deliveries.claim(
                session,
                installation_id=installation.id,
                direction=Direction.DHEALTH_TO_CONNECTOR,
                runtime_instance_id="agent-new",
                now=clock.now,
            )
            assert len(reclaimed) == 1
            assert reclaimed[0].claimed_by_runtime_instance_id == "agent-new"
            # Two hand-overs, two attempts -- counted at claim, not at reported failure.
            assert reclaimed[0].attempt_count == 2

    def test_D6_a_stale_instance_cannot_acknowledge_work_it_lost(
        self, runtime_client, keypair, sessions, installation, clock
    ):
        """Accepting it would mark delivered a message the current holder is still working on, and
        a DELIVERED row is never claimed again -- so nobody would ever deliver it."""
        private_key, _ = keypair
        deliveries = DeliveryRepository()
        with sessions.begin() as session:
            deliveries.enqueue(
                session,
                installation_id=installation.id,
                envelope=_envelope(
                    installation, payload=_order_notification(installation.organization_id)
                ),
                expected_direction=Direction.DHEALTH_TO_CONNECTOR,
                now=clock.now,
            )
        claim = runtime_client.post(
            "/exchange/v1/connect/runtime/claim",
            headers=_auth(_agent_token(private_key)),
            json={"runtime_instance_id": "agent-dead", "max_messages": 5},
        )
        assert claim.status_code == 200, claim.text
        delivery_id = claim.json()["messages"][0]["delivery_id"]

        clock.advance(timedelta(minutes=5))
        runtime_client.post(
            "/exchange/v1/connect/runtime/claim",
            headers=_auth(_agent_token(private_key)),
            json={"runtime_instance_id": "agent-new", "max_messages": 5},
        )
        stale = runtime_client.post(
            "/exchange/v1/connect/runtime/ack",
            headers=_auth(_agent_token(private_key)),
            json={
                "runtime_instance_id": "agent-dead",
                "delivery_id": delivery_id,
                "outcome": "DELIVERED",
            },
        )
        assert stale.status_code == 409

    def test_D7_a_terminal_failure_dead_letters_on_the_first_attempt(
        self, sessions, installation, clock
    ):
        """Retrying a mapping error produces the identical mapping error. The retry budget exists
        for network trouble and is not spent on something that cannot change."""
        deliveries = DeliveryRepository()
        with sessions.begin() as session:
            delivery, _ = deliveries.enqueue(
                session,
                installation_id=installation.id,
                envelope=_envelope(
                    installation, payload=_order_notification(installation.organization_id)
                ),
                expected_direction=Direction.DHEALTH_TO_CONNECTOR,
                now=clock.now,
            )
        with sessions.begin() as session:
            claimed = deliveries.claim(
                session,
                installation_id=installation.id,
                direction=Direction.DHEALTH_TO_CONNECTOR,
                runtime_instance_id="agent-1",
                now=clock.now,
            )
            updated = deliveries.mark_failed(
                session,
                delivery=claimed[0],
                runtime_instance_id="agent-1",
                category=FailureCategory.MAPPING,
                detail="code inconnu",
                now=clock.now,
            )
            assert updated.state == DeliveryState.DEAD_LETTER.value
            assert updated.attempt_count == 1
            assert updated.dead_lettered_at is not None

    def test_D8_a_retryable_failure_backs_off_until_the_budget_is_spent(
        self, sessions, installation, clock
    ):
        """And then dead-letters. A queue that retries forever is a queue where nobody ever looks
        at a dead letter, because nothing reaches one -- the work just ages invisibly."""
        deliveries = DeliveryRepository()
        with sessions.begin() as session:
            deliveries.enqueue(
                session,
                installation_id=installation.id,
                envelope=_envelope(
                    installation, payload=_order_notification(installation.organization_id)
                ),
                expected_direction=Direction.DHEALTH_TO_CONNECTOR,
                now=clock.now,
            )
        states = []
        for _ in range(DEFAULT_RETRY_POLICY.max_attempts):
            with sessions.begin() as session:
                claimed = deliveries.claim(
                    session,
                    installation_id=installation.id,
                    direction=Direction.DHEALTH_TO_CONNECTOR,
                    runtime_instance_id="agent-1",
                    now=clock.now,
                )
                assert len(claimed) == 1, "the row should still be claimable"
                updated = deliveries.mark_failed(
                    session,
                    delivery=claimed[0],
                    runtime_instance_id="agent-1",
                    category=FailureCategory.REMOTE_UNAVAILABLE,
                    detail="système source injoignable",
                    now=clock.now,
                )
                states.append(updated.state)
            clock.advance(DEFAULT_RETRY_POLICY.max_delay + timedelta(seconds=1))

        assert states[:-1] == [DeliveryState.PENDING.value] * (
            DEFAULT_RETRY_POLICY.max_attempts - 1
        )
        assert states[-1] == DeliveryState.DEAD_LETTER.value

    def test_D9_backoff_grows_and_is_capped(self):
        """Unbounded backoff means a message that failed six times is next tried in four hours,
        which for a clinical order is indistinguishable from never."""
        policy = DEFAULT_RETRY_POLICY
        delays = [policy.next_delay(n) for n in range(1, 8)]
        assert delays[0] == policy.base_delay
        assert delays[1] > delays[0]
        assert all(delay <= policy.max_delay for delay in delays)
        assert delays[-1] == policy.max_delay

    def test_D10_a_claim_batch_is_bounded(self, sessions, installation, clock):
        """§25. An agent that claims the whole backlog holds all of it under one lease, and if it
        dies the entire backlog waits rather than being picked up piecemeal."""
        deliveries = DeliveryRepository()
        with sessions.begin() as session:
            for index in range(7):
                deliveries.enqueue(
                    session,
                    installation_id=installation.id,
                    envelope=_envelope(
                        installation,
                        payload=_order_notification(installation.organization_id),
                        event=f"evt-{index}",
                    ),
                    expected_direction=Direction.DHEALTH_TO_CONNECTOR,
                    now=clock.now,
                )
        with sessions.begin() as session:
            claimed = deliveries.claim(
                session,
                installation_id=installation.id,
                direction=Direction.DHEALTH_TO_CONNECTOR,
                runtime_instance_id="agent-1",
                limit=3,
                now=clock.now,
            )
        assert len(claimed) == 3

    def test_D11_claims_hand_over_oldest_first(self, sessions, installation, clock):
        """§79. A laboratory must see an order before the amendment to its result."""
        deliveries = DeliveryRepository()
        for index in range(3):
            with sessions.begin() as session:
                deliveries.enqueue(
                    session,
                    installation_id=installation.id,
                    envelope=_envelope(
                        installation,
                        payload=_order_notification(installation.organization_id),
                        event=f"evt-{index}",
                    ),
                    expected_direction=Direction.DHEALTH_TO_CONNECTOR,
                    now=clock.advance(timedelta(seconds=1)),
                )
        with sessions.begin() as session:
            claimed = deliveries.claim(
                session,
                installation_id=installation.id,
                direction=Direction.DHEALTH_TO_CONNECTOR,
                runtime_instance_id="agent-1",
                now=clock.now,
            )
        assert [row.source_event_id for row in claimed] == ["evt-0", "evt-1", "evt-2"]

    def test_D12_the_durable_receipt_store_survives_a_new_session(self, sessions):
        """The whole difference from ``InMemoryReceiptStore``: replay protection outlives a process.

        Without it, an at-least-once transport plus one restart turns a collection event into two,
        and nothing anywhere records that it happened twice.
        """
        with sessions.begin() as session:
            DurableReceiptStore(session).record("org:sys", "evt-1", "fp-1")
        with sessions() as session:
            assert DurableReceiptStore(session).seen("org:sys", "evt-1") == "fp-1"

    def test_D13_a_receipt_recorded_twice_with_different_content_is_refused(self, sessions):
        """The row records what DHealth actually applied. Rewriting it would erase the evidence
        that the partner contradicted themselves."""
        with sessions.begin() as session:
            store = DurableReceiptStore(session)
            store.record("org:sys", "evt-1", "fp-1")
            store.record("org:sys", "evt-1", "fp-1")  # honest retry: a no-op, not an error
            with pytest.raises(DeliveryConflict):
                store.record("org:sys", "evt-1", "fp-2")

    def test_D14_an_agent_cannot_acknowledge_another_facilitys_delivery(
        self, runtime_client, keypair, sessions, installation, lab, actor, clock
    ):
        """A delivery id from another facility's queue must be indistinguishable from a made-up one.

        404 for both, the Exchange's own anti-enumeration convention. Resolving it without the
        installation check would let one connector mark another's clinical work delivered.
        """
        private_key, _ = keypair
        repository = ConnectorInstallationRepository(sessions)
        other_id = _connect_lab(sessions, "Autre Laboratoire").id
        second = repository.register(
            organization_id=other_id,
            connector_key="synthetic-lis",
            connector_version="0.1",
            source_system="synthetic-lis",
            interface_type="FHIR",
            configuration_fingerprint=CONFIG_FP,
            mapping_fingerprint=MAPPING_FP,
            actor_identity_id=actor,
        )
        deliveries = DeliveryRepository()
        with sessions() as session:
            foreign_installation = session.get(ConnectorInstallation, second.id)
            session.expunge(foreign_installation)
        with sessions.begin() as session:
            foreign, _ = deliveries.enqueue(
                session,
                installation_id=foreign_installation.id,
                envelope=_envelope(
                    foreign_installation,
                    payload=_order_notification(foreign_installation.organization_id),
                ),
                expected_direction=Direction.DHEALTH_TO_CONNECTOR,
                now=clock.now,
            )
            foreign_id = foreign.id

        response = runtime_client.post(
            "/exchange/v1/connect/runtime/ack",
            headers=_auth(_agent_token(private_key)),
            json={
                "runtime_instance_id": "agent-1",
                "delivery_id": str(foreign_id),
                "outcome": "DELIVERED",
            },
        )
        invented = runtime_client.post(
            "/exchange/v1/connect/runtime/ack",
            headers=_auth(_agent_token(private_key)),
            json={
                "runtime_instance_id": "agent-1",
                "delivery_id": str(uuid.uuid4()),
                "outcome": "DELIVERED",
            },
        )
        assert response.status_code == invented.status_code == 404
        assert response.json()["detail"] == invented.json()["detail"]

    def test_D15_a_failed_ack_must_state_a_category(
        self, runtime_client, keypair, sessions, installation, clock
    ):
        """An agent that could omit it would leave DHealth guessing, which in practice means
        retrying a permanent error forever."""
        private_key, _ = keypair
        deliveries = DeliveryRepository()
        with sessions.begin() as session:
            deliveries.enqueue(
                session,
                installation_id=installation.id,
                envelope=_envelope(
                    installation, payload=_order_notification(installation.organization_id)
                ),
                expected_direction=Direction.DHEALTH_TO_CONNECTOR,
                now=clock.now,
            )
        claim = runtime_client.post(
            "/exchange/v1/connect/runtime/claim",
            headers=_auth(_agent_token(private_key)),
            json={"runtime_instance_id": "agent-1", "max_messages": 5},
        )
        delivery_id = claim.json()["messages"][0]["delivery_id"]
        response = runtime_client.post(
            "/exchange/v1/connect/runtime/ack",
            headers=_auth(_agent_token(private_key)),
            json={
                "runtime_instance_id": "agent-1",
                "delivery_id": delivery_id,
                "outcome": "FAILED",
            },
        )
        assert response.status_code == 422

    def test_D16_submitting_the_same_event_twice_is_reported_as_a_duplicate(
        self, runtime_client, keypair, sessions, installation
    ):
        """At-least-once transport means honest retries are normal traffic, not errors.

        The agent is told which one it was, so a connector author can watch replay protection work
        rather than infer it. One row either way.
        """
        private_key, _ = keypair
        envelope = _envelope(
            installation, payload=CollectionEvent(external_order_id="EXT-1")
        ).model_dump(mode="json")
        first = runtime_client.post(
            "/exchange/v1/connect/runtime/submit",
            headers=_auth(_agent_token(private_key)),
            json={"envelope": envelope},
        )
        second = runtime_client.post(
            "/exchange/v1/connect/runtime/submit",
            headers=_auth(_agent_token(private_key)),
            json={"envelope": envelope},
        )
        assert first.status_code == second.status_code == 200, first.text
        assert first.json()["duplicate"] is False
        assert second.json()["duplicate"] is True
        assert first.json()["delivery_id"] == second.json()["delivery_id"]
        with sessions() as session:
            assert len(session.scalars(select(ConnectorDelivery)).all()) == 1

    def test_D17_an_agent_may_not_submit_an_outbound_message_type(
        self, runtime_client, keypair, installation
    ):
        """§45's first boundary. ``ORDER_NOTIFICATION`` travels DHealth -> connector; a connector
        sending one is either confused or trying something."""
        private_key, _ = keypair
        envelope = _envelope(
            installation, payload=_order_notification(installation.organization_id)
        )
        response = runtime_client.post(
            "/exchange/v1/connect/runtime/submit",
            headers=_auth(_agent_token(private_key)),
            json={"envelope": envelope.model_dump(mode="json")},
        )
        assert response.status_code == 409
        assert "connector" in response.json()["detail"]

    def test_D18_a_claim_never_returns_inbound_work(
        self, runtime_client, keypair, sessions, installation
    ):
        """Direction is decided from the caller's kind, not from the request. An agent cannot claim
        back the messages it submitted."""
        private_key, _ = keypair
        envelope = _envelope(installation, payload=CollectionEvent(external_order_id="EXT-1"))
        runtime_client.post(
            "/exchange/v1/connect/runtime/submit",
            headers=_auth(_agent_token(private_key)),
            json={"envelope": envelope.model_dump(mode="json")},
        )
        claim = runtime_client.post(
            "/exchange/v1/connect/runtime/claim",
            headers=_auth(_agent_token(private_key)),
            json={"runtime_instance_id": "agent-1", "max_messages": 10},
        )
        assert claim.status_code == 200
        assert claim.json()["messages"] == []

    def test_D19_inbound_backlog_does_not_degrade_the_connectors_health(
        self, runtime_client, keypair, sessions, installation, clock
    ):
        """A DHealth-side backlog is DHealth failing to apply, not the agent failing to keep up.

        Calling it the connector's poor health would point an operator at the partner for a problem
        on this side -- the exact wrong-owner failure the health module is written to avoid.
        """
        private_key, _ = keypair
        deliveries = DeliveryRepository()
        with sessions.begin() as session:
            delivery, _ = deliveries.enqueue(
                session,
                installation_id=installation.id,
                envelope=_envelope(installation, event="stuck"),
                expected_direction=Direction.CONNECTOR_TO_DHEALTH,
                now=clock.now - timedelta(hours=2),
            )
            delivery.created_at = clock.now - timedelta(hours=2)
        assert _beat(runtime_client, _agent_token(private_key)).status_code == 200
        with sessions() as session:
            health = runtime_health_in_session(session, installation, now=clock.now)
        assert health.state is HealthState.HEALTHY
        assert health.pending_deliveries == 0


class TestInboundRetry:
    """§52 — an inbound message that failed retryably has to be tried again by *something*.

    The something is the heartbeat. ``submit`` drains only what it just enqueued, so a message
    whose bridge application failed and backed off would otherwise sit ``PENDING`` with its attempt
    time in the past forever: durable, and never applied. Driving the retry from the agent's own
    cadence is the honest arrangement — no agent means no inbound traffic, so nothing to retry.

    This class exists because that path shipped untested. ``apply_pending_inbound`` is called from
    two places in ``runtime_api`` and was exercised by neither test file, which is exactly the shape
    of defect this ticket's own qualification model is about: a mechanism that reads well and that
    nothing checks.
    """

    def test_IR1_a_retryable_bridge_failure_is_retried_by_a_later_heartbeat(
        self, sessions, keypair, clock, installation
    ):
        """Fail once, back off, heartbeat, succeed. The whole loop, driven.

        The bridge is injected and fails only its first call, so the retry is observed rather than
        assumed: a test that used a permanently failing bridge could not tell "retried and failed
        again" from "never retried at all".
        """
        applied: list[str] = []

        class _FailsOnce:
            def __init__(self) -> None:
                self.calls = 0

            def apply(self, session, envelope):  # noqa: ANN001, ARG002
                self.calls += 1
                if self.calls == 1:
                    raise ConnectApplyError(
                        FailureCategory.REMOTE_UNAVAILABLE, "source system briefly unreachable"
                    )
                applied.append(envelope.source_event_id)
                return ApplyOutcome(applied=True)

        bridge = _FailsOnce()
        client = _runtime_client_with_bridge(sessions, keypair, clock, lambda *_a: bridge)
        private_key, _ = keypair

        envelope = _envelope(installation, payload=CollectionEvent(external_order_id="EXT-1"))
        response = client.post(
            "/exchange/v1/connect/runtime/submit",
            headers=_auth(_agent_token(private_key)),
            json={"envelope": envelope.model_dump(mode="json")},
        )
        assert response.status_code == 200, response.text

        # First pass failed and backed off: still owned by DHealth, scheduled, not dead.
        with sessions() as session:
            delivery = session.scalar(select(ConnectorDelivery))
        assert bridge.calls == 1
        assert applied == []
        assert delivery.state == DeliveryState.PENDING.value
        assert delivery.last_failure_category == FailureCategory.REMOTE_UNAVAILABLE.value
        # aware_utc because SQLite hands back a naive datetime for DateTime(timezone=True), so the
        # bare comparison raises TypeError rather than failing an assertion -- the same trap the
        # production code carries this helper for.
        assert aware_utc(delivery.next_attempt_at) > clock.now

        # A heartbeat *before* the backoff expires must not retry it -- otherwise the backoff is
        # decorative and a failing integration is hammered once per poll.
        _beat(client, _agent_token(private_key))
        assert bridge.calls == 1

        clock.advance(DEFAULT_RETRY_POLICY.max_delay + timedelta(seconds=1))
        _beat(client, _agent_token(private_key))

        assert bridge.calls == 2
        assert applied == [envelope.source_event_id]
        with sessions() as session:
            delivery = session.scalar(select(ConnectorDelivery))
        assert delivery.state == DeliveryState.DELIVERED.value
        assert delivery.delivered_at is not None

    def test_IR2_a_terminal_bridge_failure_is_not_retried_by_the_heartbeat(
        self, sessions, keypair, clock, installation
    ):
        """A dead letter stays a dead letter. Heartbeats must not resurrect one.

        ``claim`` only returns PENDING or expired-CLAIMED rows, so a DEAD_LETTER row is unreachable
        — asserted rather than assumed, because "the retry loop quietly re-tries something an
        operator was told to look at" is the failure this separation exists to prevent.
        """
        calls: list[int] = []

        class _AlwaysTerminal:
            def apply(self, session, envelope):  # noqa: ANN001, ARG002
                calls.append(1)
                raise ConnectApplyError(FailureCategory.MAPPING, "code inconnu")

        client = _runtime_client_with_bridge(
            sessions, keypair, clock, lambda *_a: _AlwaysTerminal()
        )
        private_key, _ = keypair

        envelope = _envelope(installation, payload=CollectionEvent(external_order_id="EXT-1"))
        client.post(
            "/exchange/v1/connect/runtime/submit",
            headers=_auth(_agent_token(private_key)),
            json={"envelope": envelope.model_dump(mode="json")},
        )
        assert len(calls) == 1
        with sessions() as session:
            assert (
                session.scalar(select(ConnectorDelivery)).state == DeliveryState.DEAD_LETTER.value
            )

        clock.advance(timedelta(hours=1))
        _beat(client, _agent_token(private_key))

        assert len(calls) == 1, "a dead letter must not be re-applied by the retry loop"


def _runtime_client_with_bridge(sessions, keypair, clock, bridge_factory) -> TestClient:
    """A runtime router whose inbound applier uses an injected bridge.

    Separate from the ``runtime_client`` fixture because these tests are about what the *applier*
    does with a bridge that fails, and the real bridge reaches the whole Exchange domain — which
    would make the interesting failures hard to produce and the assertions about something else.
    """
    _, public_key = keypair
    app = FastAPI()
    app.include_router(
        build_connect_runtime_router(
            session_factory=sessions,
            runtime_token_validator=build_validator(public_key, audience=RUNTIME_AUDIENCE),
            platform_admin_role=PLATFORM_ADMIN_ROLE,
            bridge_factory=bridge_factory,
            now=clock,
        )
    )
    return TestClient(app)
