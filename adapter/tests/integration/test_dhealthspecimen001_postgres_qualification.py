"""DHEALTH-SPECIMEN-001: real PostgreSQL qualification of physical specimen collection.

**Why this file has to exist, in one paragraph.** The unit suite
(``tests/test_dhealthspecimen001_specimens.py``, 38 tests) runs on SQLite, where three things this
ticket's claims depend on are silently absent: SQLAlchemy's SQLite dialect **omits ``FOR UPDATE``
entirely**, so the row lock that serializes two concurrent collections is a no-op and SQLite's
whole-database writer serialization makes the test pass for an unrelated reason;
``PRAGMA foreign_keys`` is **off** in ``tests/conftest.py``, so ``recorded_by_identity_id``'s
``NOT NULL`` foreign key to ``exchange_identities`` is never enforced; and SQLite does not enforce
``VARCHAR(n)`` or behave like an MVCC backend under contention. Every concurrency and constraint
claim in the handoff is qualified **here**, or nowhere.

What is qualified here and nowhere else:

- **PG-SPEC-1** The same logical collection submitted concurrently by N workers -> exactly **one**
  specimen row, one milestone row, one of them reporting creation and the rest reporting a replay.
  This is the test that would catch a missing row lock or a missing idempotency scope.
- **PG-SPEC-2** Two *genuinely different* collections submitted concurrently -> **two** specimen
  rows, deterministically, and the milestone still created exactly once. The domain is allowed to
  produce two here, and the point is that it produces exactly two rather than one or three.
- **PG-SPEC-3** Collection racing ``block`` -> never an impossible state. Either the specimen is
  recorded and then the order blocks, or the block wins and the collection is refused with a
  controlled ``InvalidLabWorkflowTransition``. Never a specimen recorded *against a blocked order*,
  and never an unhandled exception.
- **PG-SPEC-4** A **stale** collection replayed after the order has advanced -> the replay is
  still a replay (it does not become a new physical specimen), and a genuinely *new* key is
  refused once the order has left the recordable statuses. A stale operation cannot
  overwrite newer truth.
- **PG-SPEC-5** A rival laboratory's collection attempt racing a legitimate one -> refused, with no
  row written, while the legitimate one succeeds.
- **PG-SPEC-6** The real schema: ``lab_order_id`` is **not** unique on its own (two specimens
  for one order actually insert), ``(lab_order_id, idempotency_key)`` **is** unique (the
  duplicate actually raises at the database), and all three foreign keys are enforced and
  ``RESTRICT``.
- **PG-SPEC-7** The source-of-truth invariant against a backend that keeps microsecond precision and
  real ``timestamptz``: the milestone's instant and the earliest specimen's instant are the *same
  stored value*. On SQLite this assertion is weaker than it looks.
- **PG-SPEC-8** ``VARCHAR(128)``: a 128-character key stores, and the service layer refuses anything
  longer before PostgreSQL can raise ``StringDataRightTruncation``.

**The proof is the barrier plus the final row count, never an inspection of the lock.** Nothing here
reads ``pg_locks``. If the lock were not held, the observable outcome would be two specimen rows for
one logical collection, or two milestone rows, which is exactly what these assertions count.

**A valid race must never become a 500.** At this layer that means a refusal arrives as
``InvalidLabWorkflowTransition`` or ``LabOrderNotAssigned`` -- which ``exchange/api.py`` maps to 409
and 404 -- and never as ``IntegrityError``, ``OperationalError``, or a bare ``Exception``. Several
assertions below check the exception *type* for precisely that reason.

**Migration coverage is deliberately NOT in this file.** ``0038 -> 0039``, the one-head check, and
both downgrade behaviours (refused while rows exist, permitted while empty) are exercised by running
``alembic`` against this same disposable database, and the observed output is recorded in
``PROJECT_STATE.md``. A test that re-ran migrations inside the suite would have to tear down the
schema the rest of the file depends on.

**Manual / opt-in only**, mirroring every other live-infra test in this directory: skipped by
default, including in CI, unless every ``DHSPEC_PG_*`` variable is set.

Usage::

    DHSPEC_PG_HOST=127.0.0.1 \\
    DHSPEC_PG_PORT=55439 \\
    DHSPEC_PG_USER=adapter \\
    DHSPEC_PG_PASSWORD=... \\
    DHSPEC_PG_DATABASE=dhspec_qual \\
        pytest adapter/tests/integration/test_dhealthspecimen001_postgres_qualification.py -v

The target database must already be migrated to ``0039_lab_specimens`` (``alembic upgrade head``);
this file does not run migrations. **Never point it at a facility database** -- nothing here checks
that, so use a disposable instance and destroy it afterwards.

All patient, practitioner and catalogue data here is synthetic. No real patient data is used.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

import pytest
from exchange_support import activate_result_template, lab_staff, make_lab, make_provider
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_workflow import (
    InvalidLabWorkflowTransition,
    LabOrderNotAssigned,
    LabWorkflowRepository,
)
from daher_adapter.exchange.models import (
    LabOrder,
    LabOrderBlockReason,
    LabOrderStatus,
    LabSampleCollection,
    LabSelection,
    LabSpecimen,
)
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.provider_lab_routes import ProviderLabRouteRepository
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHSPEC_PG_HOST")
PG_PORT = os.environ.get("DHSPEC_PG_PORT")
PG_USER = os.environ.get("DHSPEC_PG_USER")
PG_PASSWORD = os.environ.get("DHSPEC_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHSPEC_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHSPEC_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed_accepted_order(sessions: sessionmaker, *, key_prefix: str):
    """A routed, accepted order plus the laboratory's own staff identity.

    Deliberately the same shape ``test_dhex011_postgres_qualification.py`` uses, so the two files'
    arrangements stay comparable. ``collector`` is a real ``exchange_identities`` row because
    ``LabSpecimen.recorded_by_identity_id`` is a ``NOT NULL`` foreign key -- and on *this* backend,
    unlike the unit suite, that constraint is actually enforced."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    workflow = LabWorkflowRepository(sessions, organizations)

    provider = make_provider(organizations, name=f"{key_prefix}-clinic")
    lab = make_lab(organizations, name=f"{key_prefix}-lab")
    entry, _ = catalog.create_entry(code=f"{key_prefix}-TEST", display_name="Qualification Test")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    # DH-LAB-003: a laboratory with no active result template cannot be assigned the work at all
    # (an order would arrive and no result could be entered), so the template is part of a
    # realistic arrangement rather than scenery. One synthetic NUMERIC observation, nothing
    # clinical invented.
    activate_result_template(catalog, offering_id=offering.id)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-1",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="pg-qualification",
        source_identity_system=f"urn:{key_prefix}:practitioner",
        source_identity_value="D-1",
    )
    order = orders.create(
        idempotency_key=key_prefix,
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order

    # DH-CLINIC-006: `assign_by_provider` refuses without an active route, regardless of how
    # eligible the laboratory otherwise is -- so the route is real configuration here, not a stub.
    ProviderLabRouteRepository(sessions, organizations).upsert(
        provider_organization_id=provider.id, lab_organization_id=lab.id, active=True
    )
    LabSelectionRepository(sessions, organizations).assign_by_provider(
        lab_order_id=order.id,
        lab_organization_id=lab.id,
        provider_organization_id=provider.id,
    )
    workflow.accept(order_id=order.id, lab_organization_id=lab.id)
    collector = lab_staff(organizations, lab.id)
    return provider, lab, order, workflow, orders, collector


def _count(sessions, model, order_id) -> int:
    with sessions() as session:
        return session.scalar(
            select(func.count()).select_from(model).where(model.lab_order_id == order_id)
        )


def _prefix(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:8]}"


