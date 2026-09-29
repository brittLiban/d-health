"""DHEALTH-LAB-BLOCKED-001: real PostgreSQL qualification of the blocked-order workflow.

The SQLite unit suite (``tests/test_dhealthlabblocked001_blocked_orders.py``) proves every *rule*
this workflow has. It cannot prove the properties that are load-bearing under concurrency, and it
cannot prove them for three specific, stated reasons:

1. **SQLAlchemy's SQLite dialect omits ``FOR UPDATE`` entirely**, so the ``LabOrder`` row lock that
   actually serializes two workers blocking the same order is silently a no-op there. SQLite's own
   whole-database writer serialization then makes those tests pass for a reason unrelated to the
   lock being taken -- the "green for the wrong reason" shape this repository has been burned by
   before.
2. **This suite's SQLite engine does not enable ``PRAGMA foreign_keys``** (``tests/conftest.py``),
   so the composite foreign key that structurally forbids a cross-tenant block row is not enforced
   there at all. It is asserted structurally in the unit suite and *behaviourally* only here.
3. **SQLite does not enforce ``VARCHAR(n)`` length.** An over-long note therefore reaches
   PostgreSQL as a ``DataError`` that the unit suite cannot see -- DH-EX-007A's lesson, checked
   here against a real backend.

What is qualified in this file, and nowhere else:

- **PG-BLOCK-1** Two workers blocking the same order at the same instant -> at most one active
  episode. Without the lock both read ``ACCEPTED``, both insert, and the partial unique index is
  the only thing left standing between the product and two simultaneous "why is this blocked?"
  answers.
- **PG-BLOCK-2** Block racing a workflow advance -> one serialized, truthful outcome. Never an
  order that is ``IN_PROGRESS`` *and* carries an unresolved block.
- **PG-BLOCK-3** Two workers resolving the same episode -> one durable resolution, one replay, and
  the resolver recorded is the one who actually won.
- **PG-BLOCK-4** Block racing release -> never ``COMPLETED`` with an active block.
- **PG-BLOCK-5** The cross-tenant composite foreign key, refused by the database itself.
- **PG-BLOCK-6** Many resolved episodes are allowed; a second *unresolved* one is not.
- Plus the CHECK constraints (reason catalog, source state, resolution consistency,
  ``resolved_at >= blocked_at``), the ``RESTRICT`` delete guard, and the ``VARCHAR`` boundary.

**The proof is the barrier plus the final state, not an inspection of the lock.** If the lock were
not held, the observable outcome would be two active episodes or a contradictory row -- which is
what these assertions catch. Nothing here reads ``pg_locks``.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHBLOCK_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHBLOCK_PG_HOST=127.0.0.1 \\
    DHBLOCK_PG_PORT=5432 \\
    DHBLOCK_PG_USER=adapter \\
    DHBLOCK_PG_PASSWORD=... \\
    DHBLOCK_PG_DATABASE=dhblock_qual \\
        pytest adapter/tests/integration/test_dhealthlabblocked001_postgres_qualification.py -v

The target database must already have migrations applied through ``0037_lab_order_blocks``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.

All patient, catalog and note data here is synthetic. No real patient data is used.
"""

from __future__ import annotations

import os
import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.lab_workflow import (
    ConflictingBlockRequest,
    InvalidLabWorkflowTransition,
    LabWorkflowRepository,
)
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    LabOrder,
    LabOrderBlock,
    LabOrderBlockReason,
    LabOrderItem,
    LabOrderStatus,
    LabSampleCollection,
    LabSelection,
    Patient,
    PatientReference,
    PractitionerReference,
)
from daher_adapter.exchange.organizations import OrganizationRepository

