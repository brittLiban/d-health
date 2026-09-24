"""DH-NOTIFY-001 §11/§12 — where a patient notification becomes owed.

Two functions, called from the two places a professional publishes a release. They run **on the
caller's session**, so the obligation commits with the release itself. That is the entire design,
and it is the same one ``connect/outbox_hook`` and ``connect/result_hook`` already use for the
provider side: a result released and the patient not owed a message are two facts that must never be
able to disagree, and enqueueing afterwards is exactly the window where they can.

**The provider call is not here.** Nothing in this module opens a socket. §12 forbids holding a
clinical release transaction open across vendor network I/O, and more importantly forbids a
messaging vendor being able to roll back a legitimate result release. The dispatcher picks the row
up afterwards, in its own transaction, and a provider outage leaves the release released and the
message durably owed.

**Silence is the default and never an error.** No enrollment for this order -- which is the ordinary
case, because notification is optional (§19) -- produces no row and no exception. A patient who
never agreed to be messaged, or who has no phone, still gets the QR, the printed sheet and their
clinic, which is Law 4's whole point.

**What does *not* reach here**, per §11: an observation entered, an observation corrected before
release, an order accepted or started, a page viewed, a test rerun, a release endpoint retried
idempotently. The first four never call these functions at all; the last two are absorbed by
``enqueue``'s deterministic key, which is a database UNIQUE rather than a hopeful check.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from daher_adapter.notify.models import (
    NotificationChannel,
    NotificationEventType,
    PatientNotification,
    PatientNotificationEnrollment,
)
from daher_adapter.notify.repository import PatientNotificationRepository, release_key


def notify_patient_of_release(
    session: Session, *, lab_order_id: uuid.UUID, release_version_id: uuid.UUID
) -> PatientNotification | None:
    """Owe the patient a content-free "there is an update" message for an original release."""
    return _owe(
        session,
        lab_order_id=lab_order_id,
        release_version_id=release_version_id,
        event_type=NotificationEventType.RESULT_RELEASED,
    )


def notify_patient_of_amendment(
    session: Session, *, lab_order_id: uuid.UUID, release_version_id: uuid.UUID
) -> PatientNotification | None:
    """Owe the patient a message for an amendment release.

    A **distinct** obligation from the original release's, because the key names the new immutable
    release version. This is the one case where a too-clever idempotency key would cause real harm:
    keyed on the order, the amendment would be indistinguishable from a replay of the release it
    corrects, silently dropped, and the patient would never learn the result had changed.
    """
    return _owe(
        session,
        lab_order_id=lab_order_id,
        release_version_id=release_version_id,
        event_type=NotificationEventType.AMENDMENT_RELEASED,
    )


def _owe(
    session: Session,
    *,
    lab_order_id: uuid.UUID,
    release_version_id: uuid.UUID,
    event_type: NotificationEventType,
) -> PatientNotification | None:
    repository = PatientNotificationRepository()
    enrollment = repository.active_enrollment(session, lab_order_id=lab_order_id)
    if enrollment is None:
        return None

    notification, _created = repository.enqueue(
        session,
        idempotency_key=release_key(
            event_type,
            release_version_id,
            _channel(enrollment),
            enrollment.destination,
        ),
        lab_order_id=lab_order_id,
        release_version_id=release_version_id,
        enrollment=enrollment,
        event_type=event_type,
    )
    return notification


def _channel(enrollment: PatientNotificationEnrollment) -> NotificationChannel:
    """The stored channel as its enum. A bad value raises rather than defaulting to SMS -- a row
    whose channel the code does not recognise is a schema/CHECK failure, not a routing choice."""
    return NotificationChannel(enrollment.channel)
