"""DH-CONNECT-AGENT-001 (provider side) — telling a provider's own system that its result is ready.

The mirror of ``outbox_hook``. That one fires when an order reaches a CONNECT *laboratory*; this one
fires when a laboratory releases, and carries the released report back to the CONNECT *provider*
that asked for it.

**The order of the questions is the design** (0031, corrected). The first version asked them
backwards:

    find the currently registered installation  →  ask it for a correlation

which lets whichever connector happens to be registered *now* decide which external system
originally asked for the order. It also loses the work entirely when that connector is suspended,
and finds nothing at all when it has been replaced. The correct order is:

    the released DHealth order
      → the provider-side ORDER correlation
          → which tells us the organization, the source system and their own order id
      → the installation currently eligible for *that* namespace
      → queue the notification

The correlation is the durable fact; the installation is the transport that happens to be available.
Asking the transport who the correspondent is was the mistake.

**Silence has exactly one cause, and it is the honest one.** No provider-side correlation means this
order was placed natively inside DHealth: the EMR never asked for it and has no identifier to file
it against. Nothing is queued and nothing is recorded, because nothing is owed. Every *other* path
through this module leaves a durable row behind.

**The release always commits.** A professional released a result; refusing that clinical act because
an integration could not be arranged inverts the priority. But §11's invariant now holds with it:

    correlated CONNECT provider + professional release
      = a durable delivery, or a durable record that one is owed and why

never a release that committed with nothing anywhere recording that a provider is waiting. That is
what ``ConnectorDeliveryObligation`` is for, and why every exit below writes one.

**Suspension does not delete an obligation.** A suspended connector means "do not carry traffic
now", not "forget the work that became due while you were away". The delivery is queued against the
suspended installation and simply waits: ``resolve_service_principal`` refuses that installation
until it is restored, so nothing can claim the message, and the moment it is restored the agent
drains it. No clinical result disappears because an operator suspended a connector for an afternoon.

**Health is not a prerequisite.** An installation whose agent has never connected still gets the
delivery. Health decides whether *new* production work should be routed to a facility; it does not
erase work already owed (§84, and §16 of this correction). The queue is precisely the mechanism that
lets an agent catch up when it returns.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    AmendmentNotification,
    Direction,
    Envelope,
    FailureCategory,
    MessageType,
    ResultNotification,
    ResultObservation,
)
from daher_adapter.connect.correlation import (
    CorrelationConflict,
    CorrelationNamespace,
    CorrelationRepository,
    OrderCorrelation,
)
from daher_adapter.connect.delivery import DeliveryConflict, DeliveryRepository
from daher_adapter.connect.models import ConnectorInstallation, ConnectorInstallationStatus
from daher_adapter.connect.outbox_hook import DHEALTH_SOURCE_SYSTEM
from daher_adapter.connect.runtime_models import (
    ConnectorDelivery,
    ConnectorDeliveryObligation,
    ObligationState,
)
from daher_adapter.exchange.models import (
    LabObservation,
    LabOrder,
    LabResultReleaseManifestEntry,
    LabResultReleaseVersion,
)
from daher_adapter.models import utc_now

#: Statuses an installation may hold and still be handed work for its namespace, in preference
#: order. ``SUSPENDED`` is included deliberately: the delivery waits in the queue, unclaimable until
#: the installation is restored, which is what makes suspension a pause rather than an erasure.
#: ``RETIRED`` is absent and must stay absent -- a retired installation never authenticates again,
#: so a delivery queued against it would wait forever while looking queued.
_ELIGIBLE_STATUSES = (
    ConnectorInstallationStatus.REGISTERED.value,
    ConnectorInstallationStatus.SUSPENDED.value,
)


#: The source system recorded on an obligation whose namespace could not be resolved. A real system
#: name would be a guess, and an empty string reads like a bug rather than a decision; this is a
#: value an operator can search for, and the only path that writes it is the ambiguous-correlation
#: refusal below -- which is exactly the state a human has to settle.
UNRESOLVED_SOURCE_SYSTEM = "?unresolved"


def result_notification_event_id(lab_order_id: uuid.UUID, release_version: int) -> str:
    """The idempotency key. Deterministic in the order **and the version**.

    The version is what keeps an amendment from colliding with the release it corrects: both are
    about one order, and a key naming only the order would make the correction look like a replay of
    the original and quietly drop it -- the one failure this whole path exists to prevent.

    Deterministic also across *time*, which is what lets an obligation recorded today be discharged
    by a reconciliation next week and still converge on one delivery rather than a second.
    """
    return f"result-notification:{lab_order_id}:{release_version}"


@dataclass(frozen=True, slots=True)
class ObligationOutcome:
    """What the hook concluded. Returned for the caller's tests, never for its control flow."""

    obligation: ConnectorDeliveryObligation | None
    delivery: ConnectorDelivery | None