PG_HOST = os.environ.get("DHBLOCK_PG_HOST")
PG_PORT = os.environ.get("DHBLOCK_PG_PORT")
PG_USER = os.environ.get("DHBLOCK_PG_USER")
PG_PASSWORD = os.environ.get("DHBLOCK_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHBLOCK_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHBLOCK_PG_* env vars) -- never in CI",
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


def _workflow(sessions: sessionmaker) -> LabWorkflowRepository:
    return LabWorkflowRepository(sessions, OrganizationRepository(sessions))


def _seed(sessions: sessionmaker, *, prefix: str, status: str = LabOrderStatus.ACCEPTED.value):
    """One provider, one laboratory, two laboratory identities, and one assigned order.

    The order is inserted at the requested status with its ``LabSelection`` in the same
    transaction, because what this file qualifies is the *block* workflow rather than the routing
    that precedes it -- which the unit suite already drives through the real endpoints. The
    ``LabSelection`` is genuine and not a shortcut: it is the composite foreign key's target, so
    PG-BLOCK-5 would be meaningless without it.
    """
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)

    provider = organizations.create(org_type="PROVIDER", name=f"{prefix}-clinic")
    lab = organizations.create(org_type="LAB", name=f"{prefix}-lab")
    rival = organizations.create(org_type="LAB", name=f"{prefix}-rival")

    entry, _ = catalog.create_entry(code=f"{prefix}-BLK", display_name="Analyse synthétique")
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

    with sessions.begin() as session:
        # `PatientReference.patient_id` carries a *composite* foreign key to
        # `(exchange_patients.id, provider_organization_id)`, so a real `Patient` has to exist
        # first -- the reference is provenance *for* a directory record, never a stand-in for one.
        patient = Patient(
            provider_organization_id=provider.id,
            display_name=f"{prefix} patient (synthetic)",
        )
        session.add(patient)
        session.flush()
        patient_ref = PatientReference(
            source_organization_id=provider.id,
            patient_id=patient.id,
            source_system="SYNTHETIC",
            source_identity_system="urn:dhealth:synthetic",
            source_identity_value=f"{prefix}-patient",
            display_name=f"{prefix} patient (synthetic)",
        )
        practitioner_ref = PractitionerReference(
            source_organization_id=provider.id,
            source_system="SYNTHETIC",
            source_identity_system="urn:dhealth:synthetic",
            source_identity_value=f"{prefix}-practitioner",
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
        if status == LabOrderStatus.IN_PROGRESS.value:
            # An IN_PROGRESS order always has one in production (DH-EX-011), so seeding it without
            # one would be building a state the product cannot produce.
            session.add(LabSampleCollection(lab_order_id=order.id, lab_organization_id=lab.id))
        order_id = order.id

    return {
        "provider_id": provider.id,
        "lab_id": lab.id,
        "rival_id": rival.id,
        "order_id": order_id,
        "workers": workers,
    }


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
    successes = [value for _index, value in outcomes if not isinstance(value, Exception)]
    failures = [value for _index, value in outcomes if isinstance(value, Exception)]
    return successes, failures


def _episodes(sessions: sessionmaker, order_id: uuid.UUID) -> list[LabOrderBlock]:
    with sessions() as session:
        return list(
            session.scalars(
                select(LabOrderBlock)
                .where(LabOrderBlock.lab_order_id == order_id)
                .order_by(LabOrderBlock.blocked_at)
            ).all()
        )


def _active_count(sessions: sessionmaker, order_id: uuid.UUID) -> int:
    with sessions() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(LabOrderBlock)
                .where(
                    LabOrderBlock.lab_order_id == order_id,
                    LabOrderBlock.resolved_at.is_(None),
                )
            )
            or 0
        )


def _status(sessions: sessionmaker, order_id: uuid.UUID) -> str:
    with sessions() as session:
        order = session.get(LabOrder, order_id)
        assert order is not None
        return order.status


# == PG-BLOCK-1: two simultaneous blocks ========================================================


