"""DH-CONNECT-AGENT-001 §45–§51 — the four endpoints a connector agent talks to.

Its own router, mounted at ``/exchange/v1/connect/runtime``, and its own ``TokenValidator`` with its
own audience. Not added to ``exchange/api.py``: that module is the clinical API for human
principals, and a runtime endpoint living beside it would sooner or later be given a human
dependency by someone reading the file top to bottom.

    POST heartbeat   the agent says it exists and reports on itself
    POST claim       the agent asks for outbound work under a lease
    POST ack         the agent confirms or fails one delivery
    POST submit      the agent sends DHealth an inbound message

**Every one derives the installation from the credential** (§8). There is no ``organization_id`` or
``connector_installation_id`` parameter anywhere in this module's request models -- not validated,
not optional, not present. The one place an organization appears in a request is inside the
canonical envelope, where it is *compared* against the credential's and the message refused on
mismatch (§47). A field that can be compared but never used as input cannot be used to choose a
tenant.

**Version negotiation fails closed** (§49). A contract major mismatch refuses the message before any
clinical processing, with a body saying requalification is required. It does not process and warn:
an agent speaking a contract DHealth does not implement is an agent whose messages DHealth cannot
claim to understand, and understanding them approximately is worse than refusing them.

**No PHI in responses beyond the payload the agent is entitled to.** ``claim`` returns the envelope
the agent must deliver, which is the whole point of the call. Nothing else here returns clinical
content: a heartbeat response carries counts, an ack response carries a state, and the refusal
bodies carry categories and short sentences (§57).
"""

# **No ``from __future__ import annotations`` here, deliberately**, and ``exchange/api.py`` omits it
# for the identical reason. FastAPI resolves a handler's annotations through ``get_type_hints``,
# which only sees module globals -- and the dependency aliases below (``ServicePrincipal``, and the
# ``get_service_principal`` it wraps) are locals of the router factory. Under postponed evaluation
# those annotations are strings FastAPI cannot resolve, so it falls back to treating the parameter
# as a *query parameter* and every request fails with "principal: Field required". It fails loudly,
# but only at request time, and the error names a field no caller has ever heard of.

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    Direction,
    Envelope,
    FailureCategory,
    contract_major,
    message_direction,
    require_aware,
)
from daher_adapter.connect.delivery import (
    DEFAULT_CLAIM_LEASE,
    MAX_CLAIM_BATCH,
    DeliveryClaimLost,
    DeliveryConflict,
    DeliveryRepository,
)
from daher_adapter.connect.inbound import (
    EXCHANGE_APPLIER_INSTANCE_ID,
    BridgeFactory,
    apply_pending_inbound,
    build_default_bridge_factory,
)
from daher_adapter.connect.models import ConnectorInstallation
from daher_adapter.connect.runtime_health import (
    DEFAULT_HEALTH_POLICY,
    RuntimeHealthPolicy,
    aware_utc,
    runtime_health_in_session,
)
from daher_adapter.connect.runtime_models import ConnectorDelivery, ConnectorRuntimeInstance
from daher_adapter.connect.service_identity import (
    ConnectorServicePrincipal,
    InstallationNotUsable,
    NotAServiceCredential,
    resolve_service_principal,
)
from daher_adapter.exchange.identity_auth import TokenValidationError, TokenValidator
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
from daher_adapter.models import utc_now

#: This build's agent protocol revision, distinct from the *contract* version. The contract
#: describes what messages mean; this describes the shape of these four endpoints. They move
#: independently -- adding a heartbeat field is not a change to what a released result is -- and one
#: number covering both would force a requalification for an operational change.
RUNTIME_PROTOCOL_VERSION = "1"


def _refuse_reserved_instance_id(value: str) -> str:
    """The applier's own lease-holder id is reserved. Shared by all three request models.

    DHealth's inbound applier claims work as ``EXCHANGE_APPLIER_INSTANCE_ID``. An agent permitted to
    call itself that could acknowledge DHealth's *own* inbound deliveries -- marking clinical
    messages delivered that nothing had applied, and there would be no second chance, because a
    DELIVERED row is never claimed again. Refused at the boundary rather than trusted.
    """
    if value == EXCHANGE_APPLIER_INSTANCE_ID:
        raise ValueError(f"{value!r} is reserved for DHealth's own applier")
    return value


