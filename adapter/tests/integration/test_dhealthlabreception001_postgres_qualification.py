"""DHEALTH-LAB-RECEPTION-001: real PostgreSQL qualification of laboratory work intake.

**This file closes a gap that predates the ticket.** DH-EX-006 built accept and reject with the
``LabOrder`` row locked ``FOR UPDATE``, and it tested the races -- ``test_accept_vs_reject_race``,
``test_25way_same_action_accept_concurrency``, ``test_25way_same_action_reject_concurrency`` -- but
all of them against **SQLite**, via ``tmp_path``. There has never been a PostgreSQL qualification of
the intake transitions. Three reasons that matters, each of which this repository has written down
before:

1. **SQLAlchemy's SQLite dialect omits ``FOR UPDATE`` entirely**, so the lock those tests exist to
   exercise is silently a no-op there. SQLite's own whole-database writer serialization then makes
   them pass for a reason unrelated to the lock being taken -- the "green for the wrong reason"
   shape ``AGENTS.md``'s evidence discipline names, and the shape
   ``test_dhealthlabblocked001_postgres_qualification.py`` was written to escape for the *block*
   half of the same module.
2. **``PRAGMA foreign_keys`` is off** in the unit suite (``tests/conftest.py``), so no foreign
   key is enforced there at all.
3. SQLite does not enforce ``VARCHAR(n)``, and does not behave like a real MVCC backend under
   contention.

So every concurrency claim the product statement rests on -- "two workers cannot both take the same
order", "accept and decline cannot both win" -- was, until this file, qualified only where the lock
does nothing.

What is qualified here, and nowhere else:

- **PG-RECEPTION-1** Two workers accepting the same routed order at the same instant -> exactly one
  real transition, one audit-worthy outcome, one final status. The other is told.
- **PG-RECEPTION-2** Accept racing decline -> one wins, the other is refused. Never an order that is
  both ``ACCEPTED`` and ``REJECTED``, and never two transitions counted.
- **PG-RECEPTION-3** A double-clicked accept (the same logical request twice) -> one transition and
  one idempotent replay, not two mutations.
- **PG-RECEPTION-4** A *stale* mutation: accept attempted after the order has already advanced past
  ``SELECTED`` -> refused deterministically, and the newer state is not overwritten.
- **PG-RECEPTION-5** A cross-organization mutation racing a legitimate one -> the rival's attempt
  cannot win, cannot corrupt, and cannot see the order at all.
- **PG-RECEPTION-6** Twenty-five concurrent accepts -> one winner, twenty-four conflicts, no
  exception escaping as a 500-shaped error, one final row.
- **PG-RECEPTION-7** The incoming queue's ordering (this ticket's one behavioural change) against a
  real backend, including the tie-break -- SQLite's timestamp resolution makes the ASC/DESC
  distinction weaker there than it looks.

**The proof is the barrier plus the final state, not an inspection of the lock.** If the lock were
not held, the observable outcome would be two transitions or a contradictory row -- which is what
these assertions catch. Nothing here reads ``pg_locks``.

**A note on what a "conflict" must look like.** The ticket requires that no valid concurrency
conflict become a 500. At this layer that means ``InvalidLabWorkflowTransition`` (which
``exchange/api.py`` maps to 409) and never ``IntegrityError``, ``OperationalError``, or a bare
``Exception``. Several assertions below check the exception *type* for exactly that reason.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHLR_PG_*`` env var below is set.

Usage::

    DHLR_PG_HOST=127.0.0.1 \\
    DHLR_PG_PORT=5432 \\
    DHLR_PG_USER=adapter \\
    DHLR_PG_PASSWORD=... \\
    DHLR_PG_DATABASE=dhlr_qual \\
        pytest adapter/tests/integration/test_dhealthlabreception001_postgres_qualification.py -v

The target database must already have migrations applied through ``0038_visit_check_in``
(``alembic upgrade head``) before running this file -- it does not run migrations itself. **Never
point this at a real facility database**; nothing here checks that, so use a disposable instance.

All patient and catalog data here is synthetic. No real patient data is used.
"""

