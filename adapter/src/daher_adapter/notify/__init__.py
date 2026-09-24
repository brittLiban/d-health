"""DH-NOTIFY-001 — patient notification: a content-free nudge, and nothing more.

This package sends a patient a message saying that something about their laboratory order has
changed, and that they should open the secure status path they already hold or contact their
facility. It never sends a result, a value, a unit, a reference range, an interpretation, a
follow-up disposition, a diagnosis, an identifier, or the name of a test.

Five modules do the work and one boundary matters more than the rest:

``models``       the durable domain. Separate from ``ResultDelivery``, which means something
                 entirely different (the ordering *provider organization*) and which nothing here
                 reads, writes, or reinterprets.
``templates``    the complete set of strings that can ever be sent. This is the content policy,
                 enforced by there being no other way to produce a body.
``phone``        E.164 normalization via libphonenumber. No country guessing.
``transport``    the vendor boundary. ``meta`` implements it (the WhatsApp Cloud API, direct --
                 ADR-059); ``fake`` is CI's. No SMS transport is selected, so SMS is a channel the
                 domain understands and no deployment can currently send on.
``repository``   idempotency, the claim lease, the retry budget -- the database's job, not the
                 application's.
``hooks``        where an obligation becomes owed, in the release's own transaction.
``dispatcher``   the runtime that drains the queue, outside it.
``webhook``      the only writer of ``DELIVERED``, and only after a signature check.

**ADR-003's gate is unchanged.** A passing test against ``fake.FakeNotificationTransport`` proves
the code above the transport boundary. It is not evidence that DHealth can notify a patient, and
WhatsApp stays ``BLOCKED`` until real Meta credentials have run the flow end to end -- see
``docs/META-WHATSAPP-RUNBOOK.md`` for what "end to end" means and what has actually been run.
"""

from daher_adapter.notify.models import (
    NotificationChannel,
    NotificationEventType,
    NotificationFailureCategory,
    NotificationState,
    PatientNotification,
    PatientNotificationAttempt,
    PatientNotificationEnrollment,
)
from daher_adapter.notify.transport import (
    NotificationsDisabled,
    NotificationTransport,
    TransportAccepted,
    TransportFailure,
)

__all__ = [
    "NotificationChannel",
    "NotificationEventType",
    "NotificationFailureCategory",
    "NotificationState",
    "NotificationTransport",
    "NotificationsDisabled",
    "PatientNotification",
    "PatientNotificationAttempt",
    "PatientNotificationEnrollment",
    "TransportAccepted",
    "TransportFailure",
]