def test_pg_block_1_two_concurrent_blocks_produce_at_most_one_active_episode(sessions):
    """Two technicians at two benches reach for the same order in the same instant.

    Without the ``FOR UPDATE`` lock both read ``ACCEPTED``, both see no active episode, and both
    insert -- leaving the order with two simultaneous, contradictory answers to "why is this
    blocked?" and no way to decide which resolution resumes it. Exactly one episode must exist
    afterwards, and the order must be ``BLOCKED`` exactly once.

    Six threads, not two: the wider the race, the less this depends on the barrier having produced
    the worst interleaving on any one run.
    """
    world = _seed(sessions, prefix="pgb1")
    workflow = _workflow(sessions)
    order_id, lab_id = world["order_id"], world["lab_id"]
    reasons = [
        LabOrderBlockReason.SPECIMEN_UNUSABLE,
        LabOrderBlockReason.SPECIMEN_INSUFFICIENT,
        LabOrderBlockReason.PATIENT_DID_NOT_ATTEND,
    ]

    outcomes = _run_concurrently(
        [
            (
                lambda index=index: workflow.block(
                    order_id=order_id,
                    lab_organization_id=lab_id,
                    reason=reasons[index % len(reasons)],
                    blocked_by_identity_id=world["workers"][index % 3],
                    note=f"note synthétique {index}",
                )
            )
            for index in range(6)
        ]
    )
    successes, failures = _split(outcomes)

    assert _active_count(sessions, order_id) == 1, "more than one unresolved episode survived"
    assert len(_episodes(sessions, order_id)) == 1, "a losing thread left a stray episode behind"
    assert _status(sessions, order_id) == LabOrderStatus.BLOCKED.value

    # Exactly one thread genuinely created the episode; every other outcome is either a refusal or
    # an idempotent replay of the winner's identical request -- never a second creation.
    created = [result for result in successes if isinstance(result, tuple) and result[2] is True]
    assert len(created) == 1, f"{len(created)} threads believed they created the block"
    for failure in failures:
        assert isinstance(failure, (ConflictingBlockRequest, IntegrityError)), failure

    # Every surviving caller was told something true: the reason they can read is the winner's.
    stored = _episodes(sessions, order_id)[0]
    assert stored.reason == created[0][1].reason


# == PG-BLOCK-2: block racing a workflow advance =================================================


def test_pg_block_2_block_racing_start_processing_yields_one_truthful_outcome(sessions):
    """One worker blocks while another starts the analysis. Both are legal from ``ACCEPTED`` and
    they lead to incompatible states, so the pair must produce one success and one refusal --
    never an order that is ``IN_PROGRESS`` while carrying an unresolved block, which is the state
    in which the queue says work is progressing and the block history says it is not.
    """
    world = _seed(sessions, prefix="pgb2")
    workflow = _workflow(sessions)
    order_id, lab_id = world["order_id"], world["lab_id"]
    with sessions.begin() as session:
        session.add(LabSampleCollection(lab_order_id=order_id, lab_organization_id=lab_id))

    outcomes = _run_concurrently(
        [
            lambda: workflow.block(
                order_id=order_id,
                lab_organization_id=lab_id,
                reason=LabOrderBlockReason.SPECIMEN_UNUSABLE,
                blocked_by_identity_id=world["workers"][0],
            ),
            lambda: workflow.start_processing(order_id=order_id, lab_organization_id=lab_id),
        ]
    )
    _successes, failures = _split(outcomes)

    status = _status(sessions, order_id)
    active = _active_count(sessions, order_id)
    episodes = _episodes(sessions, order_id)

    # ── Two legal outcomes, and the second one is not a failure ──
    #
    # This assertion was initially written as "exactly one of the two must be refused", and that
    # was wrong about the domain rather than about the code. The two orderings are not symmetric:
    #
    #   block wins first  -> the order is BLOCKED, and `start_processing` is then refused, because
    #                        IN_PROGRESS is reachable only from ACCEPTED.
    #   start wins first  -> the order is IN_PROGRESS, and blocking it is then perfectly
    #                        legitimate -- IN_PROGRESS is one of the two states work may be
    #                        blocked from. Both calls succeed, in sequence, and nothing is untrue.
    #
    # So what must hold is not a failure count. It is that the surviving state is *self-consistent*
    # and that the episode, if one exists, records the state it genuinely interrupted -- which is
    # the thing the row lock is actually responsible for. An episode claiming it interrupted
    # ACCEPTED on an order that had already started would be the real defect: the resolution would
    # later resume it to ACCEPTED, silently erasing the fact that processing had begun.
    assert (status, active) in {
        (LabOrderStatus.BLOCKED.value, 1),
        (LabOrderStatus.IN_PROGRESS.value, 0),
    }, f"impossible combination: status={status!r} active_blocks={active}"
    assert len(episodes) <= 1

    if episodes:
        episode = episodes[0]
        assert episode.blocked_from_status in {
            LabOrderStatus.ACCEPTED.value,
            LabOrderStatus.IN_PROGRESS.value,
        }
        if not failures:
            # Both succeeded, so the advance committed before the block. The episode must say so;
            # if it recorded ACCEPTED here, resolving it would rewind the order past a transition
            # that really happened.
            assert episode.blocked_from_status == LabOrderStatus.IN_PROGRESS.value, (
                "both calls succeeded, so start-processing won first -- but the episode claims it "
                "interrupted ACCEPTED, which would resume the order to a state it had left"
            )
    for failure in failures:
        assert isinstance(failure, InvalidLabWorkflowTransition), failure