# ── PG-SPEC-1: the same logical collection, concurrently ─────────────────────────────────────────


def test_pg_spec_1a_the_row_lock_is_what_serializes_two_identical_collections(
    sessions: sessionmaker,
):
    """**The deterministic proof that ``FOR UPDATE`` is load-bearing**, and the reason this test
    exists beside the N-thread one below.

    ``test_pg_spec_1_...`` releases twelve threads from a barrier and counts rows. That is a useful
    smoke test and it is **not** proof of the lock: control-tested by deleting
    ``.with_for_update()`` from ``_record_collection_in_session``, it stayed green. The GIL
    plus per-thread session setup staggers the threads enough that each one commits before the
    next one reads, so the race the test is named after does not reliably happen. Recorded
    here rather than quietly
    fixed, because a test that passes whether or not the code is right is exactly what
    `AGENTS.md`'s evidence discipline says to strengthen or delete.

    This test creates the contention deliberately instead of hoping for it. Two sessions the test
    owns, so the interleaving is ours:

    1. ``first`` records the collection and **does not commit** -- its transaction stays open,
       holding the ``LabOrder`` row lock.
    2. ``second`` attempts the same logical collection, same key, in another transaction.

    With the lock, step 2 **blocks** on ``SELECT ... FOR UPDATE`` until step 1 commits, then
    observes the committed specimen and returns it as a replay. Without the lock, step 2
    reads through the uncommitted write, finds nothing, inserts, and the commit collides with
    ``uq_exchange_lab_specimens_order_idempotency_key`` -- an ``IntegrityError``, which at the HTTP
    boundary is an unexplained 500 for what is a perfectly legitimate double click.

    So the assertion is: **``second`` returns a replay of ``first``'s specimen, and raises
    nothing.** A ``statement_timeout`` keeps a genuinely stuck lock from hanging the suite,
    and a timeout is itself a failure rather than a skip."""
    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("lockproof")
    )
    key = str(uuid.uuid4())
    started = threading.Event()
    release_first = threading.Event()
    outcomes: dict[str, object] = {}
    lock = threading.Lock()

    def hold_the_lock() -> None:
        """Record the collection and sit on the open transaction until told to commit."""
        try:
            # `sessions.begin()`, not `sessions()` plus `session.begin()`: a session from the
            # factory auto-begins on first use, so an explicit `begin()` raises
            # `InvalidRequestError`. This is the same context manager the repository layer
            # itself uses, and it commits when the block exits.
            with sessions.begin() as session:
                workflow.record_collection(
                    order_id=order.id,
                    lab_organization_id=lab.id,
                    recorded_by_identity_id=collector,
                    idempotency_key=key,
                    session=session,
                )
                started.set()
                # The commit happens when this block exits, which is after the waiter below
                # has had a real chance to contend for the same row.
                release_first.wait(timeout=20)
            with lock:
                outcomes["first"] = ("committed", None)
        except BaseException as error:  # noqa: BLE001
            started.set()
            with lock:
                outcomes["first"] = ("error", error)

    def contend() -> None:
        try:
            assert started.wait(timeout=20), "the first transaction never opened"
            with sessions.begin() as session:
                # Fail loudly rather than hang if the lock is never released. `SET LOCAL`
                # because this is inside a transaction and must not outlive it.
                session.execute(text("SET LOCAL statement_timeout = '15s'"))
                outcome = workflow.record_collection(
                    order_id=order.id,
                    lab_organization_id=lab.id,
                    recorded_by_identity_id=collector,
                    idempotency_key=key,
                    session=session,
                )
            with lock:
                outcomes["second"] = (
                    "ok",
                    outcome.specimen_created,
                    outcome.specimen.id,
                )
        except BaseException as error:  # noqa: BLE001
            with lock:
                outcomes["second"] = ("error", type(error).__name__, str(error)[:300])

    holder = threading.Thread(target=hold_the_lock)
    waiter = threading.Thread(target=contend)
    holder.start()
    waiter.start()
    # Give the waiter time to actually reach the lock and block on it before the holder commits.
    assert started.wait(timeout=20)
    time.sleep(2.0)
    release_first.set()
    holder.join(timeout=40)
    waiter.join(timeout=40)

    assert outcomes.get("first") == ("committed", None), outcomes.get("first")
    second = outcomes.get("second")
    assert second is not None, "the contending transaction never finished"
    assert second[0] == "ok", (
        f"a legitimate duplicate collection must replay, not raise -- without the FOR UPDATE row "
        f"lock this is an IntegrityError, i.e. a 500 for a double click: {second!r}"
    )
    assert second[1] is False, "the contending call must be a replay, not a second creation"

    assert _count(sessions, LabSpecimen, order.id) == 1
    assert _count(sessions, LabSampleCollection, order.id) == 1
    with sessions() as session:
        stored = session.scalars(
            select(LabSpecimen.id).where(LabSpecimen.lab_order_id == order.id)
        ).all()
    assert stored == [second[2]]


