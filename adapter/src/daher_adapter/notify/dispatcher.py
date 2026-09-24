"""DH-NOTIFY-001 §14 — the runtime that drains the queue.

**No new infrastructure** (§14). No Kafka, no Celery, no Redis, no RabbitMQ. The server already has
a transactional PostgreSQL database holding the clinical events these messages are about, and
putting the queue there is what lets an obligation and the release that caused it commit together.
A broker would make those two independent facts that fail independently, and the first bug would be
a released result with nobody owed anything and nothing anywhere recording that DHealth meant to
tell them.

This is deliberately the same shape as ``connect/delivery.py``'s engine, which is already qualified
against real PostgreSQL in this repository: claim a bounded batch under a lease with
``FOR UPDATE SKIP LOCKED``, act, report, back off, stop. Reusing a proven shape is the point --
a second, subtly different queue is a second set of concurrency bugs.

**One transaction per message, not per batch** (§14). A poisoned row that raises while being
processed rolls back its own transaction and nothing else; every other message in the batch has
already committed its outcome. A batch-wide transaction would let one bad destination hold up every
other patient's notification, which is the failure §14 names explicitly.

**The dispatcher never touches clinical state.** It reads a notification row, hands the event to a
transport -- which owns how it becomes a wire payload -- and writes back to its own two tables. It
cannot reach ``LabOrder``, ``ResultDelivery``, ``DoctorReview`` or a release version, and that is
enforced by what it imports.

**Where the tenant boundary is, and where it deliberately is not.** ``claim`` is not scoped to an
organization: one dispatcher drains the whole deployment's queue, across every facility. That is a
decision rather than an omission, and §14's "facility/tenant boundaries remain explicit" is
satisfied on the two surfaces where a *principal* is involved -- enrolment and the status/retry
reads are gated on the owning provider's ``CLINICIAN`` (``exchange/api.py``), and the operator
summary carries no tenant-identifying field at all. The dispatcher has no principal: it is the
deployment acting on its own committed obligations, and the row already carries the destination and
the template it was created with, so there is nothing for it to decide and nothing it could leak
across a boundary. A per-tenant worker would add partitioning without adding a boundary, and would
turn one stuck facility into a stuck deployment. If DHealth ever runs dispatchers on separate hosts
per facility, the scoping predicate belongs here and this paragraph is where to change it.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.models import utc_now
from daher_adapter.notify.models import (
    NotificationChannel,
    NotificationEventType,
    NotificationFailureCategory,
)
from daher_adapter.notify.repository import (
    DEFAULT_CLAIM_LEASE,
    DEFAULT_RETRY_POLICY,
    MAX_CLAIM_BATCH,
    NotificationRetryPolicy,
    PatientNotificationRepository,
)
from daher_adapter.notify.transport import (
    NotificationsDisabled,
    NotificationTransport,
    TransportFailure,
)

logger = logging.getLogger("daher_adapter.notify.dispatcher")

DEFAULT_POLL_SECONDS = 15.0


def default_worker_id() -> str:
    """Host plus pid plus a random suffix.

    The random suffix is what makes it safe: two workers in one container, or a pid reused after a
    restart, would otherwise share an identity and each could confirm the other's claim. Bounded to
    the column width.
    """
    host = socket.gethostname()[:48]
    return f"{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"[:128]


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """What one pass did. Counts only -- no identifiers, nothing sensitive."""

    claimed: int = 0
    sent: int = 0
    failed: int = 0
    errored: int = 0


class PatientNotificationDispatcher:
    """Claims due notifications and hands them to a transport.

    ``transport`` may be ``None``, and that is the "notifications disabled" state rather than a
    broken one (§10): the dispatcher claims nothing and reports nothing wrong. A deployment that has
    configured no provider has no messages to send, and treating that as an error would put a
    development stack's deliberate silence in the same alarm bucket as a production outage.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        transport: NotificationTransport | None,
        worker_id: str | None = None,
        policy: NotificationRetryPolicy = DEFAULT_RETRY_POLICY,
        lease: timedelta = DEFAULT_CLAIM_LEASE,
        batch_size: int = MAX_CLAIM_BATCH,
    ) -> None:
        self._sessions = session_factory
        self._transport = transport
        self._worker_id = worker_id or default_worker_id()
        self._policy = policy
        self._lease = lease
        self._batch_size = batch_size
        self._repository = PatientNotificationRepository()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def enabled(self) -> bool:
        return self._transport is not None

    def dispatch_once(self, *, now: datetime | None = None) -> DispatchOutcome:
        """Claim one batch and process it. Returns what happened.

        The claim commits **before** any provider call. A worker that dies immediately afterwards
        leaves rows in ``SENDING`` with an expired lease, which the next claim reclaims -- rather
        than rows it holds forever, which is what an in-process lock would have produced.
        """
        if self._transport is None:
            return DispatchOutcome()

        moment = now or utc_now()
        with self._sessions.begin() as session:
            claimed = self._repository.claim(
                session,
                worker_id=self._worker_id,
                limit=self._batch_size,
                lease=self._lease,
                now=moment,
            )
            claimed_ids = [row.id for row in claimed]

        sent = failed = errored = 0
        for notification_id in claimed_ids:
            # One transaction per message. A row that raises rolls back only itself; the messages
            # already processed in this batch keep their committed outcomes, and the next one is
            # still attempted. §14's "one poisoned notification must not block every other".
            try:
                outcome = self._process(notification_id, now=moment)
            except Exception:  # noqa: BLE001 - one bad row may not stop the queue
                # No identifiers and no exception text in the log line: the exception message could
                # carry a destination or, for an access-link row, a rendered body. The row itself
                # stays claimed until its lease expires and is then retried, which is the correct
                # outcome for a genuinely unexpected failure.
                logger.exception("patient_notification_dispatch_error")
                errored += 1
                continue
            if outcome:
                sent += 1
            else:
                failed += 1

        return DispatchOutcome(claimed=len(claimed_ids), sent=sent, failed=failed, errored=errored)

    def _process(self, notification_id: uuid.UUID, *, now: datetime) -> bool:
        """Render, send, record. Returns whether the provider accepted it."""
        assert self._transport is not None  # noqa: S101 - guarded by dispatch_once

        with self._sessions.begin() as session:
            notification = self._repository.get(session, notification_id)
            if notification is None:  # pragma: no cover - just claimed in this process
                return False
            channel = NotificationChannel(notification.channel)
            event_type = NotificationEventType(notification.event_type)
            destination = notification.destination
            locale = notification.locale

            if not self._transport.supports(channel):
                # Configuration, and terminal: retrying an unconfigured channel reproduces it.
                self._repository.mark_attempt_failed(
                    session,
                    notification=notification,
                    category=NotificationFailureCategory.CONFIGURATION,
                    detail=f"no sender configured for the {channel.value} channel",
                    policy=self._policy,
                    now=now,
                )
                return False

        # Everything the send needs was read above and the transaction is closed. **No vendor call
        # happens inside a database transaction**, which is §16's invariant and the reason a
        # provider outage can never roll back a clinically valid release.
        #
        # No `patient_access_url` is passed, and none exists to pass: `DISPATCHABLE_EVENT_TYPES`
        # excludes the one event whose template carries a link, so the binding for anything reaching
        # here takes zero parameters (DH-NOTIFY-META-001 §8). Rendering now happens inside the
        # transport -- Meta binds this event to an approved template rather than sending a body.
        try:
            accepted = self._transport.send(
                channel=channel,
                destination=destination,
                event_type=event_type,
                locale=locale,
            )
        except NotificationsDisabled:
            # Switched off underneath a running worker. Leave the row exactly as it is: it is still
            # owed, nothing failed, and the lease expiring will make it claimable again if sending
            # is switched back on.
            return False
        except TransportFailure as failure:
            with self._sessions.begin() as session:
                notification = self._repository.get(session, notification_id)
                if notification is None:  # pragma: no cover
                    return False
                self._repository.mark_attempt_failed(
                    session,
                    notification=notification,
                    category=failure.category,
                    detail=failure.detail,
                    http_status_code=failure.http_status_code,
                    policy=self._policy,
                    now=utc_now(),
                )
            return False

        with self._sessions.begin() as session:
            notification = self._repository.get(session, notification_id)
            if notification is None:  # pragma: no cover
                return False
            self._repository.mark_sent(
                session,
                notification=notification,
                provider_message_id=accepted.provider_message_id,
                provider_status=accepted.provider_status,
                http_status_code=accepted.http_status_code,
                now=utc_now(),
            )
        return True

    def run(
        self, *, stop: threading.Event, poll_seconds: float = DEFAULT_POLL_SECONDS
    ) -> None:  # pragma: no cover - exercised by run_once in tests, by the CLI in production
        """Poll until ``stop`` is set. The loop owns cadence; the repository owns durability."""
        logger.info(
            "patient_notification_dispatcher_started",
            extra={"worker_id": self._worker_id, "enabled": self.enabled},
        )
        while not stop.is_set():
            try:
                self.dispatch_once()
            except Exception:  # noqa: BLE001 - a failing pass must not kill the worker
                logger.exception("patient_notification_dispatch_pass_failed")
            stop.wait(poll_seconds)