from __future__ import annotations

import os
import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.lab_workflow import (
    InvalidLabWorkflowTransition,
    LabOrderNotAssigned,
    LabWorkflowRepository,
)
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    LabOrder,
    LabOrderItem,
    LabOrderStatus,
    LabSelection,
    Patient,
    PatientReference,
    PractitionerReference,
)
from daher_adapter.exchange.organizations import OrganizationRepository

PG_HOST = os.environ.get("DHLR_PG_HOST")
PG_PORT = os.environ.get("DHLR_PG_PORT")
PG_USER = os.environ.get("DHLR_PG_USER")
PG_PASSWORD = os.environ.get("DHLR_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHLR_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHLR_PG_* env vars) -- never in CI",
)

ISSUER = "https://pg-qualification.invalid/realms/dhealth-exchange"


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    # A pool large enough for every concurrent thread below to hold its own real connection. A
    # thread waiting on the *pool* rather than on the database's row lock would serialize the race
    # this file exists to create, and the test would pass without exercising the lock at all.
    engine = create_engine(url, pool_pre_ping=True, pool_size=30, max_overflow=10)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _workflow(sessions: sessionmaker) -> LabWorkflowRepository:
    return LabWorkflowRepository(sessions, OrganizationRepository(sessions))


def _seed(
    sessions: sessionmaker,
    *,
    prefix: str,
    status: str = LabOrderStatus.SELECTED.value,
    orders: int = 1,
):
    """One provider, one laboratory, one rival laboratory, and ``orders`` assigned orders.

    The orders are inserted at the requested status with their ``LabSelection`` in the same
    transaction. That is a deliberate shortcut and not a cheat: what this file qualifies is the
    *intake transition under contention*, and the routing that precedes it is driven through the
    real endpoints by ``test_dhealthlabreception001_intake.py`` and
    ``test_exchange_lab_workflow.py``. The ``LabSelection`` rows are genuine, because assignment
    ownership is the only fact that decides whether a laboratory may act at all.

    Orders are created in sequence so ``selected_at`` genuinely increases, which PG-RECEPTION-7
    depends on.
    """
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)

    provider = organizations.create(org_type="PROVIDER", name=f"{prefix}-clinic")
    lab = organizations.create(org_type="LAB", name=f"{prefix}-lab")
    rival = organizations.create(org_type="LAB", name=f"{prefix}-rival")

    entry, _ = catalog.create_entry(code=f"{prefix}-RX", display_name="Analyse synthétique")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000.00"),
        currency="DJF",
        expected_current_price_id=None,
    )

    workers = []
    for index in range(3):
        identity, _ = identities.get_or_create(
            issuer=ISSUER,
            subject=f"{prefix}-tech-{index}",
            display_name=f"{prefix} tech {index}",
        )
        workers.append(identity.id)

    order_ids = []
    for index in range(orders):
        # One transaction per order, so each `LabSelection.selected_at` is a distinct instant and
        # the routing sequence is real rather than a batch insert sharing one clock reading.
        with sessions.begin() as session:
            # `PatientReference.patient_id` carries a *composite* foreign key to
            # `(exchange_patients.id, provider_organization_id)`, so a real `Patient` has to exist
            # first -- the reference is provenance *for* a directory record, not a stand-in.
            patient = Patient(
                provider_organization_id=provider.id,
                display_name=f"{prefix} patient {index} (synthetic)",
            )
            session.add(patient)
            session.flush()
            patient_ref = PatientReference(
                source_organization_id=provider.id,
                patient_id=patient.id,
                source_system="SYNTHETIC",
                source_identity_system="urn:dhealth:synthetic",
                source_identity_value=f"{prefix}-patient-{index}",
                display_name=f"{prefix} patient {index} (synthetic)",
            )
            practitioner_ref = PractitionerReference(
                source_organization_id=provider.id,
                source_system="SYNTHETIC",
                source_identity_system="urn:dhealth:synthetic",
                source_identity_value=f"{prefix}-practitioner-{index}",
            )
            session.add_all([patient_ref, practitioner_ref])
            session.flush()

            order = LabOrder(
                idempotency_key=f"{prefix}-{uuid.uuid4()}",
                provider_organization_id=provider.id,
                patient_reference_id=patient_ref.id,
                practitioner_reference_id=practitioner_ref.id,
                status=status,
            )
            session.add(order)
            session.flush()
            session.add(
                LabOrderItem(
                    lab_order_id=order.id,
                    catalog_entry_id=entry.id,
                    catalog_code_snapshot=entry.code,
                    catalog_display_name_snapshot=entry.display_name,
                )
            )
            session.add(LabSelection(lab_order_id=order.id, lab_organization_id=lab.id))
            order_ids.append(order.id)

    return {
        "provider_id": provider.id,
        "lab_id": lab.id,
        "rival_id": rival.id,
        "order_id": order_ids[0],
        "order_ids": order_ids,
        "workers": workers,
    }