def test_pg_spec_1_concurrent_identical_collections_yield_one_specimen(sessions: sessionmaker):
    """Twelve threads submit **the same** logical collection -- one idempotency key -- at the same
    instant against real PostgreSQL.

    Exactly one specimen row, exactly one milestone row, exactly one thread reporting creation. This
    is the assertion a missing ``FOR UPDATE``, or an idempotency check that read before it locked,
    would redden: both failures produce two rows, and SQLite could never have shown it because its
    dialect emits no ``FOR UPDATE`` at all.

    No thread may fail: a retry of the request that already won is a *replay*, not a conflict, so
    every loser must return the winner's specimen rather than raise."""
    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("same")
    )
    key = str(uuid.uuid4())
    thread_count = 12
    barrier = threading.Barrier(thread_count)
    lock = threading.Lock()
    created_flags: list[bool] = []
    specimen_ids: set[uuid.UUID] = set()
    errors: list[BaseException] = []

    def attempt() -> None:
        try:
            barrier.wait(timeout=30)
            outcome = workflow.record_collection(
                order_id=order.id,
                lab_organization_id=lab.id,
                recorded_by_identity_id=collector,
                idempotency_key=key,
            )
            with lock:
                created_flags.append(outcome.specimen_created)
                specimen_ids.add(outcome.specimen.id)
        except BaseException as error:  # noqa: BLE001 -- the type is the assertion below
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == [], f"a replay must never raise: {errors!r}"
    assert len(created_flags) == thread_count
    assert created_flags.count(True) == 1, (
        f"exactly one thread may create the specimen, got {created_flags.count(True)}"
    )
    assert len(specimen_ids) == 1, "every thread must see the same specimen"
    assert _count(sessions, LabSpecimen, order.id) == 1
    assert _count(sessions, LabSampleCollection, order.id) == 1


