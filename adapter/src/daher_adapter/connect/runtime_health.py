"""DH-CONNECT-AGENT-001 §12–§15, §60–§63 — health derived from behaviour, never declared.

The rule this module exists to make structural: **no operator can set a connector to HEALTHY.**
There is no column holding health, no endpoint accepting one, and no UI control offering one. Health
is a function of observations -- when DHealth last heard from a runtime, what that runtime reported
about its own backlog, whether its adapter answered, and how much work is stuck in DHealth's own
outbox -- and a function has no write path.

That is not fastidiousness. A health flag somebody can set is a health flag somebody *will* set, at
2 a.m., to make an alarm stop, and the facility it unblocks then takes clinical work through a
connector nobody has heard from. Deriving it means the only way to become healthy is to actually be
healthy.

**Four states, and the two "not working" ones are separate on purpose.**

    UNKNOWN      DHealth has never heard from a runtime for this installation. Not a failure --
                 nobody has installed or started one, which is a different conversation from an
                 agent that was working and stopped.
    HEALTHY      Heard from recently, adapter reachable, backlog within bounds.
    DEGRADED     Heard from recently, but something is wrong: the adapter is unreachable, work is
                 aging in the queue, or dead letters have accumulated. The agent is alive and
                 telling DHealth it cannot do the job.
    UNAVAILABLE  Not heard from within the staleness window. Silence, and silence is not consent:
                 an agent that stopped reporting is assumed not to be working.

``DEGRADED`` blocks production routing exactly as ``UNAVAILABLE`` does (§63). A connector whose LIS
is unreachable is not "mostly fine" -- an order routed to it is an order that sits, and an order
that sits is a patient waiting. The distinction between the two states is for the operator reading
the screen, not for the routing decision.

**The agent's clock decides nothing** (§14). Staleness is measured against ``last_seen_at``, which
DHealth stamps on receipt. An agent with a fast clock cannot claim to have been heard from in the
future, and one with a slow clock cannot be declared dead for it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from daher_adapter.connect.contract import Direction, HealthState
from daher_adapter.connect.models import ConnectorInstallation, ConnectorInstallationStatus
from daher_adapter.connect.runtime_models import (
    ConnectorDelivery,
    ConnectorRuntimeInstance,
    DeliveryState,
)
from daher_adapter.models import utc_now


def aware_utc(value: datetime) -> datetime:
    """PostgreSQL's ``timestamptz`` always returns an aware datetime; SQLite -- this repo's fast
    test database, never production -- has no such type and hands back a naive one on a fresh read.
    Comparing that against ``utc_now()`` raises ``TypeError``. Every value written here is UTC, so
    re-attaching the tzinfo is lossless and a no-op against the real psycopg driver.

    The same helper ``exchange/patient_access.py`` and ``exchange/selection.py`` already carry, for
    the same reason. Worth stating rather than assuming: without it, every heartbeat would 500 on
    the test database while passing on PostgreSQL, which is the worst possible split -- the suite
    would be green and the feature untested.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RuntimeHealthPolicy:
    """Every threshold that decides health, in one place (§15).

    One dataclass rather than constants scattered across the modules that need them, because the
    question an operator asks is "what does DHealth consider healthy" and that has to have a single
    readable answer. Scattered thresholds are how a heartbeat interval and a staleness window drift
    apart until an agent reporting exactly on schedule is intermittently declared dead.

    ``heartbeat_interval`` is what the agent is told to aim for; ``heartbeat_stale_after`` is when
    DHealth gives up on it. The gap between them is deliberate slack -- three intervals, so a single
    missed beat, a slow network or a brief restart does not flip a working facility out of routing.
    Equal values would make health flap on ordinary jitter, and flapping health is indistinguishable
    from a broken connector to everyone reading the screen.
    """

    #: What the agent aims for. Not enforced by DHealth -- an agent beating faster is not punished.
    heartbeat_interval: timedelta = timedelta(seconds=30)
    #: Silence longer than this is ``UNAVAILABLE``. Three intervals, for the reason above.
    heartbeat_stale_after: timedelta = timedelta(seconds=90)
    #: Outbound work older than this, still undelivered, means the agent is not keeping up even if
    #: it is still beating. Chosen against the clinical fact it protects: an order that has not
    #: reached the laboratory in five minutes is late, not in transit.
    degraded_backlog_age: timedelta = timedelta(minutes=5)
    #: One dead letter is a clinical message nobody delivered. There is no "acceptable number", so
    #: the threshold is one -- ``>= 1`` degrades. Naming it a threshold rather than hardcoding it
    #: keeps the decision visible rather than buried in a comparison.
    degraded_dead_letters: int = 1


DEFAULT_HEALTH_POLICY = RuntimeHealthPolicy()