# == PG-BLOCK-3: two simultaneous resolutions ====================================================


def test_pg_block_3_two_concurrent_resolutions_yield_one_durable_resolution(sessions):
    """Both callers are told the block is resolved, because it is -- but only one resolution is
    recorded, and it belongs to whoever actually won.

    The failure this catches is not a crash. It is the second thread quietly overwriting
    ``resolved_by_identity_id`` and ``resolved_at``, which would make the history name the wrong
    person as the one who took responsibility for resuming the work.
    """
    world = _seed(sessions, prefix="pgb3")
    workflow = _workflow(sessions)
    order_id, lab_id = world["order_id"], world["lab_id"]

    _record, episode, created = workflow.block(
        order_id=order_id,
        lab_organization_id=lab_id,
        reason=LabOrderBlockReason.SPECIMEN_UNUSABLE,
        blocked_by_identity_id=world["workers"][0],
    )
    assert created

    outcomes = _run_concurrently(
        [
            (
                lambda index=index: workflow.resolve_block(
                    order_id=order_id,
                    block_id=episode.id,
                    lab_organization_id=lab_id,
                    resolved_by_identity_id=world["workers"][index % 3],
                    resolution_note=f"résolution synthétique {index}",
                )
            )
            for index in range(4)
        ]
    )
    successes, failures = _split(outcomes)

    assert failures == [], f"a resolution replay was refused rather than replayed: {failures}"
    resolved_flags = [result[2] for result in successes]
    assert resolved_flags.count(True) == 1, "more than one thread performed the resolution"

    stored = _episodes(sessions, order_id)
    assert len(stored) == 1
    assert stored[0].resolved_at is not None
    assert stored[0].resolved_by_identity_id is not None
    # Every replay reported the same stored resolution it did not perform.
    for result in successes:
        assert result[1].resolved_at == stored[0].resolved_at
        assert result[1].resolved_by_identity_id == stored[0].resolved_by_identity_id

    assert _status(sessions, order_id) == LabOrderStatus.ACCEPTED.value
    assert _active_count(sessions, order_id) == 0


# == PG-BLOCK-4: block racing release ============================================================