# ── PG-SPEC-2: two genuinely different collections, concurrently ─────────────────────────────────


def test_pg_spec_2_concurrent_distinct_collections_yield_two_specimens(sessions: sessionmaker):
    """Two threads submit two **different** collections -- two distinct keys -- simultaneously.

    The domain is *allowed* to produce two specimens here, and the point of the test is that it
    produces exactly two: not one (which would mean a key collision or an over-broad idempotency
    check silently discarding a real physical sample) and not three.

    The milestone is still created exactly once, by whichever thread got there first, which is the
    half of the invariant that a race could most plausibly break: both threads find no milestone,
    both try to create one, and ``unique(lab_order_id)`` plus the row lock must leave one."""
    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("distinct")
    )
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    results: list[tuple[uuid.UUID, bool, bool]] = []
    errors: list[BaseException] = []

    def attempt(key: str) -> None:
        try:
            barrier.wait(timeout=30)
            outcome = workflow.record_collection(
                order_id=order.id,
                lab_organization_id=lab.id,
                recorded_by_identity_id=collector,
                idempotency_key=key,
            )
            with lock:
                results.append(
                    (outcome.specimen.id, outcome.specimen_created, outcome.milestone_created)
                )
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [
        threading.Thread(target=attempt, args=(str(uuid.uuid4()),)),
        threading.Thread(target=attempt, args=(str(uuid.uuid4()),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == [], f"two legitimate distinct collections must both succeed: {errors!r}"
    assert len(results) == 2
    assert len({row[0] for row in results}) == 2, "two distinct keys must yield two specimens"
    assert all(row[1] for row in results), "both are genuine creations"
    assert [row[2] for row in results].count(True) == 1, (
        "the milestone is created exactly once, by whichever collection got there first"
    )
    assert _count(sessions, LabSpecimen, order.id) == 2
    assert _count(sessions, LabSampleCollection, order.id) == 1


# ── PG-SPEC-3: collection racing the block transition ────────────────────────────────────────────


def test_pg_spec_3_collection_racing_block_never_leaves_an_impossible_state(sessions: sessionmaker):
    """A collection and a ``block`` submitted at the same instant on a freshly accepted order.

    Two outcomes are legitimate and both are checked for internal consistency:

    * the collection won the lock -> a specimen exists, and the order is ``BLOCKED`` afterwards;
    * the block won the lock -> the order is ``BLOCKED`` and the collection is refused with
      ``InvalidLabWorkflowTransition``, with **no** specimen row written.

    What must never happen is a specimen recorded for an order that was already ``BLOCKED`` when the
    write landed, or either operation failing with a database-level exception. Both halves of
    ``block`` -- the status and its episode -- commit together, so the status is a safe thing to
    assert against."""
    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("blockrace")
    )
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    outcomes: dict[str, object] = {}

    def collect() -> None:
        try:
            barrier.wait(timeout=30)
            outcome = workflow.record_collection(
                order_id=order.id,
                lab_organization_id=lab.id,
                recorded_by_identity_id=collector,
                idempotency_key=str(uuid.uuid4()),
            )
            with lock:
                outcomes["collect"] = ("created", outcome.specimen_created)
        except InvalidLabWorkflowTransition as error:
            with lock:
                outcomes["collect"] = ("refused", str(error))
        except BaseException as error:  # noqa: BLE001
            with lock:
                outcomes["collect"] = ("unexpected", error)

    def block() -> None:
        try:
            barrier.wait(timeout=30)
            workflow.block(
                order_id=order.id,
                lab_organization_id=lab.id,
                reason=LabOrderBlockReason.SPECIMEN_UNUSABLE,
                blocked_by_identity_id=collector,
            )
            with lock:
                outcomes["block"] = ("blocked", None)
        except InvalidLabWorkflowTransition as error:
            with lock:
                outcomes["block"] = ("refused", str(error))
        except BaseException as error:  # noqa: BLE001
            with lock:
                outcomes["block"] = ("unexpected", error)

    threads = [threading.Thread(target=collect), threading.Thread(target=block)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert outcomes["collect"][0] != "unexpected", outcomes["collect"]
    assert outcomes["block"][0] != "unexpected", outcomes["block"]
    # The block is always legal from ACCEPTED, so it always wins its own operation.
    assert outcomes["block"] == ("blocked", None)

    with sessions() as session:
        status = session.scalar(select(LabOrder.status).where(LabOrder.id == order.id))
    assert status == LabOrderStatus.BLOCKED.value

    specimens = _count(sessions, LabSpecimen, order.id)
    if outcomes["collect"][0] == "created":
        # The collection got the lock first: its specimen is real and stays real. Resolving a block
        # must never erase the specimen that was there when it happened.
        assert specimens == 1
        assert _count(sessions, LabSampleCollection, order.id) == 1
    else:
        # The block got the lock first: the collection was refused and wrote nothing at all.
        assert outcomes["collect"][0] == "refused"
        assert specimens == 0
        assert _count(sessions, LabSampleCollection, order.id) == 0


def test_pg_spec_3b_no_specimen_is_recordable_once_the_order_is_blocked(sessions: sessionmaker):
    """The deterministic, non-racing half of the same rule, so the race above cannot be the only
    evidence: with the order settled in ``BLOCKED``, a collection is refused, every time."""
    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("blocked")
    )
    workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=str(uuid.uuid4()),
    )
    workflow.block(
        order_id=order.id,
        lab_organization_id=lab.id,
        reason=LabOrderBlockReason.SPECIMEN_INSUFFICIENT,
        blocked_by_identity_id=collector,
    )

    with pytest.raises(InvalidLabWorkflowTransition):
        workflow.record_collection(
            order_id=order.id,
            lab_organization_id=lab.id,
            recorded_by_identity_id=collector,
            idempotency_key=str(uuid.uuid4()),
        )
    assert _count(sessions, LabSpecimen, order.id) == 1


def test_pg_spec_3c_the_blocked_recollection_journey_preserves_both_specimens(
    sessions: sessionmaker,
):
    """The ticket's target story against a real backend: collect, block for an unusable specimen,
    resolve, collect again. Both specimens survive, with distinct ids and real stored instants.

    ``IN_PROGRESS`` is the resume state here, deliberately -- it is the case §16's own worked
    example produces and the one an ``ACCEPTED``-only rule would have made unrecordable."""
    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("journey")
    )
    first = workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=str(uuid.uuid4()),
    )
    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)

    _order, episode, _created = workflow.block(
        order_id=order.id,
        lab_organization_id=lab.id,
        reason=LabOrderBlockReason.SPECIMEN_UNUSABLE,
        blocked_by_identity_id=collector,
        note="Tube inutilisable",
    )
    assert episode.blocked_from_status == LabOrderStatus.IN_PROGRESS.value
    # The specimen that was there when the block happened is still there.
    assert _count(sessions, LabSpecimen, order.id) == 1

    workflow.resolve_block(
        order_id=order.id,
        block_id=episode.id,
        lab_organization_id=lab.id,
        resolved_by_identity_id=collector,
    )
    with sessions() as session:
        status = session.scalar(select(LabOrder.status).where(LabOrder.id == order.id))
    assert status == LabOrderStatus.IN_PROGRESS.value

    second = workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=str(uuid.uuid4()),
    )
    assert second.specimen_created is True
    assert second.milestone_created is False
    assert second.specimen.id != first.specimen.id

    history = workflow.list_specimens_for_order(order.id, lab.id)
    assert [row.id for row in history] == [first.specimen.id, second.specimen.id]
    assert history[0].recorded_at <= history[1].recorded_at
    # The milestone did not move: it records when the collection step began for this order.
    assert second.milestone.recorded_at == first.milestone.recorded_at


