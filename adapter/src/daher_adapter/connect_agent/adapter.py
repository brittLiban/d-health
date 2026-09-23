"""DH-CONNECT-AGENT-001 §38–§39 — the protocol every vendor adapter implements, and one synthetic.

**This is the seam, and holding it is most of what this ticket is for.** §1 and §3 forbid building a
FHIR adapter, an HL7 adapter, an OpenELIS adapter, an OpenMRS adapter or any vendor REST client
here, and the runtime must stay protocol-neutral. The way to make that a structural fact rather than
a promise is to define the narrowest interface the runtime actually needs and then never look past
it:

    handle_order(notification)      ->  the partner's system now has this order
    handle_result(notification)     ->  the partner's system now has this released report
    handle_amendment(notification)  ->  the partner's system now has this correction
    poll_events()                   ->  whatever the partner's system produced since last time
    probe()                         ->  is the partner's system reachable at all

Five methods. Nothing about transport, nothing about formats, nothing about authentication to the
partner's system -- all of that lives inside an implementation, which is DH-CONNECT-ADAPTER-001's
subject. The runner below imports this module and never imports an implementation.

**One protocol for both sides, not two.** A laboratory connector receives orders and refuses
results; a provider connector receives results and refuses orders. Splitting the protocol in two
would put that difference in the type system, where it reads well -- and would mean the runtime had
to know which kind of connector it was driving in order to dispatch, which is exactly the knowledge
§3 says it must not have. What a connector may do is decided by its declared capabilities and its
qualification; the refusal is a message, ``UNSUPPORTED_WORKFLOW``, that dead-letters in front of an
operator saying which connector was asked for what.

``SyntheticLabAdapter`` and ``SyntheticProviderAdapter`` are the only implementations that ship,
neither is a partner integration, and §82 forbids letting either look like one. They exist so the
delivery path can be exercised end to end: §41 is explicit that calling an adapter directly and
marking a capability exercised is an invalid qualification, so the qualification suites have to
drive *these* objects through the real runtime, and they have to be real enough that driving them
proves something.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from daher_adapter.connect.contract import (
    AmendmentNotification,
    Envelope,
    OrderNotification,
    ResultNotification,
    ResultObservation,
)


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
class AdapterResult:
    """One released report, as the provider's own system will be told about it.

    ``external_order_id`` is the identifier *the partner gave DHealth* when it submitted the order,
    resolved back through the correlation. It is the whole reason this message can be filed: an EMR
    has no idea what a DHealth order id is, and a result it cannot attach to one of its own orders
    is a result that did not arrive.

    ``is_amendment`` and ``corrects_release_version`` are populated only for an amendment. They are
    on this one class rather than split across two because the *adapter* work is the same shape in
    both cases -- write values against an order -- while the two message types stay distinct on the
    wire, where confusing them would be a clinical error rather than an inconvenience.
    """

    lab_order_id: uuid.UUID
    external_order_id: str
    release_version: int
    observations: tuple[ResultObservation, ...]
    released_at: datetime
    is_amendment: bool = False
    corrects_release_version: int | None = None

    @classmethod
    def from_envelope(cls, envelope: Envelope) -> AdapterResult:
        payload = envelope.payload
        if envelope.lab_order_id is None:  # pragma: no cover - the envelope validator requires it
            raise ValueError("a result notification must carry lab_order_id")
        if isinstance(payload, ResultNotification):
            return cls(
                lab_order_id=envelope.lab_order_id,
                external_order_id=payload.external_order_id,
                release_version=payload.release_version,
                observations=payload.observations,
                released_at=envelope.source_occurred_at,
            )
        if isinstance(payload, AmendmentNotification):
            return cls(
                lab_order_id=envelope.lab_order_id,
                external_order_id=payload.external_order_id,
                release_version=payload.release_version,
                observations=payload.observations,
                released_at=envelope.source_occurred_at,
                is_amendment=True,
                corrects_release_version=payload.corrects_release_version,
            )
        raise TypeError(  # pragma: no cover - guarded by the runner
            f"{payload.message_type.value} is not a result notification"
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

    def handle_result(self, result: AdapterResult) -> None:
        """File this released report into the partner's system, against their own order id.

        Idempotent on ``(external_order_id, release_version)`` for the same reason
        ``handle_order`` is idempotent on ``lab_order_id``: the runtime persists before calling and
        acknowledges after. A laboratory connector has no provider-side chart to file into and
        raises ``AdapterRefused(UNSUPPORTED_WORKFLOW)``.
        """
        ...

    def handle_amendment(self, result: AdapterResult) -> None:
        """File a **correction** to a report the partner's system already has.

        Separate from ``handle_result`` because the partner's system almost certainly does something
        different with it -- supersede a version, re-flag a chart, re-notify a clinician -- and an
        adapter that handled both identically would silently file a correction as a second
        independent result. ``corrects_release_version`` names what is being superseded.
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

    def handle_result(self, result: AdapterResult) -> None:
        """A laboratory does not receive results; it produces them."""
        raise AdapterRefused(
            "UNSUPPORTED_WORKFLOW",
            f"{self.connector_key} is a laboratory connector and does not receive released results",
        )

    def handle_amendment(self, result: AdapterResult) -> None:
        raise AdapterRefused(
            "UNSUPPORTED_WORKFLOW",
            f"{self.connector_key} is a laboratory connector and does not receive amendments",
        )

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