def test_pg_block_4_block_racing_release_never_yields_completed_with_an_active_block(sessions):
    """A releaser publishes while a technician blocks. ``COMPLETED`` is a released clinical fact
    and an active block says the work never finished; an order asserting both would mean a result
    was published from work the laboratory had declared unable to continue.

    Release is driven here through ``LabOrderRepository.transition`` rather than the full release
    repository, because what is being raced is the *status transition* and its lock, and
    reproducing the whole entry-and-fingerprint path would add moving parts without adding
    coverage of the race. The transition primitive takes the identical ``FOR UPDATE`` lock on the
    identical row, which is what serializes the pair.
    """
    from daher_adapter.exchange.models import LabResultRelease
    from daher_adapter.exchange.orders import InvalidOrderTransition, LabOrderRepository

    world = _seed(sessions, prefix="pgb4", status=LabOrderStatus.IN_PROGRESS.value)
    workflow = _workflow(sessions)
    order_id, lab_id = world["order_id"], world["lab_id"]
    orders = LabOrderRepository(
        sessions,
        OrganizationRepository(sessions),
        CatalogRepository(sessions, OrganizationRepository(sessions)),
    )
    # The release record the COMPLETED transition requires (DH-EX-012), so the release half of the
    # race is genuinely able to win rather than failing for an unrelated reason.
    with sessions.begin() as session:
        session.add(
            LabResultRelease(
                lab_order_id=order_id,
                lab_organization_id=lab_id,
                released_by_identity_id=world["workers"][1],
            )
        )

    outcomes = _run_concurrently(
        [
            lambda: workflow.block(
                order_id=order_id,
                lab_organization_id=lab_id,
                reason=LabOrderBlockReason.CLARIFICATION_REQUIRED,
                blocked_by_identity_id=world["workers"][0],
            ),
            lambda: orders.transition(order_id, LabOrderStatus.COMPLETED),
        ]
    )
    _successes, failures = _split(outcomes)

    status = _status(sessions, order_id)
    active = _active_count(sessions, order_id)

    assert not (status == LabOrderStatus.COMPLETED.value and active > 0), (
        "the order is RELEASED and BLOCKED at the same time"
    )
    assert (status, active) in {
        (LabOrderStatus.BLOCKED.value, 1),
        (LabOrderStatus.COMPLETED.value, 0),
    }, f"impossible combination: status={status!r} active_blocks={active}"
    assert len(failures) == 1, "both operations claimed to succeed"
    assert isinstance(failures[0], (InvalidLabWorkflowTransition, InvalidOrderTransition))


# == PG-BLOCK-5: the tenant boundary, in the database ============================================


def test_pg_block_5_a_cross_tenant_block_row_is_structurally_refused(sessions):
    """``fk_exchange_lab_order_blocks_selection`` is a *composite* foreign key on
    ``(lab_order_id, lab_organization_id)``. A block naming a laboratory this order is not assigned
    to therefore cannot be inserted at all -- not "is rejected by the service layer", but cannot
    exist. This is the assertion the SQLite suite cannot make, because that engine enforces no
    foreign key at all.

    Two independent single-column foreign keys would both be satisfied by this row: the order is
    real and the rival laboratory is real. It is the *pair* that is a fabrication, which is why
    the constraint is composite.
    """
    world = _seed(sessions, prefix="pgb5")

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.add(
                LabOrderBlock(
                    lab_order_id=world["order_id"],
                    lab_organization_id=world["rival_id"],
                    reason=LabOrderBlockReason.SPECIMEN_UNUSABLE.value,
                    blocked_from_status=LabOrderStatus.ACCEPTED.value,
                    blocked_by_identity_id=world["workers"][0],
                )
            )

    assert _episodes(sessions, world["order_id"]) == []

    # The service layer refuses it too, and with a 404-shaped error rather than a leak.
    from daher_adapter.exchange.lab_workflow import LabOrderNotAssigned

    with pytest.raises(LabOrderNotAssigned):
        _workflow(sessions).block(
            order_id=world["order_id"],
            lab_organization_id=world["rival_id"],
            reason=LabOrderBlockReason.SPECIMEN_UNUSABLE,
            blocked_by_identity_id=world["workers"][0],
        )