def notify_provider_of_release(
    session: Session,
    *,
    lab_order_id: uuid.UUID,
    release_version_id: uuid.UUID,
    now: datetime | None = None,
) -> ObligationOutcome:
    """Record what is owed to the ordering provider, and queue it if anything can carry it.

    Runs on the caller's session so everything it writes commits with the release. Never raises for
    an ordinary integration problem -- the outcome is written down instead.
    """
    moment = now or utc_now()
    order = session.get(LabOrder, lab_order_id)
    if order is None:  # pragma: no cover - the caller just wrote a release for this order
        return ObligationOutcome(None, None)

    version = session.get(LabResultReleaseVersion, release_version_id)
    if version is None:  # pragma: no cover - the caller just flushed this row
        return ObligationOutcome(None, None)
    # Read before the correlation lookup, so that even the path where the namespace cannot be
    # resolved records *which* message was owed. An obligation that said RESULT_NOTIFICATION for a
    # correction would send an operator looking for the wrong thing.
    message_type = (
        MessageType.RESULT_NOTIFICATION
        if version.version_number == 1
        else MessageType.AMENDMENT_NOTIFICATION
    )
    event_id = result_notification_event_id(lab_order_id, version.version_number)

    try:
        correlation = CorrelationRepository().order_correlation_for(
            session, organization_id=order.provider_organization_id, lab_order_id=lab_order_id
        )
    except CorrelationConflict as error:
        # Two source systems at one facility both claiming to have ordered this. Refusing to choose
        # is the whole of §43: there is no rule that makes one of them more likely to be right, and
        # picking either would file a clinical report into a system that did not ask for it.
        # Recorded as owed-and-unresolved, with the namespace left to an operator to settle.
        return ObligationOutcome(
            _record(
                session,
                order=order,
                namespace=CorrelationNamespace(
                    order.provider_organization_id, UNRESOLVED_SOURCE_SYSTEM
                ),
                release_version_id=release_version_id,
                message_type=message_type,
                source_event_id=event_id,
                state=ObligationState.PENDING,
                category=FailureCategory.CONFLICT,
                detail=str(error),
                moment=moment,
            ),
            None,
        )

    if correlation is None:
        # Placed natively in DHealth. The EMR never asked for it and has no identifier to file it
        # against, so nothing is owed -- and sending a DHealth id the receiving system has never
        # seen would be a message it cannot place, which is worse than none because it looks
        # delivered.
        return ObligationOutcome(None, None)

    installation = eligible_installation(session, correlation.namespace)
    if installation is None:
        # No connector can carry this: retired with no successor, or replaced by one registered for
        # a *different* source system. Both are states an operator has to resolve, and neither is a
        # reason to lose the report -- §9. Deliberately not routed to whatever connector a
        # facility does have: that would deliver MedSys's order to Epic.
        return ObligationOutcome(
            _record(
                session,
                order=order,
                namespace=correlation.namespace,
                release_version_id=release_version_id,
                message_type=message_type,
                source_event_id=event_id,
                state=ObligationState.PENDING,
                category=FailureCategory.REMOTE_UNAVAILABLE,
                detail="no eligible connector installation for this source system",
                moment=moment,
            ),
            None,
        )

    return _queue(
        session,
        order=order,
        correlation=correlation,
        installation=installation,
        version=version,
        message_type=message_type,
        event_id=event_id,
        moment=moment,
    )


