"""DH-NOTIFY-001 §26 — the deterministic provider CI uses, and what it deliberately does not prove.

**A green test against this class is not evidence that DHealth can notify a patient.** It is
evidence that the code above the transport boundary behaves correctly. ADR-003's pinned rule is
unchanged by this file's existence and is restated here because this is the exact file that would
make somebody think otherwise:

    WhatsApp/SMS stays BLOCKED, not DONE, until real credentials have run the flow end to end.
    A passing mock is never reported as a completion.

This exists so that the domain, the dispatcher, the retry budget, the webhook and the content policy
are all testable without a vendor account, and so that the native demo stack can run the whole
golden journey offline. It contacts nothing. It has no network code of any kind, which is the point:
a fake that could accidentally reach a real endpoint is a fake that will, from somebody's laptop,
to somebody's real phone.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field

from daher_adapter.notify.models import (
    NotificationChannel,
    NotificationEventType,
    NotificationFailureCategory,
)
from daher_adapter.notify.templates import DEFAULT_LOCALE, render
from daher_adapter.notify.transport import TransportAccepted, TransportFailure


@dataclass(slots=True)
class SentMessage:
    """What the fake was asked to send. Test-only; never persisted anywhere."""

    channel: NotificationChannel
    destination: str
    body: str


@dataclass
class FakeNotificationTransport:
    """An in-memory provider whose behaviour a test can script precisely.

    ``failures`` is a queue of outcomes consumed one per ``send``: ``None`` accepts, a
    ``TransportFailure`` is raised. That is what lets a test drive "fails twice with a rate limit,
    then succeeds" -- the retry path's real shape -- rather than only the two trivial ends of it.
    """

    supported_channels: frozenset[NotificationChannel] = field(
        default_factory=lambda: frozenset(NotificationChannel)
    )
    sent: list[SentMessage] = field(default_factory=list)
    failures: list[TransportFailure | None] = field(default_factory=list)
    _counter: itertools.count = field(default_factory=lambda: itertools.count(1))

    @property
    def provider_name(self) -> str:
        return "fake"

    def supports(self, channel: NotificationChannel) -> bool:
        return channel in self.supported_channels

    def send(
        self,
        *,
        channel: NotificationChannel,
        destination: str,
        event_type: NotificationEventType,
        locale: str = DEFAULT_LOCALE,
        patient_access_url: str | None = None,
    ) -> TransportAccepted:
        """Render the controlled template and record what would have gone out.

        **The rendering happens here, inside the transport**, which is where DH-NOTIFY-META-001
        moved it: Meta binds an event to an approved template rather than sending a body, so the
        intent is what crosses the boundary. This fake stands in for a transport that *does* send
        free text -- a future SMS provider -- so it renders ``templates.render()`` verbatim, which
        is also what keeps every DH-NOTIFY-001 content test asserting on ``sent[].body`` valid and
        meaningful rather than merely still compiling.
        """
        message = render(event_type, locale=locale, patient_access_url=patient_access_url)
        body = message.body
        outcome = self.failures.pop(0) if self.failures else None
        if isinstance(outcome, TransportFailure):
            # Recorded before raising: a test asserting "the body never contained a result value"
            # has to be able to inspect what a *failed* attempt tried to send, too.
            self.sent.append(SentMessage(channel=channel, destination=destination, body=body))
            raise outcome
        self.sent.append(SentMessage(channel=channel, destination=destination, body=body))
        return TransportAccepted(
            provider_message_id=f"FAKE{next(self._counter):08d}{uuid.uuid4().hex[:8]}",
            provider_status="queued",
            http_status_code=201,
        )

    def fail_next(
        self,
        category: NotificationFailureCategory,
        detail: str = "scripted failure",
        *,
        times: int = 1,
    ) -> None:
        for _ in range(times):
            self.failures.append(TransportFailure(category, detail))