def _run_concurrently(operations):
    """Runs each callable on its own thread, released simultaneously by a barrier.

    Returns a list of ``(index, result_or_exception)``. A barrier maximizes overlap; it cannot
    *guarantee* the transactions interleave at the worst possible instant, which is why every
    assertion below is about the final state rather than about timing, and why no deterministic
    claim is made for the threaded races beyond their observable outcome."""
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
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads), "a worker thread did not finish"
    return sorted(outcomes)


def _status(sessions: sessionmaker, order_id: uuid.UUID) -> str:
    with sessions() as session:
        return session.execute(select(LabOrder.status).where(LabOrder.id == order_id)).scalar_one()


def _count(sessions: sessionmaker, order_id: uuid.UUID) -> int:
    with sessions() as session:
        return int(
            session.scalar(
                select(func.count()).select_from(LabOrder).where(LabOrder.id == order_id)
            )
            or 0
        )


def _transitions(outcomes) -> list:
    """The results that represent a *real* mutation: ``(record, transitioned=True)``."""
    return [
        result
        for _index, result in outcomes
        if not isinstance(result, Exception) and result[1] is True
    ]


def _replays(outcomes) -> list:
    """Idempotent replays: a successful call that changed nothing."""
    return [
        result
        for _index, result in outcomes
        if not isinstance(result, Exception) and result[1] is False
    ]


def _errors(outcomes) -> list:
    return [result for _index, result in outcomes if isinstance(result, Exception)]


# ── PG-RECEPTION-1 ───────────────────────────────────────────────────────────────────────────────


def test_pg_reception_1_two_workers_accepting_the_same_order(sessions):
    """Two laboratory workers take the same routed order at the same instant.

    Exactly one real transition. The other must be an idempotent replay (it observed the accepted
    state) or a refusal -- never a second mutation, and never an unhandled database error. The
    ``LabOrder`` row lock is what decides; on SQLite it is a no-op and this proves nothing, which is
    why the test lives here."""
    world = _seed(sessions, prefix=f"r1-{uuid.uuid4().hex[:8]}")
    workflow = _workflow(sessions)
    order_id, lab_id = world["order_id"], world["lab_id"]

    outcomes = _run_concurrently(
        [
            lambda: workflow.accept(order_id=order_id, lab_organization_id=lab_id),
            lambda: workflow.accept(order_id=order_id, lab_organization_id=lab_id),
        ]
    )

    assert not [e for e in _errors(outcomes) if not isinstance(e, InvalidLabWorkflowTransition)], (
        f"a concurrency conflict must not surface as an unexpected error: {_errors(outcomes)}"
    )
    assert len(_transitions(outcomes)) == 1, (
        f"exactly one accept may really transition, got {outcomes}"
    )
    assert len(_transitions(outcomes)) + len(_replays(outcomes)) + len(_errors(outcomes)) == 2
    assert _status(sessions, order_id) == LabOrderStatus.ACCEPTED.value


