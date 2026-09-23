"""DH-CONNECT-AGENT-001 (provider side) — telling a provider's own system that its result is ready.

The mirror of ``outbox_hook``. That one fires when an order reaches a CONNECT *laboratory*; this one
fires when a laboratory releases, and carries the released report back to the CONNECT *provider*
that asked for it. Same rules, for the same reasons:

* **In the caller's transaction.** A release recorded and the ordering system not told are two facts
  that must never be able to disagree. Committing the delivery with the release makes the window
  where they could not exist.
* **Silence is the default and never an error.** A NATIVE provider, a provider with no connector, a
  suspended installation, a provider whose agent has never connected -- none of those produces a
  delivery or an exception. Release is a clinical act by a named professional; it does not fail
  because somebody's EMR integration is down.

**The correlation is required, and its absence is the ordinary "do not send" case.** A result
notification carries the provider's own ``external_order_id``, resolved through the binding the
submission created. An order placed *natively* in DHealth by a facility that also happens to run a
connector has no such binding -- the EMR never asked for it and has nothing to file it against -- so
no notification is queued. Sending one with a DHealth id the receiving system has never seen
would be a message that cannot be filed, which is worse than no message: it looks delivered.

**Payload is values and labels, exactly as released.** No demographics, no interpretation, no
normal/abnormal flag DHealth was not given. The observations are the release version's own manifest,
which is what makes this the released report rather than the current contents of the result table --
a distinction that matters precisely when an amendment is in flight.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    AmendmentNotification,
    Direction,
    Envelope,
    ResultNotification,
    ResultObservation,
)
from daher_adapter.connect.correlation import CorrelationRepository
from daher_adapter.connect.delivery import DeliveryConflict, DeliveryRepository
from daher_adapter.connect.models import ConnectorInstallation, ConnectorInstallationStatus
from daher_adapter.connect.outbox_hook import DHEALTH_SOURCE_SYSTEM
from daher_adapter.connect.runtime_models import ConnectorDelivery
from daher_adapter.exchange.models import (
    LabObservation,
    LabOrder,
    LabResultReleaseManifestEntry,
    LabResultReleaseVersion,
)
from daher_adapter.models import utc_now

logger = logging.getLogger("daher_adapter.connect.result_hook")


def result_notification_event_id(lab_order_id: uuid.UUID, release_version: int) -> str:
    """The idempotency key. Deterministic in the order **and the version**.

    The version is what keeps an amendment from colliding with the release it corrects: both are
    about one order, and a key naming only the order would make the correction look like a replay of
    the original and quietly drop it -- the one failure this whole path exists to prevent.
    """
    return f"result-notification:{lab_order_id}:{release_version}"


def notify_provider_of_release(
    session: Session,
    *,
    lab_order_id: uuid.UUID,
    release_version_id: uuid.UUID,
    now: datetime | None = None,
) -> ConnectorDelivery | None:
    """Queue a result or amendment notification for the ordering provider, if it takes one.

    Returns the delivery, or ``None`` when there is nothing to send -- see the module docstring for
    the four ordinary reasons there might not be. Runs on the caller's session so the row commits
    with the release.

    Which of the two messages is sent is decided by the version number and nothing else: version 1
    is the release, anything above it is a correction of the version before it. Reading the version
    number rather than taking a flag from the caller means the two release paths cannot disagree
    about what they just did.
    """
    order = session.get(LabOrder, lab_order_id)
    if order is None:  # pragma: no cover - the caller just wrote a release for this order
        return None

    installation = session.scalar(
        select(ConnectorInstallation).where(
            ConnectorInstallation.organization_id == order.provider_organization_id,
            ConnectorInstallation.status == ConnectorInstallationStatus.REGISTERED.value,
        )
    )
    if installation is None:
        return None

    external_order_id = CorrelationRepository().external_order_id_for(
        session, installation_id=installation.id, lab_order_id=lab_order_id
    )
    if external_order_id is None:
        return None

    version = session.get(LabResultReleaseVersion, release_version_id)
    if version is None:  # pragma: no cover - the caller just flushed this row
        return None
    observations = _released_observations(session, release_version_id)
    if not observations:  # pragma: no cover - a release always has at least one manifest entry
        return None

    moment = now or utc_now()
    try:
        payload: ResultNotification | AmendmentNotification
        if version.version_number == 1:
            payload = ResultNotification(
                external_order_id=external_order_id,
                release_version=version.version_number,
                observations=observations,
            )
        else:
            payload = AmendmentNotification(
                external_order_id=external_order_id,
                release_version=version.version_number,
                corrects_release_version=_corrected_version_number(session, version),
                observations=observations,
            )

        envelope = Envelope(
            contract_version=CONTRACT_VERSION,
            message_id=uuid.uuid4(),
            connector_installation_id=installation.id,
            organization_id=order.provider_organization_id,
            source_system=DHEALTH_SOURCE_SYSTEM,
            source_event_id=result_notification_event_id(lab_order_id, version.version_number),
            source_occurred_at=moment,
            sent_at=moment,
            lab_order_id=lab_order_id,
            payload=payload,
        )
    except ValueError:
        # A released report DHealth cannot express as a Connect message -- a value outside what the
        # contract's validators allow, or a chain the amendment rules refuse to describe. **The
        # release still commits.** A professional released a result; refusing that clinical act
        # because a notification would not validate is the failure this module's whole design is
        # arranged to prevent, and the laboratory has already done the work either way.
        #
        # Deliberately narrow: ``ValueError`` covers pydantic's own validation error and nothing
        # else, so a genuine programming fault still surfaces rather than being swallowed here.
        # Logged with no payload, no identifiers and no patient data (§54, §57); the operator-facing
        # signal is that the provider's system has no notification for a release DHealth holds.
        logger.warning(
            "connect result notification could not be built for a released version; "
            "the release is unaffected and no notification was queued"
        )
        return None

    try:
        delivery, _ = DeliveryRepository().enqueue(
            session,
            installation_id=installation.id,
            envelope=envelope,
            expected_direction=Direction.DHEALTH_TO_CONNECTOR,
            now=moment,
        )
    except DeliveryConflict:
        # The same order and version already queued with different content. DHealth's release
        # versions are immutable, so this is a genuine contradiction rather than a replay --
        # swallowed for the same reason the assignment hook swallows its own: refusing the release
        # over a notification conflict would block a clinical act on connector bookkeeping, and the
        # already-queued delivery stays visible to an operator.
        return None
    return delivery


def _corrected_version_number(session: Session, version: LabResultReleaseVersion) -> int:
    """Which version this amendment actually corrects, read rather than inferred.

    ``version_number - 1`` would be right today: ``release_amendment`` raises
    ``AmendmentBaseVersionSuperseded`` unless the amendment's base is still the latest version, so
    the new version always sits directly on top of its base. But that is an invariant somewhere
    else, and §77 forbids DHealth rewriting clinical chronology -- an amendment notification naming
    the wrong predecessor tells the receiving system to supersede a report that was never corrected.
    Reading the base version is the fact; arithmetic is a bet on another module not changing.

    Falls back to the arithmetic only if the chain cannot be read, which the amendment release path
    does not permit -- and the fallback is still the correct answer under today's invariant.
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
