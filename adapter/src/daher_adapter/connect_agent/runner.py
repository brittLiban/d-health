"""DH-CONNECT-AGENT-001 §28–§30, §74–§79 — one poll cycle, and what survives a crash inside it.

The cycle, in the order it must happen:

    1. drain owed acknowledgements   work already done locally that DHealth has not been told about
    2. heartbeat                     report facts; learn what DHealth currently thinks
    3. claim                         take a bounded batch of outbound work under a lease
    4. for each message:             persist -> hand to adapter -> record receipt -> acknowledge
    5. poll the adapter              whatever the partner's system produced
    6. submit                        each outbound event, persisted before it is sent

**Step 1 comes first, and that ordering is the recovery story.** An agent that crashed between
handing a message to its LIS and acknowledging it restarts owing an ACK for work already done. If
the cycle began by claiming, DHealth's lease would expire, the message would be redelivered, and the
local receipt would (correctly) make the adapter call a no-op -- but the agent would spend every
cycle re-fetching the same message. Draining first closes the loop immediately.

**Persist before acting, acknowledge after.** §75. Each of the three orderings below is chosen so
that every crash window loses nothing:

    crash after claim, before persist    DHealth's lease expires, redelivers. Nothing lost.
    crash after persist, before adapter  the row is RECEIVED; the next cycle retries the adapter.
    crash after adapter, before receipt  the receipt is written in the same transaction as the
                                         state change, so this window does not exist.
    crash after receipt, before ACK      the row is HANDLED; step 1 drains it. The adapter is not
                                         called again because the receipt says it was.

**Nothing here is vendor-specific** (§3). The runner knows about envelopes, deliveries and the
adapter protocol. It does not know what a FHIR bundle is, and there is no branch anywhere below on
``connector_key``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    Direction,
    Envelope,
    FailureCategory,
    MessageType,
    message_direction,
)
from daher_adapter.connect_agent import AGENT_VERSION
from daher_adapter.connect_agent.adapter import (
    AdapterEvent,
    AdapterOrder,
    AdapterRefused,
    AdapterUnavailable,
    ConnectorAdapter,
)
from daher_adapter.connect_agent.client import (
    ConnectRuntimeClient,
    RuntimeApiError,
    RuntimeUnreachable,
)
from daher_adapter.connect_agent.store import AgentStore, InboxRow

logger = logging.getLogger("daher_adapter.connect_agent.runner")

#: How many outbound messages one cycle claims. Bounded for the reason §25 gives on the server side:
#: a batch held under one lease is a batch that waits out that lease if this process dies.
DEFAULT_CLAIM_SIZE = 10


@dataclass
class CycleReport:
    """What one cycle did. Counts and categories -- never payloads, never patient data (§54, §57).

    Returned rather than logged from inside, so the caller decides what to record and the tests can
    assert on structure instead of on log text.
    """

    heartbeat_health: str | None = None
    acknowledged_backlog: int = 0
    claimed: int = 0
    handled: int = 0
    duplicates: int = 0
    failed: int = 0
    submitted: int = 0
    errors: list[str] = field(default_factory=list)


class AgentRunner:
    """One agent, one adapter, one store, one client. Single-threaded by design.

    ``organization_id`` and ``installation_id`` are carried because an outbound-event envelope must
    name them -- and they are **configuration the agent was given, not authority it asserts**. The
    server ignores both in favour of the credential and refuses the message if they disagree (§8,
    §47), which is what makes a misconfigured agent fail loudly here rather than act for somebody
    else's facility.
    """

    def __init__(
        self,
        *,
        store: AgentStore,
        client: ConnectRuntimeClient,
        adapter: ConnectorAdapter,
        organization_id: uuid.UUID,
        installation_id: uuid.UUID,
        source_system: str,
        claim_size: int = DEFAULT_CLAIM_SIZE,
    ) -> None:
        self._store = store
        self._client = client
        self._adapter = adapter
        self._organization_id = organization_id
        self._installation_id = installation_id
        self._source_system = source_system
        self._claim_size = claim_size
        self._runtime_instance_id = store.runtime_instance_id()

    @property
    def runtime_instance_id(self) -> str:
        return self._runtime_instance_id

    def run_once(self) -> CycleReport:
        """One complete cycle. Never raises for an ordinary failure -- it records and continues.

        A cycle that propagated the first network error would stop the agent from draining its
        outbox because its heartbeat failed, which is precisely backwards: the parts of this cycle
        are independent, and one being unavailable is not a reason to skip the others.
        """
        report = CycleReport()
        self._drain_acknowledgements(report)
        self._heartbeat(report)
        self._receive(report)
        self._send(report)
        return report

    # -- 1. owed acknowledgements -------------------------------------------------------------

    def _drain_acknowledgements(self, report: CycleReport) -> None:
        """Work the adapter already did, that DHealth has not been told about. §74."""
        for row in self._store.unacknowledged():
            try:
                self._client.acknowledge(
                    runtime_instance_id=self._runtime_instance_id,
                    delivery_id=row.delivery_id,
                    outcome="DELIVERED",
                )
            except (RuntimeApiError, RuntimeUnreachable) as error:
                # Including a 409 "no longer held by that instance": the lease expired and another
                # instance took it, which means DHealth will have it delivered by somebody. Marking
                # it acknowledged locally is right either way -- this agent has nothing left to do
                # with it, and leaving it would make the row a permanent resident of every cycle.
                if isinstance(error, RuntimeApiError) and not error.retryable:
                    self._store.mark_acknowledged(row.delivery_id)
                report.errors.append(f"ack:{type(error).__name__}")
                continue
            self._store.mark_acknowledged(row.delivery_id)
            report.acknowledged_backlog += 1

    # -- 2. heartbeat -------------------------------------------------------------------------

    def _heartbeat(self, report: CycleReport) -> None:
        try:
            reachable = self._adapter.probe()
        except Exception:  # noqa: BLE001 -- an adapter that raises on probe is an unreachable one
            reachable = False
        try:
            ack = self._client.heartbeat(
                runtime_instance_id=self._runtime_instance_id,
                agent_version=AGENT_VERSION,
                contract_version=CONTRACT_VERSION,
                queue_depth=self._store.queue_depth(),
                dead_letters=self._store.undeliverable_count(),
                adapter_reachable=reachable,
            )
        except (RuntimeApiError, RuntimeUnreachable) as error:
            report.errors.append(f"heartbeat:{type(error).__name__}")
            return
        report.heartbeat_health = ack.health_state
        if ack.contract_version != CONTRACT_VERSION:
            # Reported, not acted upon. §49's fail-closed decision belongs to the server, which
            # refuses incompatible messages at the boundary; an agent that unilaterally stopped
            # working on a *minor* difference would take a facility offline for a compatible change.
            report.errors.append(f"contract:server={ack.contract_version}")

    # -- 3/4. inbound -------------------------------------------------------------------------

    def _receive(self, report: CycleReport) -> None:
        # Anything persisted but not yet handled -- a crash between persist and adapter -- is
        # retried before new work is claimed, so a stuck message cannot be overtaken indefinitely.
        for row in self._store.pending_inbound():
            self._deliver_to_adapter(row, report)

        try:
            claimed = self._client.claim(
                runtime_instance_id=self._runtime_instance_id, max_messages=self._claim_size
            )
        except (RuntimeApiError, RuntimeUnreachable) as error:
            report.errors.append(f"claim:{type(error).__name__}")
            return

        for message in claimed.get("messages", []):
            envelope_data = message["envelope"]
            try:
                envelope = Envelope.model_validate(envelope_data)
            except ValueError as error:
                # A message this agent cannot parse is terminal for this agent: the same bytes will
                # not parse next time. Told to DHealth as VALIDATION so it dead-letters in front of
                # an operator rather than being retried against a build that cannot read it.
                self._reject(
                    message["delivery_id"],
                    FailureCategory.VALIDATION,
                    f"envelope did not validate: {type(error).__name__}",
                    report,
                )
                continue

            if message_direction(envelope.payload.message_type) is not (
                Direction.DHEALTH_TO_CONNECTOR
            ):
                # The agent's own half of §45. The server enforces the other half; an agent that
                # accepted an inbound-only type here would be relying on the server never being
                # wrong, which is not a property either side should assume of the other.
                self._reject(
                    message["delivery_id"],
                    FailureCategory.UNSUPPORTED_WORKFLOW,
                    f"{envelope.payload.message_type.value} does not travel to a connector",
                    report,
                )
                continue

            fresh = self._store.record_inbound(
                delivery_id=message["delivery_id"],
                message_type=envelope.payload.message_type.value,
                source_event_id=envelope.source_event_id,
                replay_scope=envelope.replay_scope(),
                envelope_json=envelope.model_dump_json(),
                attempt_count=int(message.get("attempt", 0)),
            )
            report.claimed += 1
            if not fresh:
                report.duplicates += 1
            self._deliver_to_adapter(
                InboxRow(
                    delivery_id=message["delivery_id"],
                    message_type=envelope.payload.message_type.value,
                    source_event_id=envelope.source_event_id,
                    replay_scope=envelope.replay_scope(),
                    envelope_json=envelope.model_dump_json(),
                    state="RECEIVED",
                    attempt_count=int(message.get("attempt", 0)),
                    failure_category=None,
                    failure_detail=None,
                ),
                report,
            )

    def _deliver_to_adapter(self, row: InboxRow, report: CycleReport) -> None:
        """Hand one persisted message to the adapter, then acknowledge it.

        The replay check comes first and is the local half of exactly-once. DHealth redelivers after
        any lost acknowledgement, and without this the partner's LIS would receive the order a
        second time -- which for a laboratory means a second specimen request for a patient who has
        already been drawn.
        """
        envelope = Envelope.model_validate_json(row.envelope_json)
        fingerprint = envelope.content_fingerprint()
        seen = self._store.already_applied(row.replay_scope, row.source_event_id)
        if seen == fingerprint:
            self._store.mark_handled(
                row.delivery_id,
                replay_scope=row.replay_scope,
                source_event_id=row.source_event_id,
                fingerprint=fingerprint,
            )
            self._acknowledge(row.delivery_id, report)
            return
        if seen is not None:
            # Same event id, different content. The partner is contradicting DHealth, or DHealth is
            # contradicting itself; either way the agent must not overwrite what it already applied.
            self._reject(
                row.delivery_id,
                FailureCategory.CONFLICT,
                "this source event was already applied with different content",
                report,
            )
            return

        try:
            if envelope.payload.message_type is MessageType.ORDER_NOTIFICATION:
                self._adapter.handle_order(AdapterOrder.from_envelope(envelope))
            else:  # pragma: no cover - the direction check above leaves only notifications today
                raise AdapterRefused(
                    FailureCategory.UNSUPPORTED_WORKFLOW.value,
                    f"no adapter handler for {envelope.payload.message_type.value}",
                )
        except AdapterUnavailable as error:
            self._reject(row.delivery_id, FailureCategory.REMOTE_UNAVAILABLE, str(error), report)
            return
        except AdapterRefused as error:
            self._reject(row.delivery_id, FailureCategory(error.category), str(error), report)
            return
        except Exception as error:  # noqa: BLE001 -- an unknown adapter fault is treated as transient
            # Fail *safe* rather than fail closed: an unrecognised adapter error is most often
            # infrastructure, and dead-lettering a clinical order for it would discard work a retry
            # would have delivered. The server's retry budget bounds how long that can go on.
            self._reject(
                row.delivery_id,
                FailureCategory.TEMPORARY,
                f"adapter raised {type(error).__name__}",
                report,
            )
            return

        self._store.mark_handled(
            row.delivery_id,
            replay_scope=row.replay_scope,
            source_event_id=row.source_event_id,
            fingerprint=fingerprint,
        )
        report.handled += 1
        self._acknowledge(row.delivery_id, report)

    def _acknowledge(self, delivery_id: str, report: CycleReport) -> None:
        try:
            self._client.acknowledge(
                runtime_instance_id=self._runtime_instance_id,
                delivery_id=delivery_id,
                outcome="DELIVERED",
            )
        except (RuntimeApiError, RuntimeUnreachable) as error:
            # Left HANDLED on purpose. Step 1 of the next cycle owes this acknowledgement, and the
            # adapter will not be called again because the receipt is already written.
            report.errors.append(f"ack:{type(error).__name__}")
            return
        self._store.mark_acknowledged(delivery_id)

    def _reject(
        self, delivery_id: str, category: FailureCategory, detail: str, report: CycleReport
    ) -> None:
        self._store.mark_inbound_failed(delivery_id, category=category.value, detail=detail)
        report.failed += 1
        try:
            self._client.acknowledge(
                runtime_instance_id=self._runtime_instance_id,
                delivery_id=delivery_id,
                outcome="FAILED",
                failure_category=category.value,
                detail=detail,
            )
        except (RuntimeApiError, RuntimeUnreachable) as error:
            report.errors.append(f"nack:{type(error).__name__}")

    # -- 5/6. outbound ------------------------------------------------------------------------

    def _send(self, report: CycleReport) -> None:
        try:
            events = self._adapter.poll_events()
        except AdapterUnavailable as error:
            report.errors.append(f"poll:{type(error).__name__}")
            events = []
        except Exception as error:  # noqa: BLE001
            report.errors.append(f"poll:{type(error).__name__}")
            events = []

        for event in events:
            envelope = self._envelope_for(event)
            if envelope is None:
                continue
            self._store.enqueue_outbound(
                message_type=envelope.payload.message_type.value,
                source_event_id=event.source_event_id,
                envelope=envelope.model_dump(mode="json"),
            )

        for row in self._store.pending_outbound():
            try:
                self._client.submit(_loads(row.envelope_json))
            except RuntimeApiError as error:
                # A 4xx will be a 4xx again next cycle, and every attempt makes the server audit
                # another refusal. `terminal` stops the resubmission without deleting the row.
                self._store.mark_outbound_failed(
                    row.id, detail=error.detail, terminal=not error.retryable
                )
                report.errors.append(f"submit:{error.status_code}")
                continue
            except RuntimeUnreachable as error:
                self._store.mark_outbound_failed(row.id, detail=type(error).__name__)
                report.errors.append(f"submit:{type(error).__name__}")
                continue
            self._store.mark_submitted(row.id)
            report.submitted += 1

    def _envelope_for(self, event: AdapterEvent) -> Envelope | None:
        """Wrap an adapter event in the canonical envelope. Refusals are the adapter's fault, not
        the server's, and are caught here rather than sent."""
        try:
            return Envelope(
                contract_version=CONTRACT_VERSION,
                message_id=uuid.uuid4(),
                connector_installation_id=self._installation_id,
                organization_id=self._organization_id,
                source_system=self._source_system,
                source_event_id=event.source_event_id,
                source_occurred_at=event.occurred_at,
                sent_at=datetime.now(UTC),
                lab_order_id=event.lab_order_id,
                payload=event.payload,
            )
        except ValueError:
            logger.warning(
                "adapter_event_rejected",
                extra={"source_event_id": event.source_event_id},
            )
            return None


def _loads(raw: str) -> dict:
    import json

    return json.loads(raw)
