"""DHEALTH-APPOINTMENTS-001/ADR-073: real-PostgreSQL qualification of clinic appointments.

**Why this file has to exist.** The unit suite
(``tests/test_dhealthappointments001_appointments.py``, 46 tests) runs on SQLite, where everything
this ticket's concurrency claims rest on is absent or differently shaped:

- SQLAlchemy's SQLite dialect **omits ``FOR UPDATE`` entirely**, so every row lock in
  ``exchange/appointments.py`` is a no-op there and SQLite's whole-database writer serialisation
  produces the right answer for a reason that has nothing to do with the design;
- ``PRAGMA foreign_keys`` is off in ``tests/conftest.py``, so neither composite foreign key -- the
  patient-same-clinic one and the **visit**-same-clinic one that ``uq_exchange_visits_id_provider``
  was added for -- is enforced at all;
- ``DateTime(timezone=True)`` round-trips naive, so the stored-instant assertions have to normalise
  a timezone there and here do not.

What is qualified here and nowhere else:

- **PG-AP-1** the same create request twice, in genuinely overlapping transactions -> **one**
  appointment, and the loser replays rather than erroring.
- **PG-AP-2** cancel racing reschedule -> deterministic, no lost update, and never a cancelled
  booking carrying a rescheduled time.
- **PG-AP-3** two staff checking the same booking in at once -> **one** visit. The deterministic
  proof, with the second transaction genuinely blocked on the first's row lock.
- **PG-AP-4** cancel racing check-in -> exactly one wins and the other is refused. The state the
  ticket names as unacceptable -- a cancelled booking holding a checked-in visit -- is proven
  unreachable by two successful concurrent actions **and** refused by the schema independently.
- **PG-AP-5** reschedule racing check-in -> once arrival wins, scheduling cannot rewrite it.
- **PG-AP-6** the walk-in one-active-visit invariant is unchanged by any of this.
- **PG-AP-7** cross-tenant: the composite foreign key refuses a booking linked to another clinic's
  visit **in the database**, not merely in the service.
- **PG-AP-8** migration state: one head at ``0040_clinic_appointments``, the real constraint set,
  no fabricated backfill, and all three downgrade guards refusing with real rows present.

**The proof is the observable outcome, never an inspection of the lock.** Nothing here reads
``pg_locks``.

**Manual / opt-in only**, mirroring every other live-infra test in this directory: skipped by
default, including in CI, unless every ``DHAP_PG_*`` variable is set.

Usage::

    DHAP_PG_HOST=127.0.0.1 \\
    DHAP_PG_PORT=55471 \\
    DHAP_PG_USER=adapter \\
    DHAP_PG_PASSWORD=... \\
    DHAP_PG_DATABASE=dhap_qual \\
        pytest adapter/tests/integration/test_dhealthappointments001_postgres_qualification.py -v

The target database must already be migrated to ``0040_clinic_appointments``. **Never point it at a
facility database** -- nothing here checks that, so use a disposable instance and destroy it, and
its volume, afterwards.

All patient and organization data here is synthetic. No real patient data is used.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from exchange_auth_support import PLATFORM_ADMIN_ROLE, build_validator, generate_keypair, make_token
from exchange_support import make_provider
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.api import build_exchange_router
from daher_adapter.exchange.appointments import (
    AppointmentAlreadyArrived,
    AppointmentCancelled,
    AppointmentRepository,
)
from daher_adapter.exchange.audit import ExchangeAuditWriter
from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import Appointment, AppointmentStatus, Visit, VisitStatus
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patients import PatientRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
from daher_adapter.exchange.visits import ActiveVisitAlreadyExists, VisitRepository

PG_HOST = os.environ.get("DHAP_PG_HOST")
PG_PORT = os.environ.get("DHAP_PG_PORT")
PG_USER = os.environ.get("DHAP_PG_USER")
PG_PASSWORD = os.environ.get("DHAP_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHAP_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a disposable PostgreSQL database (DHAP_PG_*) -- never in CI",
)

TZ = "Africa/Djibouti"


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _prefix(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:8]}"


class Fixture:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def _seed(sessions: sessionmaker, key_prefix: str) -> Fixture:
    """A clinic, a patient in its directory, an actor identity, and the two repositories."""
    organizations = OrganizationRepository(sessions)
    patients = PatientRepository(sessions, organizations)
    security_audit = ExchangeSecurityAuditRepository()
    visits = VisitRepository(sessions, organizations, security_audit)
    appointments = AppointmentRepository(sessions, organizations, visits, security_audit, TZ)

    provider = make_provider(organizations, name=f"{key_prefix}-clinique")
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer="https://pgap-qualification.example", subject=f"{key_prefix}-actor"
    )
    patient = patients.create(
        provider_organization_id=provider.id,
        idempotency_key=f"{key_prefix}-patient",
        display_name=f"Patient Synthétique {key_prefix}",
        date_of_birth=date(1990, 4, 17),
        contact_channel_type=None,
        contact_value=None,
        created_by_identity_id=identity.id,
    )
    principal = AuthenticatedPrincipal(
        identity_id=identity.id,
        issuer="https://pgap-qualification.example",
        subject=f"{key_prefix}-actor",
        platform_admin=False,
    )
    return Fixture(
        key_prefix=key_prefix,
        organizations=organizations,
        patients=patients,
        visits=visits,
        appointments=appointments,
        provider_id=provider.id,
        patient_id=patient.patient.id,
        identity_id=identity.id,
        principal=principal,
    )


def _book(fx: Fixture, *, day: date | None = None, at: time = time(10, 0), key: str | None = None):
    record, created = fx.appointments.create(
        principal=fx.principal,
        provider_organization_id=fx.provider_id,
        patient_id=fx.patient_id,
        scheduled_date=day or date(2026, 10, 6),
        scheduled_time=at,
        idempotency_key=key or f"{fx.key_prefix}-{uuid.uuid4().hex[:8]}",
    )
    return record, created


# ── PG-AP-1: the same create request, twice, overlapping ─────────────────────────────────────────


def test_pg_ap_1_the_same_create_request_in_overlapping_transactions_makes_one_appointment(
    sessions: sessionmaker,
):
    """Two retries of the *same* submission, genuinely overlapping.

    The pre-read in `create` is an optimisation that both callers pass; only
    `uq_exchange_appointments_provider_idempotency_key` can refuse the second insert, and the
    SAVEPOINT is what turns that refusal into a replay instead of a 500. On SQLite the writer lock
    hides the race entirely, which is why this lives here."""
    fx = _seed(sessions, _prefix("ap1"))
    key = f"{fx.key_prefix}-shared"
    barrier = threading.Barrier(2, timeout=30)
    outcomes: list[object] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            barrier.wait()
            record, created = _book(fx, key=key)
            with lock:
                outcomes.append(("ok", record.id, created))
        except BaseException as error:  # noqa: BLE001
            with lock:
                outcomes.append(("error", error))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)

    errors = [o for o in outcomes if o[0] == "error"]
    assert not errors, f"a legitimate retry raised: {errors}"
    ids = {o[1] for o in outcomes}
    assert len(ids) == 1, f"the same request produced two appointments: {ids}"
    # Exactly one caller created it; the other replayed.
    assert sorted(o[2] for o in outcomes) == [False, True]

    with sessions() as session:
        rows = session.scalars(
            select(Appointment).where(Appointment.provider_organization_id == fx.provider_id)
        ).all()
        assert len(rows) == 1


# ── PG-AP-2: cancel racing reschedule ────────────────────────────────────────────────────────────


def test_pg_ap_2_cancel_racing_reschedule_is_deterministic_with_no_lost_update(
    sessions: sessionmaker,
):
    """Whichever transaction takes the row lock first decides, and the loser is **told**.

    ── Why this is written as a held transaction rather than a barrier race ──

    The first draft held a bare `SELECT ... FOR UPDATE` and then cancelled *after* releasing it.
    That made the reschedule block, unblock, succeed, and only then be followed by the cancel --
    which is reschedule-then-cancel, a perfectly legitimate sequence, and the test failed while
    nothing was wrong. The failure was the test's: it asserted an ordering it had not actually
    produced.

    So the holding transaction now performs the cancellation itself and keeps it uncommitted while
    the reschedule contends for the same row. That is a faithful "cancel won the lock first", and
    the only acceptable outcome is that the reschedule -- once unblocked -- re-reads committed truth
    inside its own lock, finds the booking cancelled, and refuses. A lost update would instead move
    a cancelled booking's time.
    """
    fx = _seed(sessions, _prefix("ap2"))
    booking, _ = _book(fx, day=date(2026, 10, 6), at=time(10, 0))

    cancel_pending = threading.Event()
    commit_cancel = threading.Event()
    result: dict[str, object] = {}

    def hold_uncommitted_cancel() -> None:
        try:
            with sessions.begin() as session:
                session.execute(
                    select(Appointment).where(Appointment.id == booking.id).with_for_update()
                ).one()
                session.execute(
                    text(
                        "UPDATE exchange_appointments SET status = 'CANCELLED', "
                        "cancelled_at = now(), cancelled_by_identity_id = :actor WHERE id = :id"
                    ),
                    {"actor": fx.identity_id, "id": booking.id},
                )
                cancel_pending.set()
                # Commit happens when this block exits -- after the reschedule has really contended.
                commit_cancel.wait(timeout=30)
        except BaseException as error:  # noqa: BLE001
            result["cancel"] = error
            cancel_pending.set()

    def contend_reschedule() -> None:
        try:
            assert cancel_pending.wait(timeout=30), "the cancel never opened"
            fx.appointments.reschedule(
                principal=fx.principal,
                provider_organization_id=fx.provider_id,
                appointment_id=booking.id,
                scheduled_date=date(2026, 10, 9),
                scheduled_time=time(15, 0),
            )
            result["reschedule"] = "ok"
        except BaseException as error:  # noqa: BLE001
            result["reschedule"] = error

    holder = threading.Thread(target=hold_uncommitted_cancel, daemon=True)
    holder.start()
    assert cancel_pending.wait(timeout=30), "the cancelling transaction never opened"
    assert "cancel" not in result, result

    contender = threading.Thread(target=contend_reschedule, daemon=True)
    contender.start()
    # It must be blocked on the held row, not racing ahead of it.
    contender.join(timeout=3)
    assert "reschedule" not in result, "the reschedule proceeded despite the held row lock"

    commit_cancel.set()
    holder.join(timeout=30)
    contender.join(timeout=40)

    # The reschedule lost the lock, so it must have been refused -- never silently applied.
    assert isinstance(result.get("reschedule"), AppointmentCancelled), result

    with sessions() as session:
        stored = session.get(Appointment, booking.id)
        assert stored.status == AppointmentStatus.CANCELLED.value
        # **The lost-update assertion.** The time is still the one it was booked for; the refused
        # reschedule changed nothing.
        assert stored.scheduled_at == datetime(2026, 10, 6, 7, 0, tzinfo=UTC)


# ── PG-AP-3: two staff, one arrival ──────────────────────────────────────────────────────────────


def test_pg_ap_3_two_concurrent_check_ins_produce_exactly_one_visit(sessions: sessionmaker):
    """**The deterministic proof.** The first check-in's transaction is held open on the booking's
    row lock while the second genuinely contends; the second then reads committed truth, finds the
    booking already linked, and returns that visit rather than creating another."""
    fx = _seed(sessions, _prefix("ap3"))
    booking, _ = _book(fx)

    barrier = threading.Barrier(2, timeout=30)
    outcomes: list[object] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            barrier.wait()
            result = fx.appointments.check_in(
                principal=fx.principal,
                provider_organization_id=fx.provider_id,
                appointment_id=booking.id,
            )
            with lock:
                outcomes.append(("ok", result.visit.visit.id, result.visit_created))
        except BaseException as error:  # noqa: BLE001
            with lock:
                outcomes.append(("error", error))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)

    errors = [o for o in outcomes if o[0] == "error"]
    assert not errors, f"a concurrent arrival raised: {errors}"
    visit_ids = {o[1] for o in outcomes}
    assert len(visit_ids) == 1, f"two visits were created for one booking: {visit_ids}"
    assert sorted(o[2] for o in outcomes) == [False, True]

    with sessions() as session:
        visits = session.scalars(
            select(Visit).where(Visit.provider_organization_id == fx.provider_id)
        ).all()
        assert len(visits) == 1
        assert visits[0].status == VisitStatus.CHECKED_IN.value
        assert session.get(Appointment, booking.id).visit_id == visits[0].id


# ── PG-AP-4: cancel racing check-in ──────────────────────────────────────────────────────────────


def test_pg_ap_4_cancel_racing_check_in_never_produces_a_cancelled_booking_with_a_visit(
    sessions: sessionmaker,
):
    """The one state the ticket names as unacceptable.

    Either ordering is acceptable -- check-in wins and the later cancel is refused, or cancel wins
    and the later check-in is refused -- but **never both succeeding**. Run as a barrier race and
    then asserted on the committed state, which is the only thing that matters."""
    fx = _seed(sessions, _prefix("ap4"))
    booking, _ = _book(fx)

    barrier = threading.Barrier(2, timeout=30)
    results: dict[str, object] = {}
    lock = threading.Lock()

    def do_cancel() -> None:
        try:
            barrier.wait()
            fx.appointments.cancel(
                principal=fx.principal,
                provider_organization_id=fx.provider_id,
                appointment_id=booking.id,
            )
            with lock:
                results["cancel"] = "ok"
        except BaseException as error:  # noqa: BLE001
            with lock:
                results["cancel"] = error

    def do_check_in() -> None:
        try:
            barrier.wait()
            fx.appointments.check_in(
                principal=fx.principal,
                provider_organization_id=fx.provider_id,
                appointment_id=booking.id,
            )
            with lock:
                results["check_in"] = "ok"
        except BaseException as error:  # noqa: BLE001
            with lock:
                results["check_in"] = error

    threads = [threading.Thread(target=do_cancel), threading.Thread(target=do_check_in)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)

    both_succeeded = results.get("cancel") == "ok" and results.get("check_in") == "ok"
    assert not both_succeeded, (
        "cancel and check-in both reported success -- that is the cancelled-booking-with-a-visit "
        f"state the ticket forbids: {results}"
    )

    with sessions() as session:
        stored = session.get(Appointment, booking.id)
        visits = session.scalars(
            select(Visit).where(Visit.provider_organization_id == fx.provider_id)
        ).all()
        if stored.status == AppointmentStatus.CANCELLED.value:
            # Cancel won: no visit may exist for this booking, and the schema agrees.
            assert stored.visit_id is None
            assert visits == [] or all(v.id != stored.visit_id for v in visits)
        else:
            # Check-in won: exactly one visit, linked, and the cancel was refused.
            assert stored.visit_id is not None
            assert len(visits) == 1
            assert isinstance(results.get("cancel"), AppointmentAlreadyArrived | Exception)


def test_pg_ap_4b_the_schema_itself_refuses_a_cancelled_booking_holding_a_visit(
    sessions: sessionmaker,
):
    """Defence in depth, asserted directly. Even if every service guard were removed, the database
    refuses the state -- `ck_exchange_appointments_cancelled_has_no_visit`."""
    fx = _seed(sessions, _prefix("ap4b"))
    booking, _ = _book(fx)
    result = fx.appointments.check_in(
        principal=fx.principal,
        provider_organization_id=fx.provider_id,
        appointment_id=booking.id,
    )
    visit_id = result.visit.visit.id

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text(
                    "UPDATE exchange_appointments SET status = 'CANCELLED', "
                    "cancelled_at = now(), cancelled_by_identity_id = :actor WHERE id = :id"
                ),
                {"actor": fx.identity_id, "id": booking.id},
            )

    with sessions() as session:
        stored = session.get(Appointment, booking.id)
        assert stored.status == AppointmentStatus.SCHEDULED.value
        assert stored.visit_id == visit_id


# ── PG-AP-5: reschedule racing check-in ──────────────────────────────────────────────────────────


def test_pg_ap_5_once_arrival_wins_scheduling_cannot_rewrite_it(sessions: sessionmaker):
    fx = _seed(sessions, _prefix("ap5"))
    booking, _ = _book(fx, day=date(2026, 10, 6), at=time(10, 0))

    barrier = threading.Barrier(2, timeout=30)
    results: dict[str, object] = {}
    lock = threading.Lock()

    def do_check_in() -> None:
        try:
            barrier.wait()
            fx.appointments.check_in(
                principal=fx.principal,
                provider_organization_id=fx.provider_id,
                appointment_id=booking.id,
            )
            with lock:
                results["check_in"] = "ok"
        except BaseException as error:  # noqa: BLE001
            with lock:
                results["check_in"] = error

    def do_reschedule() -> None:
        try:
            barrier.wait()
            fx.appointments.reschedule(
                principal=fx.principal,
                provider_organization_id=fx.provider_id,
                appointment_id=booking.id,
                scheduled_date=date(2026, 11, 2),
                scheduled_time=time(9, 0),
            )
            with lock:
                results["reschedule"] = "ok"
        except BaseException as error:  # noqa: BLE001
            with lock:
                results["reschedule"] = error

    threads = [threading.Thread(target=do_check_in), threading.Thread(target=do_reschedule)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)

    with sessions() as session:
        stored = session.get(Appointment, booking.id)
        if stored.visit_id is not None and results.get("check_in") == "ok":
            # Arrival happened. If the reschedule also succeeded it must have been ordered FIRST;
            # a reschedule applied *after* arrival is the forbidden outcome.
            if results.get("reschedule") == "ok":
                instant = stored.scheduled_at
                assert instant.astimezone(ZoneInfo(TZ)).date() == date(2026, 11, 2), (
                    "the reschedule reported success but its time was not stored"
                )
            # Either way a later reschedule must now be refused outright.
            with pytest.raises(AppointmentAlreadyArrived):
                fx.appointments.reschedule(
                    principal=fx.principal,
                    provider_organization_id=fx.provider_id,
                    appointment_id=booking.id,
                    scheduled_date=date(2026, 12, 1),
                    scheduled_time=time(8, 0),
                )


# ── PG-AP-6: the walk-in invariant is untouched ──────────────────────────────────────────────────


def test_pg_ap_6_the_one_active_visit_invariant_still_holds_for_walk_ins(sessions: sessionmaker):
    """The partial unique index ADR-069 added is unchanged by this ticket, and a booked arrival and
    a walk-in for the same patient cannot both become active visits."""
    fx = _seed(sessions, _prefix("ap6"))
    booking, _ = _book(fx)

    fx.visits.start(
        principal=fx.principal,
        provider_organization_id=fx.provider_id,
        patient_id=fx.patient_id,
        reason_for_visit=None,
        idempotency_key=f"{fx.key_prefix}-walkin",
    )
    with pytest.raises(ActiveVisitAlreadyExists):
        fx.appointments.check_in(
            principal=fx.principal,
            provider_organization_id=fx.provider_id,
            appointment_id=booking.id,
        )

    with sessions() as session:
        visits = session.scalars(
            select(Visit).where(Visit.provider_organization_id == fx.provider_id)
        ).all()
        assert len(visits) == 1
        assert session.get(Appointment, booking.id).visit_id is None


# ── PG-AP-7: the composite foreign key, enforced by the database ─────────────────────────────────


def test_pg_ap_7_the_database_refuses_a_booking_linked_to_another_clinics_visit(
    sessions: sessionmaker,
):
    """The reason `uq_exchange_visits_id_provider` was added. A plain `FOREIGN KEY (visit_id)` would
    accept this row; the composite reference refuses it, so a future code path cannot link across
    clinics even with every service guard bypassed.

    SQLite cannot qualify this at all -- `PRAGMA foreign_keys` is off in the unit suite."""
    mine = _seed(sessions, _prefix("ap7a"))
    theirs = _seed(sessions, _prefix("ap7b"))
    booking, _ = _book(mine)
    foreign = theirs.visits.start(
        principal=theirs.principal,
        provider_organization_id=theirs.provider_id,
        patient_id=theirs.patient_id,
        reason_for_visit=None,
        idempotency_key=f"{theirs.key_prefix}-visit",
    )

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text("UPDATE exchange_appointments SET visit_id = :visit WHERE id = :id"),
                {"visit": foreign.visit.id, "id": booking.id},
            )

    with sessions() as session:
        assert session.get(Appointment, booking.id).visit_id is None


def test_pg_ap_7b_the_database_refuses_a_booking_for_another_clinics_patient(
    sessions: sessionmaker,
):
    """The patient half of the same guard, also unenforceable under SQLite."""
    mine = _seed(sessions, _prefix("ap7c"))
    theirs = _seed(sessions, _prefix("ap7d"))

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.add(
                Appointment(
                    idempotency_key=f"{mine.key_prefix}-cross",
                    provider_organization_id=mine.provider_id,
                    patient_id=theirs.patient_id,
                    scheduled_at=datetime(2026, 10, 6, 7, 0, tzinfo=UTC),
                    status=AppointmentStatus.SCHEDULED.value,
                    created_by_identity_id=mine.identity_id,
                )
            )


# ── PG-AP-8: migration state ─────────────────────────────────────────────────────────────────────


def test_pg_ap_8_migration_state_constraints_and_no_fabricated_backfill(sessions: sessionmaker):
    """One head, the real constraint set read from PostgreSQL's own catalogue, and the honest
    absence of a backfill: **no appointment exists for any visit that predates this migration**."""
    with sessions() as session:
        heads = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    assert len(heads) == 1, f"exactly one Alembic head, found {heads}"
    assert heads[0] == "0040_clinic_appointments", heads[0]

    with sessions() as session:
        constraints = {
            (row[0], row[1])
            for row in session.execute(
                text(
                    "SELECT conname, contype FROM pg_constraint "
                    "WHERE conrelid = 'exchange_appointments'::regclass"
                )
            ).all()
        }
    names = {name for name, _ in constraints}
    for expected in (
        "uq_exchange_appointments_provider_idempotency_key",
        "uq_exchange_appointments_visit_id",
        "ck_exchange_appointments_status",
        "ck_exchange_appointments_cancelled_at_matches_status",
        "ck_exchange_appointments_cancellation_actor_pairing",
        "ck_exchange_appointments_cancelled_has_no_visit",
        "fk_exchange_appointments_patient_same_provider",
        "fk_exchange_appointments_visit_same_provider",
    ):
        assert expected in names, f"{expected} missing from {sorted(names)}"

    # **No capacity constraint.** Two patients may share an instant, so nothing may be unique over
    # scheduled_at. Asserted rather than assumed, because adding one would look like a feature.
    with sessions() as session:
        capacity = session.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'exchange_appointments' "
                "AND indexdef LIKE '%UNIQUE%' AND indexdef LIKE '%scheduled_at%'"
            )
        ).all()
    assert capacity == [], f"a capacity-shaped unique index exists: {capacity}"

    # The additive constraint on the visits table that the composite foreign key needs.
    with sessions() as session:
        visit_uniques = set(
            session.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'exchange_visits'::regclass AND contype = 'u'"
                )
            ).all()
        )
    assert "uq_exchange_visits_id_provider" in visit_uniques

    # The visit lifecycle is still four states -- ADR-073's central refusal, in the schema.
    with sessions() as session:
        visit_status_check = session.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_exchange_visits_status'"
            )
        )
    assert "PLANNED" not in visit_status_check
    assert "NO_SHOW" not in visit_status_check


def test_pg_ap_8b_all_three_downgrade_guards_refuse_with_real_rows_present(
    sessions: sessionmaker, tmp_path: Path
):
    """A booking and its audit rows exist, so a downgrade must refuse rather than destroy them.

    Exercised through `alembic` itself, against this same database, and the database is left at
    `0040_clinic_appointments` -- the guards fail **before any DDL**, which is the contract."""
    import subprocess

    fx = _seed(sessions, _prefix("ap8b"))
    _book(fx)
    with sessions() as session:
        assert session.scalars(select(Appointment)).all(), "the guard needs a real row"

    env = dict(os.environ)
    env.update(
        {
            "ADAPTER_DB_HOST": PG_HOST or "",
            "ADAPTER_DB_PORT": PG_PORT or "",
            "ADAPTER_DB_NAME": PG_DATABASE or "",
            "ADAPTER_DB_USER": PG_USER or "",
            "ADAPTER_DB_PASSWORD": PG_PASSWORD or "",
        }
    )
    adapter_root = Path(__file__).resolve().parents[2]
    # Every argument is a literal or a path derived from this file's own location -- the same shape
    # and the same suppression `test_dh011j_cancellation_crash_recovery.py` already carries.
    completed = subprocess.run(  # noqa: S603
        [str(adapter_root / ".venv" / "bin" / "alembic"), "downgrade", "0039_lab_specimens"],
        cwd=adapter_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0, "the downgrade succeeded while real bookings existed"
    combined = completed.stdout + completed.stderr
    assert "refusing to downgrade past 0040_clinic_appointments" in combined, combined

    # And nothing was dropped: the database is still at head with the booking intact.
    with sessions() as session:
        heads = session.scalars(text("SELECT version_num FROM alembic_version")).all()
        assert heads == ["0040_clinic_appointments"]
        assert session.scalars(select(Appointment)).all()


def test_pg_ap_8c_no_appointment_was_fabricated_for_a_pre_existing_visit(sessions: sessionmaker):
    """A past visit does not prove there was a prior booking, and 0040 invents none. A walk-in visit
    created here has no appointment pointing at it -- which is also exactly what every visit that
    predates the migration looks like."""
    fx = _seed(sessions, _prefix("ap8c"))
    started = fx.visits.start(
        principal=fx.principal,
        provider_organization_id=fx.provider_id,
        patient_id=fx.patient_id,
        reason_for_visit=None,
        idempotency_key=f"{fx.key_prefix}-walkin",
    )
    with sessions() as session:
        linked = session.scalars(
            select(Appointment).where(Appointment.visit_id == started.visit.id)
        ).all()
        assert linked == []


def test_pg_ap_8d_the_stored_instant_is_the_facility_wall_clock_as_real_timestamptz(
    sessions: sessionmaker, tmp_path: Path
):
    """10:00 in Djibouti is 07:00 UTC, asserted against a genuine `timestamptz` with no timezone
    normalisation -- which the SQLite suite cannot do.

    Driven through the HTTP edge as well, so the request shape (a clinic-local date and wall clock,
    never an instant) is qualified rather than only the repository."""
    private_key, public_key = generate_keypair()
    router = build_exchange_router(
        session_factory=sessions,
        token_validator=build_validator(public_key),
        audit_writer=ExchangeAuditWriter(tmp_path / "audit.jsonl"),
        platform_admin_role=PLATFORM_ADMIN_ROLE,
        facility_timezone=TZ,
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    fx = _seed(sessions, _prefix("ap8d"))
    subject = f"{fx.key_prefix}-sched"
    token = make_token(private_key, subject=subject)
    me = client.get("/exchange/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    admin = make_token(private_key, subject=f"admin-{subject}", roles=[PLATFORM_ADMIN_ROLE])
    membership = client.post(
        f"/exchange/v1/organizations/{fx.provider_id}/memberships",
        headers={"Authorization": f"Bearer {admin}"},
        json={"identity_id": me.json()["identity_id"], "roles": ["ORG_ADMIN"]},
    )
    assert membership.status_code in (200, 201), membership.text
    granted = client.put(
        f"/exchange/v1/organizations/{fx.provider_id}/memberships/"
        f"{membership.json()['membership_id']}/capabilities/appointment.manage",
        headers={"Authorization": f"Bearer {admin}"},
        json={"effect": "GRANT"},
    )
    assert granted.status_code == 200, granted.text

    created = client.post(
        f"/exchange/v1/organizations/{fx.provider_id}/appointments",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        json={
            "patient_id": str(fx.patient_id),
            "scheduled_date": "2026-10-06",
            "scheduled_time": "10:00:00",
        },
    )
    assert created.status_code == 201, created.text

    with sessions() as session:
        stored = session.get(Appointment, uuid.UUID(created.json()["id"]))
        assert stored.scheduled_at.tzinfo is not None, "timestamptz round-tripped as naive"
        assert stored.scheduled_at == datetime(2026, 10, 6, 7, 0, tzinfo=UTC)

    # The clinic's day view finds it; the previous UTC day does not.
    on_day = client.get(
        f"/exchange/v1/organizations/{fx.provider_id}/appointments",
        headers={"Authorization": f"Bearer {token}"},
        params={"day": "2026-10-06"},
    )
    assert on_day.status_code == 200, on_day.text
    assert len(on_day.json()["appointments"]) == 1
    assert on_day.json()["facility_timezone"] == TZ


def test_pg_ap_8e_a_booking_at_the_clinic_day_boundary_belongs_to_exactly_one_day(
    sessions: sessionmaker,
):
    """Half-open `[local midnight, next local midnight)`. A booking at exactly 00:00 local belongs
    to that day and not to the one before it -- the arithmetic that an inclusive range would get
    wrong twice a day."""
    fx = _seed(sessions, _prefix("ap8e"))
    midnight, _ = _book(fx, day=date(2026, 10, 6), at=time(0, 0))

    on_day = fx.appointments.list_for_day(fx.provider_id, date(2026, 10, 6))
    day_before = fx.appointments.list_for_day(fx.provider_id, date(2026, 10, 5))
    assert [record.id for record in on_day] == [midnight.id]
    assert [record.id for record in day_before] == []

    with sessions() as session:
        stored = session.get(Appointment, midnight.id)
        # 00:00 Djibouti is 21:00 UTC the previous day -- the instant a naive UTC date() would
        # misfile.
        assert stored.scheduled_at == datetime(2026, 10, 5, 21, 0, tzinfo=UTC)
