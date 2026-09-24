"""DH-NOTIFY-001 §23 group D — real PostgreSQL qualification.

Most of this suite's claims are testable on SQLite. These are not, and the distinction is
structural rather than a matter of likelihood:

*   ``PatientNotificationRepository.claim`` uses ``SELECT ... FOR UPDATE SKIP LOCKED``. **SQLite
    silently ignores both clauses.** Every claim test in the SQLite domain suite therefore
    passes whether or not that line exists, and two dispatchers sending the same patient the
    same message twice would be caught by nothing.
*   ``uq_patient_notifications_idempotency_key`` is what makes "one release version owes one
    message" true under concurrency. A unique index that did not apply looks identical in every
    single-threaded test.
*   ``uq_patient_notifications_provider_message_id`` is a **partial** unique index over a nullable
    column, and it is what lets an authenticated delivery receipt resolve to exactly one row.
*   ``0032_patient_notifications`` drops and recreates the audit-vocabulary CHECK constraints.
    SQLite has no ``ALTER TABLE ... DROP CONSTRAINT``, so that branch never executes on SQLite.
*   The exact E.164 regex CHECK is emitted for PostgreSQL only -- the SQLite suite enforces the
    weaker portable shape guard instead.

**Manual / opt-in only**, like every other live-infrastructure test in this directory: skipped by
default (including in CI) unless every ``DHNOTIFY001_PG_*`` variable is set. Never point it at a
real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHNOTIFY001_PG_HOST=127.0.0.1 \\
    DHNOTIFY001_PG_PORT=5432 \\
    DHNOTIFY001_PG_USER=dhnotify001 \\
    DHNOTIFY001_PG_PASSWORD=dhnotify001 \\
    DHNOTIFY001_PG_DATABASE=dhnotify001_qual \\
        pytest adapter/tests/integration/test_dhnotify001_postgres_qualification.py -v

The target database must already be migrated through ``0032_patient_notifications``; this file does
not run migrations. **No real phone number and no real provider credential appears here** -- the
destinations are syntactically valid E.164 values in Djibouti's mobile range that belong to nobody,
and the only transport used is the in-memory fake, which contacts nothing.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from exchange_support import make_lab
from notify_support import (
    SYNTHETIC_DESTINATION,
    build_repos,
    enrol,
    enter_result,
    order_in_progress,
    release,
)
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import sessionmaker

from daher_adapter.notify.dispatcher import PatientNotificationDispatcher
from daher_adapter.notify.fake import FakeNotificationTransport
from daher_adapter.notify.models import (
    NotificationChannel,
    NotificationEventType,
    NotificationFailureCategory,
    NotificationState,
    PatientNotification,
    PatientNotificationEnrollment,
)
from daher_adapter.notify.repository import (
    PatientNotificationRepository,
    access_link_key,
    release_key,
)

_REQUIRED = (
    "DHNOTIFY001_PG_HOST",
    "DHNOTIFY001_PG_PORT",
    "DHNOTIFY001_PG_USER",
    "DHNOTIFY001_PG_PASSWORD",
    "DHNOTIFY001_PG_DATABASE",
)

pytestmark = pytest.mark.skipif(
    any(not os.environ.get(name) for name in _REQUIRED),
    reason="requires a disposable PostgreSQL instance; set every DHNOTIFY001_PG_* variable",
)


@pytest.fixture(scope="module")
def pg_engine():
    url = (
        f"postgresql+psycopg://{os.environ['DHNOTIFY001_PG_USER']}:"
        f"{os.environ['DHNOTIFY001_PG_PASSWORD']}@{os.environ['DHNOTIFY001_PG_HOST']}:"
        f"{os.environ['DHNOTIFY001_PG_PORT']}/{os.environ['DHNOTIFY001_PG_DATABASE']}"
    )
    made = create_engine(url)
    yield made
    made.dispose()


@pytest.fixture
def sessions(pg_engine):
    """Overrides ``conftest.py``'s SQLite fixture for this module.

    Named ``sessions`` deliberately so that ``notify_support``'s helpers -- which build a whole
    order through the real repositories -- run unchanged against PostgreSQL. Reusing the same
    fixtures against a different engine is the point: what is under test is the database, not a
    second copy of the setup code.
    """
    return sessionmaker(bind=pg_engine, expire_on_commit=False)


def _unique(prefix: str) -> str:
    """A per-run suffix. The database is disposable but not necessarily empty between runs, and
    unique keys that collided with a previous run's rows would fail for the wrong reason."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# -- D7: release and obligation are atomic --------------------------------------------------------


