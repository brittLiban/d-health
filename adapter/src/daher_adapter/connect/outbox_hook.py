"""DH-CONNECT-AGENT-001 §83 — telling a CONNECT laboratory that it has work.

One function, called from the two places an order becomes SELECTED at a laboratory. It decides
whether that laboratory is reachable through DHealth Connect and, if so, queues the order
notification **in the same transaction as the selection itself**.

The same transaction is the whole design. An order assigned and a laboratory not told are two facts
that must never be able to disagree: if the notification were enqueued afterwards, a crash between
them would produce an order sitting at a laboratory that has no idea it exists, with nothing
anywhere recording that DHealth meant to tell them. Committing together makes that window not exist.

**Silence is the default, and it is never an error.** A NATIVE laboratory, a laboratory with no
connector, a suspended or retired installation, a laboratory whose agent has never connected --
every one of those produces no delivery and no exception. Assignment is a clinical decision that
must succeed on its own terms; a connector problem is not a reason to refuse a clinician's order.
The laboratory simply works in DHealth directly, which is what it was already doing.

**Why the installation's status is checked and its health is not.** A suspended installation is a
human decision to stop it carrying traffic, so queueing for it would pile up work nobody will take.
An *unhealthy* one is different: the agent is expected back, and the queue is exactly the mechanism
that lets it catch up on return. Refusing to enqueue while a connector is briefly down would turn a
five-minute outage into permanently lost orders -- the opposite of what a durable queue is for (§84:
an offline runtime never causes assigned orders to be cancelled, or dropped).

**Known asymmetry, recorded rather than quietly fixed.** ``result_hook`` no longer treats a
*suspended* installation as a reason to produce nothing: 0031 queues the notification against the
suspended installation, where it waits unclaimable until restoration, and records a durable
obligation either way. This hook still returns silently for a suspended laboratory, so an order
assigned during a suspension is not queued and nothing records that it was owed. That is the same
class of defect in the other direction. It is out of scope for the provider-side correction and is
tracked in ``PROJECT_STATE.md``; the sentence above describes what this module does today, not what
it should do.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    Direction,
    Envelope,
    OrderNotification,
    ScopedExternalId,
    SourcePractitionerRef,
)
from daher_adapter.connect.delivery import DeliveryConflict, DeliveryRepository
from daher_adapter.connect.models import ConnectorInstallation, ConnectorInstallationStatus
from daher_adapter.connect.runtime_models import ConnectorDelivery
from daher_adapter.exchange.models import (
    LabOrder,
    LabOrderItem,
    PatientReference,
    PractitionerReference,
)
from daher_adapter.models import utc_now

#: The source system DHealth uses when it is the sender. Fixed rather than derived from the
#: laboratory, because the replay scope is ``organization:source_system`` and a varying value would
#: put DHealth's own notifications in a different scope per facility for no reason.
DHEALTH_SOURCE_SYSTEM = "dhealth"


def order_notification_event_id(lab_order_id: uuid.UUID) -> str:
    """The idempotency key. Deterministic in the order, so the hook needs no memory of its own.

    An assignment that fires twice -- a retried request, a replayed call -- converges on the single
    delivery row the first one created, because the queue's unique key is
    ``(installation, direction, replay_scope, source_event_id)`` and this is that last part.
    """
    return f"order-notification:{lab_order_id}"


def notify_connector_of_assignment(
    session: Session,
    *,
    lab_order_id: uuid.UUID,
    lab_organization_id: uuid.UUID,
    now: datetime | None = None,
) -> ConnectorDelivery | None:
    """Queue an order notification if this laboratory takes work through Connect. Else do nothing.

    Returns the delivery, or ``None`` when there is nothing to notify. Never raises for an ordinary
    "not a Connect laboratory" -- see the module docstring on why silence is correct here.

    Runs on the caller's session, deliberately, so the row commits with the selection.

    The installation lookup comes **first**, before any of the order's identity rows are read.
    Almost every assignment in this system is to a NATIVE laboratory, so doing the cheap
    disqualifying query first means the common path costs one indexed read rather than four joins
    for an answer that was always going to be None.
    """
    installation = session.scalar(
        select(ConnectorInstallation).where(
            ConnectorInstallation.organization_id == lab_organization_id,
            ConnectorInstallation.status == ConnectorInstallationStatus.REGISTERED.value,
        )
    )
    if installation is None:
        return None

    identities = _order_identities(session, lab_order_id)
    if identities is None:
        return None
    patient, practitioner, catalog_codes = identities

    moment = now or utc_now()
    envelope = Envelope(
        contract_version=CONTRACT_VERSION,
        message_id=uuid.uuid4(),
        connector_installation_id=installation.id,
        organization_id=lab_organization_id,
        source_system=DHEALTH_SOURCE_SYSTEM,
        source_event_id=order_notification_event_id(lab_order_id),
        # The order was assigned now, and DHealth is sending it now. Unlike an inbound message --
        # where the partner's event time and their transmission time genuinely differ, sometimes by
        # days -- DHealth is the source here, so the two coincide honestly rather than by accident.
        source_occurred_at=moment,
        sent_at=moment,
        lab_order_id=lab_order_id,
        payload=OrderNotification(
            patient=patient,
            practitioner=practitioner,
            catalog_codes=catalog_codes,
        ),
    )

    try:
        delivery, _ = DeliveryRepository().enqueue(
            session,
            installation_id=installation.id,
            envelope=envelope,
            expected_direction=Direction.DHEALTH_TO_CONNECTOR,
            now=moment,
        )
    except DeliveryConflict:
        # The same order already queued with different content. Reachable only if an order's items
        # or patient changed after assignment, which the Exchange does not permit -- so it is a
        # genuine contradiction rather than a replay. Swallowed rather than raised: refusing the
        # *assignment* over a notification conflict would block a clinician's order on a connector
        # bookkeeping problem, and the existing delivery is already queued and correct as of when it
        # was written. It stays visible in the queue for an operator.
        return None
    return delivery


def _order_identities(
    session: Session, lab_order_id: uuid.UUID
) -> tuple[ScopedExternalId, SourcePractitionerRef, tuple[str, ...]] | None:
    """The patient, practitioner and ordered codes, as the notification carries them.

    The identifiers are the **ordering provider's**, taken from the reference rows DHealth already
    holds -- not re-derived, not re-scoped to the laboratory, and not enriched. A laboratory learns
    who the provider says this patient is, which is what an order means; DHealth has no
    laboratory-side identity to offer and does not invent one.

    ``catalog_code_snapshot`` rather than a live catalog read: the snapshot is what the order was
    placed for, and a catalog entry renamed afterwards must not silently change what the laboratory
    is asked to run.

    Returns ``None`` if anything is missing, which sends the caller down the "do not notify" path
    rather than raising. A notification is not worth failing a clinical assignment over.
    """
    order = session.get(LabOrder, lab_order_id)
    if order is None:  # pragma: no cover - the caller just locked this row
        return None

    patient_row = session.get(PatientReference, order.patient_reference_id)
    practitioner_row = session.get(PractitionerReference, order.practitioner_reference_id)
    if patient_row is None or practitioner_row is None:  # pragma: no cover - both are non-null FKs
        return None

    codes = tuple(
        session.scalars(
            select(LabOrderItem.catalog_code_snapshot)
            .where(LabOrderItem.lab_order_id == lab_order_id)
            .order_by(LabOrderItem.catalog_code_snapshot)
        ).all()
    )
    if not codes:  # pragma: no cover - an order always has at least one item
        return None

    return (
        ScopedExternalId(
            source_organization_id=patient_row.source_organization_id,
            source_system=patient_row.source_system,
            source_identity_system=patient_row.source_identity_system,
            source_identity_value=patient_row.source_identity_value,
        ),
        SourcePractitionerRef(
            source_organization_id=practitioner_row.source_organization_id,
            source_system=practitioner_row.source_system,
            source_identity_system=practitioner_row.source_identity_system,
            source_identity_value=practitioner_row.source_identity_value,
        ),
        codes,
    )
