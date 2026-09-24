"""DH-NOTIFY-001 §23 group A — the domain, the triggers, and the boundaries that must not move.

Every test here drives the **real** release path (`notify_support` builds an order the ordinary
way and releases it through `LabResultReleaseRepository`), so a green test means the hook is
genuinely wired rather than that a fixture inserted a row.

The load-bearing ones, and what each would catch if it went red:

* A7/A8 -- a snapshot-and-diff of every ``ResultDelivery`` and ``DoctorReview`` row across the full
  release → dispatch → receipt path. These are the tests NC-NOTIFY-3 turns red, and they are a
  real assertion rather than the module docstrings' "enforced by what it imports", which is a
  comment and not a control.
* A9/A10/A11 -- the content boundary, asserted against the bytes the transport was handed, using
  the exact values the fixture entered.
* A12/A13/A14 -- the raw grant secret's absence from the database, the logs and every exception.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from exchange_support import make_lab
from notify_support import (
    SECOND_DESTINATION,
    SYNTHETIC_DESTINATION,
    amend_and_release,
    build_repos,
    enrol,
    enter_result,
    identity_id,
    order_in_progress,
    release,
)
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import DoctorReview, PatientAccessGrant, ResultDelivery
from daher_adapter.models import utc_now
from daher_adapter.notify.dispatcher import PatientNotificationDispatcher, send_access_link_inline
from daher_adapter.notify.fake import FakeNotificationTransport
from daher_adapter.notify.links import build_patient_access_url
from daher_adapter.notify.models import (
    DISPATCHABLE_EVENT_TYPES,
    NotificationChannel,
    NotificationEventType,
    NotificationFailureCategory,
    NotificationState,
    PatientNotification,
    PatientNotificationAttempt,
    PatientNotificationEnrollment,
)
from daher_adapter.notify.repository import NotificationRetryPolicy, PatientNotificationRepository
from daher_adapter.notify.templates import render

BASE_URL = "https://dhealth.example"


def _notifications(sessions: sessionmaker[Session], order_id) -> list[PatientNotification]:
    with sessions() as session:
        return list(
            session.scalars(
                select(PatientNotification)
                .where(PatientNotification.lab_order_id == order_id)
                .order_by(PatientNotification.created_at)
            ).all()
        )


def _snapshot_clinical(sessions: sessionmaker[Session]) -> dict:
    """Every field of every ``ResultDelivery`` and ``DoctorReview`` in the database.

    A9-style "the value is not in the message" tests prove content; this proves *state*. Comparing
    whole rows rather than a status column means a future change that touched `delivered_at` while
    leaving `status` alone would still be caught.
    """
    with sessions() as session:
        deliveries = {
            row.id: (row.lab_order_id, row.provider_organization_id, row.status, row.delivered_at)
            for row in session.scalars(select(ResultDelivery)).all()
        }
        reviews = {
            row.id: (
                row.lab_order_id,
                row.release_version_id,
                row.status,
                row.reviewed_at,
                row.reviewed_by_practitioner_reference_id,
            )
            for row in session.scalars(select(DoctorReview)).all()
        }
    return {"deliveries": deliveries, "reviews": reviews}


# -- A1/A2: a release owes a message; an amendment owes a *different* one ------------------------


def test_a1_original_release_produces_one_eligible_notification(sessions, engine: Engine) -> None:
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab A1")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="a1")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="a1")

    release(repos, context, key="a1")

    rows = _notifications(sessions, context.order_id)
    release_rows = [r for r in rows if r.event_type == NotificationEventType.RESULT_RELEASED.value]
    assert len(release_rows) == 1
    assert release_rows[0].state == NotificationState.PENDING.value
    assert release_rows[0].release_version_id is not None
    assert release_rows[0].destination == SYNTHETIC_DESTINATION


def test_a2_amendment_release_produces_a_distinct_notification(sessions) -> None:
    """The one a too-clever idempotency key would silently drop.

    Keyed on the order, the amendment would look like a replay of the release it corrects, and the
    patient would never learn the result had changed.
    """
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab A2")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="a2")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="a2")
    release(repos, context, key="a2")

    amend_and_release(repos, context, key="a2")

    rows = _notifications(sessions, context.order_id)
    kinds = [r.event_type for r in rows]
    assert kinds.count(NotificationEventType.RESULT_RELEASED.value) == 1
    assert kinds.count(NotificationEventType.AMENDMENT_RELEASED.value) == 1
    versions = {r.release_version_id for r in rows if r.release_version_id is not None}
    assert len(versions) == 2, "each release version owes its own message"


def test_a3_same_release_version_cannot_owe_two_messages(sessions) -> None:
    """Calling the hook again with the same version converges on the one existing row.

    Deliberately exercised at the hook, not by calling ``release()`` twice: ``_release_in_session``
    returns early on an existing release *before* reaching the hook, so a sequential double-release
    would pass whether or not the key existed and would prove nothing. The genuinely concurrent
    case is in ``tests/integration/test_dhnotify001_postgres_qualification.py`` (D2), where the
    UNIQUE constraint is the thing under test.
    """
    from daher_adapter.notify.hooks import notify_patient_of_release

    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab A3")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="a3")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="a3")
    release(repos, context, key="a3")

    version_id = _notifications(sessions, context.order_id)[-1].release_version_id
    with sessions.begin() as session:
        again = notify_patient_of_release(
            session, lab_order_id=context.order_id, release_version_id=version_id
        )
        assert again is not None

    release_rows = [
        r
        for r in _notifications(sessions, context.order_id)
        if r.event_type == NotificationEventType.RESULT_RELEASED.value
    ]
    assert len(release_rows) == 1


# -- A4/A5: nothing short of a release notifies --------------------------------------------------


def test_a4_and_a5_result_entry_and_pre_release_correction_notify_nobody(sessions) -> None:
    """§11's list, exercised rather than asserted.

    Entering an observation, correcting it before release and completing entry are all real
    mutations on a real order with a live enrollment. None of them is an authoritative release, so
    none of them may produce a message -- a patient told "there is an update" about a value no
    professional has released is the exact harm §11 exists to prevent.
    """
    from decimal import Decimal

    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab A4")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="a4")
    enrol(repos, sessions, context)

    from daher_adapter.exchange.models import LabResultType

    tech = identity_id(repos, "labtech-a4")
    recorded = repos.results.record_observation(
        order_id=context.order_id,
        lab_order_item_id=context.item_id,
        lab_organization_id=context.lab_id,
        result_type=LabResultType.NUMERIC,
        source_code="SYNTH-OBS",
        display_label_snapshot="Synthetic analyte",
        recorded_by_identity_id=tech,
        numeric_value=Decimal("5.1"),
        numeric_unit="mmol/L",
        reference_range_low=Decimal("3.5"),
        reference_range_high=Decimal("6.0"),
    )
    assert _notifications(sessions, context.order_id) == []

    repos.results.correct_observation(
        order_id=context.order_id,
        lab_order_item_id=context.item_id,
        lab_organization_id=context.lab_id,
        observation_id=recorded.id,
        result_type=LabResultType.NUMERIC,
        source_code="SYNTH-OBS",
        display_label_snapshot="Synthetic analyte",
        corrected_by_identity_id=tech,
        numeric_value=Decimal("5.4"),
        numeric_unit="mmol/L",
        reference_range_low=Decimal("3.5"),
        reference_range_high=Decimal("6.0"),
    )
    repos.results.complete_entry(
        order_id=context.order_id,
        lab_order_item_id=context.item_id,
        lab_organization_id=context.lab_id,
        completed_by_identity_id=tech,
    )
    assert _notifications(sessions, context.order_id) == [], (
        "entry, pre-release correction and completion are not releases"
    )


def test_an_order_with_no_enrollment_owes_nothing(sessions) -> None:
    """Law 4 (§19): notification is optional, and its absence is silent, never an error."""
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab NOENR")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="noenr")
    enter_result(repos, context, key="noenr")

    record, created = release(repos, context, key="noenr")

    assert created is True, "the release must succeed exactly as it did before this ticket"
    assert record is not None
    assert _notifications(sessions, context.order_id) == []


# -- A6: a provider outage never rolls back a clinical release -----------------------------------


def test_a6_failed_external_send_does_not_roll_back_the_release(sessions) -> None:
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab A6")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="a6")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="a6")
    release(repos, context, key="a6")

    transport = FakeNotificationTransport()
    transport.fail_next(NotificationFailureCategory.TRANSIENT_PROVIDER, times=1)
    PatientNotificationDispatcher(sessions, transport=transport, worker_id="w-a6").dispatch_once()

    assert repos.releases.get_for_order(context.order_id) is not None, (
        "the release is a clinical act and survives a messaging failure"
    )
    assert repos.orders.get(context.order_id).status == "COMPLETED"
    row = _notifications(sessions, context.order_id)[-1]
    assert row.state == NotificationState.PENDING.value, "still owed, and due again later"
    assert row.failure_category == NotificationFailureCategory.TRANSIENT_PROVIDER.value


# -- A7/A8: the domain boundary, as a real diff --------------------------------------------------


def test_a7_and_a8_notification_never_mutates_result_delivery_or_doctor_review(sessions) -> None:
    """The whole path -- release, dispatch, delivery receipt -- against a before/after snapshot.

    This is the test NC-NOTIFY-3 must turn red. ``ResultDelivery`` means the ordering *provider
    organization* received the result, and its semantics are an open product decision
    (``docs/MVP-1.0.md`` §10 item 6); a patient's handset accepting an SMS says nothing about it.
    """
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab A7")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="a7")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="a7")
    release(repos, context, key="a7")

    before = _snapshot_clinical(sessions)
    assert before["deliveries"], "the fixture must actually have a ResultDelivery row to protect"

    transport = FakeNotificationTransport()
    PatientNotificationDispatcher(sessions, transport=transport, worker_id="w-a7").dispatch_once()

    row = _notifications(sessions, context.order_id)[-1]
    assert row.state == NotificationState.SENT.value
    with sessions.begin() as session:
        PatientNotificationRepository().apply_delivery_receipt(
            session, provider_message_id=row.provider_message_id, delivered=True
        )

    after = _snapshot_clinical(sessions)
    assert after == before, (
        "patient notification changed a ResultDelivery or DoctorReview row; it may change neither"
    )
    with sessions() as session:
        assert (
            session.get(PatientNotification, row.id).state == NotificationState.DELIVERED.value
        ), "the notification itself did advance -- so the snapshot equality is not vacuous"


# -- A9/A10/A11: the content boundary ------------------------------------------------------------


@pytest.mark.parametrize("amendment", [False, True])
def test_a9_a10_a11_release_messages_carry_no_clinical_content(sessions, amendment: bool) -> None:
    """Asserted against the bytes the transport was handed, using the fixture's own exact values.

    The forbidden strings are not a generic list: ``5.1``/``5.9`` are the values entered,
    ``mmol/L`` the unit, ``3.5``/``6.0`` the reference range, ``Synthetic analyte`` the label. If a
    template ever gained a slot for any of them, this goes red naming the one that leaked.
    """
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab A9")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="a9")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="a9", value="5.1")
    release(repos, context, key="a9")
    if amendment:
        amend_and_release(repos, context, key="a9", value="5.9")

    transport = FakeNotificationTransport()
    PatientNotificationDispatcher(sessions, transport=transport, worker_id="w-a9").dispatch_once()

    assert transport.sent, "the dispatcher must actually have sent something"
    forbidden = [
        "5.1",  # the entered value
        "5.9",  # the amended value
        "mmol/L",  # the unit
        "3.5",  # reference range low
        "6.0",  # reference range high
        "Synthetic analyte",  # the observation label
        "SYNTH-OBS",  # the observation code
        "Synthetic Patient",  # the patient's display name
        "Dr Synthetic",  # the ordering clinician
        "Lab A9",  # the laboratory
        "NO_FOLLOW_UP_REQUIRED",  # the review disposition
        "FOLLOW_UP_REQUIRED",
        "PENDING",
        str(context.order_id),  # internal identifiers
        str(context.item_id),
        str(context.provider_id),
    ]
    for message in transport.sent:
        for needle in forbidden:
            assert needle not in message.body, f"{needle!r} leaked into a patient message"
        # And the positive half: a release message must contain no link at all, because the
        # credential that would make one work is unrecoverable by then.
        assert "http://" not in message.body
        assert "https://" not in message.body


def test_release_and_amendment_messages_are_textually_identical(sessions) -> None:
    """Deliberate, not an oversight.

    "Your earlier result has been corrected" tells whoever is holding the phone that a correction
    happened to a named person -- a clinical fact, on a lock screen, in a household. The patient
    learns what changed from the authoritative status page, which is the only surface permitted to
    say it.
    """
    first = render(NotificationEventType.RESULT_RELEASED).body
    second = render(NotificationEventType.AMENDMENT_RELEASED).body
    assert first == second


def test_no_template_but_the_access_link_can_carry_a_url(sessions) -> None:
    """The structural guarantee behind A′: exactly one template has a substitution slot."""
    for event in NotificationEventType:
        for locale in ("fr", "en"):
            if event is NotificationEventType.ACCESS_LINK_ISSUED:
                rendered = render(event, locale=locale, patient_access_url="https://x/#access=T")
                assert "https://x/#access=T" in rendered.body
                continue
            rendered = render(event, locale=locale)
            with pytest.raises(ValueError):
                render(event, locale=locale, patient_access_url="https://x/#access=T")
            assert "{" not in rendered.body, "a release template has no substitution slot at all"


def test_dispatchable_event_types_exclude_the_link_message(sessions) -> None:
    """Pinned, because the absence is the design rather than an omission.

    A dispatcher that could claim an ``ACCESS_LINK_ISSUED`` row would have nothing to render, and
    the only ways to give it something are to persist the secret or to re-issue the grant -- the
    first forbidden, the second fatal to the printed QR already in the patient's hands.
    """
    assert DISPATCHABLE_EVENT_TYPES == {
        NotificationEventType.RESULT_RELEASED,
        NotificationEventType.AMENDMENT_RELEASED,
    }
    assert NotificationEventType.ACCESS_LINK_ISSUED not in DISPATCHABLE_EVENT_TYPES


# -- A12/A13/A14: the raw credential ------------------------------------------------------------


def test_a12_a13_a14_raw_token_is_absent_from_storage_logs_and_exceptions(sessions, caplog) -> None:
    """The invariant Liban's A′ decision exists to preserve, checked three ways.

    Storage: every column of every row in all three notification tables, plus the grant table
    itself. Logs: everything emitted at DEBUG and above for the whole send. Exceptions: a send that
    fails, whose recorded failure detail and attempt row are inspected.
    """
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab A12")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="a12")
    issued = enrol(repos, sessions, context)
    raw = issued.raw_token
    assert raw, "the fixture must have a real secret for this test to mean anything"

    repository = PatientNotificationRepository()
    with sessions.begin() as session:
        enrollment = repository.active_enrollment(session, lab_order_id=context.order_id)
        notification, _ = repository.enqueue(
            session,
            idempotency_key=f"access-link:{issued.id}:SMS:{SYNTHETIC_DESTINATION}",
            lab_order_id=context.order_id,
            release_version_id=None,
            enrollment=enrollment,
            event_type=NotificationEventType.ACCESS_LINK_ISSUED,
        )
        notification_id = notification.id

    transport = FakeNotificationTransport()
    url = build_patient_access_url(BASE_URL, raw)
    with caplog.at_level(logging.DEBUG):
        assert send_access_link_inline(
            sessions,
            transport=transport,
            notification_id=notification_id,
            patient_access_url=url,
        )

    # The message the patient receives genuinely contains the link -- otherwise the storage
    # assertions below would be trivially satisfied by never having sent anything.
    assert any(raw in message.body for message in transport.sent)

    with sessions() as session:
        blobs: list[str] = []
        for model in (
            PatientNotification,
            PatientNotificationAttempt,
            PatientNotificationEnrollment,
            PatientAccessGrant,
        ):
            for row in session.scalars(select(model)).all():
                blobs.extend(str(getattr(row, column.key)) for column in model.__table__.columns)
    assert not any(raw in blob for blob in blobs), "the raw bearer secret reached the database"
    assert not any(url in blob for blob in blobs), "the composed access URL reached the database"

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert raw not in log_text
    assert url not in log_text

    # A14: the same, on the failure path, where an exception's text is what gets recorded.
    failing = FakeNotificationTransport()
    failing.fail_next(NotificationFailureCategory.PROVIDER_REJECTED, detail="rejected")
    with sessions.begin() as session:
        enrollment = repository.active_enrollment(session, lab_order_id=context.order_id)
        second, _ = repository.enqueue(
            session,
            idempotency_key=f"access-link:{issued.id}:SMS:retry-probe",
            lab_order_id=context.order_id,
            release_version_id=None,
            enrollment=enrollment,
            event_type=NotificationEventType.ACCESS_LINK_ISSUED,
        )
        second_id = second.id
    assert not send_access_link_inline(
        sessions, transport=failing, notification_id=second_id, patient_access_url=url
    )
    with sessions() as session:
        failed = session.get(PatientNotification, second_id)
        assert failed.state == NotificationState.FAILED.value, (
            "an access-link send is terminal on failure -- the URL cannot be rebuilt"
        )
        attempts = list(
            session.scalars(
                select(PatientNotificationAttempt).where(
                    PatientNotificationAttempt.patient_notification_id == second_id
                )
            ).all()
        )
    assert attempts
    for attempt in attempts:
        assert raw not in (attempt.failure_detail or "")
        assert url not in (attempt.failure_detail or "")


def test_a12_access_link_notification_can_never_be_retried(sessions) -> None:
    """§4's recovery path is revoke-and-reissue, and the repository refuses anything else."""
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab RETRY")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="retry")
    issued = enrol(repos, sessions, context)

    repository = PatientNotificationRepository()
    with sessions.begin() as session:
        enrollment = repository.active_enrollment(session, lab_order_id=context.order_id)
        notification, _ = repository.enqueue(
            session,
            idempotency_key=f"access-link:{issued.id}:SMS:{SYNTHETIC_DESTINATION}",
            lab_order_id=context.order_id,
            release_version_id=None,
            enrollment=enrollment,
            event_type=NotificationEventType.ACCESS_LINK_ISSUED,
        )
        notification.state = NotificationState.FAILED.value
        with pytest.raises(ValueError, match="not recoverable"):
            repository.retry(session, notification=notification)


