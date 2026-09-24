"""DH-NOTIFY-001 §9 — the boundary between "when to send" and "how this vendor wants it".

Everything above this line is DHealth's: the obligation, its idempotency, its retry budget, its
destination, its template, its audit. Everything below is the vendor's: authentication, request
shape, timeouts, response parsing, their message id, their error vocabulary.

The split exists so that replacing Twilio with Meta's WhatsApp Cloud API -- or adding a second
provider for a second country -- is one new class implementing three methods, with no change to a
single clinical or workflow file. ADR-002 already anticipated exactly this ("a notifier module
inside the adapter calls the WhatsApp Cloud API (or Twilio) directly"), and the interface is what
keeps that a real option rather than a sentence in a document.

**No vendor identifier is ever part of an Exchange clinical API** (§9). ``provider_message_id`` is
stored and used to resolve an authenticated delivery receipt, and it appears in the operations read
for an operator. It does not appear on an order, a result, a review, or anything a clinician sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from daher_adapter.notify.models import NotificationChannel, NotificationFailureCategory


@dataclass(frozen=True, slots=True)
class TransportAccepted:
    """The provider's API accepted the request. Nothing more is claimed.

    This is what becomes ``SENT``, and §7 is emphatic about the difference: an HTTP 201 from a
    messaging vendor means a row was created in *their* queue. It does not mean a handset saw
    anything. ``DELIVERED`` requires an authenticated receipt and is written in exactly one place.

    ``provider_status`` is the vendor's own word for where the message is (Twilio: ``queued``,
    ``accepted``). Recorded for an operator reading the attempt history; never interpreted as
    delivery.
    """

    provider_message_id: str
    provider_status: str
    http_status_code: int


class TransportFailure(RuntimeError):
    """A send that did not happen, classified into DHealth's own vocabulary.

    The adapter maps the vendor's error to a ``NotificationFailureCategory`` and writes **one short
    sentence of its own** as ``detail``. It never carries the provider's response body forward:
    those echo the destination, sometimes the rendered body -- which for an access-link message is a
    live patient bearer credential -- and occasionally the account identifier.

    ``http_status_code`` is kept because a number cannot leak any of those things.
    """

    def __init__(
        self,
        category: NotificationFailureCategory,
        detail: str,
        *,
        http_status_code: int | None = None,
    ) -> None:
        super().__init__(f"{category.value}: {detail}")
        self.category = category
        # Bounded at the boundary, not at the database. A caller building a detail from a provider
        # string would otherwise be one refactor away from persisting an unbounded vendor message.
        self.detail = detail[:256]
        self.http_status_code = http_status_code


class NotificationTransport(Protocol):
    """Three methods, no clinical vocabulary.

    ``send`` takes a channel, an E.164 destination and a finished body. It does not take an order,
    a patient, a result, a template id or a locale -- a transport that could see any of those would
    eventually be asked to make a decision using one.
    """

    @property
    def provider_name(self) -> str:
        """Stable identifier recorded in operational reads. Not a secret, not a URL."""
        ...

    def supports(self, channel: NotificationChannel) -> bool:
        """Whether this provider is configured to send on ``channel``.

        Separate from ``send`` raising, because an unsupported channel is a *configuration* fact
        knowable before an obligation is created -- so the enrollment endpoint can refuse at the
        point a clinician is still looking at the screen, rather than producing an obligation that
        can only ever fail.
        """
        ...

    def send(
        self, *, channel: NotificationChannel, destination: str, body: str
    ) -> TransportAccepted:
        """Hand one message to the provider. Raises ``TransportFailure`` if it was not accepted."""
        ...


class NotificationsDisabled(RuntimeError):
    """Sending is switched off for this deployment.

    Deliberately distinct from every ``TransportFailure``: §10 requires "notifications disabled" and
    "notifications configured but failing" to be operationally different states. Disabled means no
    obligation should have been created and nothing is wrong with the provider; failing means
    somebody needs to look at credentials or at the vendor's status page. Collapsing them would put
    a development deployment's deliberate silence in the same alarm bucket as a production outage.
    """