# ── PG-SPEC-4: stale operations ──────────────────────────────────────────────────────────────────


def test_pg_spec_4_a_stale_replay_does_not_become_a_new_specimen(sessions: sessionmaker):
    """An old collection request, replayed long after the order moved on and after a *later*
    specimen already exists.

    The replay must still be a replay. It must not become a second physical specimen, must not
    rewrite the instant it originally recorded, and must not resurrect the order's earlier state --
    "a stale operation cannot overwrite newer truth"."""
    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("stale")
    )
    stale_key = str(uuid.uuid4())
    first = workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=stale_key,
    )
    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)
    second = workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=str(uuid.uuid4()),
    )
    assert _count(sessions, LabSpecimen, order.id) == 2

    replayed = workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=stale_key,
    )
    assert replayed.specimen_created is False
    assert replayed.specimen.id == first.specimen.id
    assert replayed.specimen.id != second.specimen.id
    assert replayed.specimen.recorded_at == first.specimen.recorded_at
    assert _count(sessions, LabSpecimen, order.id) == 2

    with sessions() as session:
        status = session.scalar(select(LabOrder.status).where(LabOrder.id == order.id))
    assert status == LabOrderStatus.IN_PROGRESS.value


def test_pg_spec_4b_a_new_key_is_refused_once_the_order_has_left_the_recordable_statuses(
    sessions: sessionmaker,
):
    """And the complement: once the order is no longer work this laboratory holds, a genuinely new
    collection is refused with a controlled conflict rather than silently accepted.

    ``CANCELLED`` is reached by a raw update here rather than through a workflow method, because the
    point is the *collection* path's own refusal and not how the order got there."""
    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("left")
    )
    workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=str(uuid.uuid4()),
    )
    with sessions() as session:
        session.execute(
            LabOrder.__table__.update()
            .where(LabOrder.id == order.id)
            .values(status=LabOrderStatus.CANCELLED.value)
        )
        session.commit()

    with pytest.raises(InvalidLabWorkflowTransition):
        workflow.record_collection(
            order_id=order.id,
            lab_organization_id=lab.id,
            recorded_by_identity_id=collector,
            idempotency_key=str(uuid.uuid4()),
        )
    assert _count(sessions, LabSpecimen, order.id) == 1


