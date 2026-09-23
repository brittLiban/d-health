"""DH-CONNECT-AGENT-001 (0031) — discharging result-delivery obligations that could not be queued.

``result_hook`` records what is owed to a provider's system whether or not anything could carry it.
This is the other half: the deterministic path from *owed* to *queued*, once whatever was in the way
has been fixed.

    PENDING   no connector could take it -- suspended-and-then-retired, replaced by an installation
              for a different source system, or none registered yet
    FAILED    the notification could not be built, or contradicted a message already queued
    QUEUED    but orphaned: the delivery is attached to an installation that has since been retired,
              so it can never be claimed. See ``still_owed_condition``.

All three are re-attempted the same way, because the fix is the same: rebuild the message **from the
immutable release version** and hand it to the durable queue. Nothing is reconstructed from a stored
copy of the report, because there is no stored copy -- the manifest is the clinical truth and the
obligation only names it (§13).

**This is deliberately not a background-worker project.** It is a method. A caller runs it: a test,
an operator command, or the runtime's own heartbeat handler, bounded the way
``apply_pending_inbound`` is bounded. What matters is that the recovery path is durable and
deterministic, not that something is continuously polling -- a scheduler that has to be running for
clinical work not to be lost is a dependency on somebody having configured it.

**Nothing here deletes.** An obligation that still cannot be discharged stays exactly as it was,
with its reason updated. §51 and §56 both point the same way: an unresolved row an operator can see
beats a quiet cancellation DHealth invented on a partner's behalf.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.connect.contract import Direction, FailureCategory
from daher_adapter.connect.correlation import (
    CorrelationConflict,
    CorrelationRepository,
)
from daher_adapter.connect.delivery import DeliveryConflict, DeliveryRepository
from daher_adapter.connect.models import ConnectorInstallation, ConnectorInstallationStatus
from daher_adapter.connect.result_hook import (
    build_notification_envelope,
    eligible_installation,
    safe_failure_detail,
)
from daher_adapter.connect.runtime_models import (
    ConnectorDelivery,
    ConnectorDeliveryObligation,
    DeliveryState,
    ObligationState,
)
from daher_adapter.exchange.models import LabOrder, LabResultReleaseVersion
from daher_adapter.models import utc_now

#: How many obligations one pass attempts. Bounded for the same reason the claim batch is: a pass
#: that tried to drain an unbounded backlog would hold one transaction per row for as long as the
#: backlog took, and the caller is often a request handler.
DEFAULT_RECONCILE_LIMIT = 20


def still_owed_condition() -> ColumnElement[bool]:
    """Everything a reconciliation pass should look at, which is not simply "not QUEUED".

    An obligation can be QUEUED and still owed. Queue the delivery against a *suspended*
    installation -- correct, and the point of §8 -- and then have an operator retire that
    installation and register its successor: the delivery is now attached to a principal that will
    never authenticate again, so it waits forever while the obligation reads as discharged. That is
    precisely the "release committed, nothing will ever carry it, and nothing says so" state §11
    forbids, arrived at by a legal sequence of operator actions.

    So *orphaned* counts as owed: the obligation's delivery sits undelivered on a retired
    installation. ``DELIVERED`` is not orphaned: the provider has it. ``DEAD_LETTER`` is not
    either:
    it is terminal by decision (§56) and belongs to an operator, not to an automatic re-route that
    would replay a message a partner has already refused.
    """
    orphaned = (
        select(ConnectorDelivery.id)
        .join(
            ConnectorInstallation,
            ConnectorInstallation.id == ConnectorDelivery.connector_installation_id,
        )
        .where(
            ConnectorDelivery.id == ConnectorDeliveryObligation.connector_delivery_id,
            ConnectorInstallation.status == ConnectorInstallationStatus.RETIRED.value,
            ConnectorDelivery.state.in_((DeliveryState.PENDING.value, DeliveryState.CLAIMED.value)),
        )
        .exists()
    )
    return or_(ConnectorDeliveryObligation.state != ObligationState.QUEUED.value, orphaned)


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """What one pass did. Counts and ids -- never payloads, never patient data (§54, §57)."""

    queued: tuple[uuid.UUID, ...] = ()
    still_unresolved: tuple[uuid.UUID, ...] = ()

    @property
    def attempted(self) -> int:
        return len(self.queued) + len(self.still_unresolved)


def reconcile_delivery_obligations(
    session_factory: sessionmaker[Session],
    *,
    organization_id: uuid.UUID | None = None,
    limit: int = DEFAULT_RECONCILE_LIMIT,
    now: Callable[[], datetime] = utc_now,
) -> ReconcileReport:
    """Try to queue every obligation that is still owed. One transaction per obligation.

    ``organization_id`` narrows the pass to one facility, which is what the heartbeat path wants --
    an agent reconnecting should not make DHealth walk every other facility's backlog. Omitted, it
    considers all of them, which is what an operator command wants.

    Bounded **and round-robin**: each pass takes the least recently attempted obligations, so a
    small limit on a frequent caller still reaches every owed row rather than grinding on whichever
    ones are oldest and unfixable.

    Per-obligation transactions for the reason ``apply_pending_inbound`` uses them: one obligation
    that cannot be discharged must not roll back the ones beside it, and the next pass must not hit
    the same poison row before reaching the rest.
    """
    moment = now()
    with session_factory() as session:
        query = select(ConnectorDeliveryObligation.id).where(still_owed_condition())
        if organization_id is not None:
            query = query.where(ConnectorDeliveryObligation.organization_id == organization_id)
        # **Least recently attempted first, not oldest first.** A bounded pass ordered by
        # ``created_at`` re-attempts the same oldest rows every time, so a facility holding five
        # permanently stuck obligations -- a build failure nobody has fixed, a source system that
        # will never get a connector -- would starve every newer, dischargeable one forever. Since
        # ``_reconcile_one`` stamps ``updated_at`` on every attempt, ordering by it is round-robin
        # for free. ``unresolved_obligations`` keeps the chronological order, because that is the
        # operator's read and theirs should not rotate.
        pending_ids = list(
            session.scalars(query.order_by(ConnectorDeliveryObligation.updated_at).limit(limit))
        )

    queued: list[uuid.UUID] = []
    unresolved: list[uuid.UUID] = []
    for obligation_id in pending_ids:
        if _reconcile_one(session_factory, obligation_id=obligation_id, moment=moment):
            queued.append(obligation_id)
        else:
            unresolved.append(obligation_id)
    return ReconcileReport(queued=tuple(queued), still_unresolved=tuple(unresolved))


def _reconcile_one(
    session_factory: sessionmaker[Session], *, obligation_id: uuid.UUID, moment: datetime
) -> bool:
    """One obligation, one transaction. ``True`` when it reached the queue."""
    with session_factory() as session:
        row = session.get(ConnectorDeliveryObligation, obligation_id)
        if row is None:  # pragma: no cover - nothing deletes obligations
            return False

        if row.state == ObligationState.QUEUED.value:
            # Selected because its delivery was orphaned by a retirement. Demote first, so that a
            # pass which cannot find a successor either leaves the row saying PENDING rather than
            # QUEUED: QUEUED has to keep meaning "a live delivery exists", or the operator read is
            # worse than useless. The orphaned delivery row itself is left alone -- §51 -- and the
            # successor gets a *new* one, because the idempotency key includes the installation.
            row.state = ObligationState.PENDING.value
            row.resolved_at = None

        resolved = _attempt(session, row, moment)
        row.updated_at = moment
        session.commit()
        return resolved


def _attempt(session: Session, row: ConnectorDeliveryObligation, moment: datetime) -> bool:
    """Rebuild and enqueue, or record why it still cannot be. Never raises for an ordinary fault."""
    order = session.get(LabOrder, row.lab_order_id)
    version = session.get(LabResultReleaseVersion, row.release_version_id)
    if order is None or version is None:  # pragma: no cover - both are RESTRICT foreign keys
        return False

    try:
        correlation = CorrelationRepository().order_correlation_for(
            session, organization_id=order.provider_organization_id, lab_order_id=row.lab_order_id
        )
    except CorrelationConflict as error:
        return _still_owed(row, FailureCategory.CONFLICT, str(error))
    if correlation is None:
        # The correlation this obligation was created from has gone. Nothing in the runtime deletes
        # one, so this is unreachable in practice and is left as an unresolved row rather than a
        # deletion -- §56: an operator seeing something inexplicable beats DHealth deciding it never
        # happened.
        return _still_owed(
            row, FailureCategory.CONFLICT, "no provider-side correlation for this order"
        )

    # Re-read the namespace from the correlation rather than trusting the obligation's copy: the
    # correlation is the authority, and an obligation written before the namespace was resolvable
    # (the ambiguous-correlation path) carries a placeholder.
    row.organization_id = correlation.namespace.organization_id
    row.source_system = correlation.namespace.source_system

    installation = eligible_installation(session, correlation.namespace)
    if installation is None:
        return _still_owed(
            row,
            FailureCategory.REMOTE_UNAVAILABLE,
            "no eligible connector installation for this source system",
        )

    try:
        envelope = build_notification_envelope(
            session,
            correlation=correlation,
            installation_id=installation.id,
            version=version,
            moment=moment,
        )
    except ValueError as error:
        return _still_owed(row, FailureCategory.VALIDATION, safe_failure_detail(error))

    try:
        delivery, _ = DeliveryRepository().enqueue(
            session,
            installation_id=installation.id,
            envelope=envelope,
            expected_direction=Direction.DHEALTH_TO_CONNECTOR,
            now=moment,
        )
    except DeliveryConflict as error:
        return _still_owed(
            row, FailureCategory.CONFLICT, f"delivery conflict: {type(error).__name__}"
        )

    row.state = ObligationState.QUEUED.value
    row.connector_delivery_id = delivery.id
    row.failure_category = None
    row.failure_detail = None
    row.resolved_at = moment
    return True


def _still_owed(row: ConnectorDeliveryObligation, category: FailureCategory, detail: str) -> bool:
    """Leave the obligation owed, with a current reason. Never resolves, never deletes."""
    row.failure_category = category.value
    row.failure_detail = detail[:512]
    row.resolved_at = None
    return False


def unresolved_obligations(
    session: Session, *, organization_id: uuid.UUID | None = None
) -> list[ConnectorDeliveryObligation]:
    """Everything still owed, oldest first. The operator-facing read this model exists to make
    possible -- before it, the answer to "which released results were never queued" was in a log."""
    query = select(ConnectorDeliveryObligation).where(still_owed_condition())
    if organization_id is not None:
        query = query.where(ConnectorDeliveryObligation.organization_id == organization_id)
    return list(session.scalars(query.order_by(ConnectorDeliveryObligation.created_at)))


__all__ = [
    "DEFAULT_RECONCILE_LIMIT",
    "ReconcileReport",
    "reconcile_delivery_obligations",
    "still_owed_condition",
    "unresolved_obligations",
]
