"""DH-CONNECT-AGENT-001 — one order submission through the whole runtime, with nothing faked.

``test_connect_order_submission.py`` drives ``ConnectBridge`` directly, which is the right shape for
the refusal cases: they are decisions the bridge makes, and calling it directly is how each one gets
named. It is the wrong shape for the question this file asks, which is whether the pieces are
actually connected to each other.

Three specific things only this test can see, all of which were wrong when it was written:

* **The production bridge factory had no provider repositories.** Every submission through the real
  endpoint failed regardless of what the bridge was capable of, because the factory never handed
  it the objects that do the work.
* **The patient reference opened its own transaction.** A second connection while the applier's
  session already held one -- invisible under the unit tests, whose receipt store never touched the
  database before that call.
* **Nothing bound a correlation for a submission.** The order existed and the EMR's own identifier
  for it did not, which is an order whose results can never be filed back into the system that
  asked for them.

So this exercises the real path end to end: a real service credential, ``/submit``, the durable
delivery row, the production bridge factory, and the applier that runs inside the request.
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
from exchange_support import make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    Envelope,
    OrderSubmission,
    ScopedExternalId,
    SourcePractitionerRef,
    fingerprint,
)
from daher_adapter.connect.repository import ConnectorInstallationRepository
from daher_adapter.connect.runtime_api import build_connect_runtime_router
from daher_adapter.connect.runtime_models import ConnectorCorrelation, ConnectorDelivery
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import LabOrder, LabOrderItem, ParticipationMode
from daher_adapter.exchange.organizations import OrganizationRepository

RUNTIME_AUDIENCE = "dhealth-connect-runtime"
AGENT_CLIENT = "connect-agent-clinique"
SOURCE_OCCURRED_AT = datetime(2026, 9, 23, 7, 30, tzinfo=UTC)


@pytest.fixture
def provider_world(sessions: sessionmaker[Session]):
    """A CONNECT-enrolled **provider** facility whose agent holds a bound service credential.

    A provider rather than a laboratory, because ``LabOrderRepository.create`` calls
    ``require_type(..., PROVIDER)`` and a laboratory submitting an order is refused one layer below
    this file -- covered separately; here it would only obscure the path being tested.
    """
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    # ``operational=False`` because the default enrols the facility as NATIVE, and a facility cannot
    # be both. It also leaves the provider with no CLINICIAN membership, which is the honest shape
    # of a CONNECT facility: the people ordering work there do it in their own EMR and have no
    # DHealth account at all. Nothing on this path asks for one -- the connector's service identity
    # is the actor, which is the whole point of §7.
    provider = make_provider(organizations, "Clinique de la Paix", operational=False)
    FacilityEnrollmentRepository(sessions).create(
        organization_id=provider.id, participation_mode=ParticipationMode.CONNECT
    )
    glucose, _ = catalog.create_entry(code="GLU", display_name="Glycémie à jeun")

    actor, _ = ExchangeIdentityRepository(sessions).get_or_create(
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
        actor_identity_id=actor.id,
    )
    installations.set_service_identity(
        installation_id=record.id, service_client_id=AGENT_CLIENT, actor_identity_id=actor.id
    )
    return {
        "provider_id": provider.id,
        "installation_id": record.id,
        "glucose_id": glucose.id,
    }


@pytest.fixture
def runtime(sessions, provider_world):  # noqa: ARG001 -- the world must exist before the router
    private_key, public_key = generate_keypair()
    router = build_connect_runtime_router(
        session_factory=sessions,
        runtime_token_validator=build_validator(public_key, audience=RUNTIME_AUDIENCE),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
    )
    app = FastAPI()
    app.include_router(router)
    token = make_token(
        private_key,
        subject=f"service-account-{AGENT_CLIENT}",
        audience=RUNTIME_AUDIENCE,
        roles=[],
        extra_claims={"azp": AGENT_CLIENT},
    )
    return TestClient(app), {"Authorization": f"Bearer {token}"}


def _submission_envelope(provider_world, *, codes=("GLU",)) -> dict:
    org = provider_world["provider_id"]
    envelope = Envelope(
        contract_version=CONTRACT_VERSION,
        message_id=uuid.uuid4(),
        connector_installation_id=provider_world["installation_id"],
        organization_id=org,
        source_system="emr",
        source_event_id="emr-evt-1",
        source_occurred_at=SOURCE_OCCURRED_AT,
        sent_at=SOURCE_OCCURRED_AT + timedelta(seconds=1),
        lab_order_id=None,
        payload=OrderSubmission(
            patient=ScopedExternalId(
                source_organization_id=org,
                source_system="emr",
                source_identity_system="urn:emr:mrn",
                source_identity_value="MRN-42",
            ),
            practitioner=SourcePractitionerRef(
                source_organization_id=org,
                source_system="emr",
                source_identity_system="urn:emr:staff",
                source_identity_value="DR-7",
            ),
            external_order_id="EMR-ORDER-1",
            source_test_codes=codes,
        ),
    )
    return envelope.model_dump(mode="json")


def test_OS10_a_submission_through_the_real_endpoint_creates_an_order_and_its_correlation(
    sessions, provider_world, runtime
):
    """The whole path: credential, submit, durable row, production bridge, applier, correlation.

    The correlation assertion is not a detail. Without it the order exists and the EMR's own name
    for it does not, so when the laboratory releases a result there is no way to tell that EMR which
    of *its* orders the result belongs to -- and a result that cannot be filed is a result that was
    not delivered, whatever the delivery table says.
    """
    client, auth = runtime
    response = client.post(
        "/exchange/v1/connect/runtime/submit",
        headers=auth,
        json={"envelope": _submission_envelope(provider_world)},
    )
    assert response.status_code == 200, response.text

    with sessions() as session:
        orders = session.scalars(select(LabOrder)).all()
        items = session.scalars(select(LabOrderItem)).all()
        correlations = session.scalars(select(ConnectorCorrelation)).all()
        deliveries = session.scalars(select(ConnectorDelivery)).all()

    assert len(orders) == 1, "the submission must have created exactly one DHealth order"
    assert orders[0].provider_organization_id == provider_world["provider_id"]
    assert [item.catalog_entry_id for item in items] == [provider_world["glucose_id"]]

    assert len(correlations) == 1, "the EMR's own order id must be bound to the DHealth order"
    assert correlations[0].external_id == "EMR-ORDER-1"
    assert correlations[0].lab_order_id == orders[0].id

    assert [d.state for d in deliveries] == ["DELIVERED"]


def test_OS11_a_resubmitted_order_is_applied_once_through_the_real_path(
    sessions, provider_world, runtime
):
    """At-least-once transport, through the real endpoint rather than against a fake receipt store.

    The unit tests cover this against ``InMemoryReceiptStore``; the thing that has to hold in
    production is ``DurableReceiptStore`` bound to the applier's own session, and only a second
    request proves it was written where the next request can see it.
    """
    client, auth = runtime
    body = _submission_envelope(provider_world)
    first = client.post(
        "/exchange/v1/connect/runtime/submit", headers=auth, json={"envelope": body}
    )
    # A resend carries a new message id and the same source event -- what an agent that never saw
    # the first response actually sends.
    resent = dict(body, message_id=str(uuid.uuid4()))
    second = client.post(
        "/exchange/v1/connect/runtime/submit", headers=auth, json={"envelope": resent}
    )
    assert first.status_code == 200 and second.status_code == 200

    with sessions() as session:
        orders = session.scalars(select(LabOrder)).all()
        correlations = session.scalars(select(ConnectorCorrelation)).all()
    assert len(orders) == 1, "one clinical order, however many times the agent asked"
    assert len(correlations) == 1
