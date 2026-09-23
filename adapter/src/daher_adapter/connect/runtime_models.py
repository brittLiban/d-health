"""DH-CONNECT-AGENT-001 — the durable state a Connect runtime cannot work without.

Four tables, in the central Exchange database. Kept in their own module rather than added to
``connect/models.py`` because that file is about a decision a human made (this connector is
installed here, this qualification attempt concluded that) while these are about work in flight.
Mixing them would put an immutable evidence record and a mutable queue row under one docstring.

The shape of each is forced by a specific failure this ticket has to survive:

``connector_runtime_instances``
    An agent restarting must not look like a *new* connector, and a stopped agent must not look
    healthy. So the row is keyed on an agent-chosen instance id and stamped with **DHealth's own
    receive time** -- the agent's clock is recorded beside it and never used to decide liveness.

``connector_deliveries``
    Both directions, one table. A message that has been accepted for delivery but not yet delivered
    is the only state in which clinical work can be silently lost, and it must therefore survive a
    process death, a machine reboot and a redeploy. Claim leases rather than in-memory locks,
    because a lock in a process that has died is a message nobody will ever pick up again.

``connector_message_receipts``
    ``contract.ReceiptStore``, made durable. ``InMemoryReceiptStore`` forgets everything on
    restart, which means replay protection is exactly as strong as the agent's uptime -- and an
    at-least-once transport plus a forgetful receiver is how one collection event becomes two.

``connector_correlations``
    The exact binding between a DHealth order and the identifier the partner's system uses for it.
    Exact: §43's "no fuzzy matching" is enforced by there being nothing to match on. An external id
    that does not appear here is unknown, and unknown is refused rather than guessed.

**Payload handling.** ``ConnectorDelivery.payload`` holds the canonical envelope JSON, which for a
clinical message is patient data. It lives here, inside the Exchange security boundary, and is
deliberately not copied into ``exchange_security_audit_events``, the operational-telemetry tables,
or structured logs. It is stored rather than reconstructed because a fingerprint proves a payload
was *seen*, not what it said -- a queue that can only fingerprint cannot redeliver, and a queue
that cannot redeliver is not durable. Retention follows the ``LabOrder`` it references; see
``docs/CONNECT-AGENT.md``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from daher_adapter.models import Base, utc_now


class DeliveryState(StrEnum):
    """Where one message is in its life. Four states, and no ``FAILED``.

    A delivery that failed retryably is still ``PENDING`` with a later ``next_attempt_at`` and a
    recorded failure category -- a separate ``FAILED`` state would be a second way of saying
    "waiting", and the two would drift the first time a retry scheduler read only one of them.

    ``DEAD_LETTER`` is terminal and is **not** a deletion. §56: if discard semantics for a clinical
    event are unclear, the honest outcome is an unresolved row an operator has to look at, not a
    quiet cancellation DHealth invented on the partner's behalf.
    """

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    DELIVERED = "DELIVERED"
    DEAD_LETTER = "DEAD_LETTER"


class CorrelationKind(StrEnum):
    """What kind of partner identifier is bound to what.

    ``ORDER`` is one-to-one with a ``LabOrder``: a laboratory has exactly one accession for one
    order, and two would mean the order was duplicated in their system. ``REPORT`` is many-to-one
    on purpose -- the original release and each later amendment are separate reports about the same
    order, and collapsing them would make an amendment overwrite the thing it corrects.
    """

    ORDER = "ORDER"
    REPORT = "REPORT"


class ConnectorRuntimeInstance(Base):
    """One agent process lineage that has authenticated for one installation.

    **Separate from the installation, and that separation is the point.** The installation is what
    a platform admin registered; a runtime instance is a thing that runs, restarts, gets redeployed
    and eventually stops. Folding liveness into the installation row would mean an agent crash
    looked like a change to the connector's registration, and a redeploy looked like a new
    connector needing requalification. Neither is true.

    ``last_seen_at`` is stamped by **DHealth** on receipt. ``reported_at`` is what the agent's own
    clock said, kept only so an operator can see skew: §14 is explicit that the agent's clock may
    not decide whether the agent is alive, and an agent with a clock running fast would otherwise
    be able to claim it was heard from in the future and never go stale.

    Nothing here is a health verdict. The columns are observations -- how deep the agent's local
    queue is, how many messages it has given up on, whether its last adapter probe answered -- and
    ``runtime_health`` derives a state from them plus ``last_seen_at``. §12: there is no column an
    operator can set to ``HEALTHY``, because there is no column holding health at all.

    No PHI. §13: a heartbeat carries counts and versions, never a patient payload, and there is no
    column here that could hold one.
    """

    __tablename__ = "connector_runtime_instances"
    __table_args__ = (
        # One row per (installation, instance). A restarting agent that keeps its instance id
        # updates its row; a redeployed one with a new id gets a new row and the old one stops being
        # heard from, which is the history an operator needs to see a flapping deployment.
        UniqueConstraint(
            "connector_installation_id",
            "runtime_instance_id",
            name="uq_connector_runtime_instances_instance",
        ),
        CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_connector_runtime_instances_seen_order",
        ),
        CheckConstraint(
            "heartbeat_count >= 1", name="ck_connector_runtime_instances_heartbeat_count"
        ),
        CheckConstraint(
            "local_queue_depth >= 0 AND local_dead_letters >= 0",
            name="ck_connector_runtime_instances_counts",
        ),
        Index(
            "ix_connector_runtime_instances_installation_id",
            "connector_installation_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    connector_installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_installations.id", ondelete="RESTRICT"), nullable=False
    )
    #: Agent-chosen and stable across restarts of the same deployment. Not a hostname: a hostname
    #: is reused by whatever is redeployed onto it, and two agents behind one name would silently
    #: share a lease.
    runtime_instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The contract version the agent itself implements, which is not necessarily the one its
    #: installation was registered with -- an upgraded agent talking to an older registration is
    #: exactly the mismatch §50 has to be able to see.
    contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: **Server receive time, authoritative** (§14). Never the agent's own timestamp.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The agent's clock at the moment it composed the heartbeat. Observation only.
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: Facts the agent reports about itself. Not verdicts, and not DHealth's queue -- DHealth can
    #: count its own outbox; only the agent can see how much work is stuck on its own side.
    local_queue_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    local_dead_letters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Whether the agent's last probe of its own adapter answered. A reachable agent in front of an
    #: unreachable LIS is not a working integration, and the distinction is invisible from DHealth.
    adapter_reachable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ConnectorDelivery(Base):
    """One message, in either direction, that DHealth has taken durable responsibility for.

    One table rather than two because the failure semantics are identical -- persist, hand over,
    confirm, retry with backoff, dead-letter, never lose -- and two tables would mean writing that
    logic twice and having it diverge on the third bug. ``direction`` is what differs, and it is a
    column the runtime's boundary checks read (§45: an agent may only submit inbound types; DHealth
    only queues outbound ones).

    **Claim leases, not locks.** ``claimed_by_runtime_instance_id`` plus ``claim_expires_at`` mean a
    claimed message returns to ``PENDING`` by the passage of time alone. An agent that dies holding
    work therefore blocks that work for one lease, not forever, and no operator has to know that
    unsticking it is even a thing. An in-process lock would have been simpler and would have made
    "the agent crashed mid-batch" unrecoverable without manual surgery.

    ``attempt_count`` counts *hand-overs*, not failures: a message claimed and never confirmed has
    been attempted, whether the agent reported a failure or simply vanished. Counting only reported
    failures would let a crash-looping agent retry forever without ever reaching a dead letter.
    """

    __tablename__ = "connector_deliveries"
    __table_args__ = (
        # **The idempotency key, and the same one in both directions.** Inbound, the scope and event
        # id are the partner's (``Envelope.replay_scope``); outbound, DHealth mints them from the
        # thing being announced, so re-announcing one order to one installation is one row however
        # many times the hook fires. A weaker key on ``message_id`` would dedupe nothing: an honest
        # retry of the same event carries a new message id, which is exactly the case this exists
        # to collapse.
        UniqueConstraint(
            "connector_installation_id",
            "direction",
            "replay_scope",
            "source_event_id",
            name="uq_connector_deliveries_replay",
        ),
        CheckConstraint(
            "direction IN ('CONNECTOR_TO_DHEALTH', 'DHEALTH_TO_CONNECTOR')",
            name="ck_connector_deliveries_direction",
        ),
        CheckConstraint(
            "state IN ('PENDING', 'CLAIMED', 'DELIVERED', 'DEAD_LETTER')",
            name="ck_connector_deliveries_state",
        ),
        CheckConstraint(
            "message_type IN ("
            "'ORDER_SUBMISSION', 'ORDER_NOTIFICATION', 'TECHNICAL_ACK', "
            "'BUSINESS_ACCEPTANCE', 'BUSINESS_REJECTION', 'COLLECTION_EVENT', "
            "'RELEASED_RESULT', 'RELEASED_AMENDMENT')",
            name="ck_connector_deliveries_message_type",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_connector_deliveries_attempt_count"),
        # A lease is both halves or neither. One half alone is a row whose claim cannot expire (no
        # deadline) or whose expiry frees nothing (no holder) -- both are ways to lose a message.
        CheckConstraint(
            "(claimed_by_runtime_instance_id IS NULL) = (claim_expires_at IS NULL)",
            name="ck_connector_deliveries_lease_paired",
        ),
        CheckConstraint(
            "state <> 'CLAIMED' OR claimed_by_runtime_instance_id IS NOT NULL",
            name="ck_connector_deliveries_claimed_has_holder",
        ),
        CheckConstraint(
            "state <> 'DELIVERED' OR delivered_at IS NOT NULL",
            name="ck_connector_deliveries_delivered_at",
        ),
        CheckConstraint(
            "state <> 'DEAD_LETTER' OR dead_lettered_at IS NOT NULL",
            name="ck_connector_deliveries_dead_lettered_at",
        ),
        # The claim query's index. Ordered state-first because the claim always filters on it, and a
        # queue whose claim does a full scan gets slower exactly as the backlog it exists to drain
        # gets bigger.
        Index(
            "ix_connector_deliveries_claimable",
            "connector_installation_id",
            "direction",
            "state",
            "next_attempt_at",
        ),
        Index("ix_connector_deliveries_lab_order_id", "lab_order_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    connector_installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_installations.id", ondelete="RESTRICT"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(24), nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The envelope's own id, kept for tracing a specific transmission. Deliberately not the
    #: idempotency key -- see ``uq_connector_deliveries_replay``.
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    replay_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    lab_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=True
    )
    #: The canonical envelope, as JSON. Clinical content for most message types; see the module
    #: docstring on where it may and may not be copied.
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DeliveryState.PENDING.value
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: When this row becomes eligible again. Set at creation so a first attempt needs no special
    #: case, then pushed out by backoff on every failure.
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_by_runtime_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: ``contract.FailureCategory``. What decides retryable from terminal, recorded so the decision
    #: can be audited rather than re-derived from a message.
    last_failure_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: One short sentence for the operator screen. Never a stack trace, never a payload excerpt.
    last_failure_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ConnectorMessageReceipt(Base):
    """``contract.ReceiptStore``, durable. What DHealth has already applied, and what it said.

    Keyed on ``(replay_scope, source_event_id)`` and nothing else, because that is exactly the key
    the Protocol's two methods take. There is deliberately **no installation column**: the scope
    already begins with the organization, at most one live installation exists per organization, and
    adding the installation would make the same partner event re-appliable by a *replacement*
    installation at the same facility. Same facility, same source system, same source event id is
    the same event; a new installation replaying it is a replay.

    ``content_fingerprint`` is what makes a replay distinguishable from a contradiction:

        same key, same fingerprint      -> already applied, do nothing, report success
        same key, different fingerprint -> the partner changed history; refuse, never overwrite
    """

    __tablename__ = "connector_message_receipts"
    __table_args__ = (
        UniqueConstraint(
            "replay_scope",
            "source_event_id",
            name="uq_connector_message_receipts_event",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    replay_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    first_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ConnectorCorrelation(Base):
    """The exact, recorded binding between a DHealth id and a partner's own identifier.

    This table is how §43's "no fuzzy matching" is enforced rather than merely intended. There is no
    similarity function anywhere in the runtime because there is nothing to be similar *to*: an
    external identifier either has a row here, in which case it means precisely one DHealth order,
    or it does not, in which case the message referencing it is refused. A partner whose identifiers
    do not round-trip discovers it during qualification, which is where a constraint like this
    should surface.

    The binding is created by the message that first asserts it -- a laboratory's business
    acceptance naming its accession, a release naming its report -- and is then **immutable**. An
    external id that arrives bound to a different order is a contradiction, and a runtime that
    re-pointed it would silently move clinical results between patients.
    """

    __tablename__ = "connector_correlations"
    __table_args__ = (
        UniqueConstraint(
            "connector_installation_id",
            "correlation_kind",
            "external_id",
            name="uq_connector_correlations_external",
        ),
        # The reverse direction, and **only for orders**. One order has one accession at one
        # laboratory, so a second would mean their system duplicated it. Reports are deliberately
        # exempt: the original release and every amendment are separate reports about one order, and
        # a unique constraint here would make publishing a correction impossible.
        Index(
            "uq_connector_correlations_one_order",
            "connector_installation_id",
            "lab_order_id",
            unique=True,
            postgresql_where=text("correlation_kind = 'ORDER'"),
            sqlite_where=text("correlation_kind = 'ORDER'"),
        ),
        CheckConstraint(
            "correlation_kind IN ('ORDER', 'REPORT')",
            name="ck_connector_correlations_kind",
        ),
        Index("ix_connector_correlations_lab_order_id", "lab_order_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    connector_installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_installations.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False
    )
    #: Which of the partner's systems asserted the identifier. Two systems at one facility may
    #: number their orders identically, and the scope is what keeps those apart -- the same lesson
    #: ``PatientReference`` learned about patient ids.
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