#: Why a non-REGISTERED installation reports UNKNOWN. The two statuses mean different things to the
#: person reading the screen -- a suspended connector is expected back, a retired one is not -- and
#: ``ConnectorInstallationStatus`` already makes that distinction; flattening it here would undo it.
_STATUS_REASONS = {
    ConnectorInstallationStatus.SUSPENDED.value: (
        "Ce connecteur est suspendu ; DHealth n'interroge pas son agent, qu'il soit actif ou non."
    ),
    ConnectorInstallationStatus.RETIRED.value: (
        "Ce connecteur est retiré ; il ne peut plus acheminer de travail clinique."
    ),
}


@dataclass(frozen=True, slots=True)
class RuntimeHealth:
    """A derived health state and the observations it was derived from.

    The reasons travel with the verdict because "DEGRADED" on its own sends an operator to guess.
    They are short French sentences for the screen, and they name counts and ages -- never a
    patient, a result value or a payload excerpt (§54, §57).
    """

    state: HealthState
    runtime_instance_id: str | None
    last_seen_at: datetime | None
    agent_version: str | None
    adapter_reachable: bool | None
    pending_deliveries: int
    oldest_pending_age: timedelta | None
    dead_letters: int
    reasons: tuple[str, ...]

    @property
    def is_operational(self) -> bool:
        """Whether a live runtime is currently carrying this facility's traffic.

        ``HEALTHY`` and nothing else. ``DEGRADED`` is deliberately not operational: §63. An agent
        that is alive and cannot reach its LIS will accept an order and then fail to deliver it,
        which is worse than refusing it, because the clinician believes it went somewhere.
        """
        return self.state is HealthState.HEALTHY


def derive_health(
    *,
    instance: ConnectorRuntimeInstance | None,
    pending_deliveries: int,
    oldest_pending_age: timedelta | None,
    dead_letters: int,
    now: datetime,
    policy: RuntimeHealthPolicy = DEFAULT_HEALTH_POLICY,
    unknown_reason: str | None = None,
) -> RuntimeHealth:
    """The whole health rule, as a pure function of observations.

    Pure and session-free so the interesting cases are testable without constructing a database:
    every threshold boundary, the never-connected case and the aged-backlog case are all reachable
    by passing values. The session-bound wrapper below does nothing but gather these arguments.

    Evaluation order matters and is not arbitrary. Never-heard-from is checked first because
    ``UNKNOWN`` must not be reported as ``UNAVAILABLE`` -- "you have not installed a connector" and
    "your connector stopped answering" are different problems with different owners. Staleness is
    checked before the degradation reasons because an agent DHealth cannot hear from is not in a
    position to tell it anything, and reporting a five-minute-old backlog as the problem when the
    real problem is a dead agent would send the operator to the wrong place.
    """
    if instance is None:
        return RuntimeHealth(
            state=HealthState.UNKNOWN,
            runtime_instance_id=None,
            last_seen_at=None,
            agent_version=None,
            adapter_reachable=None,
            pending_deliveries=pending_deliveries,
            oldest_pending_age=oldest_pending_age,
            dead_letters=dead_letters,
            # ``unknown_reason`` exists because ``UNKNOWN`` has two causes and they are not the same
            # sentence. "No agent has ever reported in" is one; "an operator suspended this
            # connector, so DHealth is not consulting its agent at all" is another, and saying the
            # first when the second is true would be a finding that lies about why -- the agent may
            # be beating perfectly. This codebase refuses that in `QualificationGap` and refuses it
            # here for the same reason.
            reasons=(unknown_reason or "Aucun agent ne s'est jamais annoncé pour ce connecteur.",),
        )

    silence = now - aware_utc(instance.last_seen_at)
    if silence > policy.heartbeat_stale_after:
        return RuntimeHealth(
            state=HealthState.UNAVAILABLE,
            runtime_instance_id=instance.runtime_instance_id,
            last_seen_at=aware_utc(instance.last_seen_at),
            agent_version=instance.agent_version,
            adapter_reachable=instance.adapter_reachable,
            pending_deliveries=pending_deliveries,
            oldest_pending_age=oldest_pending_age,
            dead_letters=dead_letters,
            reasons=(
                f"Aucun signal depuis {int(silence.total_seconds())} s "
                f"(seuil {int(policy.heartbeat_stale_after.total_seconds())} s).",
            ),
        )

    reasons: list[str] = []
    if not instance.adapter_reachable:
        reasons.append("L'agent ne joint pas le système source.")
    if oldest_pending_age is not None and oldest_pending_age > policy.degraded_backlog_age:
        reasons.append(
            f"{pending_deliveries} message(s) en attente, le plus ancien depuis "
            f"{int(oldest_pending_age.total_seconds() // 60)} min."
        )
    if dead_letters >= policy.degraded_dead_letters:
        reasons.append(f"{dead_letters} message(s) non livrable(s) en attente de décision.")

    if reasons:
        return RuntimeHealth(
            state=HealthState.DEGRADED,
            runtime_instance_id=instance.runtime_instance_id,
            last_seen_at=aware_utc(instance.last_seen_at),
            agent_version=instance.agent_version,
            adapter_reachable=instance.adapter_reachable,
            pending_deliveries=pending_deliveries,
            oldest_pending_age=oldest_pending_age,
            dead_letters=dead_letters,
            reasons=tuple(reasons),
        )

    return RuntimeHealth(
        state=HealthState.HEALTHY,
        runtime_instance_id=instance.runtime_instance_id,
        last_seen_at=aware_utc(instance.last_seen_at),
        agent_version=instance.agent_version,
        adapter_reachable=instance.adapter_reachable,
        pending_deliveries=pending_deliveries,
        oldest_pending_age=oldest_pending_age,
        dead_letters=dead_letters,
        reasons=(),
    )