# -- A15/A16/A17: destination, consent, tenancy --------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["77123456", "", "   ", "not a number", "+0123456789", "+1", "+" + "9" * 40],
)
def test_a15_invalid_destinations_fail_before_any_provider_call(bad: str) -> None:
    """§6: refused at the boundary, so no obligation and no attempt ever exists for a bad number."""
    from daher_adapter.notify.phone import InvalidDestination, normalize

    with pytest.raises(InvalidDestination):
        normalize(bad)


def test_a15_no_silent_country_guessing() -> None:
    """A local number is refused without a configured region, and accepted with one.

    The seam §6 asks for: Djibouti is never hard-coded, and an unset region means "require a
    country code" rather than "assume wherever the container happens to run".
    """
    from daher_adapter.notify.phone import InvalidDestination, normalize

    with pytest.raises(InvalidDestination):
        normalize("77123456")
    assert normalize("77123456", default_region="DJ") == SYNTHETIC_DESTINATION


def test_a16_no_enrollment_means_no_send_even_after_a_release(sessions) -> None:
    """Consent is the gate, and it is a row rather than an inference.

    The order below has a patient access grant -- so the patient *could* be reached -- and no
    enrollment. §5: having a phone number, having scanned a QR, and having been issued a link are
    none of them permission to send.
    """
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab A16")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="a16")
    with sessions.begin() as session:
        repos.grants.issue(lab_order_id=context.order_id, session=session)
    enter_result(repos, context, key="a16")
    release(repos, context, key="a16")

    transport = FakeNotificationTransport()
    PatientNotificationDispatcher(sessions, transport=transport, worker_id="w-a16").dispatch_once()

    assert _notifications(sessions, context.order_id) == []
    assert transport.sent == []