def test_pg1_release_and_obligation_commit_together(sessions) -> None:
    """§12: one transaction, so a crash cannot leave a release with nobody owed a message."""
    repos = build_repos(sessions)
    key = _unique("pg1")
    lab = make_lab(repos.organizations, name=f"Lab {key}")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    enrol(repos, sessions, context)
    enter_result(repos, context, key=key)

    release(repos, context, key=key)

    with sessions() as session:
        row = session.scalars(
            select(PatientNotification).where(PatientNotification.lab_order_id == context.order_id)
        ).one()
        assert row.state == NotificationState.PENDING.value
        assert repos.releases.get_for_order(context.order_id) is not None


def test_pg6_a_rolled_back_release_leaves_no_half_created_notification(sessions) -> None:
    """The other direction of the same invariant, proved by forcing the rollback.

    The hook runs inside the caller's transaction and then the transaction is abandoned. If the
    obligation had been written on its own connection -- the mistake a "just enqueue it after"
    design makes -- a row would survive here for a release that never happened.
    """
    from daher_adapter.notify.hooks import notify_patient_of_release

    repos = build_repos(sessions)
    key = _unique("pg6")
    lab = make_lab(repos.organizations, name=f"Lab {key}")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    enrol(repos, sessions, context)
    enter_result(repos, context, key=key)
    release(repos, context, key=key)

    with sessions() as session:
        version_id = session.scalars(
            select(PatientNotification.release_version_id).where(
                PatientNotification.lab_order_id == context.order_id
            )
        ).first()

    before = _count(sessions, context.order_id)
    session = sessions()
    try:
        session.begin()
        notify_patient_of_release(
            session,
            lab_order_id=context.order_id,
            release_version_id=uuid.UUID(str(version_id)),
        )
        session.rollback()
    finally:
        session.close()

    assert _count(sessions, context.order_id) == before


def _count(sessions, order_id) -> int:
    with sessions() as session:
        return int(
            session.scalar(
                select(func.count(PatientNotification.id)).where(
                    PatientNotification.lab_order_id == order_id
                )
            )
            or 0
        )


# -- D2: the uniqueness constraint, under genuine concurrency -------------------------------------