# ── PG-SPEC-5: cross-tenant, under contention ───────────────────────────────────────────────────


def test_pg_spec_5_a_rival_laboratory_cannot_win_a_concurrent_collection(sessions: sessionmaker):
    """A rival laboratory's collection attempt racing the owner's legitimate one.

    The rival is refused with ``LabOrderNotAssigned`` -- the 404 that does not disclose whether the
    order exists -- and writes nothing, while the owner succeeds. Tested under contention
    specifically: a tenant check that happened to be evaluated *before* the row lock could pass for
    the rival in the window where the selection row was being read."""
    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("rival")
    )
    organizations = OrganizationRepository(sessions)
    rival = make_lab(organizations, name=f"rival-{uuid.uuid4().hex[:8]}")
    rival_collector = lab_staff(organizations, rival.id)

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    outcomes: dict[str, object] = {}

    def owner() -> None:
        try:
            barrier.wait(timeout=30)
            outcome = workflow.record_collection(
                order_id=order.id,
                lab_organization_id=lab.id,
                recorded_by_identity_id=collector,
                idempotency_key=str(uuid.uuid4()),
            )
            with lock:
                outcomes["owner"] = ("ok", outcome.specimen_created)
        except BaseException as error:  # noqa: BLE001
            with lock:
                outcomes["owner"] = ("error", error)

    def intruder() -> None:
        try:
            barrier.wait(timeout=30)
            workflow.record_collection(
                order_id=order.id,
                lab_organization_id=rival.id,
                recorded_by_identity_id=rival_collector,
                idempotency_key=str(uuid.uuid4()),
            )
            with lock:
                outcomes["rival"] = ("WROTE", None)
        except LabOrderNotAssigned:
            with lock:
                outcomes["rival"] = ("refused", None)
        except BaseException as error:  # noqa: BLE001
            with lock:
                outcomes["rival"] = ("unexpected", error)

    threads = [threading.Thread(target=owner), threading.Thread(target=intruder)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert outcomes["owner"] == ("ok", True), outcomes["owner"]
    assert outcomes["rival"] == ("refused", None), outcomes["rival"]
    assert _count(sessions, LabSpecimen, order.id) == 1

    with sessions() as session:
        owning = session.scalars(
            select(LabSpecimen.lab_organization_id).where(LabSpecimen.lab_order_id == order.id)
        ).all()
        selection_lab = session.scalar(
            select(LabSelection.lab_organization_id).where(LabSelection.lab_order_id == order.id)
        )
    assert owning == [lab.id]
    assert selection_lab == lab.id
    assert rival.id not in owning


# ── PG-SPEC-6: the real schema ──────────────────────────────────────────────────────────────────


def test_pg_spec_6_two_specimens_for_one_order_actually_insert(sessions: sessionmaker):
    """The schema-level proof that ``lab_order_id`` is not unique: two rows, inserted directly,
    both land. Under SQLite with foreign keys off this proves less than it looks; here it is the
    real constraint set answering."""
    _provider, lab, order, _workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("twoinsert")
    )
    with sessions() as session:
        session.add_all(
            [
                LabSpecimen(
                    lab_order_id=order.id,
                    lab_organization_id=lab.id,
                    recorded_by_identity_id=collector,
                    idempotency_key=None,
                ),
                LabSpecimen(
                    lab_order_id=order.id,
                    lab_organization_id=lab.id,
                    recorded_by_identity_id=collector,
                    idempotency_key=None,
                ),
            ]
        )
        session.commit()
    assert _count(sessions, LabSpecimen, order.id) == 2


