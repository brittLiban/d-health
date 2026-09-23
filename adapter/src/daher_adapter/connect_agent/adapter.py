"""DH-CONNECT-AGENT-001 §38–§39 — the protocol every vendor adapter implements, and one synthetic.

**This is the seam, and holding it is most of what this ticket is for.** §1 and §3 forbid building a
FHIR adapter, an HL7 adapter, an OpenELIS adapter, an OpenMRS adapter or any vendor REST client
here, and the runtime must stay protocol-neutral. The way to make that a structural fact rather than
a promise is to define the narrowest interface the runtime actually needs and then never look past
it:

    handle_order(notification)  ->  the partner's system now has this order
    poll_events()               ->  whatever the partner's system has produced since last time
    probe()                     ->  is the partner's system reachable at all

Three methods. Nothing about transport, nothing about formats, nothing about authentication to the
partner's system -- all of that lives inside an implementation, which is DH-CONNECT-ADAPTER-001's
subject. The runner below imports this module and never imports an implementation.

``SyntheticLabAdapter`` is the only implementation that ships, it is **not** a partner integration,
and §82 forbids letting it look like one. It exists so the delivery path can be exercised end to
end: §41 is explicit that calling an adapter directly and marking ``ORDER_RECEIPT`` exercised is an
invalid qualification, so the qualification has to drive *this* object through the real runtime, and
this object has to be real enough that driving it proves something.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from daher_adapter.connect.contract import Envelope, OrderNotification


@dataclass(frozen=True, slots=True)
class AdapterOrder:
    """One order, as the partner's system will be told about it.

    A flattened view rather than the envelope itself, so an adapter author never has to reason about
    DHealth's correlation rules to write one. The runtime keeps the envelope; the adapter gets the
    facts it needs and the identifiers it must echo back.
    """

    lab_order_id: uuid.UUID
    patient_source_system: str
    patient_identity_system: str
    patient_identity_value: str
    practitioner_identity_value: str
    catalog_codes: tuple[str, ...]
    ordered_at: datetime

    @classmethod
    def from_envelope(cls, envelope: Envelope) -> AdapterOrder:
        payload = envelope.payload
        if not isinstance(payload, OrderNotification):  # pragma: no cover - guarded by the runner
            raise TypeError(f"{payload.message_type.value} is not an order notification")
        if envelope.lab_order_id is None:  # pragma: no cover - the envelope validator requires it
            raise ValueError("an order notification must carry lab_order_id")
        return cls(
            lab_order_id=envelope.lab_order_id,
            patient_source_system=payload.patient.source_system,
            patient_identity_system=payload.patient.source_identity_system,
            patient_identity_value=payload.patient.source_identity_value,
            practitioner_identity_value=payload.practitioner.source_identity_value,
            catalog_codes=payload.catalog_codes,
            ordered_at=envelope.source_occurred_at,
        )


@dataclass(frozen=True, slots=True)
class AdapterEvent:
    """Something the partner's system did, ready to become a Connect message.

    ``source_event_id`` is the adapter's promise that this event has a stable identity in the
    partner's system. It is the idempotency key the whole chain rests on, so an adapter that
    generates a fresh one per poll has broken replay protection for everybody downstream -- stated
    here because it is the single easiest thing for an adapter author to get wrong.
    """

    source_event_id: str
    #: A ``contract.ConnectMessage``. Typed loosely on purpose: the runtime validates it by building
    #: an ``Envelope``, which is a stricter check than any annotation here could be.
    payload: object
    occurred_at: datetime
    lab_order_id: uuid.UUID | None = None


class AdapterUnavailable(RuntimeError):
    """The partner's system could not be reached or would not answer.

    Distinct from an adapter *refusing* a message, which is a permanent outcome. This one is the
    retryable kind, and keeping them apart is what stops a network blip from dead-lettering a
    clinical order and a malformed order from being retried forever.
    """


class AdapterRefused(RuntimeError):
    """The partner's system understood the message and will not accept it.

    Carries a ``FailureCategory`` because the runtime must not guess: "you sent a code we do not
    have" and "your message was malformed" and "we already have that order under another number" all
    need different handling, and the adapter is the only party that knows which happened.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class ConnectorAdapter(Protocol):
    """What a partner integration must implement. Nothing more, and deliberately nothing about wire
    formats."""

    #: Stable name of this adapter family, matching the installation's ``connector_key``. Used for
    #: reporting only -- the runtime never branches on it, which is what keeps it protocol-neutral.
    connector_key: str

    def handle_order(self, order: AdapterOrder) -> None:
        """Place this order into the partner's system.

        Must be **idempotent on ``lab_order_id``**: the runtime persists before calling and
        acknowledges after, so a crash in between means this is called again with the same order.
        Raise ``AdapterUnavailable`` for anything retryable and ``AdapterRefused`` for anything not.
        """
        ...

    def poll_events(self) -> list[AdapterEvent]:
        """Whatever the partner's system has produced since the last successful poll.

        May return the same event twice; the agent's outbox deduplicates on ``source_event_id``.
        Returning nothing is normal and is not an error.
        """
        ...

    def probe(self) -> bool:
        """Whether the partner's system is reachable right now. Reported in the heartbeat (§13).

        A cheap check, never a clinical query, and never anything that could carry patient data --
        the heartbeat that reports its result carries counts and versions only.
        """
        ...