def test_revoking_access_withdraws_the_enrollment_but_keeps_owed_messages(sessions) -> None:
    """Two facts that must not be confused.

    Revoking kills the link, so no *future* release enrols a message. A message already owed stays
    owed: the patient is still entitled to learn there is an update, and the message names their
    facility as the fallback -- which is the path Law 4 requires to work regardless.
    """
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab REV")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="rev")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="rev")
    release(repos, context, key="rev")
    owed_before = len(_notifications(sessions, context.order_id))
    assert owed_before >= 1

    with sessions.begin() as session:
        repos.grants.revoke(lab_order_id=context.order_id, session=session)
        assert PatientNotificationRepository().withdraw(session, lab_order_id=context.order_id)

    amend_and_release(repos, context, key="rev")

    rows = _notifications(sessions, context.order_id)
    assert len(rows) == owed_before, "a withdrawn enrollment owes nothing further"
    assert not any(r.event_type == NotificationEventType.AMENDMENT_RELEASED.value for r in rows)


def test_one_live_enrollment_per_order_and_re_enrolment_supersedes(sessions) -> None:
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab ENR2")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="enr2")
    issued = enrol(repos, sessions, context, destination=SYNTHETIC_DESTINATION)

    repository = PatientNotificationRepository()
    second_clinician = identity_id(
        repos, "clinician-2"
    )  # before the transaction: see notify_support
    with sessions.begin() as session:
        repository.enroll(
            session,
            lab_order_id=context.order_id,
            patient_access_grant_id=issued.id,
            channel=NotificationChannel.WHATSAPP,
            destination=SECOND_DESTINATION,
            consent_recorded_by_identity_id=second_clinician,
        )

    with sessions() as session:
        live = list(
            session.scalars(
                select(PatientNotificationEnrollment).where(
                    PatientNotificationEnrollment.lab_order_id == context.order_id,
                    PatientNotificationEnrollment.withdrawn_at.is_(None),
                )
            ).all()
        )
    assert len(live) == 1
    assert live[0].destination == SECOND_DESTINATION
    assert live[0].channel == NotificationChannel.WHATSAPP.value