def test_pg_spec_6b_the_composite_key_uniqueness_is_enforced_by_the_database(
    sessions: sessionmaker,
):
    """And the uniqueness that *is* there: the same ``(lab_order_id, idempotency_key)`` twice raises
    at the database, not merely in the service layer.

    This is the second layer behind the row lock -- the same relationship
    ``uq_exchange_lab_order_blocks_one_active_per_order`` has to ``block``'s own lock -- and it is
    invisible on SQLite."""
    from sqlalchemy.exc import IntegrityError

    _provider, lab, order, _workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("dupkey")
    )
    shared = str(uuid.uuid4())
    with sessions() as session:
        session.add(
            LabSpecimen(
                lab_order_id=order.id,
                lab_organization_id=lab.id,
                recorded_by_identity_id=collector,
                idempotency_key=shared,
            )
        )
        session.commit()
    with pytest.raises(IntegrityError), sessions() as session:
        session.add(
            LabSpecimen(
                lab_order_id=order.id,
                lab_organization_id=lab.id,
                recorded_by_identity_id=collector,
                idempotency_key=shared,
            )
        )
        session.commit()
    assert _count(sessions, LabSpecimen, order.id) == 1


def test_pg_spec_6c_every_foreign_key_is_enforced_and_restricts(sessions: sessionmaker):
    """All three foreign keys, against the backend that actually enforces them.

    ``recorded_by_identity_id`` matters most: it is ``NOT NULL`` precisely so provenance cannot be
    absent, and the unit suite -- where ``PRAGMA foreign_keys`` is off -- would have accepted a
    fabricated UUID without complaint. ``RESTRICT`` is then checked by deleting the order,
    which must be refused while physical-sample history references it."""
    from sqlalchemy.exc import IntegrityError

    _provider, lab, order, _workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("fks")
    )

    # A collector identity that does not exist.
    with pytest.raises(IntegrityError), sessions() as session:
        session.add(
            LabSpecimen(
                lab_order_id=order.id,
                lab_organization_id=lab.id,
                recorded_by_identity_id=uuid.uuid4(),
                idempotency_key=None,
            )
        )
        session.commit()

    # An order that does not exist.
    with pytest.raises(IntegrityError), sessions() as session:
        session.add(
            LabSpecimen(
                lab_order_id=uuid.uuid4(),
                lab_organization_id=lab.id,
                recorded_by_identity_id=collector,
                idempotency_key=None,
            )
        )
        session.commit()

    # An organization that does not exist.
    with pytest.raises(IntegrityError), sessions() as session:
        session.add(
            LabSpecimen(
                lab_order_id=order.id,
                lab_organization_id=uuid.uuid4(),
                recorded_by_identity_id=collector,
                idempotency_key=None,
            )
        )
        session.commit()

    # RESTRICT: an order carrying specimen history cannot be deleted out from under it.
    with sessions() as session:
        session.add(
            LabSpecimen(
                lab_order_id=order.id,
                lab_organization_id=lab.id,
                recorded_by_identity_id=collector,
                idempotency_key=None,
            )
        )
        session.commit()
    with pytest.raises(IntegrityError), sessions() as session:
        session.execute(LabOrder.__table__.delete().where(LabOrder.id == order.id))
        session.commit()
    assert _count(sessions, LabSpecimen, order.id) == 1