class HeartbeatRequest(BaseModel):
    """What an agent reports about itself. Facts only -- no health verdict, no patient data.

    ``extra="forbid"``: a field DHealth does not understand is a field the agent believes it
    communicated. And no ``organization_id`` or ``connector_installation_id``: §8. Those come from
    the credential, and a model that cannot express them cannot be talked into honouring them.
    """

    model_config = ConfigDict(extra="forbid")

    runtime_instance_id: str = Field(min_length=1, max_length=64)
    agent_version: str = Field(min_length=1, max_length=32)
    contract_version: str = Field(min_length=1, max_length=16)
    #: The agent's own clock. Recorded for skew, never used to decide liveness (§14).
    reported_at: datetime
    local_queue_depth: int = Field(ge=0)
    local_dead_letters: int = Field(ge=0)
    adapter_reachable: bool

    @field_validator("reported_at")
    @classmethod
    def _offset_aware(cls, value: datetime) -> datetime:
        """§59: no naive datetimes, and the same refusal ``Envelope`` already makes.

        Reusing the contract's own validator rather than writing a second one. An agent in Djibouti
        sending ``14:00`` with no offset is not telling DHealth when anything happened, and storing
        it would make the skew column -- whose entire purpose is to be compared against a real
        instant -- quietly meaningless.
        """
        return require_aware(value)

    _reserved = field_validator("runtime_instance_id")(_refuse_reserved_instance_id)


class HeartbeatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: **Server** receive time, which is what staleness is measured against. Returned so the agent
    #: can see its own skew rather than discovering it in a support conversation.
    received_at: datetime
    health_state: str
    heartbeat_interval_seconds: int
    pending_outbound: int
    contract_version: str
    runtime_protocol_version: str


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_instance_id: str = Field(min_length=1, max_length=64)
    max_messages: int = Field(default=10, ge=1, le=MAX_CLAIM_BATCH)

    _reserved = field_validator("runtime_instance_id")(_refuse_reserved_instance_id)


class ClaimedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: uuid.UUID
    attempt: int
    claim_expires_at: datetime
    envelope: Envelope


class ClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ClaimedMessage]
    #: Whether more work was left behind by the batch bound. Lets an agent poll again immediately
    #: instead of waiting out its interval while a backlog drains -- the difference between a queue
    #: that catches up after an outage and one that trickles.
    more_available: bool


class AckRequest(BaseModel):
    """Confirm or fail exactly one delivery.

    One at a time, deliberately. A batch acknowledgement has to decide what a partial failure means,
    and every answer to that is a way to lose a message: all-or-nothing re-delivers work already
    done, per-item results reintroduce the single-item shape with extra parsing.
    """

    model_config = ConfigDict(extra="forbid")

    runtime_instance_id: str = Field(min_length=1, max_length=64)
    delivery_id: uuid.UUID
    outcome: Literal["DELIVERED", "FAILED"]
    #: Required when failing. ``contract.FailureCategory`` decides retry versus dead letter, and an
    #: agent that could omit it would leave DHealth guessing -- which in practice means retrying a
    #: permanent mapping error forever.
    failure_category: FailureCategory | None = None
    detail: str | None = Field(default=None, max_length=512)

    _reserved = field_validator("runtime_instance_id")(_refuse_reserved_instance_id)


class AckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: uuid.UUID
    state: str
    attempt_count: int
    next_attempt_at: datetime | None


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope: Envelope


class SubmitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: uuid.UUID
    #: ``True`` when this exact event was already queued -- an honest retry, not a new message. Told
    #: to the agent so a connector author can see replay protection working rather than inferring
    # it.
    duplicate: bool
    received_at: datetime