# -- retry budget and state machine --------------------------------------------------------------


def test_retryable_failures_back_off_then_terminate(sessions) -> None:
    """Bounded: six attempts, then a FAILED row an operator is told about. Never an endless loop."""
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab RB")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="rb")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="rb")
    release(repos, context, key="rb")

    policy = NotificationRetryPolicy(
        max_attempts=3, base_delay=timedelta(seconds=1), max_delay=timedelta(seconds=2)
    )
    transport = FakeNotificationTransport()
    transport.fail_next(NotificationFailureCategory.TRANSIENT_PROVIDER, times=5)
    dispatcher = PatientNotificationDispatcher(
        sessions, transport=transport, worker_id="w-rb", policy=policy
    )

    moment = utc_now()
    states = []
    for step in range(4):
        dispatcher.dispatch_once(now=moment + timedelta(minutes=step))
        states.append(_notifications(sessions, context.order_id)[-1].state)

    assert states[0] == NotificationState.PENDING.value
    assert states[-1] == NotificationState.FAILED.value, "the budget is spent and it stops"
    row = _notifications(sessions, context.order_id)[-1]
    assert row.attempt_count == 3
    with sessions() as session:
        attempts = list(
            session.scalars(
                select(PatientNotificationAttempt).where(
                    PatientNotificationAttempt.patient_notification_id == row.id
                )
            ).all()
        )
    assert len(attempts) == 3, "one attempt row per hand-over"
    assert all(a.outcome == "FAILED" for a in attempts)