def test_pg_block_5b_a_block_restricts_deletion_of_the_order_it_explains(sessions):
    """``RESTRICT``, not ``CASCADE``. A laboratory's record of why work stopped is not collateral
    to be swept away with the row it refers to -- and an order with block history is exactly the
    kind of thing somebody later needs to explain."""
    world = _seed(sessions, prefix="pgb5b")
    _workflow(sessions).block(
        order_id=world["order_id"],
        lab_organization_id=world["lab_id"],
        reason=LabOrderBlockReason.SPECIMEN_UNUSABLE,
        blocked_by_identity_id=world["workers"][0],
    )

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text("DELETE FROM exchange_lab_orders WHERE id = :id"),
                {"id": str(world["order_id"])},
            )
    assert len(_episodes(sessions, world["order_id"])) == 1


# == PG-BLOCK-6: many resolved, at most one active ===============================================


def test_pg_block_6_many_resolved_episodes_are_allowed_but_only_one_unresolved(sessions):
    """The partial unique index, stated as the product rule it encodes: an order may be trouble
    repeatedly, and may be trouble for only one reason at a time.

    A plain unique index on ``lab_order_id`` would enforce the second half and destroy the first,
    which is precisely the "one mutable ``block_reason`` column" design this table exists to
    avoid.
    """
    world = _seed(sessions, prefix="pgb6")
    workflow = _workflow(sessions)
    order_id, lab_id = world["order_id"], world["lab_id"]

    for cycle in range(4):
        _record, episode, created = workflow.block(
            order_id=order_id,
            lab_organization_id=lab_id,
            reason=LabOrderBlockReason.SPECIMEN_INSUFFICIENT,
            blocked_by_identity_id=world["workers"][cycle % 3],
            note=f"cycle {cycle}",
        )
        assert created, f"cycle {cycle} did not create a new episode"
        workflow.resolve_block(
            order_id=order_id,
            block_id=episode.id,
            lab_organization_id=lab_id,
            resolved_by_identity_id=world["workers"][0],
        )

    assert len(_episodes(sessions, order_id)) == 4
    assert _active_count(sessions, order_id) == 0
    # Every one of them is still individually readable, with its own note intact.
    assert [e.note for e in _episodes(sessions, order_id)] == [f"cycle {i}" for i in range(4)]

    # A fifth, left unresolved, is fine. A sixth alongside it is refused by the index itself.
    workflow.block(
        order_id=order_id,
        lab_organization_id=lab_id,
        reason=LabOrderBlockReason.SPECIMEN_UNUSABLE,
        blocked_by_identity_id=world["workers"][0],
    )
    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.add(
                LabOrderBlock(
                    lab_order_id=order_id,
                    lab_organization_id=lab_id,
                    reason=LabOrderBlockReason.PATIENT_DID_NOT_ATTEND.value,
                    blocked_from_status=LabOrderStatus.ACCEPTED.value,
                    blocked_by_identity_id=world["workers"][1],
                )
            )
    assert _active_count(sessions, order_id) == 1


# == The CHECK constraints, against a real backend ===============================================


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("reason outside the catalog", {"reason": "PAYMENT_REQUIRED"}),
        ("a source state that cannot be blocked", {"blocked_from_status": "COMPLETED"}),
    ],
)
def test_the_check_constraints_refuse_impossible_rows(sessions, label, kwargs):
    world = _seed(sessions, prefix=f"pgchk-{abs(hash(label)) % 10000}")
    base = {
        "lab_order_id": world["order_id"],
        "lab_organization_id": world["lab_id"],
        "reason": LabOrderBlockReason.SPECIMEN_UNUSABLE.value,
        "blocked_from_status": LabOrderStatus.ACCEPTED.value,
        "blocked_by_identity_id": world["workers"][0],
    }
    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.add(LabOrderBlock(**{**base, **kwargs}))