def send_access_link_inline(
    session_factory: sessionmaker[Session],
    *,
    transport: NotificationTransport | None,
    notification_id: uuid.UUID,
    patient_access_url: str,
    now: datetime | None = None,
) -> bool:
    """Send the one message that carries a link, from the request that created it.

    **Called after the issuing transaction has committed**, never inside it (§12). The grant and
    the obligation are durable before a single byte goes to a vendor, so a provider outage cannot
    roll back the clinician's act of issuing patient access.

    ``patient_access_url`` is passed in and never stored. It exists as an argument, in the rendered
    body, and in the provider's request -- and nowhere else. It is not logged, not written to the
    notification row, not written to the attempt row, and not included in any exception raised from
    here. §8: the raw link necessarily exists briefly while sending, and that is not authorization
    to keep it.

    A failure here is **terminal** (``dispatchable=False``): no worker can ever pick this row up,
    because the URL cannot be reconstructed. The clinician is told to revoke and reissue, which is
    §4's own recovery path.
    """
    repository = PatientNotificationRepository()
    moment = now or utc_now()

    with session_factory.begin() as session:
        notification = repository.get(session, notification_id)
        if notification is None:  # pragma: no cover - created by the caller moments earlier
            return False
        channel = NotificationChannel(notification.channel)
        event_type = NotificationEventType(notification.event_type)
        destination = notification.destination
        locale = notification.locale
        notification.state = "SENDING"
        notification.attempt_count += 1
        notification.updated_at = moment

    if transport is None:
        with session_factory.begin() as session:
            notification = repository.get(session, notification_id)
            if notification is not None:
                repository.mark_attempt_failed(
                    session,
                    notification=notification,
                    category=NotificationFailureCategory.CONFIGURATION,
                    detail="patient notifications are not enabled on this deployment",
                    dispatchable=False,
                    now=utc_now(),
                )
        return False

    try:
        if not transport.supports(channel):
            raise TransportFailure(
                NotificationFailureCategory.CONFIGURATION,
                f"no sender configured for the {channel.value} channel",
            )
        # The one send that carries a link. The URL is passed as an argument, never stored, and the
        # transport's binding is the only thing that decides it may be carried at all -- for every
        # other event the binding refuses it outright.
        accepted = transport.send(
            channel=channel,
            destination=destination,
            event_type=event_type,
            locale=locale,
            patient_access_url=patient_access_url,
        )
    except TransportFailure as failure:
        with session_factory.begin() as session:
            notification = repository.get(session, notification_id)
            if notification is not None:
                repository.mark_attempt_failed(
                    session,
                    notification=notification,
                    category=failure.category,
                    detail=failure.detail,
                    http_status_code=failure.http_status_code,
                    dispatchable=False,
                    now=utc_now(),
                )
        return False
    except Exception:  # noqa: BLE001 - never let a send failure escape into the issuance response
        # Deliberately swallowed rather than propagated. The grant is already committed and the
        # clinician's browser is holding the only copy of the raw token; raising here would turn a
        # messaging failure into a failed issuance response, and the UI would then not know a live
        # grant exists. No exception text is logged -- it could carry the rendered body, which
        # contains the credential.
        logger.error("patient_access_link_send_failed")
        with session_factory.begin() as session:
            notification = repository.get(session, notification_id)
            if notification is not None:
                repository.mark_attempt_failed(
                    session,
                    notification=notification,
                    category=NotificationFailureCategory.UNKNOWN,
                    detail="the provider request failed unexpectedly",
                    dispatchable=False,
                    now=utc_now(),
                )
        return False

    with session_factory.begin() as session:
        notification = repository.get(session, notification_id)
        if notification is not None:
            repository.mark_sent(
                session,
                notification=notification,
                provider_message_id=accepted.provider_message_id,
                provider_status=accepted.provider_status,
                http_status_code=accepted.http_status_code,
                now=utc_now(),
            )
    return True