def test_permanent_failures_do_not_retry_at_all(sessions) -> None:
    """An invalid destination is still invalid in thirty seconds; retrying hides it from whoever
    could fix it, and burns a budget that exists for network trouble."""
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab PF")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="pf")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="pf")
    release(repos, context, key="pf")

    transport = FakeNotificationTransport()
    transport.fail_next(NotificationFailureCategory.INVALID_DESTINATION)
    PatientNotificationDispatcher(sessions, transport=transport, worker_id="w-pf").dispatch_once()

    row = _notifications(sessions, context.order_id)[-1]
    assert row.state == NotificationState.FAILED.value
    assert row.attempt_count == 1


def test_operator_retry_makes_a_failed_message_due_again(sessions) -> None:
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab OR")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="or")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="or")
    release(repos, context, key="or")

    transport = FakeNotificationTransport()
    transport.fail_next(NotificationFailureCategory.PROVIDER_REJECTED)
    dispatcher = PatientNotificationDispatcher(sessions, transport=transport, worker_id="w-or")
    dispatcher.dispatch_once()
    row = _notifications(sessions, context.order_id)[-1]
    assert row.state == NotificationState.FAILED.value

    repository = PatientNotificationRepository()
    with sessions.begin() as session:
        repository.retry(session, notification=session.get(PatientNotification, row.id))
    dispatcher.dispatch_once()

    after = _notifications(sessions, context.order_id)[-1]
    assert after.state == NotificationState.SENT.value
    assert after.provider_message_id
    # Still one obligation, not two -- a retry, never a resend.
    assert (
        len(
            [
                r
                for r in _notifications(sessions, context.order_id)
                if r.event_type == NotificationEventType.RESULT_RELEASED.value
            ]
        )
        == 1
    )