def _queue(
    session: Session,
    *,
    order: LabOrder,
    correlation: OrderCorrelation,
    installation: ConnectorInstallation,
    version: LabResultReleaseVersion,
    message_type: MessageType,
    event_id: str,
    moment: datetime,
) -> ObligationOutcome:
    """Build the notification and hand it to the durable queue, recording whichever happened."""
    try:
        envelope = build_notification_envelope(
            session,
            correlation=correlation,
            installation_id=installation.id,
            version=version,
            moment=moment,
        )
    except ValueError as error:
        # A released report DHealth cannot express as a Connect message. **The release still
        # commits**, and -- the whole point of 0031 -- the failure is now a row rather than a log
        # line. A log line cannot be queried after a restart, so "which released results were never
        # queued, and can they be recovered" had no answer at all.
        return ObligationOutcome(
            _record(
                session,
                order=order,
                namespace=correlation.namespace,
                release_version_id=version.id,
                message_type=message_type,
                source_event_id=event_id,
                state=ObligationState.FAILED,
                category=FailureCategory.VALIDATION,
                detail=safe_failure_detail(error),
                moment=moment,
            ),
            None,
        )

    try:
        delivery, _ = DeliveryRepository().enqueue(
            session,
            installation_id=installation.id,
            envelope=envelope,
            expected_direction=Direction.DHEALTH_TO_CONNECTOR,
            now=moment,
        )
    except DeliveryConflict as error:
        # The same order and version already queued with *different* content. DHealth's release
        # versions are immutable, so this is a genuine contradiction rather than a replay -- and it
        # is now durably visible (§15) instead of being inferable only by noticing an old queue row.
        return ObligationOutcome(
            _record(
                session,
                order=order,
                namespace=correlation.namespace,
                release_version_id=version.id,
                message_type=message_type,
                source_event_id=event_id,
                state=ObligationState.FAILED,
                category=FailureCategory.CONFLICT,
                detail=f"delivery conflict: {type(error).__name__}",
                moment=moment,
            ),
            None,
        )

    obligation = _record(
        session,
        order=order,
        namespace=correlation.namespace,
        release_version_id=version.id,
        message_type=message_type,
        source_event_id=event_id,
        state=ObligationState.QUEUED,
        category=None,
        detail=None,
        moment=moment,
        delivery_id=delivery.id,
    )
    return ObligationOutcome(obligation, delivery)


def build_notification_envelope(
    session: Session,
    *,
    correlation: OrderCorrelation,
    installation_id: uuid.UUID,
    version: LabResultReleaseVersion,
    moment: datetime,
) -> Envelope:
    """Rebuild the notification for one released version, from immutable facts only.

    Public because reconciliation calls it too, and calling the *same* function is the point: an
    obligation recorded today and discharged next week must produce the message the hook would have
    produced, not a second construction path that drifted. Everything it reads -- the release
    version, its manifest, the correlation -- is immutable, so the message is a function of stored
    clinical truth rather than of when it happened to be built.
    """
    observations = _released_observations(session, version.id)
    if not observations:  # pragma: no cover - a release always has at least one manifest entry
        raise ValueError("release version has no manifest entries")

    payload: ResultNotification | AmendmentNotification
    if version.version_number == 1:
        payload = ResultNotification(
            external_order_id=correlation.external_order_id,
            release_version=version.version_number,
            observations=observations,
        )
    else:
        payload = AmendmentNotification(
            external_order_id=correlation.external_order_id,
            release_version=version.version_number,
            corrects_release_version=_corrected_version_number(session, version),
            observations=observations,
        )

    return Envelope(
        contract_version=CONTRACT_VERSION,
        message_id=uuid.uuid4(),
        connector_installation_id=installation_id,
        organization_id=correlation.namespace.organization_id,
        source_system=DHEALTH_SOURCE_SYSTEM,
        source_event_id=result_notification_event_id(
            correlation.lab_order_id, version.version_number
        ),
        source_occurred_at=moment,
        sent_at=moment,
        lab_order_id=correlation.lab_order_id,
        payload=payload,
    )


def eligible_installation(
    session: Session, namespace: CorrelationNamespace
) -> ConnectorInstallation | None:
    """The installation that may carry work for this namespace right now, if there is one.

    **Matched on the source system, not merely on the organization.** A facility that migrated from
    MedSys to Epic has an installation registered for Epic, and Epic's connector must not be handed
    a report for an order MedSys placed -- their ``ORDER-123`` values are different orders that
    happen to share a string. No fuzzy handoff, no "the facility only has one connector so it must
    be the right one".

    Suspended installations are eligible on purpose; retired ones never are. At most one non-retired
    installation exists per organization (``uq_connector_installations_one_live_per_org``), so this
    cannot be ambiguous -- and it still refuses to choose if that ever stops being true, because a
    silent pick is how a report reaches the wrong system.
    """
    rows = session.scalars(
        select(ConnectorInstallation).where(
            ConnectorInstallation.organization_id == namespace.organization_id,
            ConnectorInstallation.source_system == namespace.source_system,
            ConnectorInstallation.status.in_(_ELIGIBLE_STATUSES),
        )
    ).all()
    if len(rows) != 1:
        return None
    return rows[0]