def latest_runtime_instance(
    session: Session, installation_id: uuid.UUID
) -> ConnectorRuntimeInstance | None:
    """The most recently heard-from runtime for this installation.

    Most recent by ``last_seen_at`` -- DHealth's own stamp -- and not by ``first_seen_at`` or
    ``reported_at``. A redeploy leaves the previous instance's row behind, and ordering by creation
    would keep reading the corpse; ordering by the agent's own clock would let a skewed agent
    nominate itself.
    """
    return session.scalar(
        select(ConnectorRuntimeInstance)
        .where(ConnectorRuntimeInstance.connector_installation_id == installation_id)
        .order_by(ConnectorRuntimeInstance.last_seen_at.desc(), ConnectorRuntimeInstance.id)
        .limit(1)
    )


def runtime_health_in_session(
    session: Session,
    installation: ConnectorInstallation | None,
    *,
    now: datetime | None = None,
    policy: RuntimeHealthPolicy = DEFAULT_HEALTH_POLICY,
) -> RuntimeHealth:
    """Health for one installation, gathering the observations ``derive_health`` needs.

    A ``None`` installation, or a suspended or retired one, is ``UNKNOWN`` **without reading any
    runtime row**. That is not a shortcut: a suspended installation may well have an agent still
    beating, and reading its heartbeat would report a facility as healthy that an operator has
    deliberately switched off. The human decision outranks the observation, so the observation is
    not consulted.

    It is still reported *truthfully*, which is why each of those three cases carries its own
    sentence. Telling an operator who suspended a connector that no agent has ever reported in would
    be false, and would send them hunting an installation problem they created on purpose.
    """
    moment = now or utc_now()
    if installation is None:
        return derive_health(
            instance=None,
            pending_deliveries=0,
            oldest_pending_age=None,
            dead_letters=0,
            now=moment,
            policy=policy,
        )
    if installation.status != ConnectorInstallationStatus.REGISTERED.value:
        return derive_health(
            instance=None,
            pending_deliveries=0,
            oldest_pending_age=None,
            dead_letters=0,
            now=moment,
            policy=policy,
            unknown_reason=_STATUS_REASONS.get(
                installation.status,
                "Ce connecteur n'est pas en service ; DHealth n'interroge pas son agent.",
            ),
        )

    instance = latest_runtime_instance(session, installation.id)

    # Outbound only. A DHealth-side backlog of messages the *agent* sent us is not the agent failing
    # to keep up, it is DHealth failing to apply them, and calling that the connector's poor health
    # would point the operator at the partner for a problem on this side.
    pending = session.execute(
        select(
            func.count(ConnectorDelivery.id),
            func.min(ConnectorDelivery.created_at),
        ).where(
            ConnectorDelivery.connector_installation_id == installation.id,
            ConnectorDelivery.direction == Direction.DHEALTH_TO_CONNECTOR.value,
            ConnectorDelivery.state.in_((DeliveryState.PENDING.value, DeliveryState.CLAIMED.value)),
        )
    ).one()
    pending_count = int(pending[0] or 0)
    oldest_created_at = pending[1]
    oldest_age = None
    if oldest_created_at is not None:
        # An old row whose clock reads later than ours (possible only with a skewed writer) must not
        # produce a negative age that silently looks like a fresh queue.
        oldest_age = max(moment - aware_utc(oldest_created_at), timedelta(0))

    dead_letters = int(
        session.scalar(
            select(func.count(ConnectorDelivery.id)).where(
                ConnectorDelivery.connector_installation_id == installation.id,
                ConnectorDelivery.direction == Direction.DHEALTH_TO_CONNECTOR.value,
                ConnectorDelivery.state == DeliveryState.DEAD_LETTER.value,
            )
        )
        or 0
    )

    return derive_health(
        instance=instance,
        pending_deliveries=pending_count,
        oldest_pending_age=oldest_age,
        dead_letters=dead_letters,
        now=moment,
        policy=policy,
    )