def test_a_resolution_without_a_resolver_is_refused(sessions):
    """A resolution with no actor is an event nobody owns, and a resolver with no instant owns
    nothing. Both halves are present or both absent -- the database, not a service method, is what
    guarantees it."""
    world = _seed(sessions, prefix="pgchk-res")
    workflow = _workflow(sessions)
    _record, episode, _created = workflow.block(
        order_id=world["order_id"],
        lab_organization_id=world["lab_id"],
        reason=LabOrderBlockReason.SPECIMEN_UNUSABLE,
        blocked_by_identity_id=world["workers"][0],
    )

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text("UPDATE exchange_lab_order_blocks SET resolved_at = now() WHERE id = :id"),
                {"id": str(episode.id)},
            )

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text(
                    "UPDATE exchange_lab_order_blocks "
                    "SET resolved_by_identity_id = :who WHERE id = :id"
                ),
                {"id": str(episode.id), "who": str(world["workers"][0])},
            )


def test_a_resolution_cannot_precede_its_own_block(sessions):
    world = _seed(sessions, prefix="pgchk-order")
    workflow = _workflow(sessions)
    _record, episode, _created = workflow.block(
        order_id=world["order_id"],
        lab_organization_id=world["lab_id"],
        reason=LabOrderBlockReason.SPECIMEN_UNUSABLE,
        blocked_by_identity_id=world["workers"][0],
    )

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text(
                    "UPDATE exchange_lab_order_blocks "
                    "SET resolved_at = blocked_at - interval '1 hour', "
                    "    resolved_by_identity_id = :who "
                    "WHERE id = :id"
                ),
                {"id": str(episode.id), "who": str(world["workers"][0])},
            )


def test_a_resolution_note_on_an_unresolved_episode_is_refused(sessions):
    world = _seed(sessions, prefix="pgchk-note")
    workflow = _workflow(sessions)
    _record, episode, _created = workflow.block(
        order_id=world["order_id"],
        lab_organization_id=world["lab_id"],
        reason=LabOrderBlockReason.SPECIMEN_UNUSABLE,
        blocked_by_identity_id=world["workers"][0],
    )

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text(
                    "UPDATE exchange_lab_order_blocks "
                    "SET resolution_note = 'note sur un blocage non résolu' WHERE id = :id"
                ),
                {"id": str(episode.id)},
            )


def test_an_over_long_note_is_refused_by_the_real_varchar_boundary(sessions):
    """DH-EX-007A's lesson, applied to this ticket's two free-text columns.

    SQLite does not enforce ``VARCHAR(n)``, so an over-long note passes the unit suite, reaches
    PostgreSQL, raises ``DataError`` rather than ``IntegrityError``, and surfaces to the caller as
    a raw 500. The API schema's ``max_length=1024`` is what stops it in production; this asserts
    that the column the schema claims to mirror really is that wide and really does refuse more.
    """
    world = _seed(sessions, prefix="pgvarchar")
    with pytest.raises(DBAPIError):
        with sessions.begin() as session:
            session.add(
                LabOrderBlock(
                    lab_order_id=world["order_id"],
                    lab_organization_id=world["lab_id"],
                    reason=LabOrderBlockReason.SPECIMEN_UNUSABLE.value,
                    blocked_from_status=LabOrderStatus.ACCEPTED.value,
                    blocked_by_identity_id=world["workers"][0],
                    note="x" * 1025,
                )
            )

    # Exactly at the boundary is accepted, so the assertion above is about the limit rather than
    # about notes in general.
    with sessions.begin() as session:
        session.add(
            LabOrderBlock(
                lab_order_id=world["order_id"],
                lab_organization_id=world["lab_id"],
                reason=LabOrderBlockReason.SPECIMEN_UNUSABLE.value,
                blocked_from_status=LabOrderStatus.ACCEPTED.value,
                blocked_by_identity_id=world["workers"][0],
                note="x" * 1024,
            )
        )
    assert _active_count(sessions, world["order_id"]) == 1