# ── PG-SPEC-7: the invariant, at real timestamp precision ───────────────────────────────────────


def test_pg_spec_7_the_milestone_and_the_first_specimen_store_one_instant(sessions: sessionmaker):
    """The source-of-truth invariant against ``timestamptz`` with microsecond precision.

    Two ``utc_now()`` calls would differ here by a measurable amount, so this is where "one clock
    read written to both rows" is genuinely proved rather than merely not disproved. Read back from
    the database rather than from the returned records, so the stored values are what is
    compared."""
    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("instant")
    )
    workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=str(uuid.uuid4()),
    )
    with sessions() as session:
        milestone_at = session.scalar(
            select(LabSampleCollection.recorded_at).where(
                LabSampleCollection.lab_order_id == order.id
            )
        )
        specimen_at = session.scalar(
            select(LabSpecimen.recorded_at)
            .where(LabSpecimen.lab_order_id == order.id)
            .order_by(LabSpecimen.recorded_at.asc())
            .limit(1)
        )
    assert milestone_at is not None
    assert specimen_at is not None
    assert milestone_at == specimen_at
    assert milestone_at.tzinfo is not None, "timestamptz must round-trip as tz-aware"


def test_pg_spec_7b_a_later_specimen_does_not_move_the_milestone(sessions: sessionmaker):
    """And the invariant's other half: the milestone is written once. A second specimen leaves it
    exactly where it was, so "the milestone's instant equals the *earliest* specimen's" stays true
    rather than drifting to the latest."""
    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("nomove")
    )
    first = workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=str(uuid.uuid4()),
    )
    workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=str(uuid.uuid4()),
    )
    with sessions() as session:
        milestone_at = session.scalar(
            select(LabSampleCollection.recorded_at).where(
                LabSampleCollection.lab_order_id == order.id
            )
        )
        earliest, latest = session.execute(
            select(func.min(LabSpecimen.recorded_at), func.max(LabSpecimen.recorded_at)).where(
                LabSpecimen.lab_order_id == order.id
            )
        ).one()
    assert milestone_at == earliest
    assert earliest <= latest
    assert milestone_at == first.milestone.recorded_at


# ── PG-SPEC-8: VARCHAR(128) ─────────────────────────────────────────────────────────────────────


def test_pg_spec_8_the_key_column_width_is_real(sessions: sessionmaker):
    """``VARCHAR(128)`` is enforced here and is not on SQLite.

    A 128-character key stores and replays correctly. A 129-character one is refused by the API
    boundary's own ``max_length`` before it can reach PostgreSQL -- that refusal is asserted in the
    unit suite, where the HTTP layer is in scope; what is asserted *here* is the half that only a
    real backend can show, namely that the column genuinely rejects an over-width value rather than
    silently truncating it the way SQLite would."""
    from sqlalchemy.exc import DataError

    _provider, lab, order, workflow, _orders, collector = _seed_accepted_order(
        sessions, key_prefix=_prefix("width")
    )
    at_limit = "k" * 128
    outcome = workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=at_limit,
    )
    assert outcome.specimen_created is True
    replay = workflow.record_collection(
        order_id=order.id,
        lab_organization_id=lab.id,
        recorded_by_identity_id=collector,
        idempotency_key=at_limit,
    )
    assert replay.specimen_created is False
    assert replay.specimen.id == outcome.specimen.id

    with pytest.raises(DataError), sessions() as session:
        session.add(
            LabSpecimen(
                lab_order_id=order.id,
                lab_organization_id=lab.id,
                recorded_by_identity_id=collector,
                idempotency_key="k" * 129,
            )
        )
        session.commit()
    assert _count(sessions, LabSpecimen, order.id) == 1