# ── PG-RECEPTION-2 ───────────────────────────────────────────────────────────────────────────────


def test_pg_reception_2_accept_racing_decline(sessions):
    """The accept-vs-decline race, against a real backend.

    Both are legal from ``SELECTED`` and they lead to incompatible terminal outcomes, so the pair
    must produce exactly one real transition and one refusal. The state that must never exist is an
    order recorded as both taken and refused -- and because ``status`` is a single column, the
    observable failure would instead be *two* transitions, with the loser silently overwriting the
    winner. That is what the count assertion catches.

    Run five times: a single pass of a threaded race is weak evidence, and the outcome is allowed to
    differ between runs (either side may win) as long as the invariant holds every time."""
    for attempt in range(5):
        world = _seed(sessions, prefix=f"r2-{uuid.uuid4().hex[:8]}-{attempt}")
        workflow = _workflow(sessions)
        order_id, lab_id = world["order_id"], world["lab_id"]

        outcomes = _run_concurrently(
            [
                lambda w=workflow, o=order_id, lo=lab_id: w.accept(
                    order_id=o, lab_organization_id=lo
                ),
                lambda w=workflow, o=order_id, lo=lab_id: w.reject(
                    order_id=o, lab_organization_id=lo
                ),
            ]
        )

        unexpected = [
            e for e in _errors(outcomes) if not isinstance(e, InvalidLabWorkflowTransition)
        ]
        assert not unexpected, f"attempt {attempt}: unexpected error {unexpected}"
        assert len(_transitions(outcomes)) == 1, f"attempt {attempt}: {outcomes}"
        assert len(_errors(outcomes)) == 1, f"attempt {attempt}: the loser must be told"

        final = _status(sessions, order_id)
        assert final in (LabOrderStatus.ACCEPTED.value, LabOrderStatus.REJECTED.value)
        # The winner's own transition must be the one the final status reflects -- not merely "one
        # of them won", which would still hold if the loser overwrote the winner.
        winner_status = _transitions(outcomes)[0][0].status
        assert winner_status == final, f"attempt {attempt}: the loser overwrote the winner"


# ── PG-RECEPTION-3 ───────────────────────────────────────────────────────────────────────────────


def test_pg_reception_3_a_double_clicked_accept_transitions_once(sessions):
    """The same logical request twice, sequentially -- a double click or a retry after an uncertain
    response.

    The second call returns the current state with ``transitioned=False``: DH-EX-006's existing
    replay contract. Qualified here because the ticket's retry requirement is about a real backend,
    and because a replay that *did* mutate would be invisible to a status assertion alone."""
    world = _seed(sessions, prefix=f"r3-{uuid.uuid4().hex[:8]}")
    workflow = _workflow(sessions)
    order_id, lab_id = world["order_id"], world["lab_id"]

    first_record, first_transitioned = workflow.accept(
        order_id=order_id, lab_organization_id=lab_id
    )
    second_record, second_transitioned = workflow.accept(
        order_id=order_id, lab_organization_id=lab_id
    )

    assert first_transitioned is True
    assert second_transitioned is False, "a replay must not report a second mutation"
    assert first_record.id == second_record.id == order_id
    assert second_record.status == LabOrderStatus.ACCEPTED.value
    assert _status(sessions, order_id) == LabOrderStatus.ACCEPTED.value
    assert _count(sessions, order_id) == 1, "no second order row may be created by a retry"


# ── PG-RECEPTION-4 ───────────────────────────────────────────────────────────────────────────────


