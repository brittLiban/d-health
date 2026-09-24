"""DH-NOTIFY-001 — the patient-notification domain, deliberately separate from every other
"delivery" this codebase already has.

Four different things in this repository mean "the result went somewhere", and conflating any two
of them would be a clinical reporting error rather than a tidiness problem:

===========================  ==================================================================
``ResultDelivery``           the **ordering provider organization** received the result. Its
                             exact semantic is an open product decision (``docs/MVP-1.0.md``
                             §10 item 6) and this module never touches, reads as authority, or
                             reinterprets it.
``ConnectorDelivery``        a **partner EMR/LIS** was handed a canonical Connect message.
``DoctorReview``             a **clinician** recorded a follow-up disposition.
``PatientNotification``      a **patient** was sent a deliberately content-free message telling
                             them to go and look. Nothing more.
===========================  ==================================================================

A patient notification reaching ``DELIVERED`` says one thing and one thing only: the transport
provider reported that a handset accepted a message. It does not mean the patient read it, opened
the link, understood it, that the provider organization has the result, or that a clinician has
reviewed anything.

**The message carries no clinical content at all** (§3). Not a value, not a unit, not a reference
range, not an interpretation, not the follow-up disposition, not which test was ordered, not a date
of birth, not an identifier of any kind. That is not a policy applied by whoever writes a template
later -- ``templates.py`` holds the only strings that can ever be sent, they contain no
substitution slot capable of carrying clinical data, and a test asserts it.

**Why `release_version_id` and not `lab_order_id` alone.** An amendment is a *new immutable release
version* (ADR-048 Model 3). Keying an obligation on the order would make the amendment's
notification look like a replay of the original release's and be silently dropped -- the single
failure this domain most needs to not have, because the patient would then never learn that the
result they were told about has been corrected.

**A′ (Liban's decision, DH-NOTIFY-001).** The raw ``PatientAccessGrant`` secret exists exactly once,
in the issuance response, and ``exchange/patient_access.py`` is emphatic that DHealth can never
recover it. That is preserved here without exception: no column on either table below holds a token,
a rendered message, or a patient-access URL. The consequence is structural and visible in
``NotificationEventType``: only ``ACCESS_LINK_ISSUED`` can carry a link, because only that event
happens while the secret is still in memory, so only that event is sent inline by the issuing
request. ``RESULT_RELEASED`` and ``AMENDMENT_RELEASED`` are queued for a background dispatcher and
carry no link at all -- they tell the patient to use the link they already hold, or to contact their
facility.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from daher_adapter.models import Base, utc_now


class NotificationChannel(StrEnum):
    """Launch 1's two channels, and no more (§5).

    Email and voice are named in §28's non-goals, so they are absent rather than present-and-unused:
    a value the transport cannot send is a value some later caller will set.
    """

    SMS = "SMS"
    WHATSAPP = "WHATSAPP"


class NotificationEventType(StrEnum):
    """What happened that is worth telling a patient about.

    ``ACCESS_LINK_ISSUED`` is the one that carries the secure link, and the one the dispatcher may
    never touch -- see ``DISPATCHABLE_EVENT_TYPES`` below.

    Deliberately **not** here: result entered, observation corrected before release, order accepted,
    processing started, page viewed, release endpoint retried. §11 lists those explicitly, and every
    one of them either is not an authoritative workflow transition or would produce a message about
    a result that no professional has released.
    """

    ACCESS_LINK_ISSUED = "ACCESS_LINK_ISSUED"
    RESULT_RELEASED = "RESULT_RELEASED"
    AMENDMENT_RELEASED = "AMENDMENT_RELEASED"


#: The only event types a background dispatcher may claim.
#:
#: ``ACCESS_LINK_ISSUED`` is absent, and its absence is load-bearing rather than an oversight. That
#: message is the only one containing the patient-access URL, and the URL is only constructible
#: while the raw grant secret is in the issuing request's memory. A dispatcher picking it up a
#: minute later would have nothing to render, and the only ways to give it something would be to
#: persist the secret (forbidden, §4/§8) or to re-issue the grant at send time (which would kill the
#: printed QR already in the patient's hands). So it is sent inline by the request that created it,
#: or it fails and says so. ``tests/test_patient_notification_domain.py`` pins this set.
DISPATCHABLE_EVENT_TYPES = frozenset(
    {NotificationEventType.RESULT_RELEASED, NotificationEventType.AMENDMENT_RELEASED}
)


class NotificationState(StrEnum):
    """Five states, each meaning exactly one thing.

    ``PENDING``   -- owed. Nothing has been handed to a provider yet, or an attempt failed
                     retryably and the next one is not due.
    ``SENDING``   -- claimed by one dispatcher under a lease, or in flight inline. A row here has
                     had an attempt counted against it already.
    ``SENT``      -- **the provider's API accepted the request.** This is the strongest claim an
                     HTTP 201 from a messaging vendor supports, and §7 is explicit that it must not
                     be dressed up as more. Nothing about a handset is known here.
    ``DELIVERED`` -- the provider sent an authenticated delivery receipt saying a handset accepted
                     it. Only ``webhook.py`` writes this, only after a signature check.
    ``FAILED``    -- terminal and unresolved. Either a permanent failure category, or a retryable
                     one whose bounded budget is spent, or a provider receipt reporting
                     non-delivery. Not a deletion and not a cancellation: it is a row an operator
                     has to look at, and ``retry`` is the only way out of it.

    There is deliberately no ``DEAD_LETTER``. ``connect/delivery.py`` needs one because ``FAILED``
    there is a transient per-attempt fact; here ``FAILED`` *is* the terminal unresolved state, and a
    second word for the same condition would only invite code to check one and not the other.
    """

    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


#: States from which no further automatic progress is made.
TERMINAL_STATES = frozenset({NotificationState.DELIVERED, NotificationState.FAILED})


class NotificationFailureCategory(StrEnum):
    """Sanitized classification, never a provider error body (§13).

    A provider's own message can and does echo the destination number, the rendered body -- which
    for an ``ACCESS_LINK_ISSUED`` message contains the patient's bearer secret -- and occasionally
    the account identifier. Persisting it would put all three in the database, the audit trail and
    every log that touches the row. So the adapter maps to one of these and keeps nothing else.

    Only the first two are retryable. Retrying an invalid destination produces the identical invalid
    destination; retrying a bad credential burns the budget that exists for network trouble, and
    hides the credential problem from the person who could fix it.
    """

    TRANSIENT_PROVIDER = "TRANSIENT_PROVIDER"
    RATE_LIMIT = "RATE_LIMIT"
    AUTHENTICATION = "AUTHENTICATION"
    INVALID_DESTINATION = "INVALID_DESTINATION"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    CONFIGURATION = "CONFIGURATION"
    UNKNOWN = "UNKNOWN"


#: Categories worth another attempt. Everything else is terminal on the first occurrence.
RETRYABLE_CATEGORIES = frozenset(
    {NotificationFailureCategory.TRANSIENT_PROVIDER, NotificationFailureCategory.RATE_LIMIT}
)


def _portable_e164_shape(column: str) -> str:
    """A destination-shape guard both PostgreSQL and SQLite can enforce.

    ``LIKE`` and ``length()`` are the largest intersection of the two dialects' expression support
    that says anything useful here: SQLite has no ``~`` operator and no ``REGEXP`` implementation
    unless the application registers one, so the full E.164 pattern below cannot be a portable
    constraint. This catches the shapes that actually reach a column by mistake -- a local number
    with no country code, an empty string, a formatted display value with spaces or dashes long
    enough to overflow, a ``+0`` prefix no country uses.

    It is deliberately the *weaker* of the two constraints on this column. ``phone.py`` is the
    authoritative validator and runs on every write path; ``_postgres_e164_regex`` below adds the
    exact pattern where the dialect can express it. This one exists so that the fast SQLite test
    suite is still testing a real database refusal rather than nothing at all.
    """
    return (
        f"{column} LIKE '+%' AND {column} NOT LIKE '+0%' "
        f"AND length({column}) >= 8 AND length({column}) <= 16"
    )


def _postgres_e164_regex(column: str) -> str:
    """The exact E.164 pattern, for the dialect that runs in production.

    Applied through ``.ddl_if(dialect="postgresql")`` so it is emitted against PostgreSQL and
    skipped on SQLite, rather than making the whole table uncreatable on the test database.
    """
    return f"{column} ~ '^\\+[1-9][0-9]{{6,14}}$'"


class PatientNotificationEnrollment(Base):
    """One facility-recorded authorization to message one patient about one order.

    **Separate from the notification rows on purpose.** A release happens hours or days after a
    clinician sat with the patient and asked whether they could be messaged. The destination, the
    channel and the fact that the patient agreed are all facts established at that earlier moment,
    and the release hook has no way to establish them. So they are durable, and the hook reads them.
    No enrollment means no message -- silently, the way ``connect/outbox_hook`` is silent for a
    NATIVE laboratory. An absent enrollment is the normal case, not an error.

    **Consent is a recorded act, never an inference** (§5). It is not derived from the patient
    having a phone number, from registration, from having scanned a QR, or from a previous message.
    ``consent_recorded_at`` plus ``consent_recorded_by_identity_id`` are the whole of what is
    persisted: which staff member affirmed it, and when. There is no free-text note field, because a
    free-text consent field is where jurisdiction-specific legal language gets invented by whoever
    is typing, and this ticket invents none.

    **Revocation of patient access withdraws this.** Revoking the grant kills the link; continuing
    to message a patient to "use your secure link" afterwards would be telling them to open
    something that no longer works. One live enrollment per order, enforced by a partial unique
    index -- the same shape ``PatientAccessGrant`` already uses for the same reason.
    """

    __tablename__ = "patient_notification_enrollments"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('SMS', 'WHATSAPP')",
            name="ck_patient_notification_enrollments_channel",
        ),
        # Enforced in the database as well as in `phone.py` because this column is what the
        # transport reads: a row that got here another way must still be unable to hold something
        # that is not a canonical destination. Two constraints, because the dialects differ in what
        # they can express -- see the two helpers above.
        CheckConstraint(
            _portable_e164_shape("destination"),
            name="ck_patient_notification_enrollments_destination_shape",
        ),
        CheckConstraint(
            _postgres_e164_regex("destination"),
            name="ck_patient_notification_enrollments_destination_e164",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "withdrawn_at IS NULL OR withdrawn_at >= created_at",
            name="ck_patient_notification_enrollments_withdrawn_after_create",
        ),
        Index(
            "uq_patient_notification_enrollments_one_live_per_order",
            "lab_order_id",
            unique=True,
            postgresql_where=text("withdrawn_at IS NULL"),
            sqlite_where=text("withdrawn_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False
    )
    #: The grant this enrollment was created alongside. Provenance, not authorization: the release
    #: hook never reads a token from here (there is none to read), it only needs to know that the
    #: enrollment belongs to a still-live access path.
    patient_access_grant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_patient_access_grants.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    destination: Mapped[str] = mapped_column(String(16), nullable=False)
    consent_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    consent_recorded_by_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PatientNotification(Base):
    """One message DHealth has taken durable responsibility for sending to one patient.

    **The database is the system of record, not the provider request** (§7). The row exists before
    anything is sent, commits in the same transaction as the clinical event that caused it, and
    survives every restart, crash and provider outage in between.

    **Idempotency is a unique index, not an ``if not exists``** (§11). ``idempotency_key`` is minted
    deterministically from the thing being announced (see ``repository.py``), so a release endpoint
    retried three times, a replayed hook, and two dispatchers racing all converge on one row --
    and the convergence is the database's doing, which is the only place it can be correct under
    concurrency.

    **No message text, no URL, no token** (§8). ``template_id`` and ``template_version`` record
    *which* controlled string was used; the string itself lives in ``templates.py``, in code, under
    review, and is reconstructable from those two fields alone. Storing the rendered body would make
    this table a warehouse of message text -- and for an ``ACCESS_LINK_ISSUED`` row, a warehouse of
    live patient bearer credentials.
    """

    __tablename__ = "patient_notifications"
    __table_args__ = (
        # **The idempotency key.** One column rather than a composite, because the natural key
        # differs per event type: an access-link message is identified by the grant it carries,
        # a release message by the immutable release version it announces. Both are minted by
        # `repository.py` and both include the channel and destination, so a genuinely different
        # intended message (a second channel, a corrected number) is a different row rather than a
        # silently-dropped duplicate.
        UniqueConstraint("idempotency_key", name="uq_patient_notifications_idempotency_key"),
        CheckConstraint(
            "event_type IN ('ACCESS_LINK_ISSUED', 'RESULT_RELEASED', 'AMENDMENT_RELEASED')",
            name="ck_patient_notifications_event_type",
        ),
        CheckConstraint("channel IN ('SMS', 'WHATSAPP')", name="ck_patient_notifications_channel"),
        CheckConstraint(
            "state IN ('PENDING', 'SENDING', 'SENT', 'DELIVERED', 'FAILED')",
            name="ck_patient_notifications_state",
        ),
        CheckConstraint(
            _portable_e164_shape("destination"),
            name="ck_patient_notifications_destination_shape",
        ),
        CheckConstraint(
            _postgres_e164_regex("destination"),
            name="ck_patient_notifications_destination_e164",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "failure_category IS NULL OR failure_category IN ("
            "'TRANSIENT_PROVIDER', 'RATE_LIMIT', 'AUTHENTICATION', 'INVALID_DESTINATION', "
            "'PROVIDER_REJECTED', 'CONFIGURATION', 'UNKNOWN')",
            name="ck_patient_notifications_failure_category",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_patient_notifications_attempt_count"),
        # A lease is both halves or neither -- the same invariant `connector_deliveries` carries,
        # for the same reason: one half alone is either a claim that can never expire or an expiry
        # that frees nothing, and both lose the message.
        CheckConstraint(
            "(claimed_by_worker_id IS NULL) = (claim_expires_at IS NULL)",
            name="ck_patient_notifications_lease_paired",
        ),
        CheckConstraint(
            "state <> 'SENT' OR sent_at IS NOT NULL", name="ck_patient_notifications_sent_at"
        ),
        CheckConstraint(
            "state <> 'DELIVERED' OR delivered_at IS NOT NULL",
            name="ck_patient_notifications_delivered_at",
        ),
        # A release notification must name the version it announces; an access-link message has no
        # release yet and must not pretend to. Enforced rather than documented, because "which
        # release was this about?" is the question an amendment makes load-bearing.
        CheckConstraint(
            "(event_type = 'ACCESS_LINK_ISSUED') = (release_version_id IS NULL)",
            name="ck_patient_notifications_release_version_presence",
        ),
        # Resolving an authenticated provider receipt back to a row is the webhook's whole job, and
        # two rows sharing a provider id would make that resolution ambiguous. Partial, because the
        # overwhelming majority of rows have no provider id yet.
        Index(
            "uq_patient_notifications_provider_message_id",
            "provider_message_id",
            unique=True,
            postgresql_where=text("provider_message_id IS NOT NULL"),
            sqlite_where=text("provider_message_id IS NOT NULL"),
        ),
        # The dispatcher's claim query: due work, oldest first.
        Index("ix_patient_notifications_due", "state", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(320), nullable=False)
    lab_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_lab_orders.id", ondelete="RESTRICT"), nullable=False
    )
    #: ``NULL`` only for ``ACCESS_LINK_ISSUED``, enforced by a CHECK above.
    release_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_release_versions.id", ondelete="RESTRICT")
    )
    #: Which access path this message points at. Never a credential -- the grant's own raw secret is
    #: unrecoverable by construction, and this is a foreign key to the row whose *digest* is stored.
    patient_access_grant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_patient_access_grants.id", ondelete="RESTRICT"), nullable=False
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient_notification_enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    destination: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Which controlled template, and which revision of it. The body is not stored; these two
    #: fields plus `templates.py` reconstruct exactly what was sent, and `templates.py` is in
    #: version control where a change is reviewable.
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="fr")
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NotificationState.PENDING.value
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    claimed_by_worker_id: Mapped[str | None] = mapped_column(String(128))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    failure_category: Mapped[str | None] = mapped_column(String(32))
    requested_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchange_identities.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PatientNotificationAttempt(Base):
    """One hand-over to a provider, and what came back -- classified, never quoted.

    Exists because ``PatientNotification`` holds only the current state, and "it failed three times
    with rate limits and then succeeded" is a different operational picture from "it failed once
    with a bad credential". Answering §22's *"is the provider rejecting them?"* needs the history,
    not the latest value.

    ``failure_detail`` is a short adapter-authored sentence, bounded, describing the *class* of
    problem. It is never the provider's response body: those echo the destination and the rendered
    body, and an ``ACCESS_LINK_ISSUED`` body contains a live patient credential.
    """

    __tablename__ = "patient_notification_attempts"
    __table_args__ = (
        UniqueConstraint(
            "patient_notification_id",
            "attempt_number",
            name="uq_patient_notification_attempts_number",
        ),
        CheckConstraint(
            "outcome IN ('ACCEPTED', 'FAILED')", name="ck_patient_notification_attempts_outcome"
        ),
        CheckConstraint(
            "attempt_number >= 1", name="ck_patient_notification_attempts_attempt_number"
        ),
        Index("ix_patient_notification_attempts_notification", "patient_notification_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_notification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient_notifications.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The provider's HTTP status. A number carries no destination, no body and no credential, so
    #: it is the one piece of the provider's response that is safe to keep verbatim.
    provider_status_code: Mapped[int | None] = mapped_column(Integer)
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    failure_category: Mapped[str | None] = mapped_column(String(32))
    failure_detail: Mapped[str | None] = mapped_column(String(256))