def test_sent_is_not_delivered(sessions) -> None:
    """§7's whole point: an HTTP 201 from a vendor is not a handset."""
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab SD")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="sd")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="sd")
    release(repos, context, key="sd")

    transport = FakeNotificationTransport()
    PatientNotificationDispatcher(sessions, transport=transport, worker_id="w-sd").dispatch_once()

    row = _notifications(sessions, context.order_id)[-1]
    assert row.state == NotificationState.SENT.value
    assert row.sent_at is not None
    assert row.delivered_at is None, "nothing about a handset is known yet"


def test_a_poisoned_row_does_not_block_the_rest_of_the_batch(sessions) -> None:
    """§14: one bad destination must not hold up every other patient's notification."""
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab POISON")
    contexts = []
    for index in range(3):
        context = order_in_progress(repos, sessions, lab_id=lab.id, key=f"poison{index}")
        enrol(repos, sessions, context)
        enter_result(repos, context, key=f"poison{index}")
        release(repos, context, key=f"poison{index}")
        contexts.append(context)

    transport = FakeNotificationTransport()
    transport.fail_next(NotificationFailureCategory.INVALID_DESTINATION)  # only the first
    outcome = PatientNotificationDispatcher(
        sessions, transport=transport, worker_id="w-poison"
    ).dispatch_once()

    assert outcome.claimed == 3
    assert outcome.sent == 2
    assert outcome.failed == 1
    states = [_notifications(sessions, c.order_id)[-1].state for c in contexts]
    assert states.count(NotificationState.SENT.value) == 2
    assert states.count(NotificationState.FAILED.value) == 1