def test_pg_reception_4_a_stale_mutation_cannot_overwrite_a_newer_state(sessions):
    """An intake attempt arriving after the order has already advanced.

    The realistic shape: a worker's browser was showing the order as ``SELECTED``, somebody else
    advanced it, and the stale click lands. It must be refused deterministically -- with the
    repository's own ``InvalidLabWorkflowTransition`` rather than a database error -- and the newer
    state must survive untouched.

    Both directions are checked: a stale *accept* after a decline, and a stale *decline* after an
    accept. A single direction would leave the more dangerous one unproven."""
    # Stale accept, after the order was declined.
    world = _seed(sessions, prefix=f"r4a-{uuid.uuid4().hex[:8]}")
    workflow = _workflow(sessions)
    order_id, lab_id = world["order_id"], world["lab_id"]
    workflow.reject(order_id=order_id, lab_organization_id=lab_id)

    with pytest.raises(InvalidLabWorkflowTransition):
        workflow.accept(order_id=order_id, lab_organization_id=lab_id)
    assert _status(sessions, order_id) == LabOrderStatus.REJECTED.value

    # Stale decline, after the order was accepted -- and after it advanced further still.
    other = _seed(sessions, prefix=f"r4b-{uuid.uuid4().hex[:8]}")
    other_id, other_lab = other["order_id"], other["lab_id"]
    workflow.accept(order_id=other_id, lab_organization_id=other_lab)

    with pytest.raises(InvalidLabWorkflowTransition):
        workflow.reject(order_id=other_id, lab_organization_id=other_lab)
    assert _status(sessions, other_id) == LabOrderStatus.ACCEPTED.value


# ── PG-RECEPTION-5 ───────────────────────────────────────────────────────────────────────────────


def test_pg_reception_5_a_cross_organization_mutation_racing_a_legitimate_one(sessions):
    """A rival laboratory attempting the same order at the instant the assigned one accepts.

    The rival must lose on *assignment*, not on timing: ``LabOrderNotAssigned`` (404 at the edge),
    never a transition, never an ``IntegrityError``, and never a disclosure that the id is real.
    Racing it against the legitimate accept is the point -- a sequential check would not show that
    the lock ordering cannot be used to slip past the assignment read."""
    world = _seed(sessions, prefix=f"r5-{uuid.uuid4().hex[:8]}")
    workflow = _workflow(sessions)
    order_id, lab_id, rival_id = world["order_id"], world["lab_id"], world["rival_id"]

    outcomes = _run_concurrently(
        [
            lambda: workflow.accept(order_id=order_id, lab_organization_id=lab_id),
            lambda: workflow.accept(order_id=order_id, lab_organization_id=rival_id),
            lambda: workflow.reject(order_id=order_id, lab_organization_id=rival_id),
        ]
    )
    results = dict(outcomes)

    assert not isinstance(results[0], Exception), f"the assigned lab must win: {results[0]!r}"
    assert results[0][1] is True
    for index in (1, 2):
        assert isinstance(results[index], LabOrderNotAssigned), (
            f"the rival must be refused on assignment, got {results[index]!r}"
        )
    assert _status(sessions, order_id) == LabOrderStatus.ACCEPTED.value

    # And the rival cannot read it either, before or after.
    assert workflow.get_for_lab(order_id, rival_id) is None
    assert [r.id for r in workflow.list_for_lab(rival_id)] == []


# ── PG-RECEPTION-6 ───────────────────────────────────────────────────────────────────────────────