def build_connect_runtime_router(
    *,
    session_factory: sessionmaker[Session],
    runtime_token_validator: TokenValidator,
    platform_admin_role: str,
    health_policy: RuntimeHealthPolicy = DEFAULT_HEALTH_POLICY,
    bridge_factory: BridgeFactory | None = None,
    now: Callable[[], datetime] = utc_now,
) -> APIRouter:
    """The runtime router. ``now`` is injectable so staleness and lease tests need no sleeping.

    ``runtime_token_validator`` must be configured with the **runtime** audience, not the
    Exchange's. Passing the human validator here would make every clinical user token a valid agent
    credential; the two are separate objects precisely so that mistake has to be made explicitly.
    """
    identities = ExchangeIdentityRepository(session_factory)
    deliveries = DeliveryRepository()
    security_audit = ExchangeSecurityAuditRepository()
    # Injectable so a test can watch exactly which domain call an inbound message makes, and so the
    # qualification harness can drive the real delivery path without the Exchange repositories being
    # imported at module scope here.
    bridges = bridge_factory or build_default_bridge_factory(session_factory)

    router = APIRouter(prefix="/exchange/v1/connect/runtime", tags=["connect-runtime"])

    def get_service_principal(
        authorization: Annotated[str | None, Header()] = None,
    ) -> ConnectorServicePrincipal:
        """Authenticate an agent and resolve its installation. The only source of tenancy here.

        The ``ExchangeIdentity`` row is resolved before the principal is built, so the clinical
        repositories that require an actor id have one. Note the ordering: the identity is created
        for any token that validates against the runtime audience, *before* it is known to belong to
        an installation. That is deliberate and matches the human path -- an identity row grants
        nothing, and refusing to record that a credential presented itself would make an
        unrecognised agent invisible.
        """
        try:
            token = runtime_token_validator.validate_header(authorization)
        except TokenValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing credentials",
            ) from error

        record, _ = identities.get_or_create(
            issuer=token.issuer,
            subject=token.subject,
            display_name=token.display_name,
            email=None,
        )

        with session_factory() as session:
            try:
                principal = resolve_service_principal(
                    session,
                    token,
                    identity_id=record.id,
                    platform_admin_role=platform_admin_role,
                )
            except NotAServiceCredential as error:
                # Same status and same message as a bad signature. A caller must not be able to
                # enumerate which OIDC clients are registered connectors by watching this change.
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="invalid or missing credentials",
                ) from error
            except InstallationNotUsable as error:
                # Told plainly, unlike the above: the caller has already proved it holds this
                # installation's credential, so nothing is leaked, and an agent whose installation
                # was suspended should stop retrying rather than hammer a 401 while an operator
                # wonders why nothing drains.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"connector installation is {error.status}",
                ) from error
            return principal

    ServicePrincipal = Annotated[ConnectorServicePrincipal, Depends(get_service_principal)]

    def _installation(
        session: Session, principal: ConnectorServicePrincipal
    ) -> ConnectorInstallation:
        installation = session.get(ConnectorInstallation, principal.installation_id)
        if installation is None:  # pragma: no cover - FK RESTRICT makes this unreachable
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="connector installation is missing"
            )
        return installation

    def _refuse(
        session: Session,
        principal: ConnectorServicePrincipal,
        *,
        code: int,
        detail: str,
    ) -> HTTPException:
        """Audit a boundary refusal and return the exception to raise.

        Recorded as ``connector_message_refused`` with outcome ``refused``, naming the installation
        and no payload -- §64 forbids PHI in audit rows, and the refusal reason is a category, not a
        message excerpt. The commit happens here because the refusal is the only thing this request
        will have done: there is no mutation for it to ride along with (§47's "reject / audit / no
        mutation").
        """
        # Rollback first. On the binding-check path nothing has been written and this is a no-op; on
        # the conflict path a failed flush has left the session unusable, and recording the refusal
        # into it would fail and mask the refusal itself.
        session.rollback()
        security_audit.record(
            session,
            event_type="connector_message_refused",
            actor_identity_id=principal.identity_id,
            organization_id=principal.organization_id,
            resource_type="connector_installation",
            resource_id=principal.installation_id,
            outcome="refused",
        )
        session.commit()
        return HTTPException(status_code=code, detail=detail)

    def _check_envelope_binding(
        session: Session, principal: ConnectorServicePrincipal, envelope: Envelope
    ) -> None:
        """§47 and §49, in that order, before anything clinical happens.

        The organization check runs first because a cross-tenant message is a security event
        whatever contract it claims, and reporting "wrong contract version" for a message aimed at
        somebody else's facility would be an accurate answer to the wrong question.

        Note what is *not* here: no reconciliation, no "use the credential's organization instead".
        §47 forbids a caller-supplied override, and quietly substituting the right value would
        accept a message whose sender is confused about which facility it serves -- a confusion
        that, left uncorrected, eventually sends a result to the wrong one.
        """
        if envelope.organization_id != principal.organization_id:
            raise _refuse(
                session,
                principal,
                code=status.HTTP_409_CONFLICT,
                detail=(
                    "envelope organization does not match the authenticated installation; "
                    "the installation is never taken from the message"
                ),
            )
        if envelope.connector_installation_id != principal.installation_id:
            raise _refuse(
                session,
                principal,
                code=status.HTTP_409_CONFLICT,
                detail=(
                    "envelope connector installation does not match the authenticated credential"
                ),
            )
        try:
            compatible = contract_major(envelope.contract_version) == contract_major(
                CONTRACT_VERSION
            )
        except ValueError:
            compatible = False
        if not compatible:
            raise _refuse(
                session,
                principal,
                code=status.HTTP_409_CONFLICT,
                detail=(
                    f"contract version {envelope.contract_version!r} is not compatible with "
                    f"{CONTRACT_VERSION}; requalification is required"
                ),
            )
        if message_direction(envelope.payload.message_type) is not Direction.CONNECTOR_TO_DHEALTH:
            raise _refuse(
                session,
                principal,
                code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{envelope.payload.message_type.value} travels from DHealth to the connector "
                    "and may not be submitted by one"
                ),
            )

    @router.post("/heartbeat", response_model=HeartbeatResponse)
    def heartbeat(body: HeartbeatRequest, principal: ServicePrincipal) -> HeartbeatResponse:
        """Record that this agent is alive, and tell it what DHealth now thinks.

        **Not audited into the security table** (§64). A heartbeat is operational telemetry, and one
        audit row per beat would bury the handful of security events under its own noise. The
        exception is the *first* sighting of a runtime instance, which is a new principal appearing
        and is recorded once.

        The response carries the server's receive time rather than echoing the agent's. An agent
        whose clock is wrong learns it here instead of in a support conversation about why it keeps
        being declared stale.
        """
        received_at = now()
        with session_factory() as session:
            installation = _installation(session, principal)
            instance = session.scalar(
                select(ConnectorRuntimeInstance).where(
                    ConnectorRuntimeInstance.connector_installation_id == installation.id,
                    ConnectorRuntimeInstance.runtime_instance_id == body.runtime_instance_id,
                )
            )
            if instance is None:
                instance = ConnectorRuntimeInstance(
                    connector_installation_id=installation.id,
                    runtime_instance_id=body.runtime_instance_id,
                    agent_version=body.agent_version,
                    contract_version=body.contract_version,
                    first_seen_at=received_at,
                    last_seen_at=received_at,
                    reported_at=body.reported_at,
                    heartbeat_count=1,
                    local_queue_depth=body.local_queue_depth,
                    local_dead_letters=body.local_dead_letters,
                    adapter_reachable=body.adapter_reachable,
                )
                session.add(instance)
                security_audit.record(
                    session,
                    event_type="connector_runtime_connected",
                    actor_identity_id=principal.identity_id,
                    organization_id=principal.organization_id,
                    resource_type="connector_installation",
                    resource_id=installation.id,
                    outcome="created",
                )
            else:
                # A heartbeat that arrives out of order must not move anything backwards. Counting
                # it is right -- it happened -- but its *facts* are older than what is already
                # stored, and letting them win would report a queue depth and an adapter state from
                # before the newer beat. That is the one direction this endpoint could invent bad
                # news in: a late beat saying "adapter unreachable" would degrade a facility that
                # has since recovered.
                instance.heartbeat_count += 1
                if received_at >= aware_utc(instance.last_seen_at):
                    instance.agent_version = body.agent_version
                    instance.contract_version = body.contract_version
                    instance.last_seen_at = received_at
                    instance.reported_at = body.reported_at
                    instance.local_queue_depth = body.local_queue_depth
                    instance.local_dead_letters = body.local_dead_letters
                    instance.adapter_reachable = body.adapter_reachable
            session.flush()

            health = runtime_health_in_session(
                session, installation, now=received_at, policy=health_policy
            )
            pending = deliveries.queue_depth(
                session,
                installation_id=installation.id,
                direction=Direction.DHEALTH_TO_CONNECTOR,
            )
            session.commit()

        # **The heartbeat is also DHealth's inbound retry clock**, and it has to be: an inbound
        # message whose bridge application failed retryably backs off to a later `next_attempt_at`
        # and then has nothing to wake it. `submit` only drains what it just enqueued, so without
        # this a message that failed once would sit PENDING with its attempt time in the past
        # forever -- durable, and never delivered, which is the worst of both.
        #
        # Driving it from the heartbeat makes the agent's own cadence the retry cadence, which is
        # the honest arrangement: no agent means no inbound traffic, so there is nothing to retry.
        # Bounded, and after the commit above, so a slow bridge cannot hold the heartbeat's
        # transaction open.
        apply_pending_inbound(
            session_factory,
            installation_id=principal.installation_id,
            organization_id=principal.organization_id,
            identity_id=principal.identity_id,
            bridge_factory=bridges,
            deliveries=deliveries,
            now=now,
        )

        return HeartbeatResponse(
            received_at=received_at,
            health_state=health.state.value,
            heartbeat_interval_seconds=int(health_policy.heartbeat_interval.total_seconds()),
            pending_outbound=pending,
            contract_version=CONTRACT_VERSION,
            runtime_protocol_version=RUNTIME_PROTOCOL_VERSION,
        )

    @router.post("/claim", response_model=ClaimResponse)
    def claim(body: ClaimRequest, principal: ServicePrincipal) -> ClaimResponse:
        """Hand this agent a bounded batch of outbound work under a lease.

        Outbound only, and not as a parameter: an agent cannot ask to claim the messages it itself
        submitted. Direction is decided here from the caller's *kind*, not from the request.
        """
        moment = now()
        with session_factory() as session:
            installation = _installation(session, principal)
            rows = deliveries.claim(
                session,
                installation_id=installation.id,
                direction=Direction.DHEALTH_TO_CONNECTOR,
                runtime_instance_id=body.runtime_instance_id,
                limit=body.max_messages,
                lease=DEFAULT_CLAIM_LEASE,
                now=moment,
            )
            messages = [
                ClaimedMessage(
                    delivery_id=row.id,
                    attempt=row.attempt_count,
                    claim_expires_at=row.claim_expires_at or moment,
                    envelope=Envelope.model_validate_json(row.payload),
                )
                for row in rows
            ]
            session.commit()
            remaining = len(rows) == body.max_messages
        return ClaimResponse(messages=messages, more_available=remaining)

    @router.post("/ack", response_model=AckResponse)
    def ack(body: AckRequest, principal: ServicePrincipal) -> AckResponse:
        """Confirm or fail one claimed delivery.

        The delivery is fetched **and** checked to belong to this installation before anything else.
        A delivery id is a guessable-shaped identifier from another facility's queue, and resolving
        it without that check would let one connector acknowledge another's clinical work -- which
        would mark it delivered and ensure nobody ever delivered it.
        """
        moment = now()
        with session_factory() as session:
            delivery = session.get(ConnectorDelivery, body.delivery_id)
            if delivery is None or delivery.connector_installation_id != principal.installation_id:
                # 404 for both cases, following the Exchange's anti-enumeration convention: "not
                # yours" must be indistinguishable from "does not exist".
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="unknown delivery"
                )

            try:
                if body.outcome == "DELIVERED":
                    updated = deliveries.mark_delivered(
                        session,
                        delivery=delivery,
                        runtime_instance_id=body.runtime_instance_id,
                        now=moment,
                    )
                else:
                    if body.failure_category is None:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="a failed delivery must state a failure category",
                        )
                    updated = deliveries.mark_failed(
                        session,
                        delivery=delivery,
                        runtime_instance_id=body.runtime_instance_id,
                        category=body.failure_category,
                        detail=body.detail,
                        now=moment,
                    )
            except DeliveryClaimLost as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="this delivery is no longer held by that runtime instance",
                ) from error

            response = AckResponse(
                delivery_id=updated.id,
                state=updated.state,
                attempt_count=updated.attempt_count,
                next_attempt_at=updated.next_attempt_at,
            )
            session.commit()
        return response

    @router.post("/submit", response_model=SubmitResponse)
    def submit(body: SubmitRequest, principal: ServicePrincipal) -> SubmitResponse:
        """Take durable responsibility for one inbound message.

        Durable *first*, applied later. The response says "we have this", not "we have acted on it",
        and those are different promises: applying a clinical message inside the request would mean
        a timeout after the mutation left the agent believing it must retry something already done.
        The row is the receipt; the bridge applies it from there.
        """
        moment = now()
        with session_factory() as session:
            _check_envelope_binding(session, principal, body.envelope)
            try:
                delivery, created = deliveries.enqueue(
                    session,
                    installation_id=principal.installation_id,
                    envelope=body.envelope,
                    expected_direction=Direction.CONNECTOR_TO_DHEALTH,
                    now=moment,
                )
            except DeliveryConflict as error:
                raise _refuse(
                    session,
                    principal,
                    code=status.HTTP_409_CONFLICT,
                    detail=(
                        "this source event is already recorded with different content; "
                        "DHealth does not overwrite a clinical event"
                    ),
                ) from error
            response = SubmitResponse(
                delivery_id=delivery.id, duplicate=not created, received_at=moment
            )
            session.commit()

        # Apply *after* the enqueue has committed, in its own transaction. The order is the whole
        # guarantee: if this process dies right here, the message is still durably held and the next
        # drain applies it. Applying inside the enqueue transaction would mean a crash between the
        # two left DHealth with neither the row nor the mutation.  The response says "durably
        # received", not "applied", and stays that way even when the apply below fails -- because it
        # is true, and because an agent told "rejected" for a message DHealth is holding and will
        # retry would resend it forever.
        if created:
            apply_pending_inbound(
                session_factory,
                installation_id=principal.installation_id,
                organization_id=principal.organization_id,
                identity_id=principal.identity_id,
                bridge_factory=bridges,
                deliveries=deliveries,
                now=now,
            )
        return response

    return router
