"""DHEALTH-CLINIC-VISIT-001: real PostgreSQL qualification of the clinic visit lifecycle.

The SQLite unit suite (``tests/test_dhealthvisit001_clinic_visits.py``) proves every *rule* this
lifecycle has. It cannot prove the properties that are load-bearing under concurrency, and it
cannot prove them for two specific, stated reasons:

1. **SQLAlchemy's SQLite dialect omits ``FOR UPDATE`` entirely**, so ``VisitRepository._lock`` is
   silently a no-op there. SQLite's own writer serialization then makes the tests pass for a reason
   unrelated to the lock being taken -- exactly the "green for the wrong reason" shape this
   repository has been burned by before.
2. **This suite's SQLite engine does not enable ``PRAGMA foreign_keys``** (``tests/conftest.py``),
   so no foreign key is enforced there at all. The composite patient/provider key and the
   ``RESTRICT`` delete guards are therefore asserted *structurally* in the unit suite and
   *behaviourally* only here.

What is qualified in this file, and nowhere else:

- **Two clinicians taking the same waiting patient at the same instant.** Without the row lock both
  read ``CHECKED_IN``, both write ``IN_PROGRESS``, and the clinician of record becomes whichever
  transaction committed last -- silently. Exactly one must win; the other must be told.
- **Two clinicians completing the same visit at the same instant.** Without the lock both write
  ``completed_at`` and both write a ``visit_completed`` audit row, so the audit trail says the visit
  was finished twice.
- **Two note revisions appended at the same instant.** ``revision_number`` is computed by reading
  the current maximum, which is the classic read-modify-write race. Either the lock serializes them
  into revisions 1 and 2, or ``uq_exchange_visit_notes_visit_revision`` refuses one -- and *never*
  two rows claiming to be the same revision, because that is the state in which "which text is
  current" has no answer.
- **Cancel racing take.** Both are legal from ``CHECKED_IN`` and they lead to incompatible terminal
  outcomes, so the pair must produce one success and one conflict, never a visit that is both.
- **The composite foreign key**: the database itself refuses a visit whose patient belongs to
  another provider organization (DH-IDENTITY-001 §8's "the database itself must reject a
  cross-provider link").
- **One idempotency key under a real double-submit race**: both callers succeed, one creating and
  one returning the existing visit, and the waiting list gains exactly one patient.
- **The timestamp CHECK constraints**, against a real backend rather than SQLite's looser one.
- **``RESTRICT``**: a visit that an order references cannot be deleted, which is the enforcement
  behind "a completed visit is never deleted".
- **Transactional audit coupling**: an audit row and the mutation it records commit or roll back
  together, which only a real transaction can demonstrate.

**The proof is the barrier plus the final state, not an inspection of the lock.** If the lock were
not held, the observable outcome would be two successes and a contradictory row -- which is what
these assertions catch. Nothing here reads ``pg_locks``.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHVISIT_PG_*`` env var below is set. Never point
this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHVISIT_PG_HOST=127.0.0.1 \\
    DHVISIT_PG_PORT=5432 \\
    DHVISIT_PG_USER=adapter \\
    DHVISIT_PG_PASSWORD=... \\
    DHVISIT_PG_DATABASE=dhvisit_qual \\
        pytest adapter/tests/integration/test_dhealthvisit001_postgres_qualification.py -v

The target database must already have migrations applied through ``0034_clinic_visits``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.

All patient data here is synthetic. No real patient data is used.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.authorization import AuthenticatedPrincipal
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    ExchangeSecurityAuditEvent,
    OrganizationRole,
    Patient,
    Visit,
    VisitNote,
    VisitStatus,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.security_audit import ExchangeSecurityAuditRepository
from daher_adapter.exchange.visits import (
    VisitAlreadyTaken,
    VisitRepository,
    VisitTransitionRefused,
)

PG_HOST = os.environ.get("DHVISIT_PG_HOST")
PG_PORT = os.environ.get("DHVISIT_PG_PORT")
PG_USER = os.environ.get("DHVISIT_PG_USER")
PG_PASSWORD = os.environ.get("DHVISIT_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHVISIT_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHVISIT_PG_* env vars) -- never in CI",
)

ISSUER = "https://pg-qualification.invalid/realms/dhealth-exchange"


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    # A pool large enough for every concurrent thread below to hold its own real connection. A
    # thread waiting on the *pool* rather than on the database's row lock would serialize the race
    # this file exists to create, and the test would pass without ever exercising the lock.
    engine = create_engine(url, pool_pre_ping=True, pool_size=12, max_overflow=8)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


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


def _seed(sessions: sessionmaker, *, key_prefix: str, clinicians: int = 2):
    """A provider organization, ``clinicians`` CLINICIAN identities, and one synthetic patient.

    Built through the same repositories ``exchange/api.py`` uses, so every uniqueness, role-matrix
    and tenant rule a real request is subject to applies here too. The patient is inserted directly
    because ``PatientRepository.create`` also creates a ``PatientReference``, which nothing in this
    file needs -- a visit is scoped to the ``Patient``.
    """
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    memberships = OrganizationMembershipRepository(sessions)
    roles = OrganizationRoleAssignmentRepository(sessions)

    organization = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")

    clinician_ids = []
    for index in range(clinicians):
        identity, _ = identities.get_or_create(
            issuer=ISSUER,
            subject=f"{key_prefix}-clinician-{index}",
            display_name=f"{key_prefix} clinician {index}",
        )
        membership, _ = memberships.create(organization_id=organization.id, identity_id=identity.id)
        roles.assign(membership_id=membership.id, role=OrganizationRole.CLINICIAN)
        clinician_ids.append(identity.id)

    with sessions.begin() as session:
        patient = Patient(
            provider_organization_id=organization.id,
            display_name=f"{key_prefix} patient (synthetic)",
        )
        session.add(patient)
        session.flush()
        patient_id = patient.id

    return organization, clinician_ids, patient_id


def _start(sessions: sessionmaker, organization_id, patient_id, clinician_id, *, prefix: str):
    return (
        _repository(sessions)
        .start(
            principal=_principal(clinician_id),
            provider_organization_id=organization_id,
            patient_id=patient_id,
            reason_for_visit=f"{prefix} motif synthétique",
            idempotency_key=f"{prefix}-{uuid.uuid4()}",
        )
        .visit
    )


def _visit_row(sessions: sessionmaker, visit_id: uuid.UUID) -> Visit:
    with sessions() as session:
        visit = session.get(Visit, visit_id)
        assert visit is not None
        return visit


def _audit_of(sessions: sessionmaker, visit_id: uuid.UUID, event_type: str) -> int:
    with sessions() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(ExchangeSecurityAuditEvent)
                .where(
                    ExchangeSecurityAuditEvent.resource_id == visit_id,
                    ExchangeSecurityAuditEvent.event_type == event_type,
                )
            )
            or 0
        )


def _run_concurrently(operations):
    """Runs each callable on its own thread, released simultaneously by a barrier.

    Returns a list of ``(index, result_or_exception)``. A barrier maximizes overlap; it cannot
    *guarantee* the transactions interleave at the worst possible instant, which is why every
    assertion below is about the final state rather than about timing.
    """
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
    for thread in threads:
        assert not thread.is_alive(), "a worker deadlocked -- the lock is held too long"
    return sorted(outcomes)


def _split(outcomes):
    """Separates successes from refusals, which is the only classification these tests need."""
    successes = [value for _index, value in outcomes if not isinstance(value, Exception)]
    failures = [value for _index, value in outcomes if isinstance(value, Exception)]
    return successes, failures


# == VISIT-PG-1: two clinicians take the same waiting patient =====================================


def test_visitpg1_only_one_clinician_can_take_a_waiting_visit(sessions):
    """The clinician of record must be decided, not raced.

    Without the row lock both transactions read ``CHECKED_IN``, both write ``IN_PROGRESS``, and the
    visit ends up attributed to whichever committed last -- with two ``visit_started`` audit rows
    and no record that anything was contested.
    """
    organization, clinicians, patient_id = _seed(sessions, key_prefix="visitpg1")
    visit = _start(sessions, organization.id, patient_id, clinicians[0], prefix="visitpg1")
    repository = _repository(sessions)

    outcomes = _run_concurrently(
        [
            (
                lambda clinician=clinician: repository.take(
                    principal=_principal(clinician),
                    provider_organization_id=organization.id,
                    visit_id=visit.id,
                )
            )
            for clinician in clinicians
        ]
    )
    successes, failures = _split(outcomes)

    assert len(successes) == 1, f"expected exactly one clinician to take the visit: {outcomes}"
    assert len(failures) == 1
    assert isinstance(failures[0], VisitAlreadyTaken), failures[0]

    row = _visit_row(sessions, visit.id)
    assert row.status == VisitStatus.IN_PROGRESS.value
    assert row.clinician_identity_id == successes[0].visit.clinician_identity_id
    assert row.clinician_identity_id in clinicians
    assert row.started_at is not None
    # One transition, one audit row. Two would say the visit was started twice.
    assert _audit_of(sessions, visit.id, "visit_started") == 1


# == VISIT-PG-2: two clinicians complete the same visit ===========================================


def test_visitpg2_a_visit_is_completed_once_even_under_a_race(sessions):
    organization, clinicians, patient_id = _seed(sessions, key_prefix="visitpg2")
    visit = _start(sessions, organization.id, patient_id, clinicians[0], prefix="visitpg2")
    repository = _repository(sessions)
    repository.take(
        principal=_principal(clinicians[0]),
        provider_organization_id=organization.id,
        visit_id=visit.id,
    )

    outcomes = _run_concurrently(
        [
            (
                lambda clinician=clinician: repository.complete(
                    principal=_principal(clinician),
                    provider_organization_id=organization.id,
                    visit_id=visit.id,
                )
            )
            for clinician in clinicians
        ]
    )
    successes, failures = _split(outcomes)

    assert len(successes) == 1, f"expected exactly one completion: {outcomes}"
    assert isinstance(failures[0], VisitTransitionRefused), failures[0]

    row = _visit_row(sessions, visit.id)
    assert row.status == VisitStatus.COMPLETED.value
    assert row.completed_at is not None
    # The audit trail must not claim the consultation was finished twice.
    assert _audit_of(sessions, visit.id, "visit_completed") == 1


# == VISIT-PG-3: concurrent note revisions ========================================================


def test_visitpg3_concurrent_note_appends_never_share_a_revision_number(sessions):
    """Either the lock serializes them into 1 and 2, or the unique constraint refuses one. What must
    never happen is two rows claiming to be the same revision, because then "which text is current"
    has no answer -- and a clinician reading a corrected note cannot tell which correction stuck."""
    organization, clinicians, patient_id = _seed(sessions, key_prefix="visitpg3", clinicians=3)
    visit = _start(sessions, organization.id, patient_id, clinicians[0], prefix="visitpg3")
    repository = _repository(sessions)
    repository.take(
        principal=_principal(clinicians[0]),
        provider_organization_id=organization.id,
        visit_id=visit.id,
    )

    outcomes = _run_concurrently(
        [
            (
                lambda index=index, clinician=clinician: repository.append_note(
                    principal=_principal(clinician),
                    provider_organization_id=organization.id,
                    visit_id=visit.id,
                    body=f"Révision concurrente {index} (synthétique).",
                )
            )
            for index, clinician in enumerate(clinicians)
        ]
    )
    successes, failures = _split(outcomes)

    for failure in failures:
        assert isinstance(failure, IntegrityError), failure

    with sessions() as session:
        revisions = list(
            session.scalars(
                select(VisitNote.revision_number)
                .where(VisitNote.visit_id == visit.id)
                .order_by(VisitNote.revision_number)
            ).all()
        )

    assert len(revisions) == len(successes), (revisions, len(successes))
    assert len(revisions) == len(set(revisions)), f"duplicate revision numbers: {revisions}"
    # Whatever survived must be a gap-free sequence starting at 1: a hole would mean a revision was
    # counted and lost, which is indistinguishable from a deleted clinical note when read later.
    assert revisions == list(range(1, len(revisions) + 1)), revisions
    assert _audit_of(sessions, visit.id, "visit_note_appended") == len(successes)


# == VISIT-PG-4: cancel racing take ===============================================================


def test_visitpg4_cancel_racing_take_produces_one_coherent_outcome(sessions):
    """Both are legal from ``CHECKED_IN`` and they lead to incompatible places. The pair must
    to exactly one, and the losing side must be told rather than silently discarded."""
    organization, clinicians, patient_id = _seed(sessions, key_prefix="visitpg4")
    visit = _start(sessions, organization.id, patient_id, clinicians[0], prefix="visitpg4")
    repository = _repository(sessions)

    outcomes = _run_concurrently(
        [
            lambda: repository.take(
                principal=_principal(clinicians[0]),
                provider_organization_id=organization.id,
                visit_id=visit.id,
            ),
            lambda: repository.cancel(
                principal=_principal(clinicians[1]),
                provider_organization_id=organization.id,
                visit_id=visit.id,
            ),
        ]
    )
    successes, failures = _split(outcomes)

    assert len(successes) == 1, f"a visit cannot be both taken and cancelled: {outcomes}"
    assert len(failures) == 1
    assert isinstance(failures[0], VisitTransitionRefused | VisitAlreadyTaken), failures[0]

    row = _visit_row(sessions, visit.id)
    assert row.status in (VisitStatus.IN_PROGRESS.value, VisitStatus.CANCELLED.value)
    if row.status == VisitStatus.CANCELLED.value:
        # A cancelled visit never started, and must not claim a clinician.
        assert row.started_at is None
        assert row.clinician_identity_id is None
        assert row.cancelled_at is not None
    else:
        assert row.cancelled_at is None
        assert row.started_at is not None
        assert row.clinician_identity_id is not None


# == VISIT-PG-5: the composite foreign key ========================================================


def test_visitpg5_the_database_refuses_a_visit_naming_another_providers_patient(sessions):
    """DH-IDENTITY-001 §8, enforced by PostgreSQL rather than by the service layer. The unit suite
    can only assert that this constraint is *declared* -- SQLite enforces no foreign key there."""
    one, _clinicians, patient_of_one = _seed(sessions, key_prefix="visitpg5a", clinicians=1)
    two, _other_clinicians, _patient_of_two = _seed(sessions, key_prefix="visitpg5b", clinicians=1)

    with pytest.raises(IntegrityError) as caught:
        with sessions.begin() as session:
            session.add(
                Visit(
                    idempotency_key=f"visitpg5-{uuid.uuid4()}",
                    provider_organization_id=two.id,
                    patient_id=patient_of_one,
                    status=VisitStatus.CHECKED_IN.value,
                    reason_for_visit="Devrait être refusé par la base",
                )
            )
    assert "fk_exchange_visits_patient_same_provider" in str(caught.value)
    assert one.id != two.id


# == VISIT-PG-6: the timestamp CHECK constraints ==================================================


@pytest.mark.parametrize(
    ("status_value", "fields", "constraint"),
    [
        (
            VisitStatus.COMPLETED.value,
            {},
            "ck_exchange_visits_completed_at_matches_status",
        ),
        (
            VisitStatus.CANCELLED.value,
            {},
            "ck_exchange_visits_cancelled_at_matches_status",
        ),
        (
            VisitStatus.CHECKED_IN.value,
            {"started_at": "stamp"},
            "ck_exchange_visits_started_at_matches_status",
        ),
    ],
)
def test_visitpg6_state_history_cannot_be_forged(sessions, status_value, fields, constraint):
    from daher_adapter.models import utc_now

    organization, clinicians, patient_id = _seed(sessions, key_prefix="visitpg6", clinicians=1)
    values = {name: utc_now() for name in fields}
    if status_value == VisitStatus.COMPLETED.value:
        values["started_at"] = utc_now()
        values["clinician_identity_id"] = clinicians[0]

    with pytest.raises(IntegrityError) as caught:
        with sessions.begin() as session:
            session.add(
                Visit(
                    idempotency_key=f"visitpg6-{uuid.uuid4()}",
                    provider_organization_id=organization.id,
                    patient_id=patient_id,
                    status=status_value,
                    reason_for_visit="État incohérent",
                    **values,
                )
            )
    assert constraint in str(caught.value)


# == VISIT-PG-7: RESTRICT ==========================================================================


def test_visitpg7_a_visit_referenced_by_a_note_cannot_be_deleted(sessions):
    """The enforcement behind "a completed visit is never deleted". A note is enough on its own --
    an order is not required for the guarantee to hold, and using the note keeps this test
    independent of the catalog."""
    organization, clinicians, patient_id = _seed(sessions, key_prefix="visitpg7", clinicians=1)
    visit = _start(sessions, organization.id, patient_id, clinicians[0], prefix="visitpg7")
    repository = _repository(sessions)
    repository.take(
        principal=_principal(clinicians[0]),
        provider_organization_id=organization.id,
        visit_id=visit.id,
    )
    repository.append_note(
        principal=_principal(clinicians[0]),
        provider_organization_id=organization.id,
        visit_id=visit.id,
        body="Une note qui empêche la suppression.",
    )

    with pytest.raises((IntegrityError, DBAPIError)) as caught:
        with sessions.begin() as session:
            session.execute(
                text("DELETE FROM exchange_visits WHERE id = :visit_id"), {"visit_id": visit.id}
            )
    assert "exchange_visit_notes" in str(caught.value)


# == VISIT-PG-8: transactional audit coupling =====================================================


def test_visitpg8_a_refused_transition_leaves_no_audit_row_behind(sessions):
    """The mutation and its audit row share one transaction. A refusal that had already flushed
    something must roll both back -- SQLite cannot stand in for this, because the coupling is only
    meaningful against a real transaction."""
    organization, clinicians, patient_id = _seed(sessions, key_prefix="visitpg8", clinicians=1)
    visit = _start(sessions, organization.id, patient_id, clinicians[0], prefix="visitpg8")
    repository = _repository(sessions)
    repository.take(
        principal=_principal(clinicians[0]),
        provider_organization_id=organization.id,
        visit_id=visit.id,
    )

    before = _audit_of(sessions, visit.id, "visit_cancelled")
    with pytest.raises(VisitTransitionRefused):
        repository.cancel(
            principal=_principal(clinicians[0]),
            provider_organization_id=organization.id,
            visit_id=visit.id,
        )

    assert _audit_of(sessions, visit.id, "visit_cancelled") == before == 0
    assert _visit_row(sessions, visit.id).status == VisitStatus.IN_PROGRESS.value


# == VISIT-PG-9: idempotency under concurrency ====================================================


def test_visitpg9_one_key_under_a_real_race_creates_exactly_one_visit(sessions):
    """A double-clicked check-in on a slow connection is two simultaneous requests with one key. The
    waiting list must gain one patient, not two -- and the unique constraint is what guarantees it
    when both transactions pass the pre-read."""
    organization, clinicians, patient_id = _seed(sessions, key_prefix="visitpg9", clinicians=2)
    repository = _repository(sessions)
    shared_key = f"visitpg9-{uuid.uuid4()}"

    outcomes = _run_concurrently(
        [
            (
                lambda clinician=clinician: repository.start(
                    principal=_principal(clinician),
                    provider_organization_id=organization.id,
                    patient_id=patient_id,
                    reason_for_visit="Double clic synthétique",
                    idempotency_key=shared_key,
                )
            )
            for clinician in clinicians
        ]
    )
    successes, failures = _split(outcomes)

    # **Both must succeed**, one creating and one returning the existing row. Not "one may fail": a
    # double-clicked check-in is the ordinary case, and `start()` catches the uniqueness race in a
    # SAVEPOINT and re-reads rather than letting `IntegrityError` escape as a 500. This assertion
    # was deliberately tightened from tolerating a failure once that branch existed -- a test that
    # accepts a 500 for the most common client behaviour is not qualifying anything.
    assert failures == [], f"a double-submitted check-in must not fail: {failures}"
    assert len(successes) == 2, outcomes
    assert len({result.visit.id for result in successes}) == 1, successes
    assert sorted(result.created for result in successes) == [False, True], (
        f"exactly one of the two must report created=True: {outcomes}"
    )

    with sessions() as session:
        count = int(
            session.scalar(
                select(func.count())
                .select_from(Visit)
                .where(
                    Visit.provider_organization_id == organization.id,
                    Visit.idempotency_key == shared_key,
                )
            )
            or 0
        )
    assert count == 1