def test_an_expired_claim_is_reclaimable(sessions) -> None:
    """A worker that dies holding work blocks it for one lease, not forever."""
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab LEASE")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="lease")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="lease")
    release(repos, context, key="lease")

    repository = PatientNotificationRepository()
    moment = utc_now()
    with sessions.begin() as session:
        claimed = repository.claim(session, worker_id="dead-worker", now=moment)
        assert len(claimed) == 1

    with sessions.begin() as session:
        assert repository.claim(session, worker_id="live-worker", now=moment) == [], (
            "the lease is still held"
        )

    later = moment + timedelta(minutes=5)
    with sessions.begin() as session:
        reclaimed = repository.claim(session, worker_id="live-worker", now=later)
    assert len(reclaimed) == 1
    assert reclaimed[0].attempt_count == 2, "a hand-over is counted even when the worker vanished"


def test_disabled_notifications_claim_nothing_and_report_nothing_wrong(sessions) -> None:
    """§10: disabled and failing are different operational states."""
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab OFF")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="off")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="off")
    release(repos, context, key="off")

    dispatcher = PatientNotificationDispatcher(sessions, transport=None, worker_id="w-off")
    assert dispatcher.enabled is False
    outcome = dispatcher.dispatch_once()
    assert (outcome.claimed, outcome.sent, outcome.failed, outcome.errored) == (0, 0, 0, 0)
    assert _notifications(sessions, context.order_id)[-1].state == NotificationState.PENDING.value