def test_pg2_concurrent_hooks_for_one_release_version_produce_exactly_one_row(sessions) -> None:
    """The test ``NC-NOTIFY-1`` must turn red.

    Two real threads, two real transactions, one release version. The loser's INSERT violates
    ``uq_patient_notifications_idempotency_key`` and is resolved by re-reading the winner --
    **the database decides**, which is the only place that decision can be correct. The
    application-level "check then insert" would let both threads observe "no row yet" and both
    insert, sending the same patient the same message twice.
    """
    from daher_adapter.notify.hooks import notify_patient_of_release

    repos = build_repos(sessions)
    key = _unique("pg2")
    lab = make_lab(repos.organizations, name=f"Lab {key}")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    enrol(repos, sessions, context)
    enter_result(repos, context, key=key)
    release(repos, context, key=key)

    with sessions() as session:
        version_id = uuid.UUID(
            str(
                session.scalars(
                    select(PatientNotification.release_version_id).where(
                        PatientNotification.lab_order_id == context.order_id
                    )
                ).first()
            )
        )
        session.execute(
            text("DELETE FROM patient_notifications WHERE lab_order_id = :o"),
            {"o": context.order_id},
        )
        session.commit()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _fire() -> None:
        try:
            with sessions.begin() as own:
                barrier.wait(timeout=10)
                notify_patient_of_release(
                    own, lab_order_id=context.order_id, release_version_id=version_id
                )
        except BaseException as error:  # noqa: BLE001 - recorded, then asserted on
            errors.append(error)

    threads = [threading.Thread(target=_fire) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == [], f"a concurrent hook raised rather than converging: {errors}"
    assert _count(sessions, context.order_id) == 1


def test_pg2b_the_unique_constraint_exists_and_bites(sessions) -> None:
    """Named directly, so removing it fails here as well as in the concurrency test above."""
    repos = build_repos(sessions)
    key = _unique("pg2b")
    lab = make_lab(repos.organizations, name=f"Lab {key}")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    issued = enrol(repos, sessions, context)
    repository = PatientNotificationRepository()
    duplicate_key = access_link_key(issued.id, NotificationChannel.SMS, SYNTHETIC_DESTINATION)

    with sessions.begin() as session:
        enrollment = repository.active_enrollment(session, lab_order_id=context.order_id)
        repository.enqueue(
            session,
            idempotency_key=duplicate_key,
            lab_order_id=context.order_id,
            release_version_id=None,
            enrollment=enrollment,
            event_type=NotificationEventType.ACCESS_LINK_ISSUED,
        )

    # A raw insert, bypassing `enqueue`'s own read-first fast path, so the constraint is what is
    # being tested rather than the application check in front of it.
    with pytest.raises(IntegrityError), sessions.begin() as session:
        enrollment = repository.active_enrollment(session, lab_order_id=context.order_id)
        session.add(
            PatientNotification(
                idempotency_key=duplicate_key,
                lab_order_id=context.order_id,
                release_version_id=None,
                patient_access_grant_id=issued.id,
                enrollment_id=enrollment.id,
                event_type=NotificationEventType.ACCESS_LINK_ISSUED.value,
                channel=NotificationChannel.SMS.value,
                destination=SYNTHETIC_DESTINATION,
                template_id="patient_access_link",
                template_version=1,
                locale="fr",
                state=NotificationState.PENDING.value,
                attempt_count=0,
                next_attempt_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.flush()


def test_an_amendment_is_a_different_key_and_is_never_collapsed(sessions) -> None:
    """The one place a key that was "obviously" the order id would silently lose a correction."""
    repos = build_repos(sessions)
    key = _unique("pgamend")
    lab = make_lab(repos.organizations, name=f"Lab {key}")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    enrol(repos, sessions, context)
    enter_result(repos, context, key=key)
    release(repos, context, key=key)
    from notify_support import amend_and_release

    amend_and_release(repos, context, key=key)

    with sessions() as session:
        rows = list(
            session.scalars(
                select(PatientNotification).where(
                    PatientNotification.lab_order_id == context.order_id
                )
            ).all()
        )
    assert len(rows) == 2
    assert {r.event_type for r in rows} == {
        NotificationEventType.RESULT_RELEASED.value,
        NotificationEventType.AMENDMENT_RELEASED.value,
    }
    assert len({r.idempotency_key for r in rows}) == 2
    assert len({r.release_version_id for r in rows}) == 2


# -- D1: two dispatchers cannot claim the same row ------------------------------------------------


def test_pg3_two_concurrent_dispatchers_never_claim_the_same_notification(sessions) -> None:
    """``FOR UPDATE SKIP LOCKED``, which SQLite ignores silently.

    Ten due notifications, two dispatchers claiming simultaneously. Every row must be claimed by
    exactly one of them -- a row claimed twice is a patient messaged twice about one result.
    """
    repos = build_repos(sessions)
    contexts = []
    for index in range(10):
        key = _unique(f"pg3-{index}")
        lab = make_lab(repos.organizations, name=f"Lab {key}")
        context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
        enrol(repos, sessions, context)
        enter_result(repos, context, key=key)
        release(repos, context, key=key)
        contexts.append(context)

    order_ids = {c.order_id for c in contexts}
    repository = PatientNotificationRepository()
    barrier = threading.Barrier(2)
    claimed: list[list[uuid.UUID]] = []
    lock = threading.Lock()

    def _claim(worker: str) -> None:
        with sessions.begin() as session:
            barrier.wait(timeout=10)
            rows = repository.claim(session, worker_id=worker, limit=10)
            mine = [row.id for row in rows if row.lab_order_id in order_ids]
        with lock:
            claimed.append(mine)

    threads = [threading.Thread(target=_claim, args=(f"worker-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(claimed) == 2
    first, second = (set(batch) for batch in claimed)
    assert first.isdisjoint(second), "two dispatchers claimed the same notification"
    assert len(first | second) == 10, "every due notification was claimed exactly once"


# -- D3/D4: crash recovery and restart survival ----------------------------------------------------


def test_pg4_a_worker_that_dies_after_claiming_is_recoverable(sessions) -> None:
    """The lease, against the real database. A worker killed mid-batch blocks its rows for one
    lease and then they become claimable again -- no operator gesture, no cleanup job."""
    repos = build_repos(sessions)
    key = _unique("pg4")
    lab = make_lab(repos.organizations, name=f"Lab {key}")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    enrol(repos, sessions, context)
    enter_result(repos, context, key=key)
    release(repos, context, key=key)

    repository = PatientNotificationRepository()
    moment = datetime.now(UTC)

    def _claim_until_ours(worker: str, now: datetime) -> list:
        """Drain claimable batches until this test's own row appears, or the queue is empty.

        The database is shared across this module, so earlier tests leave their own due rows
        behind, and a single ``claim`` -- bounded by design -- may return a full batch
        that does not include ours. Draining is what makes the assertion about *the lease*
        rather than about how many rows happened to precede it.
        """
        found: list = []
        for _ in range(20):
            with sessions.begin() as session:
                batch = repository.claim(session, worker_id=worker, now=now)
                found.extend(row for row in batch if row.lab_order_id == context.order_id)
                if not batch or found:
                    break
        return found

    assert len(_claim_until_ours("dies", moment)) == 1

    # Simulating the crash literally: the process is gone, nothing released the claim. Another
    # worker asking *now* must not be handed a message the first one still holds.
    assert _claim_until_ours("other", moment) == []

    # One lease later, it is claimable again -- with no operator gesture and no cleanup job.
    later = moment + timedelta(minutes=5)
    reclaimed = _claim_until_ours("other", later)
    assert len(reclaimed) == 1
    assert reclaimed[0].attempt_count == 2, (
        "a hand-over is counted even when the worker vanished without reporting"
    )


def test_pg4b_a_retry_survives_a_restart(sessions) -> None:
    """Durability, stated plainly: the obligation is a committed row, so "restart" is just a new
    dispatcher object reading the same table."""
    repos = build_repos(sessions)
    key = _unique("pg4b")
    lab = make_lab(repos.organizations, name=f"Lab {key}")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    enrol(repos, sessions, context)
    enter_result(repos, context, key=key)
    release(repos, context, key=key)

    failing = FakeNotificationTransport()
    failing.fail_next(NotificationFailureCategory.TRANSIENT_PROVIDER)
    PatientNotificationDispatcher(
        sessions, transport=failing, worker_id="before-restart"
    ).dispatch_once()

    with sessions() as session:
        row = session.scalars(
            select(PatientNotification).where(PatientNotification.lab_order_id == context.order_id)
        ).one()
        assert row.state == NotificationState.PENDING.value
        next_attempt = row.next_attempt_at

    # A brand-new dispatcher, as a restarted process would be.
    after = PatientNotificationDispatcher(
        sessions, transport=FakeNotificationTransport(), worker_id="after-restart"
    )
    after.dispatch_once(now=next_attempt + timedelta(seconds=1))

    with sessions() as session:
        row = session.scalars(
            select(PatientNotification).where(PatientNotification.lab_order_id == context.order_id)
        ).one()
    assert row.state == NotificationState.SENT.value


# -- D5: a poisoned row does not block the queue --------------------------------------------------


def test_pg5_a_poisoned_notification_does_not_block_later_valid_ones(sessions) -> None:
    repos = build_repos(sessions)
    contexts = []
    for index in range(3):
        key = _unique(f"pg5-{index}")
        lab = make_lab(repos.organizations, name=f"Lab {key}")
        context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
        enrol(repos, sessions, context)
        enter_result(repos, context, key=key)
        release(repos, context, key=key)
        contexts.append(context)

    transport = FakeNotificationTransport()
    transport.fail_next(NotificationFailureCategory.INVALID_DESTINATION)
    PatientNotificationDispatcher(sessions, transport=transport, worker_id="poison").dispatch_once()

    states = []
    with sessions() as session:
        for context in contexts:
            states.append(
                session.scalars(
                    select(PatientNotification).where(
                        PatientNotification.lab_order_id == context.order_id
                    )
                )
                .one()
                .state
            )
    assert states.count(NotificationState.SENT.value) == 2
    assert states.count(NotificationState.FAILED.value) == 1


# -- schema properties SQLite cannot enforce -------------------------------------------------------


def test_pg7_the_exact_e164_check_is_enforced_by_postgresql(sessions) -> None:
    """The PostgreSQL-only regex CHECK. SQLite has no ``~`` operator, so this branch of the
    schema is never exercised by the fast suite, and a destination the portable guard happens to
    admit -- letters inside a plausibly-shaped string -- would reach production unchecked."""
    repos = build_repos(sessions)
    key = _unique("pg7")
    lab = make_lab(repos.organizations, name=f"Lab {key}")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    issued = enrol(repos, sessions, context)

    with pytest.raises((IntegrityError, DataError)), sessions.begin() as session:
        session.add(
            PatientNotificationEnrollment(
                lab_order_id=context.order_id,
                patient_access_grant_id=issued.id,
                channel=NotificationChannel.SMS.value,
                # Passes `LIKE '+%'`, passes the length bounds, and is not a phone number.
                destination="+2537712ABCD",
                consent_recorded_at=datetime.now(UTC),
                consent_recorded_by_identity_id=issued.id,
                created_at=datetime.now(UTC),
            )
        )
        session.flush()


def test_pg8_provider_message_id_is_uniquely_resolvable(sessions) -> None:
    """The partial unique index the webhook depends on. Two rows sharing a provider id would make
    an authenticated receipt ambiguous -- and the column is nullable, so most rows share ``NULL``,
    which is exactly why the index has to be partial."""
    repos = build_repos(sessions)
    ids = []
    for index in range(2):
        key = _unique(f"pg8-{index}")
        lab = make_lab(repos.organizations, name=f"Lab {key}")
        context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
        enrol(repos, sessions, context)
        enter_result(repos, context, key=key)
        release(repos, context, key=key)
        with sessions() as session:
            ids.append(
                session.scalars(
                    select(PatientNotification).where(
                        PatientNotification.lab_order_id == context.order_id
                    )
                )
                .one()
                .id
            )

    shared = _unique("SM-shared")
    with sessions.begin() as session:
        session.get(PatientNotification, ids[0]).provider_message_id = shared
    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.get(PatientNotification, ids[1]).provider_message_id = shared
        session.flush()

    # And the reason it must be partial: many rows legitimately carry NULL at once.
    with sessions() as session:
        nulls = int(
            session.scalar(
                select(func.count(PatientNotification.id)).where(
                    PatientNotification.provider_message_id.is_(None)
                )
            )
            or 0
        )
    assert nulls >= 1


def test_pg9_the_audit_vocabulary_check_admits_the_new_event_types(pg_engine) -> None:
    """``0032``'s drop-and-recreate of the audit CHECK constraints, read out of ``pg_constraint``.

    SQLite has no ``ALTER TABLE ... DROP CONSTRAINT``, so the migration branch that widens these is
    never executed by the fast suite. Reading the definition back is what proves the migration ran
    rather than that the ORM's own metadata says the right thing.
    """
    with pg_engine.connect() as connection:
        definitions = {
            name: definition
            for name, definition in connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname IN ("
                    "'ck_exchange_security_audit_events_event_type', "
                    "'ck_exchange_security_audit_events_resource_type')"
                )
            ).all()
        }

    event_check = definitions["ck_exchange_security_audit_events_event_type"]
    assert "patient_notification_requested" in event_check
    assert "patient_notification_retry_requested" in event_check
    # Nothing the previous revision admitted was lost in the rewrite.
    assert "connector_message_refused" in event_check
    assert "lab_results_released" in event_check

    resource_check = definitions["ck_exchange_security_audit_events_resource_type"]
    assert "patient_notification" in resource_check
    assert "connector_installation" in resource_check


def test_pg10_the_release_version_presence_check_is_enforced(sessions) -> None:
    """ "Which release was this about?" is the question an amendment makes load-bearing, so it is a
    constraint rather than a convention: an access-link row may not name a version, and a release
    row must."""
    repos = build_repos(sessions)
    key = _unique("pg10")
    lab = make_lab(repos.organizations, name=f"Lab {key}")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    issued = enrol(repos, sessions, context)
    enter_result(repos, context, key=key)
    release(repos, context, key=key)

    with sessions() as session:
        version_id = session.scalars(
            select(PatientNotification.release_version_id).where(
                PatientNotification.lab_order_id == context.order_id
            )
        ).first()
        enrollment_id = session.scalars(
            select(PatientNotificationEnrollment.id).where(
                PatientNotificationEnrollment.lab_order_id == context.order_id
            )
        ).first()

    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(
            PatientNotification(
                idempotency_key=_unique("bad-presence"),
                lab_order_id=context.order_id,
                # An access-link row naming a release version: forbidden.
                release_version_id=version_id,
                patient_access_grant_id=issued.id,
                enrollment_id=enrollment_id,
                event_type=NotificationEventType.ACCESS_LINK_ISSUED.value,
                channel=NotificationChannel.SMS.value,
                destination=SYNTHETIC_DESTINATION,
                template_id="patient_access_link",
                template_version=1,
                locale="fr",
                state=NotificationState.PENDING.value,
                attempt_count=0,
                next_attempt_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.flush()


def test_pg11_one_live_enrollment_per_order_is_enforced_by_the_database(sessions) -> None:
    repos = build_repos(sessions)
    key = _unique("pg11")
    lab = make_lab(repos.organizations, name=f"Lab {key}")
    context = order_in_progress(repos, sessions, lab_id=lab.id, key=key)
    issued = enrol(repos, sessions, context)

    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(
            PatientNotificationEnrollment(
                lab_order_id=context.order_id,
                patient_access_grant_id=issued.id,
                channel=NotificationChannel.WHATSAPP.value,
                destination=SYNTHETIC_DESTINATION,
                consent_recorded_at=datetime.now(UTC),
                consent_recorded_by_identity_id=issued.id,
                created_at=datetime.now(UTC),
            )
        )
        session.flush()


def test_pg12_release_keys_are_distinct_per_channel_and_destination() -> None:
    """A pure-function check of the key shape, kept here beside the constraint it feeds: a
    corrected number or a second channel is a genuinely different intended message, and must not
    be collapsed into the one already sent."""
    version = uuid.uuid4()
    keys = {
        release_key(
            NotificationEventType.RESULT_RELEASED, version, NotificationChannel.SMS, "+25377111111"
        ),
        release_key(
            NotificationEventType.RESULT_RELEASED, version, NotificationChannel.SMS, "+25377222222"
        ),
        release_key(
            NotificationEventType.RESULT_RELEASED,
            version,
            NotificationChannel.WHATSAPP,
            "+25377111111",
        ),
        release_key(
            NotificationEventType.AMENDMENT_RELEASED,
            version,
            NotificationChannel.SMS,
            "+25377111111",
        ),
    }
    assert len(keys) == 4