@dataclass
class SyntheticLabAdapter:
    """A laboratory adapter that is honest about being synthetic. **Not a partner integration.**

    §82: the platform UI must never imply this is anybody's production connector, and it is never
    seeded into real facility onboarding. What it is for is §41: the qualification suite has to
    prove ``ORDER_RECEIPT`` by driving the *actual* durable delivery path, and that needs something
    at the far end which genuinely receives an order and can be asked afterwards whether it did.

    It keeps received orders in memory, which is exactly right for its purpose and exactly wrong for
    anything else. It has no persistence, no network, no formats and no configuration -- there is
    nothing here that could be mistaken for the beginning of a real LIS integration, which is the
    point.

    ``fail_next`` and ``unavailable`` exist so the retry, dead-letter and degraded-health paths can
    be driven from the outside. A test that cannot make the adapter fail cannot prove what happens
    when a real one does.
    """

    connector_key: str = "synthetic-lis"
    received_orders: list[AdapterOrder] = field(default_factory=list)
    #: How many times ``handle_order`` was *called*, as distinct from how many distinct orders were
    #: accepted. The two differ exactly when the runtime redelivers something it should not have,
    #: and only this counter can tell them apart -- ``received_orders`` cannot, because this adapter
    #: is itself idempotent. A test asserting on the list alone would pass with the agent's local
    #: replay check deleted, which is how NC-RUNTIME-4 found this file.
    #:
    #: A real partner adapter may well not be idempotent. The runtime must not depend on it being
    #: so, and this counter is how that independence is actually tested rather than assumed.
    handle_order_calls: int = 0
    queued_events: list[AdapterEvent] = field(default_factory=list)
    #: Set to a ``FailureCategory`` value to make the next ``handle_order`` refuse permanently.
    fail_next: str | None = None
    #: While true, every call raises ``AdapterUnavailable`` and ``probe`` answers false.
    unavailable: bool = False

    def handle_order(self, order: AdapterOrder) -> None:
        self.handle_order_calls += 1
        if self.unavailable:
            raise AdapterUnavailable("synthetic source system is unreachable")
        if self.fail_next is not None:
            category, self.fail_next = self.fail_next, None
            raise AdapterRefused(category, "synthetic source system refused the order")
        # Idempotent on the DHealth order id, as the protocol requires of every implementation. A
        # real adapter satisfies this by looking the order up in its own system; this one, which has
        # no system, satisfies it by looking in the list it keeps.
        if any(existing.lab_order_id == order.lab_order_id for existing in self.received_orders):
            return
        self.received_orders.append(order)

    def poll_events(self) -> list[AdapterEvent]:
        if self.unavailable:
            raise AdapterUnavailable("synthetic source system is unreachable")
        events, self.queued_events = self.queued_events, []
        return events

    def probe(self) -> bool:
        return not self.unavailable

    # -- test affordances, and named so nobody mistakes them for protocol ----------------------

    def has_order(self, lab_order_id: uuid.UUID) -> bool:
        return any(order.lab_order_id == lab_order_id for order in self.received_orders)