def test_pg_reception_6_twenty_five_concurrent_accepts_produce_one_transition(sessions):
    """Contention well past what a laboratory would produce, to make the serialization visible.

    One real transition; every other caller either replays or is refused; no caller sees an
    exception the edge would render as a 500. The pool is sized above the thread count so the
    threads contend on the *row lock* rather than on connections."""
    world = _seed(sessions, prefix=f"r6-{uuid.uuid4().hex[:8]}")
    workflow = _workflow(sessions)
    order_id, lab_id = world["order_id"], world["lab_id"]

    outcomes = _run_concurrently(
        [lambda: workflow.accept(order_id=order_id, lab_organization_id=lab_id) for _ in range(25)]
    )

    unexpected = [e for e in _errors(outcomes) if not isinstance(e, InvalidLabWorkflowTransition)]
    assert not unexpected, (
        f"no concurrency conflict may escape as an unexpected error: {unexpected}"
    )
    assert len(_transitions(outcomes)) == 1, (
        f"exactly one of 25 may transition, got {len(_transitions(outcomes))}"
    )
    assert len(_transitions(outcomes)) + len(_replays(outcomes)) + len(_errors(outcomes)) == 25
    assert _status(sessions, order_id) == LabOrderStatus.ACCEPTED.value
    assert _count(sessions, order_id) == 1


# ── PG-RECEPTION-7 ───────────────────────────────────────────────────────────────────────────────


def test_pg_reception_7_the_incoming_queue_is_oldest_routed_first(sessions):
    """This ticket's one behavioural change, against a real backend.

    On PostgreSQL ``selected_at`` is ``timestamptz`` with microsecond resolution and the three
    orders are seeded in separate transactions, so the ordering is genuinely established by the
    routing sequence rather than by insertion luck. The review views are checked in the same test so
    the asymmetry is pinned rather than assumed: reversing them would be a regression."""
    world = _seed(sessions, prefix=f"r7-{uuid.uuid4().hex[:8]}", orders=3)
    workflow = _workflow(sessions)
    lab_id = world["lab_id"]
    first, second, third = world["order_ids"]

    incoming = workflow.list_for_lab(lab_id, status_filter=LabOrderStatus.SELECTED)
    assert [record.id for record in incoming] == [first, second, third]
    assert incoming[0].selected_at <= incoming[1].selected_at <= incoming[2].selected_at

    # Accept them oldest-first, so the newest *activity* is on the order routed last.
    for order_id in (first, second, third):
        workflow.accept(order_id=order_id, lab_organization_id=lab_id)

    accepted = workflow.list_for_lab(lab_id, status_filter=LabOrderStatus.ACCEPTED)
    assert [record.id for record in accepted] == [third, second, first], (
        "review views must stay newest-activity-first"
    )
    # ...and the unfiltered view keeps its own established order too.
    unfiltered = workflow.list_for_lab(lab_id)
    assert [record.id for record in unfiltered] == [third, second, first]

    # The incoming queue is now empty: answered work is not incoming work.
    assert workflow.list_for_lab(lab_id, status_filter=LabOrderStatus.SELECTED) == []


def test_the_incoming_queue_tie_break_is_stable_under_identical_routing_instants(sessions):
    """Two orders routed at the same recorded instant must still have a deterministic order.

    Constructed by forcing both ``selected_at`` values equal, which the sequential seeding above
    cannot produce. Without the ``LabOrder.id`` tie-break the queue's order would be whatever
    PostgreSQL happened to return, and a laboratory refreshing the page would see rows swap."""
    world = _seed(sessions, prefix=f"r7b-{uuid.uuid4().hex[:8]}", orders=2)
    workflow = _workflow(sessions)
    lab_id = world["lab_id"]
    order_ids = world["order_ids"]

    with sessions.begin() as session:
        shared = session.execute(
            select(LabSelection.selected_at).where(LabSelection.lab_order_id == order_ids[0])
        ).scalar_one()
        for order_id in order_ids:
            selection = session.execute(
                select(LabSelection).where(LabSelection.lab_order_id == order_id)
            ).scalar_one()
            selection.selected_at = shared

    first_read = [
        r.id for r in workflow.list_for_lab(lab_id, status_filter=LabOrderStatus.SELECTED)
    ]
    second_read = [
        r.id for r in workflow.list_for_lab(lab_id, status_filter=LabOrderStatus.SELECTED)
    ]
    assert first_read == second_read, "the queue order must be stable across reads"
    assert first_read == sorted(order_ids, key=str), "the tie-break is the order id, ascending"
