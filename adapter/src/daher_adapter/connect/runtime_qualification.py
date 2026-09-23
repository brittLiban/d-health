"""DH-CONNECT-AGENT-001 §40–§41 — proving ``ORDER_RECEIPT`` by actually delivering an order.

§41 is unusually specific about what would make this invalid:

    Invalid qualification: call ``synthetic_adapter.handle_order(order)`` directly and mark
    ORDER_RECEIPT exercised. That bypasses the runtime this ticket is supposed to prove.

So nothing here touches an adapter. The driver puts a real ``ORDER_NOTIFICATION`` into the real
central outbox, runs a real agent cycle over the real runtime API, and then checks three independent
places -- the partner side, the DHealth side, and the agent's own durable record. A pass requires
all three, because each one alone could be true while the path is broken:

    the adapter has the order          ... but DHealth might never have been told it arrived
    the central row is DELIVERED       ... but the agent might have acknowledged without delivering
    the local inbox row is ACKNOWLEDGED ... but that is the agent agreeing with itself

**Why this is a separate phase, committed, run before the harness opens its session.** The harness
executes its scenarios inside one long transaction. The agent commits through its own sessions
against the same database, and on SQLite the harness's shared read lock and the agent's exclusive
write lock deadlock each other -- and even on PostgreSQL the harness's session would not see the
agent's committed row. The ordering is therefore not a style preference: run the delivery first, to
completion, and hand the finished result into the harness.

**The runner is injected, never constructed here.** Building one needs an HTTP client bound to a
running app, which in tests means ``starlette.testclient`` -- a test dependency that has no business
being imported by ``src/``. The caller assembles the agent; this module drives it and judges it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    Capability,
    Direction,
    Envelope,
    OrderNotification,
    ScopedExternalId,
    SourcePractitionerRef,
)
from daher_adapter.connect.delivery import DeliveryRepository
from daher_adapter.connect.models import EvidenceLevel
from daher_adapter.connect.runtime_models import ConnectorDelivery, DeliveryState


#: How the qualification's own order notification is identified. Deterministic in the order id, so
#: re-running the suite against the same order converges on one delivery rather than accumulating
#: one per attempt -- the same idempotency the production hook relies on.
def qualification_event_id(lab_order_id: uuid.UUID) -> str:
    return f"order-notification:{lab_order_id}"


@dataclass(frozen=True, slots=True)
class OrderReceiptEvidence:
    """What the drive observed, in the three places that have to agree.

    Kept as separate booleans rather than one verdict because the *pattern* of failure is the
    diagnosis: adapter-yes/central-no is a lost acknowledgement, adapter-no/central-yes is an agent
    confirming work it did not do, and the second is far more serious than the first.
    """

    delivered_to_adapter: bool
    central_state: str | None
    locally_acknowledged: bool
    detail: str | None = None

    @property
    def passed(self) -> bool:
        return (
            self.delivered_to_adapter
            and self.central_state == DeliveryState.DELIVERED.value
            and self.locally_acknowledged
        )


#: Answers "does the partner's system have this order?" without this module knowing what a partner
#: system is. The caller closes over its adapter; §3's protocol-neutrality survives.
AdapterHasOrder = Callable[[uuid.UUID], bool]

#: Answers "is this delivery acknowledged in the agent's own store?". Same reasoning.
LocallyAcknowledged = Callable[[], bool]


def enqueue_qualification_order(
    session: Session,
    *,
    installation_id: uuid.UUID,
    organization_id: uuid.UUID,
    lab_order_id: uuid.UUID,
    source_system: str = "dhealth",
    now: datetime | None = None,
) -> ConnectorDelivery:
    """Put one order notification into the central outbox, exactly as the production hook does.

    Deliberately the same call the Exchange makes when an order is assigned --
    ``DeliveryRepository`` with ``expected_direction=DHEALTH_TO_CONNECTOR`` -- rather than a
    hand-built row. A qualification that inserted its own row would prove the agent can drain a
    queue, not that DHealth can fill one.
    """
    moment = now or datetime.now(UTC)
    envelope = Envelope(
        contract_version=CONTRACT_VERSION,
        message_id=uuid.uuid4(),
        connector_installation_id=installation_id,
        organization_id=organization_id,
        source_system=source_system,
        source_event_id=qualification_event_id(lab_order_id),
        source_occurred_at=moment,
        sent_at=moment,
        lab_order_id=lab_order_id,
        payload=OrderNotification(
            patient=ScopedExternalId(
                source_organization_id=organization_id,
                source_system="dhealth",
                source_identity_system="urn:dhealth:qualification",
                source_identity_value=f"qualification-{lab_order_id}",
            ),
            practitioner=SourcePractitionerRef(
                source_organization_id=organization_id,
                source_system="dhealth",
                source_identity_system="urn:dhealth:qualification",
                source_identity_value="qualification-practitioner",
            ),
            catalog_codes=("GLU",),
        ),
    )
    delivery, _ = DeliveryRepository().enqueue(
        session,
        installation_id=installation_id,
        envelope=envelope,
        expected_direction=Direction.DHEALTH_TO_CONNECTOR,
        now=moment,
    )
    return delivery


def drive_order_receipt(
    sessions: sessionmaker[Session],
    *,
    installation_id: uuid.UUID,
    organization_id: uuid.UUID,
    lab_order_id: uuid.UUID,
    run_cycle: Callable[[], object],
    adapter_has_order: AdapterHasOrder,
    locally_acknowledged: LocallyAcknowledged,
    cycles: int = 2,
) -> OrderReceiptEvidence:
    """Queue an order, run the agent, and report what the three sides say.

    ``cycles`` defaults to two because one is not always enough and the reason is worth keeping: if
    the acknowledgement is lost, the agent owes it and pays it at the *start* of the following
    cycle. Two cycles therefore covers the ordinary case and the recovery case identically, and a
    driver that ran once would report a spurious failure for a path that is working exactly as
    designed.

    Each cycle is its own committed unit. Nothing here holds a session open across the agent's work,
    because the agent commits on its own sessions against the same database.
    """
    with sessions.begin() as session:
        enqueue_qualification_order(
            session,
            installation_id=installation_id,
            organization_id=organization_id,
            lab_order_id=lab_order_id,
        )

    for _ in range(cycles):
        run_cycle()

    with sessions() as session:
        row = session.scalar(
            select(ConnectorDelivery).where(
                ConnectorDelivery.connector_installation_id == installation_id,
                ConnectorDelivery.source_event_id == qualification_event_id(lab_order_id),
            )
        )
        central_state = None if row is None else row.state
        failure_detail = None if row is None else row.last_failure_detail

    delivered = adapter_has_order(lab_order_id)
    acknowledged = locally_acknowledged()

    detail: str | None = None
    if not delivered:
        detail = (
            failure_detail
            or "la commande n'a pas atteint le système source via le chemin de livraison durable"
        )
    elif central_state != DeliveryState.DELIVERED.value:
        detail = f"livraison non confirmée à DHealth (état {central_state})"
    elif not acknowledged:
        detail = "l'agent n'a pas enregistré l'accusé de réception localement"

    return OrderReceiptEvidence(
        delivered_to_adapter=delivered,
        central_state=central_state,
        locally_acknowledged=acknowledged,
        detail=detail,
    )


def order_receipt_scenario(evidence: OrderReceiptEvidence):
    """Turn the drive into the scenario result the harness records.

    ``EXERCISED`` **whether it passed or failed**, and that is the point of the distinction:
    evidence level says how the claim was tested, not whether the test succeeded. A connector that
    was driven and refused the order has been exercised and has failed; recording that as
    ``DECLARED`` would lose the fact that DHealth actually tried.
    """
    from daher_adapter.connect.harness import ScenarioResult

    return ScenarioResult(
        scenario="LQ1-order-receipt-delivered",
        capability=Capability.ORDER_RECEIPT,
        evidence_level=EvidenceLevel.EXERCISED,
        passed=evidence.passed,
        detail=evidence.detail,
        limitation=(
            "exercée contre l'adaptateur synthétique : aucun système partenaire réel n'a reçu "
            "cette commande"
        ),
    )