def test_operational_summary_carries_nothing_identifying(sessions) -> None:
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab OPS")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="ops")
    enrol(repos, sessions, context)
    enter_result(repos, context, key="ops")
    release(repos, context, key="ops")

    with sessions() as session:
        summary = PatientNotificationRepository().operational_summary(session)

    blob = repr(summary)
    assert SYNTHETIC_DESTINATION not in blob
    assert str(context.order_id) not in blob
    assert summary["by_state"][NotificationState.PENDING.value] == 1
    assert summary["oldest_pending_age_seconds"] is not None
    assert set(summary) == {
        "by_state",
        "by_failure_category",
        "by_channel",
        "oldest_pending_age_seconds",
    }


def test_enqueue_losing_the_insert_race_leaves_the_callers_transaction_usable(
    sessions, monkeypatch
) -> None:
    """The regression test for a bug real PostgreSQL found and SQLite had hidden.

    The first version of ``enqueue`` called ``session.add(notification)`` **before** opening the
    savepoint. Entering ``begin_nested()`` calls ``_take_snapshot()``, which flushes everything
    already pending -- so the INSERT was emitted by the *outer* transaction, before the savepoint
    existed. A unique-key collision then aborted the caller's transaction rather than the
    savepoint, the recovery query in the ``except`` clause could not even run, and the caller here
    is a **clinical result release**: a patient owed a duplicate message would have rolled back a
    released result.

    Concurrency is what *surfaced* it, but the failure itself is deterministic, so this reproduces
    it single-threaded. The one thing a single thread cannot do naturally is lose the race, because
    ``enqueue``'s fast-path read would find the existing row and return before any insert is
    attempted. Making that read miss exactly once is precisely what losing the race looks like from
    inside the loser, so it is simulated rather than approximated.

    The final assertion is the one that distinguishes the bug from the fix. Returning the winner
    proves the *recovery* ran; executing a further statement inside the same ``with`` proves the
    caller's transaction was never poisoned -- and that is the property protecting the release.
    """
    repos = build_repos(sessions)
    lab = make_lab(repos.organizations, name="Lab ENQRACE")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key="enqrace")
    issued = enrol(repos, sessions, context)
    repository = PatientNotificationRepository()
    key = f"access-link:{issued.id}:SMS:{SYNTHETIC_DESTINATION}"

    with sessions.begin() as session:
        enrollment = repository.active_enrollment(session, lab_order_id=context.order_id)
        _first, created = repository.enqueue(
            session,
            idempotency_key=key,
            lab_order_id=context.order_id,
            release_version_id=None,
            enrollment=enrollment,
            event_type=NotificationEventType.ACCESS_LINK_ISSUED,
        )
        assert created is True

    with sessions.begin() as session:
        enrollment = repository.active_enrollment(session, lab_order_id=context.order_id)

        # Patched only now, after the enrollment read, so exactly one query is blinded: the
        # fast-path lookup at the top of `enqueue`. Everything afterwards -- including the
        # recovery query in the `except` -- runs normally.
        original_scalar = Session.scalar
        blinded = {"done": False}

        def _miss_once(self, statement, *args, **kwargs):
            if not blinded["done"]:
                blinded["done"] = True
                return None
            return original_scalar(self, statement, *args, **kwargs)

        monkeypatch.setattr(Session, "scalar", _miss_once)

        winner, created = repository.enqueue(
            session,
            idempotency_key=key,
            lab_order_id=context.order_id,
            release_version_id=None,
            enrollment=enrollment,
            event_type=NotificationEventType.ACCESS_LINK_ISSUED,
        )

        assert blinded["done"], "the fast path must actually have been blinded"
        assert created is False, "the loser converges on the existing obligation"
        assert winner.idempotency_key == key
        # The property a clinical release depends on: the caller's transaction is still alive.
        assert session.execute(select(PatientNotification.id)).first() is not None

    # And it committed. One obligation, not two, and nothing the outer transaction did was lost.
    with sessions() as session:
        rows = list(
            session.scalars(
                select(PatientNotification).where(PatientNotification.idempotency_key == key)
            ).all()
        )
    assert len(rows) == 1