def _record(
    session: Session,
    *,
    order: LabOrder,
    namespace: CorrelationNamespace,
    release_version_id: uuid.UUID,
    message_type: MessageType,
    source_event_id: str,
    state: ObligationState,
    category: FailureCategory | None,
    detail: str | None,
    moment: datetime,
    delivery_id: uuid.UUID | None = None,
) -> ConnectorDeliveryObligation:
    """Write or update the one obligation row for this release version.

    Upsert rather than insert, because the release path can legitimately be re-entered -- and
    because reconciliation moves the same row from PENDING or FAILED to QUEUED rather than writing a
    second one. The unique key on ``release_version_id`` is what makes that safe without a lock.
    """
    existing = session.scalar(
        select(ConnectorDeliveryObligation).where(
            ConnectorDeliveryObligation.release_version_id == release_version_id
        )
    )
    row = existing or ConnectorDeliveryObligation(
        organization_id=namespace.organization_id,
        source_system=namespace.source_system,
        lab_order_id=order.id,
        release_version_id=release_version_id,
        intended_message_type=message_type.value,
        source_event_id=source_event_id,
        created_at=moment,
    )
    row.state = state.value
    row.failure_category = None if category is None else category.value
    row.failure_detail = detail
    row.connector_delivery_id = delivery_id
    row.updated_at = moment
    row.resolved_at = moment if state is ObligationState.QUEUED else None
    if existing is None:
        session.add(row)
    session.flush()
    return row


def safe_failure_detail(error: ValueError) -> str:
    """An operator-readable reason that cannot carry clinical content.

    ``str(ValidationError)`` quotes the value that failed -- ``input_value=...`` -- and for an
    observation label or a text result that value *is* patient data. §57 forbids putting it in an
    operational record, so what survives is the shape of the fault: which fields failed and what
    kind of failure it was. That is what an operator needs to fix a configuration anyway.
    """
    errors = getattr(error, "errors", None)
    if not callable(errors):
        return type(error).__name__
    try:
        fields = sorted({".".join(str(part) for part in item.get("loc", ())) for item in errors()})
        kinds = sorted({str(item.get("type", "unknown")) for item in errors()})
    except Exception:  # noqa: BLE001 - a detail we cannot read is not worth failing a release over
        return type(error).__name__
    return f"{type(error).__name__}: fields={', '.join(fields)[:300]}; kinds={', '.join(kinds)}"[
        :512
    ]


def _corrected_version_number(session: Session, version: LabResultReleaseVersion) -> int:
    """Which version this amendment corrects, read rather than inferred.

    ``version_number - 1`` would be right today: ``release_amendment`` raises
    ``AmendmentBaseVersionSuperseded`` unless the amendment's base is still the latest version, so
    the new version always sits directly on top of its base. But that is an invariant somewhere
    else, and §77 forbids DHealth rewriting clinical chronology -- an amendment notification naming
    the wrong predecessor tells the receiving system to supersede a report that was never corrected.
    Reading the base version is the fact; arithmetic is a bet on another module not changing.
    """
    from daher_adapter.exchange.models import LabResultAmendment

    if version.amendment_id is not None:
        amendment = session.get(LabResultAmendment, version.amendment_id)
        if amendment is not None:
            base = session.get(LabResultReleaseVersion, amendment.base_release_version_id)
            if base is not None:
                return base.version_number
    return version.version_number - 1  # pragma: no cover - unreachable via release_amendment


def _released_observations(
    session: Session, release_version_id: uuid.UUID
) -> tuple[ResultObservation, ...]:
    """The observations **this version released**, from its manifest rather than from the order.

    The manifest is the point. Reading the order's current observations would send whatever the
    result table holds at the moment the message is built, which during an amendment is the
    corrected value -- so the original release notification would carry the correction, and the
    amendment notification that followed would carry the same thing again. The version's manifest is
    immutable and says what was actually released under that version number.

    It is also what makes reconciliation honest: rebuilding from the manifest months later produces
    the report as released, not as the record looks now.

    Values, units and ranges are relayed exactly as the laboratory recorded them. DHealth does not
    normalize a unit, convert a range or infer normal/abnormal on the way out any more than it did
    on the way in.
    """
    rows = session.scalars(
        select(LabObservation)
        .join(
            LabResultReleaseManifestEntry,
            LabResultReleaseManifestEntry.lab_observation_id == LabObservation.id,
        )
        .where(LabResultReleaseManifestEntry.release_version_id == release_version_id)
        .order_by(LabObservation.source_code)
    ).all()
    return tuple(
        ResultObservation(
            source_code=row.source_code,
            display_label=row.display_label_snapshot,
            result_type=row.result_type,
            numeric_value=row.numeric_value,
            numeric_unit=row.numeric_unit,
            reference_range_low=row.reference_range_low,
            reference_range_high=row.reference_range_high,
            text_value=row.text_value,
        )
        for row in rows
    )
