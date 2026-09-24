"""DH-NOTIFY-001 §7/§11/§12/§13/§14 — the only writer of the patient-notification tables.

Every method takes an explicit ``now`` rather than reading the clock, for the reason
``connect/delivery.py`` gives: half of what this class decides is *when* something next becomes
eligible, and a decision about time that reads its own clock can only be tested by waiting.

**Idempotency is the database's job.** ``enqueue`` mints a deterministic ``idempotency_key`` and
inserts under a UNIQUE constraint; a concurrent duplicate surfaces as ``IntegrityError`` and is
resolved by re-reading the winner. The application-level "check then insert" that §11 forbids is
present only as a fast path for the overwhelmingly common uncontended case -- the *correctness*
under concurrency comes from the constraint, and `NC-NOTIFY-1` proves it by removing it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from daher_adapter.models import utc_now
from daher_adapter.notify.models import (
    DISPATCHABLE_EVENT_TYPES,
    RETRYABLE_CATEGORIES,
    NotificationChannel,
    NotificationEventType,
    NotificationFailureCategory,
    NotificationState,
    PatientNotification,
    PatientNotificationAttempt,
    PatientNotificationEnrollment,
)
from daher_adapter.notify.templates import DEFAULT_LOCALE, template_identity


@dataclass(frozen=True, slots=True)
class NotificationRetryPolicy:
    """How many times, how far apart, and when to stop (§13).

    Deliberately more patient than ``connect/delivery.py``'s five-attempts-over-four-minutes. A
    connector agent that is down is a partner with an operator watching it; a patient's phone
    network, a WhatsApp session window or a provider's regional outage resolves on a scale of tens
    of minutes, and giving up in four would turn every brief outage into an operator ticket.

    Bounded, though, and the bound is the point: six attempts over roughly half an hour, then a
    ``FAILED`` row somebody is told about. A queue that retries a hundred times is a queue where
    nothing ever reaches an operator, and the work merely ages invisibly.
    """

    max_attempts: int = 6
    base_delay: timedelta = timedelta(seconds=30)
    max_delay: timedelta = timedelta(minutes=10)

    def next_delay(self, attempt_count: int) -> timedelta:
        exponent = max(attempt_count - 1, 0)
        return min(self.base_delay * (2**exponent), self.max_delay)


DEFAULT_RETRY_POLICY = NotificationRetryPolicy()

#: How long a dispatcher's claim holds. A claim expires rather than being released, so a worker
#: killed mid-batch blocks its rows for one lease and no operator has to know that unsticking them
#: is a thing.
DEFAULT_CLAIM_LEASE = timedelta(minutes=2)

#: The largest batch one claim returns. A bound rather than "all due work": a worker that claims
#: the entire backlog holds all of it under one lease, and if it dies the whole backlog waits.
MAX_CLAIM_BATCH = 25


class EnrollmentNotFound(RuntimeError):
    """No live notification enrollment exists for this order."""


def access_link_key(grant_id: uuid.UUID, channel: NotificationChannel, destination: str) -> str:
    """Idempotency key for the one message that carries a link.

    Keyed on the **grant**, not the order: the link is only meaningful for the grant that produced
    it, and a revoke-and-reissue is genuinely a new message about a new link rather than a replay
    of the old one.
    """
    return f"access-link:{grant_id}:{channel.value}:{destination}"


def release_key(
    event_type: NotificationEventType,
    release_version_id: uuid.UUID,
    channel: NotificationChannel,
    destination: str,
) -> str:
    """Idempotency key for a release or amendment message.

    Keyed on the **immutable release version**, which is what makes an amendment a distinct
    notification rather than a suppressed duplicate of the release it corrects (ADR-048 Model 3).
    A key naming only the order would make the correction look like a replay, and the patient would
    never learn that the result they were told about has changed -- the single worst outcome this
    domain has.

    Channel and destination are in the key so that a corrected phone number or a second channel is
    a new intended message, not a silently-dropped one.
    """
    return f"{event_type.value.lower()}:{release_version_id}:{channel.value}:{destination}"


class PatientNotificationRepository:
    """Enqueue, claim, record, retry. Nothing here knows what a provider is."""

    # -- enrollment ---------------------------------------------------------------------------

    def enroll(
        self,
        session: Session,
        *,
        lab_order_id: uuid.UUID,
        patient_access_grant_id: uuid.UUID,
        channel: NotificationChannel,
        destination: str,
        consent_recorded_by_identity_id: uuid.UUID,
        now: datetime | None = None,
    ) -> PatientNotificationEnrollment:
        """Record that this facility may message this patient about this order.

        Runs on the caller's session so it commits with the grant that created it. ``destination``
        must already be E.164 -- ``phone.py`` is the validator and the API layer calls it before
        reaching here, so a bad number fails while the clinician is still looking at the screen
        rather than becoming an obligation that can only fail.

        Withdraws any existing live enrollment first. Re-enrolling with a different number is a
        deliberate operator act, and leaving the old row live would mean two destinations for one
        order with nothing saying which is current.
        """
        moment = now or utc_now()
        existing = self.active_enrollment(session, lab_order_id=lab_order_id)
        if existing is not None:
            existing.withdrawn_at = moment
            session.flush()

        enrollment = PatientNotificationEnrollment(
            lab_order_id=lab_order_id,
            patient_access_grant_id=patient_access_grant_id,
            channel=channel.value,
            destination=destination,
            consent_recorded_at=moment,
            consent_recorded_by_identity_id=consent_recorded_by_identity_id,
            created_at=moment,
        )
        session.add(enrollment)
        session.flush()
        return enrollment

    def active_enrollment(
        self, session: Session, *, lab_order_id: uuid.UUID
    ) -> PatientNotificationEnrollment | None:
        return session.scalar(
            select(PatientNotificationEnrollment).where(
                PatientNotificationEnrollment.lab_order_id == lab_order_id,
                PatientNotificationEnrollment.withdrawn_at.is_(None),
            )
        )

    def withdraw(
        self, session: Session, *, lab_order_id: uuid.UUID, now: datetime | None = None
    ) -> bool:
        """Stop messaging about this order. Idempotent; ``False`` means there was nothing live.

        Called when patient access is revoked: the link is dead, and continuing to tell a patient
        to "use your secure link" would be pointing them at something that no longer works.

        Deliberately does **not** cancel notifications already queued for this order. A message
        that was owed when the release happened stays owed -- the patient is still entitled to
        learn there is an update, and their facility is the fallback the message already names.
        Silently dropping queued clinical-workflow messages on an unrelated administrative action
        is exactly the class of quiet data loss this repository refuses elsewhere.
        """
        enrollment = self.active_enrollment(session, lab_order_id=lab_order_id)
        if enrollment is None:
            return False
        enrollment.withdrawn_at = now or utc_now()
        session.flush()
        return True

    # -- obligations --------------------------------------------------------------------------

    def enqueue(
        self,
        session: Session,
        *,
        idempotency_key: str,
        lab_order_id: uuid.UUID,
        release_version_id: uuid.UUID | None,
        enrollment: PatientNotificationEnrollment,
        event_type: NotificationEventType,
        requested_by_identity_id: uuid.UUID | None = None,
        locale: str = DEFAULT_LOCALE,
        now: datetime | None = None,
    ) -> tuple[PatientNotification, bool]:
        """Persist one owed message, or return the row that already owes it.

        Returns ``(notification, created)``. The flag is not a convenience: a caller has to be able
        to distinguish a genuinely new obligation from a replay, and inferring it by comparing
        ``created_at`` against the caller's clock is wrong for exactly the rows that matter -- one
        written a millisecond ago by a concurrent retry looks new under any tolerance.

        Runs on the caller's session, deliberately (§12). The obligation commits with the release
        that caused it, so "a result was released" and "the patient was owed a message" cannot
        disagree. The provider call happens afterwards, outside this transaction, and cannot roll
        back a clinically valid release.
        """
        moment = now or utc_now()
        channel = NotificationChannel(enrollment.channel)
        template_id, template_version = template_identity(event_type, locale)

        existing = session.scalar(
            select(PatientNotification).where(
                PatientNotification.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            return existing, False

        notification = PatientNotification(
            idempotency_key=idempotency_key,
            lab_order_id=lab_order_id,
            release_version_id=release_version_id,
            patient_access_grant_id=enrollment.patient_access_grant_id,
            enrollment_id=enrollment.id,
            event_type=event_type.value,
            channel=channel.value,
            destination=enrollment.destination,
            template_id=template_id,
            template_version=template_version,
            locale=locale,
            state=NotificationState.PENDING.value,
            attempt_count=0,
            next_attempt_at=moment,
            requested_by_identity_id=requested_by_identity_id,
            created_at=moment,
            updated_at=moment,
        )
        try:
            # A SAVEPOINT, so that losing the insert race does not poison the *caller's*
            # transaction -- which is a clinical release. Without it, the IntegrityError would
            # leave the outer transaction unusable and a duplicate hook call would roll back a
            # legitimate result release.
            #
            # **``session.add`` belongs inside this block, and the first version of this method had
            # it outside.** Entering ``begin_nested()`` calls ``_take_snapshot()``, which flushes
            # everything already pending -- so an object added beforehand is INSERTed by the *outer*
            # transaction, before the savepoint exists. The UniqueViolation then aborts the outer
            # transaction rather than the savepoint, the ``except`` below cannot even run its
            # recovery query, and a released result rolls back because a patient was owed a
            # duplicate message. Real PostgreSQL under two concurrent hooks is what surfaced it;
            # SQLite's savepoint timing hid it completely. Same shape as
            # ``exchange/patient_access.py``'s issuance retry, which had it right.
            with session.begin_nested():
                session.add(notification)
                session.flush()
        except IntegrityError:
            winner = session.scalar(
                select(PatientNotification).where(
                    PatientNotification.idempotency_key == idempotency_key
                )
            )
            if winner is None:  # pragma: no cover - the only unique key here is the one that fired
                raise
            return winner, False
        return notification, True

    # -- dispatch -----------------------------------------------------------------------------

    def claim(
        self,
        session: Session,
        *,
        worker_id: str,
        limit: int = MAX_CLAIM_BATCH,
        lease: timedelta = DEFAULT_CLAIM_LEASE,
        now: datetime | None = None,
    ) -> list[PatientNotification]:
        """Hand a bounded batch of due work to one worker under a lease.

        ``with_for_update(skip_locked=True)`` is what makes two workers safe rather than merely
        unlikely: the second skips the rows the first holds instead of blocking on them or reading
        them. **SQLite ignores both silently**, so this property is unprovable on the unit-test
        database and is qualified against real PostgreSQL instead -- a passing SQLite test here
        would be evidence of nothing.

        ``ACCESS_LINK_ISSUED`` is excluded, structurally. That message contains the patient-access
        URL, which is only constructible while the raw grant secret is in the issuing request's
        memory; a worker picking one up later would have nothing to render. It is sent inline or it
        fails. See ``models.DISPATCHABLE_EVENT_TYPES``.
        """
        moment = now or utc_now()
        bounded = max(1, min(limit, MAX_CLAIM_BATCH))

        rows = (
            session.execute(
                select(PatientNotification)
                .where(
                    PatientNotification.event_type.in_(
                        sorted(event.value for event in DISPATCHABLE_EVENT_TYPES)
                    ),
                    PatientNotification.next_attempt_at <= moment,
                    _claimable(moment),
                )
                # Oldest first, so a backlog drains in the order the clinical events happened and a
                # patient is told about a release before the amendment that corrects it.
                .order_by(PatientNotification.created_at, PatientNotification.id)
                .limit(bounded)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )

        for row in rows:
            row.state = NotificationState.SENDING.value
            row.claimed_by_worker_id = worker_id
            row.claim_expires_at = moment + lease
            # Counted at hand-over, not at failure. A worker that claims a message and vanishes has
            # attempted it; counting only reported failures lets a crash-looping worker retry
            # forever without any row ever reaching an operator.
            row.attempt_count += 1
            row.updated_at = moment
        session.flush()
        return list(rows)

    def mark_sent(
        self,
        session: Session,
        *,
        notification: PatientNotification,
        provider_message_id: str,
        provider_status: str,
        http_status_code: int,
        now: datetime | None = None,
    ) -> PatientNotification:
        """The provider accepted it. **Not** delivered -- see ``NotificationState``.

        ``provider_status`` is the vendor's own word, recorded on the attempt for an operator and
        never consulted as a delivery signal. Only an authenticated receipt writes ``DELIVERED``.
        """
        moment = now or utc_now()
        notification.state = NotificationState.SENT.value
        notification.sent_at = moment
        notification.provider_message_id = provider_message_id
        notification.failure_category = None
        notification.claimed_by_worker_id = None
        notification.claim_expires_at = None
        notification.updated_at = moment
        self._record_attempt(
            session,
            notification=notification,
            outcome="ACCEPTED",
            provider_status_code=http_status_code,
            provider_message_id=provider_message_id,
            failure_category=None,
            failure_detail=f"provider accepted ({provider_status})"[:256],
            now=moment,
        )
        session.flush()
        return notification

    def mark_attempt_failed(
        self,
        session: Session,
        *,
        notification: PatientNotification,
        category: NotificationFailureCategory,
        detail: str,
        http_status_code: int | None = None,
        policy: NotificationRetryPolicy = DEFAULT_RETRY_POLICY,
        dispatchable: bool = True,
        now: datetime | None = None,
    ) -> PatientNotification:
        """Record a failed attempt, and decide whether there will be another.

        Two ways to reach ``FAILED``, and they are different operational facts an attempt row keeps
        apart: a **terminal** category (bad destination, bad credential, provider refusal,
        misconfiguration) where retrying reproduces the identical failure; or a **retryable**
        category whose bounded budget is spent.

        ``dispatchable=False`` is the ``ACCESS_LINK_ISSUED`` case: no worker will ever pick this row
        up again, so "retry later" would be a lie told by a PENDING state. It goes straight to
        ``FAILED`` and the clinician is told to revoke and reissue -- which is §4's own recovery
        path, and the only honest one when the credential is gone.
        """
        moment = now or utc_now()
        notification.failure_category = category.value
        notification.claimed_by_worker_id = None
        notification.claim_expires_at = None
        notification.updated_at = moment

        retryable = category in RETRYABLE_CATEGORIES and dispatchable
        exhausted = notification.attempt_count >= policy.max_attempts
        if retryable and not exhausted:
            notification.state = NotificationState.PENDING.value
            notification.next_attempt_at = moment + policy.next_delay(notification.attempt_count)
        else:
            notification.state = NotificationState.FAILED.value

        self._record_attempt(
            session,
            notification=notification,
            outcome="FAILED",
            provider_status_code=http_status_code,
            provider_message_id=None,
            failure_category=category.value,
            failure_detail=detail,
            now=moment,
        )
        session.flush()
        return notification

    def retry(
        self,
        session: Session,
        *,
        notification: PatientNotification,
        now: datetime | None = None,
    ) -> PatientNotification:
        """Operator-requested re-attempt of a ``FAILED`` row.

        A retry rather than a resend: the same obligation is made due again, so it keeps its
        identity, its attempt history and its idempotency key. A *resend* would need a new row and
        therefore a new key, which §11 permits only behind an explicit operator function -- this is
        deliberately the narrower of the two, because the message is content-free and a patient
        receiving "there is an update" twice is worse than useless.

        Refuses anything that is not ``FAILED`` and anything a dispatcher cannot claim. An
        ``ACCESS_LINK_ISSUED`` row can never be retried: its link is unrecoverable, and the
        replacement path is revoke-and-reissue.
        """
        moment = now or utc_now()
        if notification.state != NotificationState.FAILED.value:
            raise ValueError(
                f"only a FAILED notification can be retried; this one is {notification.state}"
            )
        if NotificationEventType(notification.event_type) not in DISPATCHABLE_EVENT_TYPES:
            raise ValueError(
                "an access-link notification cannot be retried -- its secure link is not "
                "recoverable; revoke patient access and issue a replacement instead"
            )
        notification.state = NotificationState.PENDING.value
        notification.attempt_count = 0
        notification.next_attempt_at = moment
        notification.failure_category = None
        notification.claimed_by_worker_id = None
        notification.claim_expires_at = None
        notification.updated_at = moment
        session.flush()
        return notification

    def apply_delivery_receipt(
        self,
        session: Session,
        *,
        provider_message_id: str,
        delivered: bool,
        category: NotificationFailureCategory | None = None,
        detail: str | None = None,
        now: datetime | None = None,
    ) -> PatientNotification | None:
        """Apply an **already-authenticated** provider receipt. Returns ``None`` if unknown.

        This method does not authenticate anything -- ``webhook.py`` does, before calling. Keeping
        the signature check out here would be the mistake that makes a spoofed callback work.

        Idempotent and monotonic. A row already in a terminal state is left alone, which is what
        makes a duplicated or out-of-order callback harmless: providers replay, and a ``sent``
        arriving after a ``delivered`` must not walk the row backwards.

        Touches **only** this table. Not ``ResultDelivery``, not ``DoctorReview``, not
        ``LabOrder``, not any release version -- a messaging vendor's callback is not an authority
        on clinical state, and the one-line way to guarantee that is for this method to be unable
        to reach it.
        """
        moment = now or utc_now()
        notification = session.scalar(
            select(PatientNotification).where(
                PatientNotification.provider_message_id == provider_message_id
            )
        )
        if notification is None:
            return None
        if notification.state in (
            NotificationState.DELIVERED.value,
            NotificationState.FAILED.value,
        ):
            return notification

        # **A receipt never writes an attempt row**, in either direction, and the symmetry is the
        # decision. An attempt is a *hand-over*: DHealth gave the provider a message and the
        # provider answered. A receipt arrives later, unprompted, about a hand-over that already
        # has its row -- recording it as a second attempt would inflate `attempt_count`'s meaning
        # and make "how many times did we try" unanswerable from the history.
        #
        # So the evidence for each outcome is stated once and lives in one place: `delivered_at`
        # is the whole record of a delivery, and `failure_category` plus `updated_at` are the whole
        # record of a reported non-delivery. Both are on the notification row, both are in the
        # operator read, and neither pretends to be an attempt that never happened.
        if delivered:
            notification.state = NotificationState.DELIVERED.value
            notification.delivered_at = moment
            notification.failure_category = None
        else:
            notification.state = NotificationState.FAILED.value
            notification.failure_category = (
                category or NotificationFailureCategory.PROVIDER_REJECTED
            ).value
        notification.updated_at = moment
        session.flush()
        return notification

    # -- reads --------------------------------------------------------------------------------

    def get(self, session: Session, notification_id: uuid.UUID) -> PatientNotification | None:
        return session.get(PatientNotification, notification_id)

    def list_for_order(
        self, session: Session, *, lab_order_id: uuid.UUID
    ) -> list[PatientNotification]:
        return list(
            session.scalars(
                select(PatientNotification)
                .where(PatientNotification.lab_order_id == lab_order_id)
                .order_by(PatientNotification.created_at.desc(), PatientNotification.id)
            ).all()
        )

    def operational_summary(self, session: Session, *, now: datetime | None = None) -> dict:
        """§22's five questions, answered from the database and carrying nothing sensitive.

        No phone number, no order id, no grant id, no patient identifier, no message id, no body.
        Counts, one age in seconds, and a failure-category histogram -- which is exactly what
        "are messages backing up, is the provider rejecting them, did credentials stop working"
        needs and nothing that would make this read a privacy surface.
        """
        moment = now or utc_now()
        by_state = dict(
            session.execute(
                select(PatientNotification.state, func.count(PatientNotification.id)).group_by(
                    PatientNotification.state
                )
            ).all()
        )
        by_failure = dict(
            session.execute(
                select(PatientNotification.failure_category, func.count(PatientNotification.id))
                .where(PatientNotification.failure_category.is_not(None))
                .group_by(PatientNotification.failure_category)
            ).all()
        )
        by_channel = dict(
            session.execute(
                select(PatientNotification.channel, func.count(PatientNotification.id)).group_by(
                    PatientNotification.channel
                )
            ).all()
        )
        oldest_pending = session.scalar(
            select(func.min(PatientNotification.created_at)).where(
                PatientNotification.state.in_(
                    (NotificationState.PENDING.value, NotificationState.SENDING.value)
                )
            )
        )
        oldest_age = None
        if oldest_pending is not None:
            # PostgreSQL's `timestamptz` returns aware datetimes; SQLite (the fast test database,
            # never production) hands back a naive one on a fresh read, and subtracting it from an
            # aware `moment` raises. Every value written here is already UTC, so re-attaching the
            # tzinfo is lossless -- the same normalization `exchange/patient_access.py` does, for
            # the same reason.
            if oldest_pending.tzinfo is None:
                oldest_pending = oldest_pending.replace(tzinfo=UTC)
            oldest_age = max(0, int((moment - oldest_pending).total_seconds()))

        return {
            "by_state": {
                state.value: int(by_state.get(state.value, 0)) for state in NotificationState
            },
            "by_failure_category": {key: int(value) for key, value in by_failure.items()},
            "by_channel": {key: int(value) for key, value in by_channel.items()},
            "oldest_pending_age_seconds": oldest_age,
        }

    # -- internals ----------------------------------------------------------------------------

    def _record_attempt(
        self,
        session: Session,
        *,
        notification: PatientNotification,
        outcome: str,
        provider_status_code: int | None,
        provider_message_id: str | None,
        failure_category: str | None,
        failure_detail: str | None = None,
        detail_override: str | None = None,
        now: datetime,
    ) -> None:
        """One row per hand-over, and *only* per hand-over.

        ``attempt_number`` mirrors the notification's own counter, which is incremented exactly
        once when the message is claimed (or handed over inline). The only two callers are
        ``mark_sent`` and ``mark_attempt_failed``, each of which runs once per hand-over, so the
        number is unambiguous. Provider receipts deliberately do not reach here -- see
        ``apply_delivery_receipt``.

        The collision bump below is defence, not an expected path: reaching it means some future
        caller recorded two attempts against one hand-over, and taking the next number preserves
        both rows rather than letting the UNIQUE abort a transaction that is otherwise correct.
        """
        detail = detail_override if detail_override is not None else failure_detail
        number = max(1, int(notification.attempt_count))
        existing = session.scalar(
            select(PatientNotificationAttempt).where(
                PatientNotificationAttempt.patient_notification_id == notification.id,
                PatientNotificationAttempt.attempt_number == number,
            )
        )
        if existing is not None:  # pragma: no cover - defensive; see the docstring
            number += 1
        session.add(
            PatientNotificationAttempt(
                patient_notification_id=notification.id,
                attempt_number=number,
                started_at=now,
                finished_at=now,
                outcome=outcome,
                provider_status_code=provider_status_code,
                provider_message_id=provider_message_id,
                failure_category=failure_category,
                failure_detail=None if detail is None else detail[:256],
            )
        )


def _claimable(moment: datetime):
    """``PENDING``, or ``SENDING`` under a lease that has run out.

    Separated out because omitting the second half is the kind of wrong that is silent: the queue
    works perfectly until a worker dies, and then strands exactly the messages that were in flight
    when it did.
    """
    return (PatientNotification.state == NotificationState.PENDING.value) | (
        (PatientNotification.state == NotificationState.SENDING.value)
        & (PatientNotification.claim_expires_at <= moment)
    )