@dataclass
class SyntheticProviderAdapter:
    """A provider-side adapter that is honest about being synthetic. **Not a partner integration.**

    The mirror of ``SyntheticLabAdapter``, and it exists for the mirror of that class's reason: a
    provider connector declares ``ORDER_SUBMISSION``, ``RELEASED_RESULT_RECEIPT`` and
    ``AMENDMENT_RECEIPT``, and before this existed all three were permanently ``DECLARED`` because
    nothing could exercise them. §41 forbids marking a capability exercised by calling the adapter
    directly, so the qualification drives this object through the real durable delivery path, and
    this object has to be real enough that driving it proves something.

    It stands in for an EMR: it emits orders and receives reports. Everything it holds is in memory,
    it has no network, no formats and no configuration, and §82 forbids letting it appear in a real
    facility's onboarding as though it were their production connector.
    """

    connector_key: str = "synthetic-emr"
    #: Reports filed, keyed ``(external_order_id, release_version)`` -- the EMR's own chart, near
    #: enough. A correction lands as a *new version* beside the original rather than replacing it,
    #: because that is what a system holding a clinical record must do: the superseded value stays
    #: readable, which is the whole difference between an amendment and an overwrite.
    filed_reports: dict[tuple[str, int], AdapterResult] = field(default_factory=dict)
    #: Call counts, not list lengths. This adapter is idempotent, so the lists cannot distinguish
    #: "delivered once" from "redelivered and deduplicated" -- and that distinction is precisely
    #: what the runtime's exactly-once claim is about. NC-RUNTIME-4 found this the hard way on the
    #: laboratory side; the provider side is built with the counters from the start.
    handle_result_calls: int = 0
    handle_amendment_calls: int = 0
    queued_events: list[AdapterEvent] = field(default_factory=list)
    fail_next: str | None = None
    unavailable: bool = False

    def handle_order(self, order: AdapterOrder) -> None:
        """A provider's system places orders; it does not receive them."""
        raise AdapterRefused(
            "UNSUPPORTED_WORKFLOW",
            f"{self.connector_key} is a provider connector and does not receive orders",
        )

    def handle_result(self, result: AdapterResult) -> None:
        self.handle_result_calls += 1
        self._file(result)

    def handle_amendment(self, result: AdapterResult) -> None:
        self.handle_amendment_calls += 1
        self._file(result)

    def _file(self, result: AdapterResult) -> None:
        if self.unavailable:
            raise AdapterUnavailable("synthetic provider system is unreachable")
        if self.fail_next is not None:
            category, self.fail_next = self.fail_next, None
            raise AdapterRefused(category, "synthetic provider system refused the report")
        # Idempotent on the pair the protocol names, as every implementation must be.
        self.filed_reports.setdefault((result.external_order_id, result.release_version), result)

    def poll_events(self) -> list[AdapterEvent]:
        if self.unavailable:
            raise AdapterUnavailable("synthetic provider system is unreachable")
        events, self.queued_events = self.queued_events, []
        return events

    def probe(self) -> bool:
        return not self.unavailable

    # -- test affordances, and named so nobody mistakes them for protocol ----------------------

    def report_for(self, external_order_id: str, release_version: int) -> AdapterResult | None:
        return self.filed_reports.get((external_order_id, release_version))
