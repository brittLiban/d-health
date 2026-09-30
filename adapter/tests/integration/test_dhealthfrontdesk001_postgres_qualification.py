"""DHEALTH-FRONT-DESK-001/ADR-069: real PostgreSQL qualification of the arrival invariant.

The SQLite unit suite (``tests/test_dhealthfrontdesk001_arrival.py``) proves every *rule* this
workflow has. It cannot prove the properties that are load-bearing under concurrency, for the same
two reasons DHEALTH-CLINIC-VISIT-001's qualification records and this one inherits:

1. **SQLAlchemy's SQLite dialect omits ``FOR UPDATE`` entirely**, so ``VisitRepository._lock`` is
   silently a no-op there, and SQLite's own writer serialization then makes the tests pass for a
   reason unrelated to the lock being taken.
2. **The unit suite's SQLite engine does not enable ``PRAGMA foreign_keys``**
   (``tests/conftest.py``), so the composite patient/provider key is asserted *structurally* there
   and *behaviourally* only here.

What is qualified in this file, and nowhere else:

- **PG-FD-1 / PG-FD-2 — two staff members check the same patient in at the same instant.** The
  service's pre-read is a check-then-act race that both callers pass, so without
  ``uq_exchange_visits_one_active_per_patient`` both would insert and the waiting list would hold
  the same person twice. Exactly one must win; the other must receive
  ``ActiveVisitAlreadyExists``, never a 500 and never a silent success.
- **PG-FD-3 — check-in racing the clinician starting an existing visit.** The outcome must stay
  truthful either way: if the start wins, the check-in is refused because the patient is now
  ``IN_PROGRESS``; the patient is never in two active encounters.
- **PG-FD-4 — cancel racing take.** Both are legal from ``CHECKED_IN`` and lead to incompatible
  terminal outcomes, so the pair must produce one success and one conflict -- never a visit that is
  ``IN_PROGRESS`` and cancelled at once.
- **PG-FD-5 — the composite foreign key**: the database itself refuses a visit whose patient
  belongs to another provider organization, with no application check involved at all.
- **PG-FD-6 — the clinician invariant**: no ``IN_PROGRESS`` or ``COMPLETED`` row can exist without
  a clinician, enforced by ``ck_exchange_visits_taken_visit_has_clinician`` against a real backend.
- **PG-FD-7 — the migration**: historical visits survive ``0038``, the partial index permits a
  returning patient, and the upgrade refuses rather than repairing a pre-existing duplicate.
- **The partial index is genuinely partial**: completed and cancelled history does not block a
  return visit, which is the property a plain unique constraint would silently break.

**The proof is the barrier plus the final state, not an inspection of the lock.** If the index were
absent, the observable outcome would be two active visits for one patient -- which is what these
assertions catch. Nothing here reads ``pg_locks``.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHFD_PG_*`` env var below is set.

Usage::

    DHFD_PG_HOST=127.0.0.1 \\
    DHFD_PG_PORT=5432 \\
    DHFD_PG_USER=adapter \\
    DHFD_PG_PASSWORD=... \\
    DHFD_PG_DATABASE=dhfd_qual \\
        pytest adapter/tests/integration/test_dhealthfrontdesk001_postgres_qualification.py -v

The target database must already have migrations applied through ``0038_visit_check_in``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.

**Never point this at a real facility database.** Nothing here checks that; use a disposable
instance. All patient data here is synthetic. No real patient data is used.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import OrganizationRole, Patient, Visit, VisitStatus
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
from daher_adapter.exchange.visits import (
    ActiveVisitAlreadyExists,
    VisitRepository,
    VisitTransitionRefused,
)

PG_HOST = os.environ.get("DHFD_PG_HOST")
PG_PORT = os.environ.get("DHFD_PG_PORT")
PG_USER = os.environ.get("DHFD_PG_USER")
PG_PASSWORD = os.environ.get("DHFD_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHFD_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHFD_PG_* env vars) -- never in CI",
)

ISSUER = "https://pg-qualification.invalid/realms/dhealth-exchange"

#: The migration's own detection query, repeated here on purpose rather than imported: what is being
#: qualified is that the *statement* finds a real duplicate against a real backend, and importing it
#: would make the test agree with whatever the migration currently says.
_DUPLICATE_ACTIVE_VISITS = (
    "SELECT COUNT(*) FROM ("
    "  SELECT provider_organization_id, patient_id FROM exchange_visits"
    "  WHERE status IN ('CHECKED_IN', 'IN_PROGRESS')"
    "  GROUP BY provider_organization_id, patient_id HAVING COUNT(*) > 1"
    ") AS duplicated"
)


@pytest.fixture
def engine():
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    # A pool large enough for every concurrent thread below to hold its own real connection. A
    # thread waiting on the *pool* rather than on the database would serialize the race this file
    # exists to create, and every test would pass without exercising anything.
    created = create_engine(url, pool_pre_ping=True, pool_size=12, max_overflow=8)
    yield created
    created.dispose()


@pytest.fixture
def sessions(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def _repository(sessions: sessionmaker) -> VisitRepository:
    return VisitRepository(
        sessions, OrganizationRepository(sessions), ExchangeSecurityAuditRepository()
    )


def _principal(identity_id: uuid.UUID) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        identity_id=identity_id,
        issuer=ISSUER,
        subject=str(identity_id),
        platform_admin=False,
    )


def _seed(sessions: sessionmaker, *, key_prefix: str, staff: int = 2, patients: int = 1):
    """A provider organization, ``staff`` identities, and ``patients`` synthetic patients.

    Built through the same repositories ``exchange/api.py`` uses, so every uniqueness, role-matrix
    and tenant rule a real request is subject to applies here too. The patient rows are inserted
    directly because ``PatientRepository.create`` also creates a ``PatientReference``, which nothing
    here needs -- a visit is scoped to the ``Patient``.

    The staff hold ``CLINICIAN`` because this file drives ``VisitRepository`` **below** the HTTP
    gate: capability enforcement is proved over HTTP in the unit suite, and what is qualified here
    is
    the database, not the gate. Two identities exist so "two different staff members" is a real pair
    of actors rather than one actor twice.
    """
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    memberships = OrganizationMembershipRepository(sessions)
    roles = OrganizationRoleAssignmentRepository(sessions)

    organization = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")

    staff_ids = []
    for index in range(staff):
        identity, _ = identities.get_or_create(
            issuer=ISSUER,
            subject=f"{key_prefix}-staff-{index}",
            display_name=f"{key_prefix} staff {index}",
        )
        membership, _ = memberships.create(organization_id=organization.id, identity_id=identity.id)
        roles.assign(membership_id=membership.id, role=OrganizationRole.CLINICIAN)
        staff_ids.append(identity.id)

    patient_ids = []
    with sessions.begin() as session:
        for index in range(patients):
            patient = Patient(
                provider_organization_id=organization.id,
                display_name=f"{key_prefix} patient {index} (synthetic)",
            )
            session.add(patient)
            session.flush()
            patient_ids.append(patient.id)

    return organization, staff_ids, patient_ids


def _check_in(sessions, organization_id, patient_id, actor_id, *, prefix: str):
    return (
        _repository(sessions)
        .start(
            principal=_principal(actor_id),
            provider_organization_id=organization_id,
            patient_id=patient_id,
            # No reason: the arrival path's ordinary shape since ADR-069, and the one this
            # qualification should exercise rather than the clinician's.
            reason_for_visit=None,
            idempotency_key=f"{prefix}-{uuid.uuid4()}",
        )
        .visit
    )


def _active_count(sessions, organization_id, patient_id) -> int:
    with sessions() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(Visit)
                .where(
                    Visit.provider_organization_id == organization_id,
                    Visit.patient_id == patient_id,
                    Visit.status.in_((VisitStatus.CHECKED_IN.value, VisitStatus.IN_PROGRESS.value)),
                )
            )
            or 0
        )


def _visit_row(sessions, visit_id: uuid.UUID) -> Visit:
    with sessions() as session:
        visit = session.get(Visit, visit_id)
        assert visit is not None
        return visit


def _run_concurrently(operations):
    """Runs each callable on its own thread, released simultaneously by a barrier.

    Returns a list of ``(index, result_or_exception)``. A barrier maximizes overlap; it cannot
    *guarantee* the transactions interleave at the worst possible instant, which is why every
    assertion below is about the final state rather than about timing, and why the nondeterministic
    ones are reported as such rather than as proofs."""
    barrier = threading.Barrier(len(operations))
    outcomes: list[tuple[int, object]] = []
    lock = threading.Lock()

    def worker(index: int, operation):
        barrier.wait()
        try:
            result = operation()
        except Exception as error:  # noqa: BLE001 -- the exception *is* the observation here
            result = error
        with lock:
            outcomes.append((index, result))

    threads = [
        threading.Thread(target=worker, args=(index, operation))
        for index, operation in enumerate(operations)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads), "a worker thread did not finish"
    return sorted(outcomes)


# ── PG-FD-1 / PG-FD-2 ────────────────────────────────────────────────────────────────────────────


def test_pg_fd_1_a_double_submitted_check_in_creates_one_active_visit(sessions):
    """PG-FD-1: the same staff member's arrival submitted twice at the same instant.

    Two *different* idempotency keys, deliberately: the replay case is already covered by the
    idempotency constraint and would pass without the new index. This is the case the index exists
    for -- a genuine second insert attempt for a patient who is already waiting."""
    organization, staff, patients = _seed(sessions, key_prefix=f"fd1-{uuid.uuid4().hex[:8]}")
    actor, patient = staff[0], patients[0]

    outcomes = _run_concurrently(
        [
            lambda: _check_in(sessions, organization.id, patient, actor, prefix="fd1a"),
            lambda: _check_in(sessions, organization.id, patient, actor, prefix="fd1b"),
        ]
    )
    results = [result for _index, result in outcomes]
    created = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, Exception)]

    assert len(created) == 1, f"expected exactly one arrival to win, got {results}"
    assert len(refused) == 1
    # The loser is told what happened. A 500 here would mean the `IntegrityError` recovery path
    # failed to recognise which uniqueness rule refused it -- the exact defect that path was
    # rewritten for, and the one this test found.
    assert isinstance(refused[0], ActiveVisitAlreadyExists), (
        f"the losing caller must receive ActiveVisitAlreadyExists, not {refused[0]!r}"
    )
    assert _active_count(sessions, organization.id, patient) == 1


def test_pg_fd_2_two_different_staff_checking_in_one_patient_yields_one_visit(sessions):
    """PG-FD-2: two reception workers, two identities, one patient, one instant.

    The operationally real case: one worker does not see that a colleague has already done it. The
    waiting list must gain one entry, and the second worker must be told rather than shown a
    success."""
    organization, staff, patients = _seed(sessions, key_prefix=f"fd2-{uuid.uuid4().hex[:8]}")
    patient = patients[0]

    outcomes = _run_concurrently(
        [
            lambda: _check_in(sessions, organization.id, patient, staff[0], prefix="fd2a"),
            lambda: _check_in(sessions, organization.id, patient, staff[1], prefix="fd2b"),
        ]
    )
    results = [result for _index, result in outcomes]
    assert sum(1 for r in results if not isinstance(r, Exception)) == 1, results
    assert sum(1 for r in results if isinstance(r, ActiveVisitAlreadyExists)) == 1, results
    assert _active_count(sessions, organization.id, patient) == 1


def test_the_database_itself_refuses_a_second_active_visit(sessions):
    """The invariant with the service removed from the picture entirely.

    A direct insert, bypassing ``start`` and its pre-read, must still be refused --
    ``uq_exchange_visits_one_active_per_patient`` and not the application is what guarantees this.
    NC-FD-5 drops the index and reddens exactly this test deterministically, which the threaded
    races above cannot claim."""
    organization, staff, patients = _seed(sessions, key_prefix=f"fddb-{uuid.uuid4().hex[:8]}")
    patient = patients[0]
    _check_in(sessions, organization.id, patient, staff[0], prefix="fddb")

    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(
            Visit(
                idempotency_key=f"fddb-direct-{uuid.uuid4()}",
                provider_organization_id=organization.id,
                patient_id=patient,
                status=VisitStatus.CHECKED_IN.value,
                reason_for_visit=None,
            )
        )
        session.flush()

    assert _active_count(sessions, organization.id, patient) == 1


def test_the_index_is_partial_so_history_never_blocks_a_return_visit(sessions):
    """The property a plain unique constraint would silently break.

    A patient whose arrival was cancelled, and a patient who was seen and completed, must both be
    able to come back. This is why the index carries a ``WHERE`` clause, and it is the case that
    would fail *only* for returning patients -- in production, weeks after release."""
    organization, staff, patients = _seed(sessions, key_prefix=f"fdpart-{uuid.uuid4().hex[:8]}")
    repository = _repository(sessions)
    patient, actor = patients[0], staff[0]

    first = _check_in(sessions, organization.id, patient, actor, prefix="fdpart1")
    repository.cancel(
        principal=_principal(actor),
        provider_organization_id=organization.id,
        visit_id=first.id,
    )
    second = _check_in(sessions, organization.id, patient, actor, prefix="fdpart2")
    repository.take(
        principal=_principal(actor),
        provider_organization_id=organization.id,
        visit_id=second.id,
    )
    repository.complete(
        principal=_principal(actor),
        provider_organization_id=organization.id,
        visit_id=second.id,
    )
    third = _check_in(sessions, organization.id, patient, actor, prefix="fdpart3")

    assert len({first.id, second.id, third.id}) == 3, "three distinct durable visits"
    assert _active_count(sessions, organization.id, patient) == 1


# ── PG-FD-3 ──────────────────────────────────────────────────────────────────────────────────────


def test_pg_fd_3_check_in_racing_the_clinician_starting_the_existing_visit(sessions):
    """PG-FD-3: a second arrival submitted at the instant a clinician takes the first.

    Both orderings are truthful and both are acceptable; what is not acceptable is the patient
    ending up in two active encounters. ``take`` moves ``CHECKED_IN`` -> ``IN_PROGRESS``, and
    ``IN_PROGRESS`` is still active, so the index refuses the new arrival either way."""
    organization, staff, patients = _seed(sessions, key_prefix=f"fd3-{uuid.uuid4().hex[:8]}")
    repository = _repository(sessions)
    patient = patients[0]
    existing = _check_in(sessions, organization.id, patient, staff[0], prefix="fd3seed")

    outcomes = _run_concurrently(
        [
            lambda: repository.take(
                principal=_principal(staff[1]),
                provider_organization_id=organization.id,
                visit_id=existing.id,
            ),
            lambda: _check_in(sessions, organization.id, patient, staff[0], prefix="fd3new"),
        ]
    )
    results = dict(outcomes)

    # The take must succeed: nothing competes with it for the row it locks.
    assert not isinstance(results[0], Exception), f"the clinician's start failed: {results[0]!r}"
    # The second arrival must be refused, whichever side committed first.
    assert isinstance(results[1], ActiveVisitAlreadyExists), (
        f"a second arrival for an active patient must be refused, got {results[1]!r}"
    )
    assert _active_count(sessions, organization.id, patient) == 1
    assert _visit_row(sessions, existing.id).status == VisitStatus.IN_PROGRESS.value


# ── PG-FD-4 ──────────────────────────────────────────────────────────────────────────────────────


def test_pg_fd_4_cancel_racing_take_serializes_to_one_truthful_outcome(sessions):
    """PG-FD-4/§20D: the desk cancels an arrival at the instant a clinician starts it.

    Both transitions are legal from ``CHECKED_IN`` and they are mutually exclusive, so the row lock
    must serialize them into one success and one ``VisitTransitionRefused``. The state that must
    never exist is ``IN_PROGRESS`` with a ``cancelled_at`` -- which the database's own
    ``ck_exchange_visits_cancelled_at_matches_status`` also forbids, making this two layers rather
    than one."""
    organization, staff, patients = _seed(sessions, key_prefix=f"fd4-{uuid.uuid4().hex[:8]}")
    repository = _repository(sessions)
    visit = _check_in(sessions, organization.id, patients[0], staff[0], prefix="fd4")

    outcomes = _run_concurrently(
        [
            lambda: repository.take(
                principal=_principal(staff[1]),
                provider_organization_id=organization.id,
                visit_id=visit.id,
            ),
            lambda: repository.cancel(
                principal=_principal(staff[0]),
                provider_organization_id=organization.id,
                visit_id=visit.id,
            ),
        ]
    )
    results = [result for _index, result in outcomes]
    succeeded = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, Exception)]

    assert len(succeeded) == 1, f"exactly one of take/cancel must win, got {results}"
    assert len(refused) == 1
    assert isinstance(refused[0], VisitTransitionRefused), (
        f"the loser must be told the transition is illegal now, not {refused[0]!r}"
    )

    row = _visit_row(sessions, visit.id)
    assert row.status in (VisitStatus.IN_PROGRESS.value, VisitStatus.CANCELLED.value)
    if row.status == VisitStatus.IN_PROGRESS.value:
        assert row.cancelled_at is None
        assert row.clinician_identity_id is not None, "a started visit must have its clinician"
    else:
        assert row.started_at is None
        assert row.clinician_identity_id is None, (
            "an arrival cancelled before contact must never name a clinician"
        )


# ── PG-FD-5 ──────────────────────────────────────────────────────────────────────────────────────


def test_pg_fd_5_the_database_refuses_a_cross_organization_visit(sessions):
    """PG-FD-5/§32: the **composite** foreign key, with no application check involved.

    ``fk_exchange_visits_patient_same_provider`` targets ``(id, provider_organization_id)``, so a
    visit naming another provider's patient cannot exist as a row. Two independent single-column
    keys would each be satisfiable while the *pair* is a fabrication -- which is exactly the
    guessed-UUID attack §32 describes. NC-FD-2 removes the service-layer check and this test keeps
    the boundary, which is the point of enforcing it here."""
    prefix = uuid.uuid4().hex[:8]
    mine, staff, _patients = _seed(sessions, key_prefix=f"fd5a-{prefix}")
    _theirs, _their_staff, their_patients = _seed(sessions, key_prefix=f"fd5b-{prefix}")

    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(
            Visit(
                idempotency_key=f"fd5-{uuid.uuid4()}",
                provider_organization_id=mine.id,
                patient_id=their_patients[0],
                status=VisitStatus.CHECKED_IN.value,
                reason_for_visit=None,
            )
        )
        session.flush()

    # And the service refuses it before the database ever sees it, with a 404-shaped error.
    with pytest.raises(Exception) as refusal:
        _check_in(sessions, mine.id, their_patients[0], staff[0], prefix="fd5svc")
    assert "patient" in str(refusal.value).lower()


# ── PG-FD-6 ──────────────────────────────────────────────────────────────────────────────────────


def test_pg_fd_6_a_started_or_completed_visit_cannot_exist_without_a_clinician(sessions):
    """PG-FD-6: ``ck_exchange_visits_taken_visit_has_clinician``, against a real backend.

    The other half of ADR-069's clinician semantics. ``CHECKED_IN`` with a NULL clinician is the
    truthful arrival state and must be *permitted*; ``IN_PROGRESS`` and ``COMPLETED`` with a NULL
    clinician must be impossible, so a front-desk arrival can never be promoted into a consultation
    that nobody performed.

    Both directions are asserted: a constraint that refused everything would pass a one-sided
    test."""
    organization, staff, patients = _seed(sessions, key_prefix=f"fd6-{uuid.uuid4().hex[:8]}")

    # Permitted: the arrival itself, with no clinician.
    waiting = _check_in(sessions, organization.id, patients[0], staff[0], prefix="fd6")
    assert _visit_row(sessions, waiting.id).clinician_identity_id is None

    # Refused: promoting it without naming one. `started_at`/`completed_at` are set so the *other*
    # two CHECKs are satisfied and this can only fail for the reason it claims.
    #
    # `completed_at` is spelled per status rather than with a `CASE` over a reused bind: PostgreSQL
    # deduces one type per parameter, and the same name in a varchar `SET` and a text comparison is
    # `AmbiguousParameter` -- which would fail this test for an unrelated reason.
    forbidden_updates = {
        VisitStatus.IN_PROGRESS.value: (
            "UPDATE exchange_visits SET status = :status, started_at = now(), "
            "completed_at = NULL, clinician_identity_id = NULL WHERE id = :id"
        ),
        VisitStatus.COMPLETED.value: (
            "UPDATE exchange_visits SET status = :status, started_at = now(), "
            "completed_at = now(), clinician_identity_id = NULL WHERE id = :id"
        ),
    }
    for forbidden, statement in forbidden_updates.items():
        with pytest.raises(IntegrityError), sessions.begin() as session:
            session.execute(text(statement), {"status": forbidden, "id": waiting.id})

    assert _visit_row(sessions, waiting.id).status == VisitStatus.CHECKED_IN.value


def test_taking_a_visit_records_the_acting_clinician_and_nothing_else_can_set_it(sessions):
    """§10/§11: the treating clinician is **server-derived**, on a visit created by somebody else.

    The arrival was registered by one identity and taken by another, and the clinician of record is
    the one who took it. The desk's identity appears nowhere on the row -- it is in the audit trail
    as the actor that created the visit, which is a different fact."""
    organization, staff, patients = _seed(sessions, key_prefix=f"fdclin-{uuid.uuid4().hex[:8]}")
    desk, clinician = staff[0], staff[1]

    visit = _check_in(sessions, organization.id, patients[0], desk, prefix="fdclin")
    assert _visit_row(sessions, visit.id).clinician_identity_id is None

    _repository(sessions).take(
        principal=_principal(clinician),
        provider_organization_id=organization.id,
        visit_id=visit.id,
    )
    row = _visit_row(sessions, visit.id)
    assert row.clinician_identity_id == clinician
    assert row.clinician_identity_id != desk, "the desk must never become the treating clinician"
    assert row.started_at is not None


# ── PG-FD-7 ──────────────────────────────────────────────────────────────────────────────────────


def test_pg_fd_7_the_migration_left_historical_visits_intact(sessions):
    """PG-FD-7/§53: the schema this file runs against is post-0038, and the old shape still holds.

    Asserted against the live catalogue rather than against the migration source, which is the
    difference between "the file says so" and "the database agrees":

    * ``reason_for_visit`` is nullable **and** still ``VARCHAR(500)`` -- the column was relaxed, not
      retyped or recreated;
    * every constraint 0034 created is still present, so ``batch_alter_table`` did not silently drop
      one while recreating the table (the failure mode that makes a SQLite-shaped migration
      dangerous on a real backend);
    * the new index is **partial**, so a returning patient is never blocked;
    * a row in the pre-0038 shape -- carrying a reason, the only shape that existed then -- writes
      and reads back unchanged.
    """
    organization, staff, patients = _seed(sessions, key_prefix=f"fd7-{uuid.uuid4().hex[:8]}")

    with sessions() as session:
        nullable, length = session.execute(
            text(
                "SELECT is_nullable, character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'exchange_visits' AND column_name = 'reason_for_visit'"
            )
        ).one()
    assert nullable == "YES", "0038 must have dropped NOT NULL"
    assert length == 500, "the column was relaxed, not retyped"

    with sessions() as session:
        constraints = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = 'exchange_visits'::regclass"
                )
            )
        }
        indexes = {
            row[0]
            for row in session.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'exchange_visits'")
            )
        }

    for required in (
        "ck_exchange_visits_status",
        "ck_exchange_visits_started_at_matches_status",
        "ck_exchange_visits_completed_at_matches_status",
        "ck_exchange_visits_cancelled_at_matches_status",
        "ck_exchange_visits_taken_visit_has_clinician",
        "fk_exchange_visits_patient_same_provider",
        "uq_exchange_visits_provider_idempotency_key",
    ):
        assert required in constraints, f"0038 lost {required}, which 0034 created"

    assert "uq_exchange_visits_one_active_per_patient" in indexes

    # The partial predicate is what makes history unlimited. Asserted against the catalogue so a
    # migration that created a *total* unique index would be caught here rather than in production.
    with sessions() as session:
        definition = session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_exchange_visits_one_active_per_patient'"
            )
        )
    assert definition is not None
    assert "WHERE" in definition.upper(), "the index must be partial, not total"
    assert "CHECKED_IN" in definition and "IN_PROGRESS" in definition

    # A visit in the pre-0038 shape -- with a stated reason -- still writes and reads back intact.
    legacy = (
        _repository(sessions)
        .start(
            principal=_principal(staff[0]),
            provider_organization_id=organization.id,
            patient_id=patients[0],
            reason_for_visit="motif synthétique historique",
            idempotency_key=f"fd7-{uuid.uuid4()}",
        )
        .visit
    )
    assert _visit_row(sessions, legacy.id).reason_for_visit == "motif synthétique historique"


def test_the_upgrade_would_refuse_a_pre_existing_duplicate_rather_than_repair_it(sessions, engine):
    """§52/§53: the migration's own refusal path, exercised against real data.

    0038's ``upgrade`` counts patient/organization pairs holding more than one active visit and
    raises rather than choosing a row to cancel -- which of two real arrivals is the real one is a
    question about what happened in a clinic, and a migration that answered it would be fabricating
    clinical history.

    The check cannot be run by applying the migration here (the index already exists, so the state
    it guards against is unreachable), so what is qualified is the **query**: it is run against a
    deliberately constructed pair and must count it. The pair is built with the index temporarily
    dropped inside a transaction that is rolled back, so the database is left exactly as it was.
    """
    organization, staff, patients = _seed(
        sessions, key_prefix=f"fd8-{uuid.uuid4().hex[:8]}", patients=1
    )
    patient = patients[0]
    _check_in(sessions, organization.id, patient, staff[0], prefix="fd8")

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            assert connection.execute(text(_DUPLICATE_ACTIVE_VISITS)).scalar_one() == 0, (
                "the database must be clean before the duplicate is constructed"
            )
            connection.execute(text("DROP INDEX uq_exchange_visits_one_active_per_patient"))
            connection.execute(
                text(
                    "INSERT INTO exchange_visits "
                    "(id, idempotency_key, provider_organization_id, patient_id, status, "
                    " created_at) "
                    "VALUES (gen_random_uuid(), :key, :org, :patient, 'CHECKED_IN', now())"
                ),
                {"key": f"fd8-dup-{uuid.uuid4()}", "org": organization.id, "patient": patient},
            )
            # The migration's own detection query now finds exactly the pair it must refuse.
            assert connection.execute(text(_DUPLICATE_ACTIVE_VISITS)).scalar_one() == 1
        finally:
            # Rolled back unconditionally: the index returns and the fabricated duplicate never
            # persists.
            transaction.rollback()

    with sessions() as session:
        assert session.execute(text(_DUPLICATE_ACTIVE_VISITS)).scalar_one() == 0
        surviving = session.scalar(
            text(
                "SELECT COUNT(*) FROM pg_indexes "
                "WHERE indexname = 'uq_exchange_visits_one_active_per_patient'"
            )
        )
        assert surviving == 1
    assert _active_count(sessions, organization.id, patient) == 1
